from recovery_fixture import legacy_version
import json,hashlib,time,uuid,os
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import pytest
from sqlalchemy import select,text,update,create_engine
from sqlalchemy.engine import make_url
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.app import create_app
from backend.store import Store,tasks,devices,events,version,Problem,SCHEMA_VERSION
from backend.schemas import Enroll,TaskInput,Result
from backend.operations import backup,restore
TOKEN='ops-public-test-token-long-enough'
MARKER='RAW-RECOVERY-MARKER-393847'

def setup_store(tmp_path):
 s=Settings(TOKEN,'sqlite:///'+(tmp_path/'source.sqlite3').as_posix());return s,Store(s)
def enroll(store):return store.enroll(Enroll(agent_protocol=1,enrollment_token=store.ticket()['enrollment_token'],name='ops-qa',os_family='Windows'))
def health(store,node,session='ops'):
 return store.create_task(TaskInput(device_id=node['device_id'],session_id=session))
def complete(store,node,job):
 return store.finish(node['device_id'],node['credential'],job['task_id'],Result(lease_id=job['lease_id'],outcome='ok',exit_code=0,duration_ms=2,cpu_percent=2,memory_percent=10,disk_free_gb=20,output=MARKER))

def test_sqlite_snapshot_recovery_marker_history_and_conservative_states(tmp_path):
 s,store=setup_store(tmp_path);node=enroll(store);task=health(store,node);job=store.poll(node['device_id'],node['credential'],agent_protocol=1);complete(store,node,job)
 active=health(store,node,'active');store.poll(node['device_id'],node['credential'],agent_protocol=1);queued=health(store,node,'queued')
 path=tmp_path/'backup.json';report=backup(s.database_url,path)
 assert MARKER.encode() not in path.read_bytes()
 target='sqlite:///'+(tmp_path/'restored.sqlite3').as_posix();r=restore(path,target,report['digest']);assert r['remote_execution'] is False
 restored=Store(replace(s,database_url=target));assert restored.get_task(task['id'])['state']=='verified'
 assert restored.get_task(active['id'])['state']=='outcome_unknown' and restored.get_task(queued['id'])['state']=='plan_expired'
 assert restored.poll(node['device_id'],node['credential'],agent_protocol=1) is None
 assert restored.outputs.data=={}
 with restored.tx() as c:assert c.scalar(select(devices.c.credential_hash))==__import__('hashlib').sha256(node['credential'].encode()).hexdigest()
 assert MARKER.encode() not in (tmp_path/'restored.sqlite3').read_bytes()
 with pytest.raises(ValueError):restore(path,target,report['digest'])

@pytest.mark.parametrize('case',['digest','unknown-field','unsupported-schema','raw-summary','duplicate-key'])
def test_invalid_snapshot_rejected_before_target_creation(tmp_path,case):
 s,store=setup_store(tmp_path);node=enroll(store);task=health(store,node);path=tmp_path/'backup.json';report=backup(s.database_url,path);value=json.loads(path.read_bytes())
 if case=='unknown-field':value['tables']['tasks'][0]['output']=MARKER
 elif case=='unsupported-schema':value['schema_version']=99
 elif case=='raw-summary':value['tables']['tasks'][0]['summary']={'output':MARKER}
 elif case=='duplicate-key':value['tables']['devices']*=2
 if case!='digest':path.write_text(json.dumps(value),encoding='utf-8')
 digest='0'*64 if case=='digest' else hashlib.sha256(path.read_bytes()).hexdigest();target=tmp_path/'empty.sqlite3'
 with pytest.raises(Exception):restore(path,'sqlite:///'+target.as_posix(),digest)
 assert not target.exists()

def test_unknown_source_schema_or_raw_json_cannot_be_exported(tmp_path):
 s,store=setup_store(tmp_path);node=enroll(store);task=health(store,node)
 with store.tx() as c:c.execute(update(tasks).where(tasks.c.id==task['id']).values(summary={'output':MARKER}))
 with pytest.raises(ValueError):backup(s.database_url,tmp_path/'bad.json')
 assert not (tmp_path/'bad.json').exists()

def test_health_scope_and_fixed_outage_error(tmp_path,monkeypatch):
 s,store=setup_store(tmp_path);app=create_app(s)
 with TestClient(app,headers={'Authorization':'Bearer '+TOKEN}) as c:
  r=c.get('/api/operations/status');assert r.status_code==200 and r.json()['production_admitted'] is False
  assert c.get('/readyz').json()['external_acceptance']=='pending'
  def broken(*args,**kwargs):raise RuntimeError(MARKER+' secret-dsn')
  monkeypatch.setattr(app.state.store.engine,'connect',broken)
  for route in ('/api/operations/status','/readyz'):
   r=c.get(route);assert r.status_code==503 and r.json()=={'error':'database_unavailable'} and MARKER not in r.text

@contextmanager
def postgres_database():
 config=os.getenv('PSLC_TEST_POSTGRES_CONFIG')
 if not config:pytest.skip('Explicit isolated PostgreSQL configuration required')
 import psycopg
 from psycopg import sql
 settings=json.loads(Path(config).read_text(encoding='utf-8'));base=make_url(settings['database_url'])
 assert base.host=='127.0.0.1' and base.port==55433 and base.database=='pslc_qa','Only isolated QA endpoint allowed'
 name='pslc_test_'+uuid.uuid4().hex
 connection=psycopg.connect(base.set(drivername='postgresql',database='pslc_qa').render_as_string(hide_password=False),autocommit=True)
 connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
 url=base.set(database=name).render_as_string(hide_password=False)
 try:yield Settings(TOKEN,url)
 finally:
  connection.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)));connection.close()

