"""Scheduler acceptance on isolated accounts/DB and disposable config; no real schedule."""
import hashlib,json,time
from pathlib import Path
from dataclasses import replace
import pytest,httpx
from sqlalchemy import select,update,text
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings
from backend.store import schedules,schedule_runs,devices,tasks,accounts,events,SCHEMA_VERSION,Store
from backend.operations import backup,restore
from backend.schedule_clock import slot_key,next_slot
from test_manual_rollback import run as run_native
ORIGIN='http://127.0.0.1:8787'

def fixture(tmp,transport=None):
 s=Settings('public-schedule-fixture-token','sqlite:///'+(tmp/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,schedule_vault_path=str(tmp/'vault'),system_preferences_path=str(tmp/'prefs.json'),model_url='https://fixture.invalid/v1' if transport else '',model_id='fixture-model' if transport else '')
 app=create_app(s,transport);queued=[];app.state.scheduler.launch=queued.append
 return s,app,queued

def login(c,name='admin',password='admin'):assert c.post('/auth/login',json={'username':name,'password':password}).status_code==200

def node(c,app,name='qa-workstation',os='Windows',kind='workstation',commands=False,diagnostics=False):
 r=c.post('/api/enrollment-tokens').json()
 n=c.post('/agent/enroll',json={'enrollment_token':r['enrollment_token'],'name':name,'os_family':os,'commands_enabled':commands,'diagnostics_enabled':diagnostics,'agent_protocol':1}).json()
 with app.state.store.tx() as con:con.execute(update(devices).where(devices.c.id==n['device_id']).values(device_kind=kind))
 return n

def rule(c,template=None,scope=None):
 r=c.post('/api/schedules',json={'title':'QA reviewed rule','calendar':{'frequency':'daily','local_time':'07:00','timezone':'Europe/Moscow'},'targets':scope or {'kind':'workstations'},'template':template or {'mode':'health'}})
 assert r.status_code==201,r.text
 return r.json()

def enable(c,r):
 v=c.get('/api/schedules/'+r['id']).json()
 response=c.post('/api/schedules/'+r['id']+'/decision',json={'expected_revision':v['revision'],'definition_hash':v['review']['definition_hash'],'consent':True})
 assert response.status_code==200,response.text
 return response.json()

def prepare(c,r,key='manual-fixture-001'):
 response=c.post('/api/schedules/'+r['id']+'/runs',json={'request_id':key});assert response.status_code==202,response.text;return response.json()

def getrun(c,r,run):return c.get('/api/schedules/'+r['id']+'/runs/'+run['id']).json()

def test_health_workstations_logout_unique_consent_scope_and_statistics(tmp_path):
 s,app,queued=fixture(tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app);server=node(c,app,'qa-server',kind='server');unknown=node(c,app,'qa-unknown',kind='unknown')
  r=rule(c);assert not r['enabled'] and r['consented_ids']==[]
  assert c.post('/api/schedules/'+r['id']+'/runs',json={'request_id':'before-consent'}).status_code==409
  bad=c.post('/api/schedules/'+r['id']+'/decision',json={'expected_revision':1,'definition_hash':'0'*64,'consent':True});assert bad.status_code==409
  r=enable(c,r);assert r['consented_ids']==[pc['device_id']]
  newpc=node(c,app,'qa-later-workstation')
  run=prepare(c,r);assert prepare(c,r)['id']==run['id'] and queued==[run['id']]
  c.post('/auth/logout');app.state.scheduler.execute(run['id'])
  h={'Authorization':'Bearer '+pc['credential'],'X-PSLC-Agent-Protocol':'1'}
  job=c.post('/agent/'+pc['device_id']+'/poll',headers=h).json()['job'];assert job and job['payload']['kind']=='health'
  for n in (server,unknown,newpc):assert c.post('/agent/'+n['device_id']+'/poll',headers={'Authorization':'Bearer '+n['credential'],'X-PSLC-Agent-Protocol':'1'}).json()['job'] is None
  result=c.post('/agent/'+pc['device_id']+'/tasks/'+job['task_id']+'/result',headers=h,json={'lease_id':job['lease_id'],'outcome':'ok','exit_code':0,'cpu_percent':2,'memory_percent':20,'disk_free_gb':10});assert result.status_code==200
  login(c);result=getrun(c,r,run);assert result['state']=='verified' and any(x['reason']=='outside_consent' for x in result['summary']['skipped'])
  stats=c.get('/api/overview').json()['statistics']['counts'];assert stats.get('confirmed',0)==0 and stats['schedule_admitted']==1
  assert c.post('/api/users',json={'username':'other','password':'public-other-password','roles':['operator'],'device_ids':['*']}).status_code==201
  c.post('/auth/logout');login(c,'other','public-other-password')
  assert c.get('/api/schedules/'+r['id']).status_code==404 and c.get('/api/schedules').json()==[]

def test_static_script_paired_execution_metadata_privacy_and_manual_rollback(tmp_path):
 s,app,queued=fixture(tmp_path);folder=tmp_path/'configuration';folder.mkdir();file=folder/'settings.json';file.write_text('before',encoding='utf-8')
 template={'mode':'script','script':{'runtime':'python','code':'from pathlib import Path;Path('+repr(str(file))+').write_text("after",encoding="utf-8");print("PRIVATE-SCHEDULE-SCRIPT-MARKER")','verification':{'runtime':'python','code':'from pathlib import Path;print("ok" if Path('+repr(str(file))+').read_text()=="after" else "bad")','expected_output':'ok'},'recovery':{'resources':[{'type':'configuration_file','path':str(file)}]}}}
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app,commands=True);r=rule(c,template,{'kind':'manual','ids':[pc['device_id']]});assert file.read_text()=='before';r=enable(c,r)
  run=prepare(c,r);c.post('/auth/logout');app.state.scheduler.execute(run['id'])
  job,outcome=run_native(c,pc,tmp_path);assert outcome['state']=='verified' and file.read_text()=='after'
  login(c);rundata=getrun(c,r,run);assert rundata['state']=='verified'
  task=c.get('/api/tasks/'+job['task_id']).json();assert task['recovery']['status']=='ready'
  path=tmp_path/'backup.json';report=backup(s.database_url,path)
  assert b'PRIVATE-SCHEDULE-SCRIPT-MARKER' not in path.read_bytes() and str(file).encode() not in path.read_bytes()
  assert all(b'PRIVATE-SCHEDULE-SCRIPT-MARKER' not in p.read_bytes() for p in (tmp_path/'vault').glob('*.enc'))
  undo=c.post('/api/tasks/'+job['task_id']+'/rollback-plans',json={'session_id':'schedule-undo-fixture','request_id':'schedule-undo-fixture'});assert undo.status_code==200,undo.text;undo=undo.json()
  assert c.post('/api/workflows/'+undo['id']+'/decision',json={'approve':True,'plan_hash':undo['plan_hash']}).status_code==200
  assert run_native(c,pc,tmp_path)[1]['state']=='restored' and file.read_text()=='before'
  target='sqlite:///'+(tmp_path/'restored.sqlite3').as_posix();restore(path,target,report['digest']);restored=Store(replace(s,database_url=target))
  with restored.tx() as con:
   restored_rule=con.execute(select(schedules)).mappings().one();assert not restored_rule['enabled'] and restored_rule['consent_id'] is None
  restored.engine.dispose()

