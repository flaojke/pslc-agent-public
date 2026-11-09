"""Controlled actual clean app, real CPU engine/native health. No production credentials."""
from pathlib import Path
from dataclasses import replace
import json,os,socket,subprocess,sys,secrets
from threading import Thread
PROJECT=Path(__file__).resolve().parents[1];variant=sys.argv[1];assert variant in ('Full','Lite')
index=PROJECT/'.local/clean-distributions.json';info=json.loads(index.read_text());entry=info['variants'][variant];ROOT=Path(entry['root']).resolve()
sys.path.insert(0,str(ROOT))
from backend.config import load_settings
from backend.app import create_app
from backend import config
import uvicorn
assert config.ROOT.resolve()==ROOT,'Candidate source isolation failed'
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
url='http://127.0.0.1:'+str(port);s=replace(load_settings(),public_origin=url)
app=create_app(s);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='warning'))
state=ROOT/'.local/qa-node/identity.json';native=ROOT/'resources/agents/windows-x64/pslc-agent/pslc-agent.exe'
@app.get('/qa/status')
def status():
 local=app.state.model_connection.local
 return {'variant':variant,'version':info['version'],'source_isolated':True,'model_downloads_requested':bool(local.jobs),'native_identity_present':state.is_file(),'real_host_mutation':False}
@app.post('/qa/native-health-once')
def native_health():
 if not state.is_file():return {'node_not_enrolled':True}
 result=subprocess.run([str(native),'--server',url,'--state',str(state),'--once'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45,creationflags=0x08000000)
 return {'returncode':result.returncode,'commands_enabled':False,'services_installed':False}
@app.post('/qa/shutdown')
def shutdown():
 server.should_exit=True;return {'stopping':True}
for _ in range(3):app.router.routes.insert(0,app.router.routes.pop())
entry.update(url=url,node_state=str(state),server_pid=os.getpid());index.write_text(json.dumps(info,indent=2),encoding='utf-8')
server.run()
