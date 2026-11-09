from dataclasses import replace
import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from backend.app import create_app
from backend.config import Settings
from backend.demo import DemoNode,PREFIX
from backend.store import Store,Problem
from backend.schemas import Enroll
from backend.operations import backup

def fixture(tmp):
 s=Settings('public-unused-demo-fixture-token','sqlite:///'+(tmp/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,model_config_path=str(tmp/'model/key.enc'),demo_config_path=str(tmp/'demo/identity.enc'))
 app=create_app(s);c=TestClient(app,base_url='http://127.0.0.1:8787',headers={'Origin':'http://127.0.0.1:8787'})
 assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
 return s,app,c

def test_demo_exact_workflow_confirmation_source_and_restart(tmp_path,monkeypatch):
 s,app,c=fixture(tmp_path);demo=app.state.demo
 assert c.get('/api/devices').json()==[]
 node=demo.create();assert demo.create()['created'] is False
 listing=c.get('/api/devices').json();assert len(listing)==1 and listing[0]['virtual'] and not listing[0]['commands_enabled']
 flow=c.post('/api/agent/requests',json={'request_id':'demo-request-001','session_id':'demo-session','question':'Проверь состояние узла '+PREFIX+'WIN-01'}).json()
 def forbidden(*a,**kw):raise AssertionError('Host process launch forbidden in demo')
 monkeypatch.setattr('subprocess.Popen',forbidden)
 demo.tick();assert c.get('/api/workflows/'+flow['id']).json()['state']=='awaiting_confirmation'
 response=c.post('/api/workflows/'+flow['id']+'/decision',json={'approve':True,'plan_hash':flow['plan_hash']});assert response.status_code==200,response.text
 demo.tick()
 result=c.get('/api/workflows/'+flow['id']).json();assert result['state']=='verified'
 assert result['steps'][0]['summary']['source']=='demo_simulation' and result['steps'][0]['summary']['cpu_percent']==12.5
 clone=DemoNode(app.state.store);assert clone.create()['device_id']==node['device_id']
 private=demo.identity['credential'];assert private.encode() not in Path(s.demo_config_path).read_bytes()
 app.state.store.engine.dispose();info=backup(s.database_url,tmp_path/'backup.json')
 saved=(tmp_path/'backup.json').read_text(encoding='utf-8')
 assert private not in saved and 'demo_simulation' in saved and 'fixed virtual metrics' not in saved

def test_reserved_label_and_readonly_settings_and_revocation(tmp_path):
 s,app,c=fixture(tmp_path);store=app.state.store;demo=app.state.demo;node=demo.create()
 ticket=store.ticket()
 with pytest.raises(Problem):store.enroll(Enroll(agent_protocol=1,enrollment_token=ticket['enrollment_token'],name=PREFIX+'FAKE',os_family='Windows'))
 body={'name':'not-demo','commands_enabled':False,'installations_enabled':False,'diagnostics_enabled':False}
 assert c.post('/api/devices/'+node['device_id']+'/configuration-plans',json=body).status_code==422
 body.update(name=PREFIX+'RENAMED',commands_enabled=True)
 assert c.post('/api/devices/'+node['device_id']+'/configuration-plans',json=body).status_code==422
 body['commands_enabled']=False
 plan=c.post('/api/devices/'+node['device_id']+'/configuration-plans',json=body).json()
 assert c.post('/api/devices/'+node['device_id']+'/configuration-plans/'+plan['id']+'/decision',json={'approve':True,'plan_hash':plan['plan_hash']}).status_code==200
 assert c.post('/api/devices/'+node['device_id']+'/revoke').status_code==200
 demo.tick();assert DemoNode(store).identity is None
 with pytest.raises(Problem):DemoNode(store).create()
 assert len(store.list_devices())==1

def test_lost_demo_secret_does_not_create_duplicate(tmp_path):
 s,app,c=fixture(tmp_path);demo=app.state.demo;demo.create()
 Path(s.demo_config_path).unlink()
 with pytest.raises(Problem) as e:DemoNode(app.state.store).create()
 assert e.value.code=='demo_already_exists' and len(app.state.store.list_devices())==1

def test_demo_secret_write_failure_rolls_back_enrollment(tmp_path,monkeypatch):
 s,app,c=fixture(tmp_path)
 from backend.secret_store import SecretStore,SecretUnavailable
 def fail(*a):raise SecretUnavailable()
 monkeypatch.setattr(SecretStore,'write',fail)
 with pytest.raises(Problem):app.state.demo.create()
 assert app.state.store.list_devices()==[]
