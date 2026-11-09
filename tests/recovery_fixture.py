"""Explicit synthetic contracts/evidence for existing queue and identity tests.
These helpers never execute a command or claim physical-device acceptance.
"""
import time
from pathlib import Path
from sqlalchemy import inspect,text
from backend.recovery_types import fingerprint,make_program
def command_contract(path='C:/PSLC-QA-fixture/config.json'):
 return {'recovery':{'resources':[{'type':'configuration_file','path':str(path)}]},'verification':{'runtime':'python','code':'print("checked")','expected_output':'checked','timeout_seconds':15}}
def simulated_recovery(job,state='ready'):
 p=job['payload'];program=p['anti_program'];ph=fingerprint(program)
 return {'phase':'forward','receipt':{'version':1,'device_id':p['device_id'],'original_task_id':job['task_id'],'original_plan_hash':job['plan_hash'],'program_hash':ph,'resource_set_hash':program['resource_set_hash'],'capsule_id':fingerprint({'device_id':p['device_id'],'task_id':job['task_id'],'plan_hash':job['plan_hash'],'program_hash':ph}),'expires':float(time.time()+7*86400),'state':state,'baseline_hash':'b'*64,'post_hash':'c'*64,'resource_count':sum(program['type_counts'].values()),'restored_count':0}}
def paired_fixture(payload,tmp_path,device_id='fixture-device',task_id='fixture-task'):
 p={**command_contract(tmp_path/'temporary-config.json'),**payload,'device_id':device_id,'owner_id':'local-development','task_id':task_id}
 p['anti_program']=make_program(p['recovery']['resources'],device_id,task_id)
 # A real local execution only uses a disposable identity directory.
 state=tmp_path/'native-identity/state.json';state.parent.mkdir(exist_ok=True)
 return p,{'state_path':str(state),'device_id':device_id,'task_id':task_id,'plan_hash':fingerprint(p)}
def legacy_version(connection,sql):
 # Build a genuine pre-v8 fixture before the old test removes its earlier-version additions.
 known=inspect(connection)
 for name in ('schedule_runs','schedules'):
  if known.has_table(name):connection.execute(text('DROP TABLE '+name))
 if 'schedule_run_id' in {c['name'] for c in inspect(connection).get_columns('tasks')}:connection.execute(text('ALTER TABLE tasks DROP COLUMN schedule_run_id'))
 if known.has_table('task_recoveries'):connection.execute(text('DROP TABLE task_recoveries'))
 for table,column in [('devices','agent_protocol'),('tasks','recovery_of')]:
  if column in {c['name'] for c in inspect(connection).get_columns(table)}:connection.execute(text('ALTER TABLE '+table+' DROP COLUMN '+column))
 return connection.execute(text(sql))
def strip_recovery_snapshot(data):
 tables=data['tables'];tables.pop('task_recoveries',None);tables.pop('schedules',None);tables.pop('schedule_runs',None)
 for row in tables['tasks']:row.pop('schedule_run_id',None)
 for row in tables['devices']:row.pop('agent_protocol',None)
 for row in tables['tasks']:row.pop('recovery_of',None)
 return data

def historical_metadata(revision):
 from sqlalchemy import MetaData,Table,Column
 from backend.store import metadata
 names={'dispatch_guard','schema_version','devices','enrollment','tasks','events'}
 if revision>=3:names.add('investigation_jobs')
 omitted={'schedule_run_id','agent_protocol','recovery_of','device_kind','execution_mode','target_snapshot','conversation_id','owner_id','actor_id'}
 if revision<2:omitted|={'installations_enabled','package_meta'}
 if revision<3:omitted.add('diagnostics_enabled')
 old=MetaData()
 for table in metadata.sorted_tables:
  if table.name in names:Table(table.name,old,*[Column(c.name,c.type,primary_key=c.primary_key,nullable=c.nullable) for c in table.columns if c.name not in omitted and not (table.name=='enrollment' and c.name=='profile')])
 return old
