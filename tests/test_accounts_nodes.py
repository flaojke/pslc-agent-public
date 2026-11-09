from recovery_fixture import strip_recovery_snapshot
from recovery_fixture import command_contract,legacy_version
import json,time,hashlib
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,text
from backend.app import create_app
from backend.config import Settings
from backend.schemas import Enroll,Result
from backend.store import accounts,devices,version,Store,SCHEMA_VERSION
from backend.operations import backup,restore
TOKEN='local-account-public-fixture-token'
ORIGIN='http://127.0.0.1:8787'
SECRET='employee-password-marker-838273'

def fixture(tmp_path,**changes):
 settings=Settings(TOKEN,'sqlite:///'+(tmp_path/'accounts.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,**changes)
 app=create_app(settings);return settings,app,TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN})
def login(c,name='admin',password='admin'):return c.post('/auth/login',json={'username':name,'password':password})
def user(c,name,roles=None):
 r=c.post('/api/users',json={'username':name,'password':SECRET,'roles':roles or ['operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False});assert r.status_code==201,r.text;return r.json()
def enroll(app,enabled=False):
 store=app.state.store;return store.enroll(Enroll(agent_protocol=1,enrollment_token=store.ticket()['enrollment_token'],name='fixture-node',os_family='Windows',commands_enabled=enabled))
def changes(name='fixture-node',enabled=False):return {'name':name,'commands_enabled':enabled,'installations_enabled':False,'diagnostics_enabled':False}

def test_bootstrap_cookie_verifier_no_fallback_csrf_and_logout(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  assert login(c).status_code==200
  cookie=c.cookies.get('pslc_session');assert cookie and c.get('/api/me').json()['id']=='local-development'
  r=c.get('/api/users');assert 'password_hash' not in r.text
  with app.state.store.tx() as conn:verifier=conn.scalar(select(accounts.c.password_hash))
  assert verifier.startswith('scrypt$') and verifier!='admin'
  assert c.post('/api/enrollment-tokens',headers={'Origin':'https://evil.invalid'}).status_code==403
  assert c.get('/api/me',headers={'Authorization':'Bearer '+TOKEN}).status_code==401
  assert c.post('/auth/logout').status_code==200 and c.get('/api/me').status_code==401
  assert c.post('/auth/login',headers={'Origin':'https://evil.invalid'},json={'username':'admin','password':'admin'}).status_code==403

def test_throttle_is_bounded_for_parallel_bad_passwords(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  with ThreadPoolExecutor(max_workers=8) as pool:codes=list(pool.map(lambda _:login(c,'admin','wrong').status_code,range(8)))
  assert codes.count(401)==5 and codes.count(429)==3
  assert len(app.state.auth.login_attempts.data)==1

def test_password_change_restart_and_backup_preserve_verifier(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  assert login(c).status_code==200
  assert c.post('/api/me/password',json={'old_password':'wrong','new_password':SECRET}).status_code==401
  assert c.post('/api/me/password',json={'old_password':'admin','new_password':SECRET}).status_code==200
  assert c.get('/api/me').status_code==401
 app.state.store.engine.dispose();snapshot=tmp_path/'backup.json';info=backup(s.database_url,snapshot)
 assert SECRET.encode() not in snapshot.read_bytes() and SECRET.encode() not in (tmp_path/'accounts.sqlite3').read_bytes()
 restored_url='sqlite:///'+(tmp_path/'restored.sqlite3').as_posix();restore(snapshot,restored_url,info['digest'])
 restarted=create_app(replace(s,database_url=restored_url))
 with TestClient(restarted,base_url=ORIGIN,headers={'Origin':ORIGIN}) as fresh:
  assert login(fresh).status_code==401
  assert login(fresh,password=SECRET).status_code==200
  assert len(fresh.get('/api/users').json())==1

def test_last_admin_and_stale_revision_guard(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  login(c);admin=c.get('/api/users').json()[0]
  body={'roles':['operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':True,'expected_revision':admin['auth_version']}
  assert c.put('/api/users/'+admin['id'],json=body).json()['error']=='last_admin_required'
  second=user(c,'secondadmin',['platform_admin'])
  body['expected_revision']=99
  assert c.put('/api/users/'+admin['id'],json=body).status_code==409
  assert c.get('/api/me').status_code==200

def test_employee_owner_isolation_and_revoke_before_dispatch(tmp_path):
 s,app,admin=fixture(tmp_path)
 with admin:
  login(admin);alice=user(admin,'alice');bob=user(admin,'bob');node=enroll(app,True)
  with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as a,TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as b:
   assert login(a,'alice',SECRET).status_code==200 and login(b,'bob',SECRET).status_code==200
   task=a.post('/api/tasks',json={**command_contract(),'device_id':node['device_id'],'session_id':'employee','kind':'command','code':'print(42)'}).json()
   assert b.get('/api/tasks/'+task['id']).status_code==404
   assert a.post('/api/tasks/'+task['id']+'/decision',json={'approve':True,'plan_hash':task['plan_hash']}).status_code==200
   body={k:alice[k] for k in ('roles','device_ids','knowledge_spaces')};body.update(disabled=True,expected_revision=alice['auth_version'])
   assert admin.put('/api/users/'+alice['id'],json=body).status_code==200
   assert a.get('/api/tasks').status_code==401
   assert app.state.store.poll(node['device_id'],node['credential'],agent_protocol=1) is None
   assert app.state.store.get_task(task['id'])['state']=='access_revoked'
   assert b.post('/api/enrollment-tokens').status_code==403

def test_bound_profile_rejects_wrong_os_or_capability_without_spending(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  login(c);r=c.post('/api/nodes/onboarding',json={'name':'pilot-windows','os_family':'Windows','state_path':'C:/ProgramData/PSLC-Agent/id.json',**{k:False for k in ('commands_enabled','installations_enabled','diagnostics_enabled')}})
  assert r.status_code==200,r.text
  ticket=r.json();token=ticket['enrollment_token'];assert 'enrollment_token' not in ticket['config'] and not any(ticket['config'][k] for k in ('commands_enabled','installations_enabled','diagnostics_enabled'))
  body={'enrollment_token':token,'name':'reported-host','os_family':'Linux'}
  assert c.post('/agent/enroll',json=body).status_code==403
  body['os_family']='Windows';body['commands_enabled']=True
  assert c.post('/agent/enroll',json=body).status_code==403
  body['commands_enabled']=False;node=c.post('/agent/enroll',json=body).json()
  assert c.get('/api/devices').json()[0]['name']=='pilot-windows'
  assert c.post('/agent/enroll',json=body).status_code==401
  app.state.store.engine.dispose();data=(tmp_path/'accounts.sqlite3').read_bytes();assert token.encode() not in data and node['credential'].encode() not in data

def test_exact_configuration_stale_owner_ttl_and_local_optin(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  login(c);other=user(c,'otheradmin',['platform_admin']);node=enroll(app)
  endpoint='/api/devices/'+node['device_id']+'/configuration-plans'
  p=c.post(endpoint,json=changes(enabled=True)).json()
  with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as second:
   login(second,'otheradmin',SECRET)
   assert second.post(endpoint+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']}).status_code==404
  assert c.post(endpoint+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':'0'*64}).status_code==409
  q=c.post(endpoint,json=changes('renamed')).json()
  assert c.post(endpoint+'/'+q['id']+'/decision',json={'approve':True,'plan_hash':q['plan_hash']}).status_code==200
  assert c.post(endpoint+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']}).status_code==409
  p=c.post(endpoint,json=changes('renamed',True)).json()
  assert c.post(endpoint+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']}).status_code==200
  from agent.executor import execute
  assert execute({'kind':'command','runtime':'python','code':'print(42)'},commands_enabled=False)['outcome']=='commands_disabled'
  assert c.get('/api/devices').json()[0]['id']==node['device_id']

def test_disable_blocks_confirmed_command_and_keeps_running_truth(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  login(c);node=enroll(app,True)
  task=c.post('/api/tasks',json={**command_contract(),'device_id':node['device_id'],'session_id':'config','kind':'command','code':'print(42)'}).json()
  c.post('/api/tasks/'+task['id']+'/decision',json={'approve':True,'plan_hash':task['plan_hash']})
  endpoint='/api/devices/'+node['device_id']+'/configuration-plans';p=c.post(endpoint,json=changes()).json()
  assert c.post(endpoint+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']}).status_code==200
  assert app.state.store.poll(node['device_id'],node['credential'],agent_protocol=1) is None
  assert c.get('/api/tasks/'+task['id']).json()['state']=='commands_disabled'

def test_schema5_migration_preserves_identity_and_bootstrap_once(tmp_path):
 legacy=Settings(TOKEN,'sqlite:///'+(tmp_path/'legacy.sqlite3').as_posix());store=Store(legacy)
 node=store.enroll(Enroll(agent_protocol=1,enrollment_token=store.ticket()['enrollment_token'],name='legacy',os_family='Windows'))
 with store.engine.begin() as conn:
  conn.execute(text('DROP TABLE accounts'));conn.execute(text('ALTER TABLE enrollment DROP COLUMN profile'));legacy_version(conn,'UPDATE schema_version SET id=5')
 store.engine.dispose();app=create_app(replace(legacy,auth_mode='accounts'))
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  assert login(c).status_code==200 and len(c.get('/api/users').json())==1
  assert c.get('/api/devices').json()[0]['id']==node['device_id']
  assert app.state.store.poll(node['device_id'],node['credential'],agent_protocol=1) is None
  with app.state.store.tx() as conn:assert conn.scalar(select(version.c.id))==SCHEMA_VERSION

def test_deferred_confluence_no_egress_even_with_old_credentials(tmp_path):
 calls=[]
 def transport(r):calls.append(r);return httpx.Response(500)
 s=Settings(TOKEN,'sqlite:///'+(tmp_path/'deferred.sqlite3').as_posix(),knowledge_enabled=False,confluence_url='https://old-confluence.invalid',confluence_token='old-fixture-token',confluence_spaces=('IT',),model_url='https://model.invalid',model_id='fixture')
 with TestClient(create_app(s,httpx.MockTransport(transport)),headers={'Authorization':'Bearer '+TOKEN}) as c:
  assert c.get('/api/knowledge?q=failure').json()['status']=='deferred'
  assert c.get('/api/overview').json()['integrations']['confluence']=='deferred'
  for path,body in (('/api/knowledge/retrieve',{'question':'failure'}),('/api/knowledge/connection',{'token':'old-fixture-token'}),('/api/agent/requests',{'request_id':'deferred-123','session_id':'s','question':'Найди инструкцию failure'})):
   assert c.post(path,json=body).json()['error']=='knowledge_deferred'
  r=c.get('/api/readiness').json();assert 'Confluence' in r['deferred_capabilities'] and not any(g['id']=='confluence' for g in r['external_gates'])
 assert calls==[]


def test_expired_node_plan_and_running_task_not_falsely_cancelled(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  login(c);node=enroll(app,True)
  task=c.post('/api/tasks',json={**command_contract(),'device_id':node['device_id'],'session_id':'running','kind':'command','code':'print(42)'}).json()
  c.post('/api/tasks/'+task['id']+'/decision',json={'approve':True,'plan_hash':task['plan_hash']});job=app.state.store.poll(node['device_id'],node['credential'],agent_protocol=1);assert job
  url='/api/devices/'+node['device_id']+'/configuration-plans';p=c.post(url,json=changes()).json()
  assert c.post(url+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']}).status_code==200
  assert c.get('/api/tasks/'+task['id']).json()['state']=='running'
  p=c.post(url,json=changes('expired')).json()
  manager=app.state.nodes
  manager.plans.data[p['id']]=(0,{})
  assert c.post(url+'/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']}).status_code==404

def test_schema5_snapshot_upgrades_without_passwords(tmp_path):
 s=Settings(TOKEN,'sqlite:///'+(tmp_path/'v5.sqlite3').as_posix());store=Store(s);node=store.enroll(Enroll(agent_protocol=1,enrollment_token=store.ticket()['enrollment_token'],name='prior',os_family='Linux'))
 path=tmp_path/'legacy.json';backup(s.database_url,path);value=json.loads(path.read_bytes());del value['tables']['accounts']
 for table in ('conversations','conversation_turns'):del value['tables'][table]
 for row in value['tables']['devices']:del row['device_kind']
 for row in value['tables']['workflows']:
  for column in ('execution_mode','target_snapshot','conversation_id'):del row[column]
 strip_recovery_snapshot(value);value['schema_version']=5;value['tables']['schema_version']=[{'id':5}]
 for row in value['tables']['enrollment']:del row['profile']
 path.write_text(json.dumps(value),encoding='utf-8');digest=hashlib.sha256(path.read_bytes()).hexdigest();target='sqlite:///'+(tmp_path/'upgraded.sqlite3').as_posix();restore(path,target,digest)
 app=create_app(replace(s,database_url=target,auth_mode='accounts'))
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  assert login(c).status_code==200 and c.get('/api/devices').json()[0]['id']==node['device_id']

def test_postgres_account_grants_inside_dispatch_and_recovery(tmp_path):
 from test_operations_recovery import postgres_database
 with postgres_database() as s,postgres_database() as restored_settings:
  s=replace(s,auth_mode='accounts',knowledge_enabled=False);app=create_app(s)
  with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
   login(c);alice=user(c,'pgemployee');node=enroll(app)
   with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as employee:
    login(employee,'pgemployee',SECRET)
    task=employee.post('/api/tasks',json={'device_id':node['device_id'],'session_id':'pg-account'}).json()
    # Core dispatch reads current account grants from the same outer DB transaction.
    job=app.state.store.poll(node['device_id'],node['credential'],agent_protocol=1);assert job and job['task_id']==task['id']
    app.state.store.finish(node['device_id'],node['credential'],task['id'],Result(lease_id=job['lease_id'],outcome='ok',exit_code=0,cpu_percent=1,memory_percent=2,disk_free_gb=3))
   snapshot=tmp_path/'pg-accounts.json';result=backup(s.database_url,snapshot);assert SECRET.encode() not in snapshot.read_bytes();restore(snapshot,restored_settings.database_url,result['digest'])
  restored=create_app(replace(restored_settings,auth_mode='accounts',knowledge_enabled=False))
  with TestClient(restored,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
   assert login(c).status_code==200 and len(c.get('/api/users').json())==2


def test_onboarding_preserves_global_enrollment_scope_gate(tmp_path):
 s,app,c=fixture(tmp_path)
 with c:
  login(c);r=c.post('/api/users',json={'username':'limitedadmin','password':SECRET,'roles':['platform_admin'],'device_ids':[],'knowledge_spaces':[],'disabled':False});assert r.status_code==201
  with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as limited:
   login(limited,'limitedadmin',SECRET)
   assert limited.post('/api/enrollment-tokens').status_code==403
   assert limited.post('/api/nodes/onboarding',json={'name':'not-allowed','os_family':'Linux','state_path':'/var/lib/pslc-agent/id.json'}).status_code==403
