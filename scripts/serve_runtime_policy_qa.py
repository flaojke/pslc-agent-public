"""Isolated Windows-authorization UI fixture; no native binary, model provider or node call."""
from pathlib import Path
import json,hashlib,socket,tempfile,types
import uvicorn
from backend.config import ROOT,Settings
from backend.app import create_app
from backend.integrations import IntegrationError
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
url='http://127.0.0.1:'+str(port);base=Path(tempfile.mkdtemp(prefix='runtime-policy-ui-',dir=ROOT/'.local'))
catalog=json.loads((ROOT/'config/local-models.json').read_text(encoding='utf-8'));content=b'public-tiny-model-fixture'
(base/'models').mkdir()
for row in catalog['models']:
 row.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
 (base/'models'/row['filename']).write_bytes(content)
(base/'catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
settings=Settings('public-runtime-policy-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,
 knowledge_enabled=False,model_config_path=str(base/'model/connection.enc'),local_models_enabled=True,
 local_models_dir=str(base/'models'),local_catalog_path=str(base/'catalog.json'),system_preferences_path=str(base/'preferences.json'))
app=create_app(settings,frontend_directory=ROOT/'.local/ui-builds/runtime-policy');local=app.state.model_connection.local
def blocked(mid):
 local.runtime_state='failed';local.runtime_error='local_runtime_policy_blocked';local.running_id=None
 raise IntegrationError('local_runtime_policy_blocked')
def warmup(mid):
 local.runtime_state='failed';local.runtime_error='local_runtime_policy_blocked';local.running_id=None
local.ensure_running=blocked;local.warmup=warmup
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='warning'))
@app.post('/qa/shutdown')
def shutdown():server.should_exit=True;return {'stopping':True}
app.router.routes.insert(0,app.router.routes.pop())
(ROOT/'.local/runtime-policy-qa.json').write_text(json.dumps({'url':url,'database':str(base/'qa.sqlite3')}),encoding='utf-8')
if __name__=='__main__':server.run()
