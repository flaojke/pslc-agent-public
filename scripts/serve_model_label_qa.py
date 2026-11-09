"""Isolated accounts + mocked models for active-model label refresh, no enrolled nodes."""
from pathlib import Path
import json,socket,tempfile,os
import httpx,uvicorn
from backend.app import create_app
from backend.config import ROOT,Settings
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
url='http://127.0.0.1:'+str(port)
base=Path(tempfile.mkdtemp(prefix='model-label-refresh-',dir=ROOT/'.local'))
meta={'model_calls':0,'models_used':[],'real_provider_calls':False,'node_actions':0}
def provider(request):
 assert request.url.host=='qa-model.invalid'
 assert request.url.path.endswith('/chat/completions')
 body=json.loads(request.content);assert not body.get('tools') and not body.get('tool_ids')
 mid=body['model'];assert mid in ('fixture-a','fixture-b')
 meta['model_calls']+=1;meta['models_used'].append(mid)
 return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'Искусственный ответ '+mid+'. Действий на узлах нет.'}}]})
settings=Settings('public-unused-model-label-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),
 auth_mode='accounts',public_origin=url,knowledge_enabled=False,
 model_url='https://qa-model.invalid/api',model_token='PUBLIC-MODEL-REFRESH-KEY-A',model_id='fixture-a',
 model_config_path=str(base/'model/connection.enc'),system_preferences_path=str(base/'system/preferences.json'))
app=create_app(settings,httpx.MockTransport(provider),frontend_directory=ROOT/'.local/ui-builds/model-label-refresh')
connection=app.state.model_connection
connection.profiles['fixture_b']={'url':'https://qa-model.invalid/api','key':'PUBLIC-MODEL-REFRESH-KEY-B','model':'fixture-b','title':'Fixture B'}
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False))
@app.get('/qa/status')
def status():return {**meta,'enrolled_nodes':0,'scope':'synthetic isolated operator/admin browser contexts'}
@app.post('/qa/shutdown')
def shutdown():server.should_exit=True;return {'stopping':True}
for _ in range(2):app.router.routes.insert(0,app.router.routes.pop())
(ROOT/'.local/model-label-qa.json').write_text(json.dumps({'url':url,'database':str(base/'qa.sqlite3'),'pid':os.getpid()},indent=2))
server.run()
