"""Synthetic scheduler UI. Real mutation restricted to a new temporary configuration file."""
from pathlib import Path
from threading import Thread,Event
import json,socket,tempfile,time,httpx,uvicorn
from sqlalchemy import update
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.schemas import Enroll,Result
from backend.store import devices
from backend.accounts import NewAccount
from agent.executor import execute
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
url='http://127.0.0.1:'+str(port);base=Path(tempfile.mkdtemp(prefix='scheduler-browser-',dir=ROOT/'.local'));folder=base/'configuration';folder.mkdir();file=folder/'settings.json';file.write_text('before',encoding='utf-8')
gate=Event();errors=[];executions=[]
def provider(request):
 data=json.loads(request.content);system=data['messages'][0]['content']
 if 'аналитик инцидентов' in system:value={'conclusion':'insufficient_evidence','hypotheses':[]}
 else:
  context=json.loads(data['messages'][-1]['content'])['context'];value={'status':'plan','steps':[{'kind':'health','device_id':context['devices'][0]['id']}]}
 return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value)}}]})
s=Settings('public-scheduler-browser-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False,schedule_vault_path=str(base/'vault'),system_preferences_path=str(base/'preferences.json'),model_url='https://fixture.invalid/v1',model_id='explicit-fixture-model',model_config_path=str(base/'model.enc'))
app=create_app(s,httpx.MockTransport(provider),frontend_directory=ROOT/'.local/ui-builds/scheduler');store=app.state.store
nodes=[]
for name,kind in [('QA-PC-01','workstation'),('QA-PC-02','workstation'),('QA-SERVER-01','server'),('QA-UNKNOWN-01','unknown')]:
 n=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name=name,os_family='Windows',commands_enabled=True,diagnostics_enabled=True,agent_protocol=1))
 with store.tx() as c:c.execute(update(devices).where(devices.c.id==n['device_id']).values(device_kind=kind))
 nodes.append(n)
app.state.auth.accounts.create(NewAccount(username='qa-auditor',password='public-browser-fixture',roles=['auditor'],device_ids=['*']),actor='local-development')
def worker():
 while True:
  if not gate.wait(.1):continue
  for n in nodes:
   try:
    job=store.poll(n['device_id'],n['credential'],1)
    if not job:continue
    p=job['payload']
    if p['kind']=='health':result={'outcome':'ok','exit_code':0,'cpu_percent':4,'memory_percent':30,'disk_free_gb':90}
    elif p['kind']=='investigate':result={'outcome':'collected','exit_code':0,'diagnostics':{'scope_hash':p['scope_hash'],'records':[],'coverage':[{'target':ch,'status':'complete'} for ch in p['scope']['channels']]}}
    else:
     # Exact script is authored by browser QA to change only file above.
     work=base/'identity/agent-work';work.mkdir(parents=True,exist_ok=True)
     result=execute(p,True,str(work),task_context={'state_path':str(base/'identity/state.json'),'device_id':n['device_id'],'task_id':job['task_id'],'plan_hash':job['plan_hash']})
    result['lease_id']=job['lease_id'];store.finish(n['device_id'],n['credential'],job['task_id'],Result.model_validate(result,strict=True))
    if p['kind']=='investigate':store.investigations.analyze(job['task_id'],store.integrations)
    executions.append({'kind':p['kind'],'device_id':n['device_id']})
   except Exception:errors.append('worker_failed')
  time.sleep(.1)
Thread(target=worker,daemon=True).start()
@app.get('/qa/status')
def status():return {'scope':'synthetic nodes + disposable config only','state':file.read_text(),'executions':executions,'errors':errors,'real_host_actions':False}
@app.post('/qa/allow')
def allow():gate.set();return {'synthetic_worker_enabled':True}
for _ in range(2):app.router.routes.insert(0,app.router.routes.pop())
(ROOT/'.local/scheduler-browser-qa.json').write_text(json.dumps({'url':url,'directory':str(base),'file':str(file)}),encoding='utf-8')
uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False)
