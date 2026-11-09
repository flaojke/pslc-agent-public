from pathlib import Path
import json
import os
import platform
import re
import subprocess
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
settings=json.loads((ROOT/".local/settings.json").read_text(encoding="utf-8"))
base="http://127.0.0.1:8787"
identity_path=ROOT/".local/smoke-agent.json"
with httpx.Client(base_url=base,headers={"Authorization":"Bearer "+settings["operator_token"]},timeout=10,trust_env=False) as c:
    if not identity_path.exists():
        ticket=c.post("/api/enrollment-tokens");ticket.raise_for_status()
        name=re.sub(r"[^a-zA-Z0-9_.-]","-","local-"+platform.node())[:64]
        response=c.post("/agent/enroll",json={"enrollment_token":ticket.json()["enrollment_token"],"name":name,"os_family":"Windows" if os.name=="nt" else "Linux","commands_enabled":False})
        response.raise_for_status();identity_path.write_text(json.dumps(response.json()),encoding="utf-8")
    identity=json.loads(identity_path.read_text(encoding="utf-8"))
    response=c.post("/api/tasks",json={"device_id":identity["device_id"],"session_id":"local-health-smoke","kind":"health"})
    response.raise_for_status();task=response.json()
    subprocess.run([sys.executable,"-m","agent","--state",str(identity_path),"--once"],cwd=ROOT,check=True,timeout=15)
    final=c.get("/api/tasks/"+task["id"]);final.raise_for_status();result=final.json()
    assert result["state"]=="verified"
    assert "memory_percent" in result["summary"]
    report={"mode":"real-loopback-health-smoke","os_family":platform.system(),"os_version":platform.version(),"device_id":identity["device_id"],"task_id":task["id"],"state":result["state"],"commands_executed":False,"at":time.time()}
    out=ROOT/"docs/qa";out.mkdir(parents=True,exist_ok=True)
    (out/"local-health-smoke.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"mode":report["mode"],"os_family":report["os_family"],"state":report["state"],"commands_executed":False}))