def test_pause_resume_cannot_revive_queued_run_and_tamper_blocks(tmp_path):
 s,app,queued=fixture(tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app);r=enable(c,rule(c));first=prepare(c,r);app.state.scheduler.execute(first['id']);assert getrun(c,r,first)['state']=='queued'
  oldnonce=r['consent_id'];r=c.post('/api/schedules/'+r['id']+'/pause',json={'expected_revision':1}).json();assert not r['enabled']
  r=enable(c,r);assert r['consent_id']!=oldnonce
  assert c.post('/agent/'+pc['device_id']+'/poll',headers={'Authorization':'Bearer '+pc['credential'],'X-PSLC-Agent-Protocol':'1'}).json()['job'] is None
  run=prepare(c,r,'after-resume-fixture');p=app.state.scheduler.vault.path(r['id'],r['revision'],r['template_hash']);p.write_bytes(b'corrupt')
  app.state.scheduler.execute(run['id']);assert getrun(c,r,run)['state']=='template_unavailable'
  assert c.get('/api/schedules/'+r['id']).json()['review_error']=='template_unavailable'

def test_slot_claim_once_no_catchup_overlap_and_restart(tmp_path):
 s,app,queued=fixture(tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);node(c,app);r=enable(c,rule(c));scheduler=app.state.scheduler
  now=time.time();future=next_slot(r['calendar'],now)[0]
  scheduler.tick(now=future);scheduler.tick(now=future)
  assert len(queued)==1
  scheduler.execute(queued[0])
  scheduler.tick(now=future+86400);assert len(queued)==1
  with app.state.store.tx() as con:
   states=list(con.execute(select(schedule_runs.c.state)).scalars());assert 'skipped_overlap' in states
  # A second independent rule missed during downtime is recorded, never launched.
  r2=enable(c,rule(c));scheduler.tick(now=future+3*86400,startup=True);assert len(queued)==1
  with app.state.store.tx() as con:
   assert con.execute(select(schedule_runs.c.state).where(schedule_runs.c.schedule_id==r2['id'])).scalar()=='missed'
  assert c.post('/api/schedules/'+r['id']+'/delete',json={'expected_revision':1}).status_code==200
  assert c.get('/api/schedules/'+r['id']).status_code==404

