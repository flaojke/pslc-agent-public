"""Metadata-only operational tools. No Store startup during snapshot export."""
from pathlib import Path
import argparse,hashlib,json,math,os,re,time
from sqlalchemy import create_engine,inspect,select,text,func,Boolean,Integer,Float,String,JSON
from .store import metadata,version,tasks,workflows,investigation_jobs,Problem,SCHEMA_VERSION
from .diagnostic_types import Scope,Coverage,Hypothesis

MAX_BYTES=134217728
SUMMARY={'restored_count','resource_count','outcome','exit_code','duration_ms','cpu_percent','memory_percent','disk_free_gb','source','package_id','package_version','verified_version','version_matched','reboot_required','agent_result_received','verification_hash','postcondition_matched','scope_hash','collected_count','coverage','references','messages_truncated','cause_status','visibility','critical_count','error_count','warning_count'}
META={'policy_version','cause_status','collection_complete','hypotheses','model_id','conclusion'}

def engine(url):
 if not url.startswith(('sqlite:///','postgresql+psycopg://')):raise ValueError('unsupported_database')
 return create_engine(url,echo=False,hide_parameters=True,**({'pool_timeout':5,'connect_args':{'connect_timeout':5,'options':'-c statement_timeout=10000 -c lock_timeout=5000'}} if url.startswith('postgresql') else {}))
def check_schema(connection):
 inspector=inspect(connection)
 if set(inspector.get_table_names())!=set(metadata.tables):raise ValueError('unsupported_metadata_tables')
 for name,table in metadata.tables.items():
  if {c['name'] for c in inspector.get_columns(name)}!={c.name for c in table.columns}:raise ValueError('unsupported_metadata_columns')
 if list(connection.execute(select(version.c.id)).scalars())!=[SCHEMA_VERSION]:raise ValueError('unsupported_schema_version')
def hash_string(value):
 if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('invalid_metadata_hash')
