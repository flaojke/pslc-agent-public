"""Explicit isolated QA node; only health and one predeclared temporary-file command."""
from pathlib import Path
from threading import Thread
import json
import tempfile
import time
import httpx
import uvicorn
from agent.executor import execute, verify_job
from backend.app import create_app
from backend.config import Settings, ROOT
from backend.schemas import Enroll

base=Path(tempfile.mkdtemp(prefix='workflow-qa-',dir=ROOT/'.local'))
proof_file=base/'selected-qa-proof.txt'
marker='QA-WORKFLOW-PROOF-82917'
code=f"from pathlib import Path;Path({str(proof_file)!r}).write_text({marker!r});print('written')"
check=f"from pathlib import Path;print(Path({str(proof_file)!r}).read_text())"
question=f'Выполни python на workflow-qa-fixture:\n{code}\nПроверка python:\n{check}\nОжидается:\n{marker}'
token='workflow-browser-public-fixture-token'
url='http://127.0.0.1:8798'
app=create_app(Settings(token,'sqlite:///'+(base/'qa.sqlite3').as_posix(),plan_ttl=12))
store=app.state.store
identity=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='workflow-qa-fixture',os_family='Windows',commands_enabled=True))
(ROOT/'.local/workflow-browser-qa.json').write_text(json.dumps({'url':url,'token':token,'directory':str(base),'proof_file':str(proof_file),'command_prompt':question,'marker':marker}),encoding='utf-8')


def selected_agent():
    with httpx.Client(base_url=url,timeout=5,follow_redirects=False,trust_env=False,headers={'Authorization':'Bearer '+identity['credential']}) as client:
        while True:
            try:
                response=client.post('/agent/'+identity['device_id']+'/poll');response.raise_for_status()
                job=response.json()['job']
                if job:
                    payload=job['payload']
                    approved=payload['kind']=='health' or (payload['kind']=='command' and payload.get('runtime')=='python' and payload.get('code')==code and payload.get('verification',{}).get('code')==check)
                    result=execute(payload,True,str(base)) if approved and verify_job(job,identity['device_id']) else {'outcome':'failed'}
                    client.post('/agent/'+identity['device_id']+'/tasks/'+job['task_id']+'/result',json={'lease_id':job['lease_id'],**result}).raise_for_status()
            except httpx.HTTPError:pass
            time.sleep(.2)

Thread(target=selected_agent,daemon=True).start()
uvicorn.run(app,host='127.0.0.1',port=8798,access_log=False,proxy_headers=False,workers=1,log_level='warning')
