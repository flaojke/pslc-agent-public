"""Fixed local read-only collectors. Raw output is bounded RAM, never a file."""
import base64
from datetime import datetime,timezone,timedelta
import hashlib
import json
import math
import platform
import shutil
import time
from pydantic import ValidationError
from backend.diagnostic_types import admit_scope,scope_hash,timestamp,EvidenceRecord,EvidenceEnvelope,Coverage,canonical

MAX_BYTES=65536
MAX_SECONDS=30


def windows_argv(scope,channel,executable):
    # Only normalized dates, an enum channel and a bounded integer enter this fixed script.
    script=r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
try {
  $start=[DateTime]::Parse('__START__',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
  $end=[DateTime]::Parse('__END__',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
  $items=@(Get-WinEvent -FilterHashtable @{LogName='__CHANNEL__';StartTime=$start;EndTime=$end;Level=@(1,2,3)} -MaxEvents __COUNT__ -ErrorAction Stop)
  foreach($item in $items){
    $message='';$missing=$false;try{$message=[string]$item.Message}catch{$missing=$true}
    $provider=[string]$item.ProviderName
    $severity=switch([int]$item.Level){1{'critical'} 2{'error'} 3{'warning'}}
    [ordered]@{type='event';at=$item.TimeCreated.ToUniversalTime().ToString('o');event_id=[long]$item.Id;record_id=[long]$item.RecordId;severity=$severity;provider=$provider.Substring(0,[Math]::Min(128,$provider.Length));message=$message.Substring(0,[Math]::Min(512,$message.Length));text_truncated=($missing -or $message.Length -gt 512)} | ConvertTo-Json -Compress -Depth 3
  }
  @{type='coverage';status='complete'} | ConvertTo-Json -Compress
}catch{
  $status='collector_failed'
  if($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*'){$status='complete'}
  elseif($_.CategoryInfo.Category -eq 'PermissionDenied' -or $_.Exception -is [System.UnauthorizedAccessException]){$status='access_denied'}
  @{type='coverage';status=$status} | ConvertTo-Json -Compress
}
'''
    script=script.replace('__START__',scope.start_utc).replace('__END__',scope.end_utc).replace('__CHANNEL__',channel).replace('__COUNT__',str(scope.max_events+1))
    encoded=base64.b64encode(script.encode('utf-16le')).decode()
    return [executable,'-NoProfile','-NonInteractive','-EncodedCommand',encoded]


def journal_argv(scope,executable):
    date=lambda value:timestamp(value).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
    return [executable,'--system','--no-pager','--quiet','--utc','--all','--output=json',
        '--output-fields=__CURSOR,__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,UNIT,OBJECT_SYSTEMD_UNIT,PRIORITY,SYSLOG_IDENTIFIER,MESSAGE',
        '--unit='+scope.unit,'--since='+date(scope.start_utc),'--until='+date(scope.end_utc),
        '--priority=warning','--lines='+str(scope.max_events+1)]


def record_from_windows(row,channel):
    value={k:v for k,v in row.items() if k!='type'}
    value['channel']=channel
    ref=hashlib.sha256(canonical({k:value.get(k) for k in ('channel','at','record_id','event_id')}).encode()).hexdigest()
    return EvidenceRecord.model_validate(dict(value,ref=ref),strict=True)


def record_from_journal(row,scope):
    if scope.unit not in (row.get('_SYSTEMD_UNIT'),row.get('UNIT'),row.get('OBJECT_SYSTEMD_UNIT')):raise ValueError('foreign_unit')
    priority=int(row['PRIORITY'])
    if not 0<=priority<=4:raise ValueError('foreign_priority')
    cursor=row['__CURSOR']
    if not isinstance(cursor,str) or not 1<=len(cursor)<=1024:raise ValueError('invalid_cursor')
    message=row.get('MESSAGE','');provider=row.get('SYSLOG_IDENTIFIER','')
    if not isinstance(message,str) or not isinstance(provider,str):raise ValueError('unsupported_binary_message')
    at=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=int(row['__REALTIME_TIMESTAMP']))).isoformat().replace('+00:00','Z')
    return EvidenceRecord(ref=hashlib.sha256(cursor.encode()).hexdigest(),at=at,channel='journal',severity='critical' if priority<=2 else 'error' if priority==3 else 'warning',
                          provider=provider[:128],message=message[:512],text_truncated=len(message)>512)


def collect(payload,enabled=False,runner=None,which=None,os_family=None):
    if not enabled:return {'outcome':'diagnostics_disabled','output':''}
    try:
        scope=admit_scope(payload['scope'],os_family or platform.system())
        if payload.get('scope_hash')!=scope_hash(scope):raise ValueError('scope_hash_mismatch')
    except (KeyError,ValueError,TypeError,ValidationError):return {'outcome':'verification_failed','output':''}
    if runner is None:
        from .executor import run_bounded
        runner=run_bounded
    which=which or shutil.which
    started=time.monotonic();used=0;records=[];coverage=[];refs=set()
    targets=scope.channels if scope.source=='windows_events' else ['journal']
    executable=(which('pwsh') or which('powershell')) if scope.source=='windows_events' else which('journalctl')
    for target in targets:
        if not executable:
            coverage.append(Coverage(target=target,status='prerequisite_missing'));continue
        remaining_time=MAX_SECONDS-(time.monotonic()-started);remaining_bytes=MAX_BYTES-used
        if remaining_time<=0 or remaining_bytes<=0:
            coverage.append(Coverage(target=target,status='timeout' if remaining_time<=0 else 'output_limit'));continue
        argv=windows_argv(scope,target,executable) if scope.source=='windows_events' else journal_argv(scope,executable)
        result=runner(argv,timeout_seconds=max(1,math.ceil(remaining_time)),max_output=remaining_bytes)
        raw=result.get('output','').encode('utf-8')
        output=raw[:remaining_bytes].decode('utf-8',errors='ignore');used+=min(len(raw),remaining_bytes)
        status='complete' if scope.source=='systemd' and result.get('exit_code')==0 else 'collector_failed'
        if scope.source=='windows_events':status='parse_error'
        source_count=0;bad=False;seen_coverage=False;truncated=False
        for line in output.splitlines():
            if not line.strip():continue
            try:
                row=json.loads(line)
                if not isinstance(row,dict):raise ValueError()
                if scope.source=='windows_events' and row.get('type')=='coverage':
                    if seen_coverage or row.get('status') not in ('complete','access_denied','collector_failed'):raise ValueError()
                    seen_coverage=True;status=row['status'];continue
                if scope.source=='windows_events' and row.get('type')!='event':raise ValueError()
                record=record_from_windows(row,target) if scope.source=='windows_events' else record_from_journal(row,scope)
                if not timestamp(scope.start_utc)<=timestamp(record.at)<=timestamp(scope.end_utc) or record.ref in refs:raise ValueError()
                refs.add(record.ref);source_count+=1
                if len(records)<scope.max_events:records.append(record)
                else:truncated=True
            except (ValueError,KeyError,TypeError,OverflowError,ValidationError):bad=True
        if bad:status='parse_error'
        if source_count>scope.max_events or truncated:status='truncated'
        reason=result.get('outcome')
        if reason in ('timeout','output_limit','prerequisite_missing'):status=reason
        elif reason!='process_succeeded' and status=='complete':status='collector_failed'
        if len(raw)>remaining_bytes:status='output_limit'
        coverage.append(Coverage(target=target,status=status))
    envelope_data={'scope_hash':scope_hash(scope),'records':[r.model_dump() for r in records],'coverage':[c.model_dump() for c in coverage]}
    while len(json.dumps(envelope_data,ensure_ascii=False).encode())>MAX_BYTES and envelope_data['records']:
        dropped=envelope_data['records'].pop()
        for c in envelope_data['coverage']:
            if c['target']==dropped['channel']:c['status']='output_limit'
    evidence=EvidenceEnvelope.model_validate(envelope_data,strict=True)
    complete=all(c.status=='complete' for c in evidence.coverage) and not any(r.text_truncated for r in evidence.records)
    outcome='collected' if complete else 'prerequisite_missing' if all(c.status=='prerequisite_missing' for c in evidence.coverage) else 'partial'
    return {'outcome':outcome,'exit_code':0 if complete else None,'duration_ms':int((time.monotonic()-started)*1000),'output':'','diagnostics':evidence.model_dump()}