def json_metadata(table,column,value):
 if value is None:return
 if table=='accounts':
  if not isinstance(value,list):raise ValueError('invalid_account_metadata')
  return
 if (table,column) in (('workflows','target_snapshot'),('schedules','consented_ids'),('schedule_runs','target_snapshot')):
  if not isinstance(value,list) or len(value)>500 or len(set(value))!=len(value) or any(not isinstance(x,str) or not 1<=len(x)<=64 for x in value):raise ValueError('invalid_target_snapshot')
  return
 if not isinstance(value,dict):raise ValueError('invalid_metadata_json')
 if table=='schedules' and column=='calendar':
  from .schedule_types import Calendar
  Calendar.model_validate(value,strict=True)
 elif table=='schedule_runs' and column=='summary':
  from .schedule_types import RunBrief
  RunBrief.model_validate(value,strict=True)
 elif table in ('conversations','schedules') and column=='target_scope':
  from .targets import TargetScope
  TargetScope.model_validate(value,strict=True)
 elif table=='conversation_turns' and column=='summary':
  if set(value)-{'message_count','context_truncated','code','model_id'}:raise ValueError('invalid_turn_metadata')
  if 'message_count' in value and (type(value['message_count'])!=int or not 0<=value['message_count']<=40):raise ValueError('invalid_turn_count')
  if 'context_truncated' in value and type(value['context_truncated'])!=bool:raise ValueError('invalid_turn_flag')
  for key in ('code','model_id'):
   if key in value and (not isinstance(value[key],str) or len(value[key])>128 or any(ord(ch)<32 for ch in value[key])):raise ValueError('invalid_turn_metadata')
 elif table=='tasks' and column=='summary':
  if set(value)-SUMMARY:raise ValueError('unsupported_summary_field')
  for key,item in value.items():
   if key in ('coverage','references'):
    if not isinstance(item,list) or len(item)>100:raise ValueError('invalid_metadata_list')
    for entry in item:
     if key=='coverage':Coverage.model_validate(entry,strict=True)
     else:
      if set(entry)!={'ref','at','channel','severity','event_id','record_id'}:raise ValueError('invalid_reference')
      from .diagnostic_types import EvidenceRecord
      EvidenceRecord.model_validate(entry,strict=True)
   elif key.endswith('_hash'):hash_string(item)
   elif key=='outcome':
    from .store import TERMINAL,ACTIVE
    if item not in TERMINAL+ACTIVE:raise ValueError('invalid_outcome_metadata')
   elif key=='source':
    if item not in ('operator_external_check','demo_simulation'):raise ValueError('invalid_source_metadata')
   elif key=='cause_status':
    if item!='unconfirmed':raise ValueError('invalid_cause_metadata')
   elif key=='visibility':
    if item!='process_account':raise ValueError('invalid_visibility_metadata')
   elif key in ('package_id','package_version','verified_version'):
    if not isinstance(item,str) or not re.fullmatch(r'[A-Za-z0-9_.+:-]{1,128}',item):raise ValueError('invalid_package_metadata')
   elif key in ('version_matched','reboot_required','agent_result_received','postcondition_matched'):
    if type(item)!=bool:raise ValueError('invalid_summary_flag')
   elif item is not None and (type(item) not in (int,float) or not math.isfinite(item)):raise ValueError('invalid_summary_number')
 elif table=='tasks' and column=='package_meta':
  if set(value)!={'id','version','os_family','sha256','manifest_hash'}:raise ValueError('invalid_package_metadata')
  hash_string(value['sha256']);hash_string(value['manifest_hash'])
  if any(not isinstance(x,str) or len(x)>128 for x in value.values()):raise ValueError('invalid_package_metadata')
 elif table=='investigation_jobs' and column=='scope':Scope.validate_python(value,strict=True)
 elif table=='investigation_jobs' and column=='analysis_meta':
  if set(value)-META:raise ValueError('invalid_analysis_metadata')
  for item in value.get('hypotheses',[]):
   if set(item)!={'category','confidence','evidence_ids'}:raise ValueError('invalid_hypothesis_metadata')
   Hypothesis.model_validate({**item,'explanation':'metadata only'},strict=True)
   for ref in item['evidence_ids']:hash_string(ref)
  for key in ('policy_version','cause_status','model_id','conclusion'):
   if value.get(key) is not None and (not isinstance(value[key],str) or len(value[key])>128):raise ValueError('invalid_analysis_metadata')
 elif table=='task_recoveries' and column=='program':
  from .recovery_types import RestoreProgram
  RestoreProgram.model_validate(value,strict=True)
 elif table=='task_recoveries' and column=='receipt':
  from .recovery_types import RecoveryReceipt
  RecoveryReceipt.model_validate(value,strict=True)
 elif table=='enrollment' and column=='profile':
  from .nodes import OnboardingProfile
  OnboardingProfile.model_validate(value,strict=True)
 else:raise ValueError('unsupported_json_column')
 # Reject nonfinite numbers anywhere before persistence.
 json.dumps(value,allow_nan=False)