def test_postgres_concurrent_20_and_5_across_connections():
 with postgres_database() as s:
  store=Store(s);pollers=[Store(s) for _ in range(4)]
  try:
   nodes=[enroll(store) for _ in range(30)]
   for i,node in enumerate(nodes):health(store,node,'same-session' if i<10 else 'session-'+str(i))
   def poll(entry):
    index,node=entry;return pollers[index%4].poll(node['device_id'],node['credential'],agent_protocol=1)
   with ThreadPoolExecutor(max_workers=16) as pool:jobs=list(pool.map(poll,enumerate(nodes)))
   assert sum(job is not None for job in jobs)==20
   assert sum(job is not None for job in jobs[:10])==5
   assert store.statistics()['active_fleet']==20
   node=nodes[next(i for i,j in enumerate(jobs) if j)];job=next(j for j in jobs if j)
   complete(store,node,job);complete(store,node,job)
   assert store.statistics()['counts']['completed']==1
  finally:
   for p in pollers:p.engine.dispose()
   store.engine.dispose()

def test_postgres_v4_migration_backup_restore_and_restart(tmp_path):
 with postgres_database() as s,postgres_database() as target:
  store=Store(s);node=enroll(store);done=health(store,node);complete(store,node,store.poll(node['device_id'],node['credential'],agent_protocol=1))
  with store.engine.begin() as c:c.execute(text('DROP TABLE knowledge_records'));legacy_version(c,'UPDATE schema_version SET id=4')
  store.engine.dispose();store=Store(s)
  with store.tx() as c:assert c.scalar(select(version.c.id))==SCHEMA_VERSION
  task=health(store,node,'active');store.poll(node['device_id'],node['credential'],agent_protocol=1);snapshot=tmp_path/'pg-backup.json';report=backup(s.database_url,snapshot)
  assert MARKER.encode() not in snapshot.read_bytes()
  r=restore(snapshot,target.database_url,report['digest']);assert r['duration_ms']<4*3600*1000
  restored=Store(target)
  assert restored.get_task(done['id'])['state']=='verified' and restored.get_task(task['id'])['state']=='outcome_unknown'
  assert restored.poll(node['device_id'],node['credential'],agent_protocol=1) is None
  store.engine.dispose();restored.engine.dispose()

def test_postgres_500_registry_cap_is_synthetic():
 with postgres_database() as s:
  store=Store(s)
  try:
   with store.tx() as c:c.execute(devices.insert(),[{'id':str(uuid.uuid4()),'name':'synthetic','os_family':'Linux','credential_hash':'0'*64,'revoked':False,'commands_enabled':False,'installations_enabled':False,'diagnostics_enabled':False,'last_seen':0.0} for _ in range(500)])
   with pytest.raises(Problem):enroll(store)
   assert len(store.list_devices())==500
  finally:store.engine.dispose()


def test_postgres_handshake_timeout_is_bounded_and_error_is_fixed(tmp_path):
 import socket
 from threading import Event,Thread
 from backend.operations import engine,status
 listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);stop=Event()
 def blackhole():
  connection,_=listener.accept()
  try:stop.wait(8)
  finally:connection.close()
 thread=Thread(target=blackhole,daemon=True);thread.start();s,store=setup_store(tmp_path);original=store.engine
 store.engine=engine('postgresql+psycopg://public_fixture:public_fixture@127.0.0.1:'+str(listener.getsockname()[1])+'/fixture')
 started=time.monotonic()
 try:
  with pytest.raises(Problem) as error:status(store)
  assert error.value.code=='database_unavailable' and error.value.status==503 and time.monotonic()-started<8
 finally:stop.set();listener.close();store.engine.dispose();original.dispose();thread.join(2)


def test_postgres_schema6_to7_and_conversation_metadata_restore(tmp_path):
 from backend.store import accounts,conversations,conversation_turns
 with postgres_database() as s,postgres_database() as target:
  s=replace(s,auth_mode='accounts');app=create_app(s)
  with TestClient(app,base_url='http://127.0.0.1:8787',headers={'Origin':'http://127.0.0.1:8787'}) as c:
   assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
   cid=c.post('/api/conversations',json={'title':'PostgreSQL case'}).json()['id']
   with app.state.store.tx() as conn:verifier=conn.scalar(select(accounts.c.password_hash))
   report=backup(s.database_url,tmp_path/'pg-chat.json')
  store=app.state.store
  with store.engine.begin() as c:
   c.execute(text('DROP TABLE conversation_turns'));c.execute(text('DROP TABLE conversations'))
   c.execute(text('ALTER TABLE devices DROP COLUMN device_kind'))
   for name in ('execution_mode','target_snapshot','conversation_id'):c.execute(text('ALTER TABLE workflows DROP COLUMN '+name))
   legacy_version(c,'UPDATE schema_version SET id=6')
  store.engine.dispose();upgraded=Store(s)
  with upgraded.tx() as c:
   assert c.scalar(select(version.c.id))==SCHEMA_VERSION
   assert c.scalar(select(accounts.c.password_hash))==verifier
  restore(tmp_path/'pg-chat.json',target.database_url,report['digest'])
  restored=Store(target)
  with restored.tx() as c:assert c.scalar(select(conversations.c.id))==cid
  upgraded.engine.dispose();restored.engine.dispose()
