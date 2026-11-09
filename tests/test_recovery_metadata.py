import hashlib,json
from dataclasses import replace
from sqlalchemy import create_engine,select,text
import pytest
from backend.config import Settings
from backend.store import Store,devices,accounts,tasks,task_recoveries,version,Problem,SCHEMA_VERSION
from backend.accounts import LocalAccounts
from backend.schemas import Enroll,TaskInput,Decision
from backend.operations import backup,restore
from backend.recovery_types import make_program,fingerprint
def fresh(tmp,url=None):
 s=Settings('public-recovery-fixture-token','sqlite:///'+(tmp/'source.sqlite3').as_posix() if url is None else url,knowledge_enabled=False)
 store=Store(s);LocalAccounts(store)
 n=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='recovery-fixture',os_family='Windows',agent_protocol=1,commands_enabled=True))
 return s,store,n
def identity(store):
 with store.tx() as c:
  return list(c.execute(select(accounts)).mappings()),[{k:v for k,v in row.items() if k!='agent_protocol'} for row in c.execute(select(devices)).mappings()]
def downgrade_genuine_seven(store):
 with store.engine.begin() as c:
  c.execute(text('DROP TABLE schedule_runs'));c.execute(text('DROP TABLE schedules'));c.execute(text('ALTER TABLE tasks DROP COLUMN schedule_run_id'))
  c.execute(text('DROP TABLE task_recoveries'));c.execute(text('ALTER TABLE devices DROP COLUMN agent_protocol'));c.execute(text('ALTER TABLE tasks DROP COLUMN recovery_of'));c.execute(text('UPDATE schema_version SET id=7'))
 store.engine.dispose()
def check_migration(s,store,n):
 before=identity(store);downgrade_genuine_seven(store);up=Store(s)
 assert identity(up)==before
 with up.tx() as c:
  assert c.scalar(select(version.c.id))==SCHEMA_VERSION and c.scalar(select(devices.c.agent_protocol))==0
 assert up.check_agent
 up.poll(n['device_id'],n['credential'],0)
 up.engine.dispose()
def test_genuine_seven_sqlite_upgrade_preserves_credentials_and_grants(tmp_path):
 check_migration(*fresh(tmp_path))
def test_genuine_seven_postgres_upgrade(tmp_path):
 from test_operations_recovery import postgres_database
 with postgres_database() as s:
  settings,store,n=fresh(tmp_path,s.database_url);check_migration(settings,store,n)
@pytest.mark.parametrize('old',[6,7,8])
def test_restore_known_schema_and_validate_only_typed_recovery(tmp_path,old):
 from recovery_fixture import command_contract
 s,store,n=fresh(tmp_path);t=store.create_task(TaskInput(device_id=n['device_id'],session_id='metadata',kind='command',code='print("checked")',**command_contract('C:/fixture/config.json')))
 store.decision(t['id'],Decision(approve=True,plan_hash=t['plan_hash']))
 source=tmp_path/'backup.json';backup(s.database_url,source);data=json.loads(source.read_text(encoding='utf-8'))
 assert b'C:/fixture/config.json' not in source.read_bytes()
 if old<9:
  data['tables'].pop('schedules');data['tables'].pop('schedule_runs')
  for row in data['tables']['tasks']:row.pop('schedule_run_id')
  data['schema_version']=old;data['tables']['schema_version']=[{'id':old}]
 if old<8:
  data['tables'].pop('task_recoveries')
  for row in data['tables']['devices']:row.pop('agent_protocol')
  for row in data['tables']['tasks']:row.pop('recovery_of')
  if old==6:
   data['tables'].pop('conversations');data['tables'].pop('conversation_turns')
   for row in data['tables']['devices']:row.pop('device_kind')
   for row in data['tables']['workflows']:
    for key in ('execution_mode','target_snapshot','conversation_id'):row.pop(key)
  data['schema_version']=old;data['tables']['schema_version']=[{'id':old}]
 source.write_text(json.dumps(data),encoding='utf-8')
 target='sqlite:///'+(tmp_path/'restore.sqlite3').as_posix();restore(source,target,hashlib.sha256(source.read_bytes()).hexdigest());restored=Store(replace(s,database_url=target));assert identity(restored)==identity(store)
 if old==8:
  data['tables']['task_recoveries'][0]['program']['code']='RAW-FORWARD-MARKER'
  source.write_text(json.dumps(data),encoding='utf-8')
  with pytest.raises(ValueError):restore(source,'sqlite:///'+(tmp_path/'invalid.sqlite3').as_posix(),hashlib.sha256(source.read_bytes()).hexdigest())
 store.engine.dispose();restored.engine.dispose()
def test_observed_protocol_credential_before_update_and_legacy_health(tmp_path):
 s,store,n=fresh(tmp_path)
 with pytest.raises(Problem):store.poll(n['device_id'],'wrong-credential',0)
 with store.tx() as c:assert c.scalar(select(devices.c.agent_protocol))==1
 t=store.create_task(TaskInput(device_id=n['device_id'],session_id='legacy-health'))
 assert store.poll(n['device_id'],n['credential'],0)['task_id']==t['id']
 with store.tx() as c:assert c.scalar(select(devices.c.agent_protocol))==0
 store.engine.dispose()

def test_readonly_backup_of_supported_legacy_seven_source(tmp_path):
 s,store,n=fresh(tmp_path);before=identity(store);downgrade_genuine_seven(store)
 path=tmp_path/'pre-upgrade.json';report=backup(s.database_url,path)
 assert report['schema']==7
 target='sqlite:///'+(tmp_path/'upgraded-copy.sqlite3').as_posix()
 restore(path,target,report['digest']);up=Store(replace(s,database_url=target));assert identity(up)==before
 # Source is unchanged, not silently upgraded by the standalone backup utility.
 e=create_engine(s.database_url)
 with e.connect() as c:assert c.scalar(text('SELECT id FROM schema_version'))==7
 e.dispose();up.engine.dispose()

def test_legacy_poll_cannot_use_stale_protocol_one_for_a_confirmed_mutation(tmp_path):
 from recovery_fixture import command_contract
 s,store,n=fresh(tmp_path)
 t=store.create_task(TaskInput(device_id=n['device_id'],session_id='protocol',kind='command',code='print("checked")',**command_contract()))
 store.decision(t['id'],Decision(approve=True,plan_hash=t['plan_hash']))
 assert store.poll(n['device_id'],n['credential'],0) is None
 with store.tx() as c:assert c.scalar(select(devices.c.agent_protocol))==0
 assert store.statistics()['active_fleet']==0
 assert store.poll(n['device_id'],n['credential'],1)['task_id']==t['id']
 store.engine.dispose()