def validate_snapshot(data):
 if set(data)!={'format','schema_version','created','tables'} or data['format']!='pslc-metadata-v1' or data['schema_version']!=SCHEMA_VERSION:raise ValueError('unsupported_snapshot')
 if type(data['created']) not in (int,float) or not math.isfinite(data['created']) or not 0<data['created']<=time.time()+600:raise ValueError('invalid_snapshot_time')
 if not isinstance(data['tables'],dict) or set(data['tables'])!=set(metadata.tables):raise ValueError('unsupported_snapshot_tables')
 for name,table in metadata.tables.items():
  rows=data['tables'][name]
  if not isinstance(rows,list) or len(rows)>100000:raise ValueError('snapshot_row_limit')
  primary=set()
  for row in rows:
   if not isinstance(row,dict) or set(row)!={c.name for c in table.columns}:raise ValueError('invalid_snapshot_columns')
   pk=tuple(row[c.name] for c in table.primary_key.columns)
   if pk in primary:raise ValueError('duplicate_snapshot_key')
   primary.add(pk)
   for column in table.columns:
    value=row[column.name]
    if value is None:
     if not column.nullable:raise ValueError('null_metadata')
     continue
    kind=column.type
    if isinstance(kind,Boolean):valid=type(value)==bool
    elif isinstance(kind,Integer):valid=type(value)==int
    elif isinstance(kind,Float):valid=type(value) in (int,float) and math.isfinite(value)
    elif isinstance(kind,String):valid=isinstance(value,str) and len(value)<=(kind.length or 1024)
    elif isinstance(kind,JSON):json_metadata(name,column.name,value);valid=True
    else:valid=False
    if not valid:raise ValueError('invalid_metadata_type')
 from .accounts import valid_verifier,validate_grant
 for row in data['tables']['accounts']:
  valid_verifier(row['password_hash']);validate_grant(row['roles'],row['device_ids'],row['knowledge_spaces'])
 from .recovery_types import RestoreProgram,RecoveryReceipt,fingerprint
 task_index={r['id']:r for r in data['tables']['tasks']}
 for row in data['tables']['task_recoveries']:
  original=task_index.get(row['original_task_id']);program=RestoreProgram.model_validate(row['program'],strict=True)
  if not original or original['kind'] not in ('command','install') or original['device_id']!=row['device_id'] or original['owner_id']!=row['owner_id'] or original['plan_hash']!=row['original_plan_hash'] or program.device_id!=row['device_id'] or program.original_task_id!=row['original_task_id'] or fingerprint(row['program'])!=row['program_hash']:raise ValueError('invalid_recovery_binding')
  if original['kind']=='command' and row['verification_hash'] is None:raise ValueError('missing_recovery_verification')
  if row['verification_hash'] is not None:hash_string(row['verification_hash'])
  if row['receipt'] is not None:
   receipt=RecoveryReceipt.model_validate(row['receipt'],strict=True)
   if receipt.device_id!=row['device_id'] or receipt.original_task_id!=row['original_task_id'] or receipt.original_plan_hash!=row['original_plan_hash'] or receipt.program_hash!=row['program_hash'] or receipt.resource_set_hash!=program.resource_set_hash or receipt.resource_count!=sum(program.type_counts.model_dump().values()):raise ValueError('invalid_recovery_receipt')
  if row['latest_recovery_id'] is not None:
   undo=task_index.get(row['latest_recovery_id'])
   if not undo or undo['recovery_of']!=row['original_task_id'] or undo['owner_id']!=row['owner_id']:raise ValueError('invalid_recovery_lineage')
 from .schedule_types import Calendar
 schedule_index={r['id']:r for r in data['tables']['schedules']};runs={r['id']:r for r in data['tables']['schedule_runs']};slots=set()
 for row in schedule_index.values():
  if not re.fullmatch('[a-f0-9]{32}',row['id']) or not 1<=row['revision']<=1000000 or row['mode'] not in ('prompt','health','errors','script'):raise ValueError('invalid_schedule_metadata')
  hash_string(row['template_hash'])
  if row['definition_hash'] is not None:
   hash_string(row['definition_hash'])
   from .schedule_vault import definition_fingerprint
   if definition_fingerprint(row,row['consented_ids'])!=row['definition_hash']:raise ValueError('invalid_schedule_hash')
  if row['consent_id'] is not None and not re.fullmatch('[a-f0-9]{32}',row['consent_id']):raise ValueError('invalid_schedule_consent')
  if row['enabled'] and (not row['definition_hash'] or not row['consent_id'] or row['deleted']):raise ValueError('invalid_schedule_consent')
 for row in runs.values():
  parent=schedule_index.get(row['schedule_id']);slot=(row['schedule_id'],row['slot_key'])
  if not parent or parent['owner_id']!=row['owner_id'] or slot in slots or not 1<=row['revision']<=parent['revision']:raise ValueError('invalid_schedule_run_binding')
  if row['state'] not in ('claimed','planning','awaiting_confirmation','queued','running','verified','partial','stopped','failed','missed','interrupted','worker_busy','skipped_overlap','no_targets','template_unavailable','access_revoked','plan_failed','outcome_unknown','cancel_requested','plan_expired','clarification','unsupported','rejected','cancelled','policy_changed'):raise ValueError('invalid_schedule_run_state')
  hash_string(row['definition_hash']);slots.add(slot)
  if row['workflow_id'] is not None:
   w=next((w for w in data['tables']['workflows'] if w['id']==row['workflow_id']),None)
   # A claim reserves a workflow ID before workflow creation.
   if w is None and row['state'] not in ('claimed','planning','interrupted','failed','plan_failed','no_targets','template_unavailable','access_revoked','worker_busy','skipped_overlap','missed'):raise ValueError('missing_schedule_workflow')
   if w and w['owner_id']!=row['owner_id']:raise ValueError('invalid_schedule_workflow')
 for task in task_index.values():
  if task['schedule_run_id'] is not None:
   run=runs.get(task['schedule_run_id'])
   if not run or run['owner_id']!=task['owner_id'] or task['device_id'] not in run['target_snapshot']:raise ValueError('invalid_scheduled_task')
 if data['tables']['schema_version']!=[{'id':SCHEMA_VERSION}] or data['tables']['dispatch_guard']!=[{'id':1}]:raise ValueError('invalid_snapshot_guard')
 return data

