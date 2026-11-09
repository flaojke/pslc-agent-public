"""Synthetic UI transfer/profiles fixture; real GGUF inference is checked separately."""
import sys,json,tempfile,time,hashlib,types,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx,uvicorn
from backend.config import Settings
from backend.app import create_app
with socket.socket() as sock:sock.bind(('127.0.0.1',0));PORT=sock.getsockname()[1]
URL='http://127.0.0.1:'+str(PORT);base=Path(tempfile.mkdtemp(prefix='local-model-ui-',dir=ROOT/'.local'));content=b'CPU-download-fixture-only'*65536
catalog=json.loads((ROOT/'config/local-models.json').read_text(encoding='utf-8'))
for row in catalog['models']:row['bytes']=len(content);row['sha256']=hashlib.sha256(content).hexdigest()
(base/'catalog.json').write_text(json.dumps(catalog));(base/'models').mkdir()
(base/'models'/catalog['models'][1]['filename']).write_bytes(content)
class Slow(httpx.SyncByteStream):
 def __iter__(self):
  for p in range(0,len(content),65536):time.sleep(.2);yield content[p:p+65536]
def provider(r):
 if r.method=='GET' and r.url.host=='huggingface.co':return httpx.Response(200,stream=Slow())
 if r.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':'qwen-external-fixture'},{'id':'gemma-external-fixture'}]})
 return httpx.Response(200,json={'choices':[{'message':{'content':'{"status":"ok"}'}}]})
s=Settings('unused-public-local-ui-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=URL,knowledge_enabled=False,
 model_config_path=str(base/'secret/model.enc'),local_models_enabled=True,local_models_dir=str(base/'models'),local_catalog_path=str(base/'catalog.json'))
app=create_app(s,httpx.MockTransport(provider),frontend_directory=ROOT/'.local/ui-builds/local-model');local=app.state.model_connection.local
def ready(mid):
 local.running_id=mid;local.runtime_state='ready';local.runtime_error=None
local.ensure_running=ready;local.warmup=lambda mid:ready(mid)
(ROOT/'.local/local-model-ui.json').write_text(json.dumps({'url':URL,'database':str(base/'qa.sqlite3'),'model_dir':str(base/'models')}),encoding='utf-8')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=PORT,access_log=False,log_level='warning')
