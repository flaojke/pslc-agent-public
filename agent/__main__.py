import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import random
import time
import httpx
from threading import Event
from .runtime import instance_lock,tls_context,load_config
from backend.integrations import validate_endpoint
from .executor import execute, verify_job


def run(server,state_path,commands_enabled=False,enroll=False,once=False,installations_enabled=False,diagnostics_enabled=False,ca_file='',stop_event=None,config_path=''):
    with instance_lock(state_path):
        return _run(server,state_path,commands_enabled,enroll,once,installations_enabled,diagnostics_enabled,ca_file,stop_event or Event(),config_path)

def _run(server,state_path,commands_enabled,enroll,once,installations_enabled,diagnostics_enabled,ca_file,stop_event,config_path=""):
    validate_endpoint(server)
    state_path=Path(state_path)
    state_path.parent.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=server,timeout=15,follow_redirects=False,trust_env=False,verify=tls_context(ca_file)) as client:
        if enroll:
            if state_path.exists(): raise RuntimeError("existing_identity_preserved")
            ticket=getpass.getpass("One-time enrollment token: ")
            response=client.post("/agent/enroll",json={"enrollment_token":ticket,"name":platform.node().split(".")[0],"os_family":"Windows" if os.name=="nt" else "Linux","commands_enabled":commands_enabled,"installations_enabled":installations_enabled,"diagnostics_enabled":diagnostics_enabled,"agent_protocol":1})
            response.raise_for_status();identity=response.json()
            state_path.write_text(json.dumps(identity),encoding="utf-8")
            if os.name!="nt": state_path.chmod(0o600)
        identity=json.loads(state_path.read_text(encoding="utf-8"))
        did=identity["device_id"];headers={"Authorization":"Bearer "+identity["credential"],"X-PSLC-Agent-Protocol":"1"}
        workdir=state_path.parent/"agent-work";workdir.mkdir(exist_ok=True)
        pending=None
        while True:
            if stop_event.is_set() and pending is None:return
            try:
                if pending:
                    tid,result=pending
                    response=client.post(f"/agent/{did}/tasks/{tid}/result",headers=headers,json=result)
                    response.raise_for_status();pending=None
                    if once or stop_event.is_set(): return
                response=client.post(f"/agent/{did}/poll",headers=headers)
                response.raise_for_status();job=response.json()["job"]
                if job:
                    outcome=execute(job["payload"],commands_enabled,str(workdir),installations_enabled,diagnostics_enabled,task_context={"state_path":str(state_path),"config_path":config_path,"device_id":did,"task_id":job["task_id"],"plan_hash":job["plan_hash"]}) if verify_job(job,did) else {"outcome":"failed","output":""}
                    outcome["lease_id"]=job["lease_id"]
                    pending=(job["task_id"],outcome)
                    continue
                if once: return
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401,403): raise RuntimeError("agent_access_revoked") from None
            except httpx.TransportError:
                if once:raise RuntimeError("transport_verification_failed") from None
                pass  # Keep a pending result only in RAM. Never rerun its command.
            if stop_event.wait(random.uniform(2,4)):return

def main():
    p=argparse.ArgumentParser(description="PSLC Agent: explicit enrollment and least-privilege runtime")
    p.add_argument("--version",action="version",version="PSLC Agent "+__import__("agent").__version__)
    p.add_argument("--config",default="")
    p.add_argument("--server",default="http://127.0.0.1:8787")
    p.add_argument("--state",default=".local/agent.json")
    p.add_argument("--ca-file",default="")
    p.add_argument("--enroll",action="store_true")
    p.add_argument("--allow-commands",action="store_true")
    p.add_argument("--allow-installations",action="store_true")
    p.add_argument("--allow-diagnostics",action="store_true")
    p.add_argument("--once",action="store_true")
    a=p.parse_args()
    try:
        if a.config:
            if a.allow_commands or a.allow_installations or a.allow_diagnostics or a.ca_file or a.state!=".local/agent.json" or a.server!="http://127.0.0.1:8787":raise ValueError('config_cli_conflict')
            c=load_config(a.config)
            run(c.server,c.state,c.commands_enabled,a.enroll,a.once,c.installations_enabled,c.diagnostics_enabled,c.ca_file,config_path=a.config)
        else:run(a.server,a.state,a.allow_commands,a.enroll,a.once,a.allow_installations,a.allow_diagnostics,a.ca_file)
    except Exception as error:
        # Exceptions may contain transport headers or endpoint content. Never print them.
        raise SystemExit('Agent stopped ['+type(error).__name__+']: configuration, identity or transport check failed.') from None

if __name__=="__main__":main()