def normalize_snapshot(data):
 old=data.get('schema_version')
 if old in (5,6,7,8):
  from .migrations import schema_contract
  expected_tables,omitted=schema_contract(old);tables=data.get('tables',{})
  if set(tables)!=expected_tables:raise ValueError('invalid_legacy_snapshot')
  for name,rows in tables.items():
   expected={column.name for column in metadata.tables[name].columns}-omitted.get(name,set())
   if not isinstance(rows,list) or any(not isinstance(row,dict) or set(row)!=expected for row in rows):raise ValueError('invalid_legacy_columns')
  if old==5:
   tables['accounts']=[]
   for row in tables['enrollment']:row['profile']=None
  if old<8:
   for row in tables['devices']:row['agent_protocol']=0
   for row in tables['tasks']:row['recovery_of']=None
   tables['task_recoveries']=[]
  if old<7:
   for row in tables['devices']:row['device_kind']='unknown'
   for row in tables['workflows']:row.update(execution_mode='sequential',target_snapshot=None,conversation_id=None)
   tables.update(conversations=[],conversation_turns=[])
  for row in tables['tasks']:row['schedule_run_id']=None
  tables.update(schedules=[],schedule_runs=[])
  data['schema_version']=SCHEMA_VERSION;tables['schema_version']=[{'id':SCHEMA_VERSION}]
 return validate_snapshot(data)