def test_prompt_requires_new_human_decision_and_revoked_grant_blocks_dispatch(tmp_path):
 from backend.planner import ActionPlan
 s,app,queued=fixture(tmp_path)
 class Planner:
  def propose(self,q,inventory,packages):return ActionPlan.model_validate({'status':'plan','steps':[{'kind':'health','device_id':inventory[0]['id']}]}),'fixture_model'
 app.state.scheduler.planner=Planner()
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app);r=enable(c,rule(c,{'mode':'prompt','prompt':'Проверь состояние выбранного узла, без утверждения действий'}))
  run=prepare(c,r);c.post('/auth/logout');app.state.scheduler.execute(run['id'])
  h={'Authorization':'Bearer '+pc['credential'],'X-PSLC-Agent-Protocol':'1'};assert c.post('/agent/'+pc['device_id']+'/poll',headers=h).json()['job'] is None
  login(c);v=getrun(c,r,run);assert v['state']=='awaiting_confirmation'
  w=v['workflow'];assert c.post('/api/workflows/'+w['id']+'/decision',json={'approve':True,'plan_hash':w['plan_hash']}).status_code==200
  stats=c.get('/api/overview').json()['statistics']['counts'];assert stats['confirmed']==1 and stats['confirmation_requested']==1
  # Trusted DB change models an administrative revocation, no cached-session fallback.
  with app.state.store.tx() as con:con.execute(update(accounts).where(accounts.c.id=='local-development').values(device_ids=['another-device']))
  assert c.post('/agent/'+pc['device_id']+'/poll',headers=h).json()['job'] is None
  with app.state.store.tx() as con:assert con.scalar(select(tasks.c.state).where(tasks.c.schedule_run_id==run['id']))=='access_revoked'

def test_fixed_errors_exact_window_units_optins_and_no_server_target(tmp_path):
 s,app,queued=fixture(tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app,diagnostics=True);linux=node(c,app,'qa-linux',os='Linux',diagnostics=True);server=node(c,app,'qa-server',kind='server',diagnostics=True)
  r=enable(c,rule(c,{'mode':'errors','linux_unit':'app.service'}));run=prepare(c,r);app.state.scheduler.execute(run['id'])
  v=getrun(c,r,run);assert v['state']=='queued' and v['summary']['eligible_count']==2
  for n,source in ((pc,'windows_events'),(linux,'systemd')):
   job=c.post('/agent/'+n['device_id']+'/poll',headers={'Authorization':'Bearer '+n['credential'],'X-PSLC-Agent-Protocol':'1'}).json()['job'];assert job['payload']['kind']=='investigate'
   scope=job['payload']['scope'];assert scope['source']==source and scope['max_events']==100
   from backend.diagnostic_types import timestamp
   assert (timestamp(scope['end_utc'])-timestamp(scope['start_utc'])).total_seconds()==86400
   if source=='systemd':assert scope['unit']=='app.service'
  assert c.post('/agent/'+server['device_id']+'/poll',headers={'Authorization':'Bearer '+server['credential'],'X-PSLC-Agent-Protocol':'1'}).json()['job'] is None
  # Scope/type edits reset permission; a repeated activation does not mint new consent.
  nonce=r['consent_id'];assert enable(c,r)['consent_id']==nonce

def test_native_flags_and_static_scope_fail_before_scheduled_admission(tmp_path):
 s,app,queued=fixture(tmp_path);file=tmp_path/'safe.json'
 template={'mode':'script','script':{'runtime':'python','code':'print("test")','verification':{'runtime':'python','code':'print("ok")','expected_output':'ok'},'recovery':{'resources':[{'type':'configuration_file','path':str(file)}]}}}
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app);r=rule(c,template,{'kind':'manual','ids':[pc['device_id']]})
  review=r['review'];assert review['skipped'][0]['reason']=='commands_disabled'
  assert c.post('/api/schedules/'+r['id']+'/decision',json={'expected_revision':1,'definition_hash':review['definition_hash'],'consent':True}).json()['error']=='schedule_targets_not_ready'
  # Whole template, postcheck, resources and target selection require a new revision/review.
  update={'title':r['title'],'expected_revision':1,'calendar':r['calendar'],'targets':r['target_scope'],'template':{'mode':'health'}}
  edited=c.put('/api/schedules/'+r['id'],json=update);assert edited.status_code==200
  edited=edited.json();assert edited['revision']==2 and edited['definition_hash'] is None and not edited['enabled']
  assert c.post('/api/schedules/'+r['id']+'/decision',json={'expected_revision':1,'definition_hash':review['definition_hash'],'consent':True}).status_code==409

