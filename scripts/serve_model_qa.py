"""Local synthetic Open WebUI transport. No external inference or agent execution."""
import sys,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import uvicorn,httpx
from backend.config import Settings
from backend.app import create_app
from backend.schemas import Enroll
URL='http://127.0.0.1:8793'
base=Path(tempfile.mkdtemp(prefix='model-qa-',dir=ROOT/'.local'))
def provider(r):
 if r.headers.get('Authorization')!='Bearer PUBLIC-BROWSER-FIXTURE-KEY':
  return httpx.Response(401,text='provider failure body must not echo')
 if r.url.path=='/api/models':return httpx.Response(200,json={'data':[{'id':'qwen-fixture'},{'id':'gemma-fixture'}]})
 return httpx.Response(200,json={'choices':[{'message':{'content':'{"status":"ok"}'}}]})
app=create_app(Settings('public-unused-model-qa-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=URL,knowledge_enabled=False,model_config_path=str(base/'secret/connection.enc')),httpx.MockTransport(provider))
store=app.state.store
store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='qa-node-no-execution',os_family='Windows'))
(ROOT/'.local/model-qa.json').write_text(json.dumps({'url':URL,'database':str(base/'qa.sqlite3'),'secret':str(base/'secret/connection.enc')}),encoding='utf-8')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8793,access_log=False,log_level='warning')
