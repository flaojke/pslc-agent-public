"""Synthetic local account/onboarding UI fixture, no real remote node or command."""
import sys,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import uvicorn
from backend.config import Settings
from backend.app import create_app
from backend.schemas import Enroll
base=Path(tempfile.mkdtemp(prefix='accounts-qa-',dir=ROOT/'.local'));url='http://127.0.0.1:8792'
app=create_app(Settings('accounts-qa-public-unused-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False))
store=app.state.store;node=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='accounts-qa-node',os_family='Windows'))
(ROOT/'.local/accounts-browser-qa.json').write_text(json.dumps({'url':url,'database':str(base/'qa.sqlite3'),'node_id':node['device_id']}),encoding='utf-8')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8792,access_log=False,log_level='warning')