def test_genuine_eight_to_nine_preserves_recovery_and_backup_sqlite_postgres(tmp_path):
 from backend.migrations import schema_shape
 from recovery_fixture import command_contract
 from backend.schemas import TaskInput,Decision
 from backend.store import task_recoveries
 def check(settings):
  store=Store(settings);from backend.accounts import LocalAccounts;LocalAccounts(store)
  from backend.schemas import Enroll
  n=store.enroll(Enroll(enrollment_token=store.ticket()['enrollment_token'],name='v8-fixture',os_family='Windows',commands_enabled=True,agent_protocol=1))
  task=store.create_task(TaskInput(device_id=n['device_id'],session_id='v8-fixture',kind='command',code='print("checked")',**command_contract()))
  store.decision(task['id'],Decision(approve=True,plan_hash=task['plan_hash']))
  with store.engine.begin() as con:
   prior=list(con.execute(select(task_recoveries)).mappings())
   con.execute(text('DROP TABLE schedule_runs'));con.execute(text('DROP TABLE schedules'));con.execute(text('ALTER TABLE tasks DROP COLUMN schedule_run_id'));con.execute(text('UPDATE schema_version SET id=8'));schema_shape(con,revision=8)
  store.engine.dispose();source=tmp_path/('v8-'+('pg' if settings.database_url.startswith('postgresql') else 'sqlite')+'.json');report=backup(settings.database_url,source);assert report['schema']==8
  upgraded=Store(settings)
  with upgraded.tx() as con:
   assert list(con.execute(select(task_recoveries)).mappings())==prior
   assert con.scalar(select(devices.c.credential_hash)) and con.scalar(select(devices.c.commands_enabled))
  upgraded.engine.dispose()
 s=Settings('public-migration-schedule-fixture','sqlite:///'+(tmp_path/'v8.sqlite3').as_posix())
 check(s)
 from test_operations_recovery import postgres_database
 with postgres_database() as pg:check(pg)

def test_delegated_run_keeps_existing_five_per_session_limit(tmp_path):
 s,app,queued=fixture(tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);nodes=[node(c,app,'qa-limit-'+str(i)) for i in range(6)];r=enable(c,rule(c));run=prepare(c,r);c.post('/auth/logout');app.state.scheduler.execute(run['id'])
  jobs=[c.post('/agent/'+n['device_id']+'/poll',headers={'Authorization':'Bearer '+n['credential'],'X-PSLC-Agent-Protocol':'1'}).json()['job'] for n in nodes]
  assert sum(job is not None for job in jobs)==5 and jobs[-1] is None

def test_scheduled_diagnostic_analysis_after_logout(tmp_path):
 s,app,queued=fixture(tmp_path,httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'conclusion':'insufficient_evidence','hypotheses':[]})}}]})))
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app,diagnostics=True);r=enable(c,rule(c,{'mode':'errors'}));run=prepare(c,r);c.post('/auth/logout');app.state.scheduler.execute(run['id'])
  h={'Authorization':'Bearer '+pc['credential'],'X-PSLC-Agent-Protocol':'1'};job=c.post('/agent/'+pc['device_id']+'/poll',headers=h).json()['job']
  result=c.post('/agent/'+pc['device_id']+'/tasks/'+job['task_id']+'/result',headers=h,json={'lease_id':job['lease_id'],'outcome':'collected','exit_code':0,'diagnostics':{'scope_hash':job['payload']['scope_hash'],'records':[{'ref':'a'*64,'at':job['payload']['scope']['end_utc'],'channel':'Application','severity':'error','event_id':1,'record_id':1,'provider':'qa-fixture','message':'PRIVATE-SCHEDULE-LOG-MARKER'}],'coverage':[{'target':name,'status':'complete'} for name in ('Application','System')]}})
  assert result.status_code==200
  from backend.store import investigation_jobs
  with app.state.store.tx() as con:assert con.scalar(select(investigation_jobs.c.analysis_state).where(investigation_jobs.c.task_id==job['task_id']))=='ready'

def test_definition_metadata_tamper_and_shutdown_cannot_admit_new_work(tmp_path):
 s,app,queued=fixture(tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);pc=node(c,app);r=enable(c,rule(c));run=prepare(c,r)
  with app.state.store.tx() as con:con.execute(update(schedules).where(schedules.c.id==r['id']).values(calendar={**r['calendar'],'local_time':'08:00'}))
  app.state.scheduler.execute(run['id'])
  assert getrun(c,r,run)['state']=='access_revoked'
  # A second valid rule cannot cross the shutdown boundary after claim.
  second=enable(c,rule(c));job=prepare(c,second,'shutdown-boundary-fixture');app.state.scheduler.stop.set();app.state.scheduler.execute(job['id'])
  assert getrun(c,second,job)['state']=='stopped'
  assert c.post('/agent/'+pc['device_id']+'/poll',headers={'Authorization':'Bearer '+pc['credential'],'X-PSLC-Agent-Protocol':'1'}).json()['job'] is None