def backup(url,destination):
 started=time.monotonic();db=engine(url)
 try:
  with db.connect() as c:
   if db.dialect.name=='postgresql':c=c.execution_options(isolation_level='REPEATABLE READ')
   with c.begin():
    if db.dialect.name=='postgresql':c.execute(text('SET TRANSACTION READ ONLY'))
    else:c.execute(text('BEGIN'))
    revision=c.scalar(select(version.c.id));omitted={};removed=set()
    if revision==SCHEMA_VERSION:check_schema(c)
    elif revision in (6,7,8):
     from .migrations import schema_contract,schema_shape
     schema_shape(c,revision=revision)
     expected,omitted=schema_contract(revision);removed=set(metadata.tables)-expected
    else:raise ValueError('unsupported_schema_version')
    tables={}
    for name,table in metadata.tables.items():
     if name in removed:continue
     rows=c.execute(select(*[col for col in table.columns if col.name not in omitted.get(name,set())]).limit(100001)).mappings().all()
     if len(rows)>100000:raise ValueError('snapshot_row_limit')
     tables[name]=[dict(x) for x in rows]
    value={'format':'pslc-metadata-v1','schema_version':revision,'created':time.time(),'tables':tables}
    import copy
    normalize_snapshot(copy.deepcopy(value))
  payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
  if len(payload)>MAX_BYTES:raise ValueError('snapshot_size_limit')
  path=Path(destination)
  if path.is_symlink() or path.exists() or Path(str(path)+'.sha256').exists():raise ValueError('backup_exists')
  path.parent.mkdir(parents=True,exist_ok=True)
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
  digest=hashlib.sha256(payload).hexdigest()
  fd=os.open(str(path)+'.sha256',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write(digest+'\n')
  return {'schema':revision,'digest':digest,'bytes':len(payload),'created':value['created'],'rows':{name:len(rows) for name,rows in tables.items()},'duration_ms':int((time.monotonic()-started)*1000)}
 finally:db.dispose()

def restore(source,url,expected_digest):
 started=time.monotonic();path=Path(source)
 if path.is_symlink() or path.stat().st_size>MAX_BYTES:raise ValueError('invalid_backup_file')
 payload=path.read_bytes();hash_string(expected_digest)
 if hashlib.sha256(payload).hexdigest()!=expected_digest:raise ValueError('backup_digest_mismatch')
 data=json.loads(payload)
 data=normalize_snapshot(data);db=engine(url)
 try:
  if inspect(db).get_table_names():raise ValueError('restore_target_not_empty')
  for row in data['tables']['tasks']:
   if row['state'] in ('running','cancel_requested','outcome_unknown'):
    row['state']='outcome_unknown';row['lease_id']=None;row['lease_until']=None
   elif row['state'] in ('queued','awaiting_confirmation'):row['state']='plan_expired'
  for row in data['tables']['workflows']:
   if row['state'] in ('queued','awaiting_confirmation','planning'):row['state']='plan_expired'
   elif row['state'] in ('running','cancel_requested'):row['state']='outcome_unknown'
  for row in data['tables']['investigation_jobs']:
   if row['analysis_state'] in ('pending','analyzing'):row['analysis_state']='context_lost'
  for row in data['tables']['conversation_turns']:
   if row['state'] in ('queued','generating'):row['state']='context_lost'
  for row in data['tables']['schedules']:row.update(enabled=False,definition_hash=None,consent_id=None,consented_ids=[],next_due=None)
  for row in data['tables']['schedule_runs']:
   if row['state'] in ('claimed','planning','queued','running','awaiting_confirmation'):row.update(state='interrupted',summary={**row['summary'],'code':'interrupted'})
  metadata.create_all(db)
  with db.begin() as c:
   for name,rows in data['tables'].items():
    if rows:c.execute(metadata.tables[name].insert(),rows)
   check_schema(c)
  return {'schema':SCHEMA_VERSION,'history_preserved':True,'volatile_restored':False,'remote_execution':False,'duration_ms':int((time.monotonic()-started)*1000)}
 finally:db.dispose()

def status(store):
 started=time.monotonic()
 try:
  with store.engine.connect() as c:
   revision=c.scalar(select(version.c.id))
   if revision!=SCHEMA_VERSION:raise ValueError()
   c.execute(text('SELECT 1'))
  return {'database':'ready','engine':store.engine.dialect.name,'schema':revision,'latency_ms':int((time.monotonic()-started)*1000),'api_processes_supported':1,'limits':{'session':5,'fleet':20,'devices':500},'volatile_counts':{'plans':len(store.plans.data),'outputs':len(store.outputs.data)},'mode':'pilot-network' if getattr(getattr(store,'settings',None),'pilot_network_admitted',False) else 'local-development','production_admitted':False}
 except Exception:raise Problem('database_unavailable',503) from None

def main():
 from .config import load_settings
 p=argparse.ArgumentParser(description='Metadata backup/recovery; database URL from private environment only')
 commands=p.add_subparsers(dest='command',required=True)
 b=commands.add_parser('backup');b.add_argument('--output',required=True)
 r=commands.add_parser('restore');r.add_argument('--input',required=True);r.add_argument('--expected-digest',required=True)
 a=p.parse_args()
 try:
  settings=load_settings();settings.validate()
  result=backup(settings.database_url,a.output) if a.command=='backup' else restore(a.input,settings.database_url,a.expected_digest)
  print(json.dumps(result))
 except Exception:raise SystemExit('Metadata operation rejected: schema, integrity, permissions, limits or empty-target check failed.') from None
if __name__=='__main__':main()
