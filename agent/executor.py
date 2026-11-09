import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from threading import Event, Thread
import time
import psutil

MAX_OUTPUT=65536

def kill_tree(proc):
    if os.name=="nt":
        if proc.poll() is None:
            try:
                subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            except (OSError,subprocess.TimeoutExpired):
                proc.kill()
    else:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired: proc.kill()


def run_bounded(argv,timeout_seconds=30,cwd=None,max_output=MAX_OUTPUT):
    started=time.monotonic(); chunks=[]; size=0; overflow=Event()
    options={"stdout":subprocess.PIPE,"stderr":subprocess.STDOUT,"stdin":subprocess.DEVNULL,"cwd":cwd,"shell":False}
    if os.name=="nt": options["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else: options["start_new_session"]=True
    try: proc=subprocess.Popen(argv,**options)
    except OSError: return {"outcome":"prerequisite_missing","exit_code":None,"duration_ms":0,"output":""}

    def read():
        nonlocal size
        try:
            while True:
                part=proc.stdout.read(4096)
                if not part: break
                remaining=max_output-size
                if remaining>0: chunks.append(part[:remaining]); size+=min(len(part),remaining)
                if len(part)>remaining:
                    overflow.set(); break
        except (OSError,ValueError): pass

    thread=Thread(target=read,daemon=True);thread.start();reason=None
    while proc.poll() is None:
        if overflow.is_set(): reason="output_limit";break
        if time.monotonic()-started>=timeout_seconds: reason="timeout";break
        time.sleep(.02)
    if reason: kill_tree(proc)
    thread.join(timeout=1)
    if thread.is_alive():
        kill_tree(proc)
        thread.join(timeout=1)
        reason=reason or "timeout"
    if overflow.is_set(): reason=reason or "output_limit"
    if not thread.is_alive(): proc.stdout.close()
    return {"outcome":reason or ("process_succeeded" if proc.returncode==0 else "failed"),"exit_code":proc.returncode,
        "duration_ms":int((time.monotonic()-started)*1000),"output":b"".join(chunks).decode("utf-8",errors="replace")[:MAX_OUTPUT]}


def _execute_basic(payload,commands_enabled=False,workdir=None,installations_enabled=False):
    if payload.get("kind")=="install":
        from .installation import execute_installation
        return execute_installation(payload,enabled=installations_enabled,workdir=workdir)
    if payload.get("kind")=="health":
        started=time.monotonic()
        result={"outcome":"ok","exit_code":0,"output":"",
            "cpu_percent":psutil.cpu_percent(interval=.1),"memory_percent":psutil.virtual_memory().percent,
            "disk_free_gb":round(shutil.disk_usage(Path.cwd().anchor).free/1e9,2)}
        result["duration_ms"]=int((time.monotonic()-started)*1000)
        return result
    if not commands_enabled: return {"outcome":"commands_disabled","output":""}
    runtime=payload.get("runtime");code=payload.get("code","")
    if runtime not in ("powershell","cmd","python","node") or not isinstance(code,str) or len(code)>16000:
        return {"outcome":"failed","output":""}
    if runtime=="python": argv=[(shutil.which("python3") or shutil.which("python") or "") if getattr(sys,"frozen",False) else sys.executable,"-I","-c",code]
    elif runtime=="node": argv=[shutil.which("node") or "", "--eval",code]
    elif runtime=="cmd": argv=[shutil.which("cmd") or "", "/d","/s","/c",code] if os.name=="nt" else [""]
    else:
        exe=shutil.which("pwsh") or (shutil.which("powershell") if os.name=="nt" else None)
        argv=[exe or "","-NoProfile","-NonInteractive","-EncodedCommand",base64.b64encode(code.encode("utf-16le")).decode()]
    if not argv[0]: return {"outcome":"prerequisite_missing","output":""}
    timeout=payload.get("timeout_seconds",30)
    if not isinstance(timeout,int) or not 1<=timeout<=120: return {"outcome":"failed","output":""}
    return run_bounded(argv,timeout,cwd=workdir)



def _execute_forward(payload,commands_enabled=False,workdir=None,installations_enabled=False,diagnostics_enabled=False):
    if payload.get("kind")=="investigate":
        from .diagnostics import collect
        return collect(payload,enabled=diagnostics_enabled)
    if payload.get("kind")!="command" or not payload.get("verification"):
        if payload.get("kind")=="command" and payload.get("workflow_id"):
            return {"outcome":"verification_failed","output":""}
        return _execute_basic(payload,commands_enabled,workdir,installations_enabled)
    from backend.planner import CommandCheck
    from pydantic import ValidationError
    try:
        check=CommandCheck.model_validate(payload["verification"],strict=True)
        if not check.code.strip() or not check.expected_output.strip(): raise ValueError()
    except (ValidationError,ValueError,TypeError):
        return {"outcome":"verification_failed","output":""}
    verification_hash=hashlib.sha256(json.dumps(check.model_dump(),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    result=_execute_basic(payload,commands_enabled,workdir,installations_enabled)
    if result['outcome']!='process_succeeded':
        if result['outcome'] in ('timeout','output_limit'):result['outcome']='outcome_unknown'
        return result
    checked=_execute_basic({'kind':'command','runtime':check.runtime,'code':check.code,'timeout_seconds':check.timeout_seconds},commands_enabled,workdir)
    matched=checked['outcome']=='process_succeeded' and checked.get('exit_code')==0 and checked.get('output','').strip()==check.expected_output.strip()
    result['command_verification']={'verification_hash':verification_hash,'matched':matched,'exit_code':checked.get('exit_code')}
    result['duration_ms']=result.get('duration_ms',0)+checked.get('duration_ms',0)
    result['output']=(result.get('output','')+'\n'+checked.get('output',''))[:MAX_OUTPUT]
    if checked['outcome'] in ('timeout','output_limit','outcome_unknown'): result['outcome']='outcome_unknown'
    elif not matched:result['outcome']='verification_failed'
    return result


def _runtime_ready(runtime):
    if runtime=='python':
        return bool(shutil.which('python3') or shutil.which('python')) if getattr(sys,'frozen',False) else bool(sys.executable)
    if runtime=='node':return bool(shutil.which('node'))
    if runtime=='cmd':return os.name=='nt' and bool(shutil.which('cmd'))
    if runtime=='powershell':return bool(shutil.which('pwsh') or (shutil.which('powershell') if os.name=='nt' else None))
    return False

def execute(payload,commands_enabled=False,workdir=None,installations_enabled=False,diagnostics_enabled=False,task_context=None):
    kind=payload.get('kind');started_forward=False
    if kind not in ('command','install','rollback'):
        return _execute_forward(payload,commands_enabled,workdir,installations_enabled,diagnostics_enabled)
    if kind=='command' and not commands_enabled:return {'outcome':'commands_disabled','output':''}
    if kind=='install' and not installations_enabled:return {'outcome':'installations_disabled','output':''}
    from backend.recovery_types import RestoreProgram,RecoveryResources,validate_pair,fingerprint,RecoveryReceipt
    from .recovery_vault import Vault
    from .recovery_files import RecoveryError
    try:
        context=task_context or {}
        if not context.get('state_path') or not context.get('task_id') or context.get('plan_hash')!=fingerprint(payload):return {'outcome':'recovery_required','output':''}
        program=RestoreProgram.model_validate(payload.get('anti_program'),strict=True)
        if program.device_id!=context.get('device_id') or payload.get('device_id')!=program.device_id or not payload.get('owner_id'):return {'outcome':'recovery_required','output':''}
        if kind!='rollback' and program.original_task_id!=context['task_id']:return {'outcome':'recovery_required','output':''}
        original_kind=payload.get('original_kind') if kind=='rollback' else kind
        if original_kind=='install' and not installations_enabled:return {'outcome':'installations_disabled','output':''}
        if original_kind=='command' and not commands_enabled:return {'outcome':'commands_disabled','output':''}
        if original_kind not in ('command','install'):return {'outcome':'recovery_required','output':''}
        state=Path(context['state_path']).resolve();protected=[state.parent]
        if context.get('config_path'):protected.append(Path(context['config_path']).resolve())
        if getattr(sys,'frozen',False):protected.append(Path(sys.executable).parent.resolve())
        package_provider=None
        if original_kind=='install':
            from .recovery_package import PackageProvider
            package_provider=PackageProvider(payload.get('manifest') if kind=='install' else None,workdir=workdir)
        vault=Vault(state.parent/'recovery',program.device_id,payload['owner_id'],protected=protected,package_provider=package_provider)
        if kind=='rollback':
            def restoring():
                nonlocal started_forward
                started_forward=True
            receipt=vault.rollback(payload.get('capsule_id',''),program.model_dump(),payload.get('original_plan_hash',''),on_restore=restoring)
            result={'outcome':{'restored':'restored','conflict':'recovery_conflict','partial':'recovery_partial'}.get(receipt['state'],'recovery_unavailable'),'output':'','duration_ms':0}
            result['recovery']={'phase':'rollback','receipt':receipt};return result
        resources=RecoveryResources.model_validate(payload.get('recovery'),strict=True)
        values=[r.model_dump() for r in resources.resources];validate_pair(program.model_dump(),values)
        if kind=='command':
            from backend.schemas import CommandCheck
            check=CommandCheck.model_validate(payload.get('verification'),strict=True)
            if not _runtime_ready(payload.get('runtime')) or not _runtime_ready(check.runtime):return {'outcome':'prerequisite_missing','output':''}
        else:
            if len(values)!=1 or values[0]['type']!='package_presence':return {'outcome':'recovery_required','output':''}
            status=package_provider.snapshot(values[0])
            if status['presence']=='target':
                # Proven target-version no-op; no baseline or uninstall entitlement is invented.
                c=package_provider.contract
                result={'outcome':'already_installed','exit_code':0,'duration_ms':0,'output':'','installation':{'package_id':c['package_id'],'version':c['version'],'manifest_hash':c['manifest_hash'],'artifact_sha256':None,'version_matched':True,'already_installed':True,'reboot_required':False}}
                receipt=RecoveryReceipt(device_id=program.device_id,original_task_id=program.original_task_id,original_plan_hash=context['plan_hash'],program_hash=fingerprint(program.model_dump()),resource_set_hash=program.resource_set_hash,capsule_id=fingerprint({'device_id':program.device_id,'task_id':program.original_task_id,'plan_hash':context['plan_hash'],'program_hash':fingerprint(program.model_dump())}),expires=float(time.time()+7*86400),state='not_needed',resource_count=1).model_dump()
                result['recovery']={'phase':'forward','receipt':receipt};return result
        cid=vault.prepare(program.model_dump(),values,context['plan_hash']);vault.started(cid);started_forward=True
        try:result=_execute_forward(payload,commands_enabled,workdir,installations_enabled,diagnostics_enabled)
        except Exception:result={'outcome':'outcome_unknown','output':''}
        receipt=vault.finished(cid)
        if kind=='install' and result.get('outcome')=='already_installed':receipt['state']='not_needed'
        result['recovery']={'phase':'forward','receipt':receipt}
        if receipt['state']!='ready' and result.get('outcome') in ('process_succeeded','installed','reboot_required','already_installed'):result['outcome']='outcome_unknown'
        return result
    except RecoveryError:
        return {'outcome':'outcome_unknown' if started_forward else 'recovery_unavailable','output':''}
    except Exception:
        return {'outcome':'outcome_unknown' if started_forward else 'recovery_required','output':''}

def verify_job(job,device_id):
    payload=job.get("payload",{})
    if payload.get("kind")=="health" and not payload.get("workflow_id") and not payload.get("device_id"): return True
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return payload.get("device_id")==device_id and hashlib.sha256(encoded.encode()).hexdigest()==job.get("plan_hash")
