"""Isolated UI fixture. The only mutating target is our new temporary application.json."""
from pathlib import Path
from threading import Event,Thread
import json,socket,tempfile,httpx,uvicorn
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.schemas import Enroll,Result
from agent.executor import execute
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
url='http://127.0.0.1:'+str(port);base=Path(tempfile.mkdtemp(prefix='rollback-browser-',dir=ROOT/'.local'));config=base/'configuration';config.mkdir();file=config/'application.json';file.write_text('{"before":true}',encoding='utf-8');gate=Event();stop=Event();executions=[];errors=[]
node_id=[None]
def handler(request):
 step={'kind':'command','device_id':node_id[0],'runtime':'python','code':'from pathlib import Path;Path('+repr(str(file))+').write_text(\'{"after":true}\',encoding="utf-8")','verification':{'runtime':'python','code':'from pathlib import Path;print("checked" if Path('+repr(str(file))+').read_text()==\'{"after":true}\' else "bad")','expected_output':'checked'},'recovery':{'resources':[{'type':'configuration_file','path':str(file)}]}}
 return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'status':'plan','steps':[step]})}}]})
s=Settings('rollback-browser-public-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False,model_url='https://fixture.invalid/v1',model_id='fixture-rollback',model_config_path=str(base/'models/connection.enc'),system_preferences_path=str(base/'system/preferences.json'))
app=create_app(s,httpx.MockTransport(handler),frontend_directory=ROOT/'.local/ui-builds/rollback');store=app.state.store
node=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='QA-ROLLBACK-TEMP-FILE',os_family='Windows',commands_enabled=True,agent_protocol=1));node_id[0]=node['device_id']
state=base/'identity/state.json';work=state.parent/'agent-work';work.mkdir(parents=True)
def worker():
 while not stop.is_set():
  if not gate.wait(.2):continue
  try:
   job=store.poll(node['device_id'],node['credential'],1)
   if not job:stop.wait(.1);continue
   gate.clear()
   result=execute(job['payload'],True,str(work),task_context={'state_path':str(state),'device_id':node['device_id'],'task_id':job['task_id'],'plan_hash':job['plan_hash']})
   result['lease_id']=job['lease_id'];store.finish(node['device_id'],node['credential'],job['task_id'],Result.model_validate(result,strict=True));executions.append({'task_id':job['task_id'],'outcome':result['outcome']})
  except Exception:errors.append('worker_failed');stop.wait(.2)
Thread(target=worker,daemon=True).start()
@app.get('/qa/status')
def status():return {'scope':'disposable temp config only','state':file.read_text(encoding='utf-8'),'executions':len(executions),'errors':errors,'managed_host_mutation':False}
@app.post('/qa/allow-one')
def allow_one():gate.set();return {'allowed_one_fixture_job':True}
@app.post('/qa/drift')
def drift():file.write_text('{"independent":true}',encoding='utf-8');return {'temporary_fixture_modified':True}
for _ in range(3):app.router.routes.insert(0,app.router.routes.pop())
(ROOT/'.local/rollback-browser-qa.json').write_text(json.dumps({'url':url,'directory':str(base)}),encoding='utf-8')
uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False)
