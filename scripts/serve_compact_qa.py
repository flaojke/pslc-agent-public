"""Synthetic long dialogue fixture. No real models or endpoint commands."""
from pathlib import Path
from threading import Event
import json,socket,tempfile
import httpx,uvicorn
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.schemas import Enroll
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
url='http://127.0.0.1:'+str(port);base=Path(tempfile.mkdtemp(prefix='compact-browser-',dir=ROOT/'.local'));gate=Event()
def handle(request):
 body=json.loads(request.content);n=sum(m['role']=='user' for m in body['messages'])
 if n==3:gate.wait(10)
 answer='QA-ANSWER-'+str(n)+' · первая строка\n'+('Подробный разбор synthetic fixture, без исполнения команд и реальных наблюдений.\n'*18)+'QA-END-'+str(n)
 return httpx.Response(200,json={'choices':[{'message':{'content':answer}}]})
app=create_app(Settings('compact-browser-public-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False,model_url='https://fixture.invalid/v1',model_id='fixture-long'),httpx.MockTransport(handle))
store=app.state.store;store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='QA-COMPACT-READONLY',os_family='Windows'))
@app.post('/qa/release-third')
def release_third():gate.set();return {'released':True}
route=app.router.routes.pop();app.router.routes.insert(0,route)
@app.get('/qa/status')
def status():return {'scope':'synthetic long messages','real_host_actions':False}
route=app.router.routes.pop();app.router.routes.insert(0,route)
(ROOT/'.local/compact-browser-qa.json').write_text(json.dumps({'url':url,'directory':str(base)}),encoding='utf-8')
uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False)
