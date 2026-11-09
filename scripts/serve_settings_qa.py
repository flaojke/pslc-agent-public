"""Isolated settings UI; synthetic nodes and explicit fake provider, never user's chat."""
from pathlib import Path
import json,socket,tempfile,httpx,uvicorn
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.accounts import NewAccount
from backend.schemas import Enroll
from backend.model_connection import Candidate
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
url='http://127.0.0.1:'+str(port);base=Path(tempfile.mkdtemp(prefix='settings-browser-',dir=ROOT/'.local'))
calls=[]
def handler(request):
 calls.append(request.url.path)
 if request.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':'qwen-fixture'},{'id':'gemma-fixture'}]})
 return httpx.Response(200,json={'choices':[{'message':{'content':'{"status":"ok"}'}}]})
s=Settings('settings-browser-public-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False,model_config_path=str(base/'model/connection.enc'),system_preferences_path=str(base/'system/preferences.json'))
app=create_app(s,httpx.MockTransport(handler),frontend_directory=ROOT/'.local/ui-builds/settings')
for role in ('operator','auditor','reviewer','platform_admin'):
 app.state.auth.accounts.create(NewAccount(username='qa-'+role,password='public-browser-fixture',roles=[role],device_ids=['fixture-only'] if role=='platform_admin' else ['*']),actor='local-development')
store=app.state.store
for name,os,kind in [('QA-WORKSTATION','Windows','workstation'),('QA-SERVER','Linux','server')]:
 from backend.store import devices
 node=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name=name,os_family=os))
 with store.tx() as c:c.execute(devices.update().where(devices.c.id==node['device_id']).values(device_kind=kind))
m=app.state.model_connection
candidate=Candidate(server_url='https://fixture.invalid',api_key='public-QA-not-real-model-key',model_id='qwen-fixture',revision=m.view()['revision'])
m.test(candidate,authorize=lambda:None)
m.save(candidate,authorize=lambda:None)
calls.clear()
@app.get('/qa/status')
def qa_status():return {'scope':'synthetic settings','provider_calls':len(calls),'real_host_actions':False}
route=app.router.routes.pop();app.router.routes.insert(0,route)
(ROOT/'.local/settings-browser-qa.json').write_text(json.dumps({'url':url,'directory':str(base)}),encoding='utf-8')
uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False)
