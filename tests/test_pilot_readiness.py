from recovery_fixture import command_contract
import json,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from dataclasses import replace
import pytest
from sqlalchemy import select,update,func
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings
from backend.readiness import report,PilotPlan
from backend.store import Store,tasks,events
from backend.schemas import Enroll,TaskInput,Result
from scripts.run_evals import fingerprint
from test_operator_identity import api_fixture,auth_header,TOKEN
MARKER='RAW-PILOT-SECRET-MARKER-435678'

def valid_evals(tmp_path):
 row=lambda sid:{'state':'passed','exit_code':0,'counts':{'passed':1,'failed':0,'skipped':0},'external_acceptance':'pending','cases':[{'name':'tests/test_fixture.py::test_'+sid,'status':'passed','duration':0.1}]}
 value={'format':'pslc-evals-v1','source_fingerprint':fingerprint(),'source_unchanged':True,'created':time.time(),'external_acceptance':False,'scenarios':{'A0'+str(i):row('A0'+str(i)) for i in range(1,9)}}
 p=tmp_path/'evals.json';p.write_text(json.dumps(value),encoding='utf-8');return p,value

def local(tmp_path):
 path,value=valid_evals(tmp_path);s=Settings(TOKEN,'sqlite:///'+(tmp_path/'source.sqlite3').as_posix(),readiness_eval_path=str(path));store=Store(s);return s,store,path,value

@pytest.mark.parametrize('case',['stale','fingerprint','partial','missing','external-claim','corrupt'])
def test_preflight_rejects_stale_or_incomplete_evidence(tmp_path,case):
 s,store,path,value=local(tmp_path)
 if case=='stale':value['created']=time.time()-90000
 elif case=='fingerprint':value['source_fingerprint']='0'*64
 elif case=='partial':value['scenarios']['A01']['counts']['skipped']=1
 elif case=='missing':del value['scenarios']['A08']
 elif case=='external-claim':value['external_acceptance']=True
 if case=='corrupt':path.write_text('bad')
 else:path.write_text(json.dumps(value))
 r=report(s,store);assert r['code_ready'] is False and r['production_admitted'] is False and r['evals']['reason']=='rerun_current_evals'

def test_green_local_checks_or_claimed_shadow_never_promote(tmp_path):
 s,store,path,value=local(tmp_path);now=datetime.now(timezone.utc)
 pilot={'schema_version':1,'targets':[{'id':'node-'+str(i),'os_family':'Windows' if i<3 else 'Linux','environment':'test'} for i in range(5)],'shadow_start_utc':(now-timedelta(days=15)).isoformat(),'shadow_end_utc':now.isoformat(),'shadow_evidence_reference':'TICKET-123','it_approval_reference':'IT-1','security_approval_reference':'IB-1'}
 plan=tmp_path/'plan.json';plan.write_text(json.dumps(pilot));s=replace(s,pilot_plan_path=str(plan),model_url='https://'+MARKER+'.invalid/v1',model_id='fixture')
 r=report(s,store);assert r['code_ready'] and r['shadow_duration_reported_sufficient'] and not r['shadow_evidence_verified'] and not r['production_admitted'] and not r['autonomy_promotion']
 assert r['pilot_readiness']=='requires_external_acceptance' and MARKER not in json.dumps(r)
 assert next(g for g in r['external_gates'] if g['id']=='security')['state']=='declared_unverified'
 pilot['shadow_start_utc']=(now-timedelta(days=1)).isoformat();plan.write_text(json.dumps(pilot));assert not report(s,store)['shadow_duration_reported_sufficient']

@pytest.mark.parametrize('case',['duplicates','too-few','one-os','future','naive','secret-field'])
def test_strict_pilot_declaration_rejected(tmp_path,case):
 now=datetime.now(timezone.utc);value={'schema_version':1,'targets':[{'id':'n'+str(i),'os_family':'Windows' if i<3 else 'Linux','environment':'test'} for i in range(5)]}
 if case=='duplicates':value['targets'][1]['id']='n0'
 elif case=='too-few':value['targets']=value['targets'][:2]
 elif case=='one-os':
  for t in value['targets']:t['os_family']='Windows'
 elif case=='future':value.update(shadow_start_utc=now.isoformat(),shadow_end_utc=(now+timedelta(days=15)).isoformat())
 elif case=='naive':value.update(shadow_start_utc='2026-10-01T00:00:00',shadow_end_utc='2026-10-16T00:00:00')
 else:value['token']=MARKER
 with pytest.raises(ValueError):PilotPlan.model_validate(value,strict=True)

def test_preflight_cli_style_does_not_migrate_or_start_tasks(tmp_path):
 s,store,path,value=local(tmp_path)
 with store.tx() as c:c.execute(__import__('sqlalchemy').text('UPDATE schema_version SET id=4'))
 store.engine.dispose();r=report(s);assert r['code_ready'] is False
 from sqlalchemy import create_engine,text
 db=create_engine(s.database_url)
 with db.connect() as c:assert c.scalar(text('SELECT id FROM schema_version'))==4
 db.dispose()

def test_readiness_admin_scope_and_feedback_owner_retention(tmp_path):
 fake,settings,app,node=api_fixture(tmp_path);alice=auth_header(fake);bob=auth_header(fake,'bob');admin=auth_header(fake,'admin',['Admins']);auditor=auth_header(fake,'auditor',['IB'])
 with TestClient(app) as c:
  assert c.get('/api/readiness',headers=alice).status_code==403
  assert c.get('/api/readiness',headers=admin).status_code==200
  assert c.get('/api/operations/status',headers=alice).status_code==403
  assert c.get('/api/operations/status',headers=auditor).status_code==403
  task=c.post('/api/tasks',headers=alice,json={**command_contract(),'device_id':node['device_id'],'session_id':'feedback','kind':'command','code':"print('"+MARKER+"')"}).json()
  url='/api/tasks/'+task['id']+'/improvement'
  assert c.get(url,headers=alice).status_code==409
  assert c.post('/api/tasks/'+task['id']+'/decision',headers=alice,json={'approve':True,'plan_hash':task['plan_hash']}).status_code==200
  job=app.state.store.poll(node['device_id'],node['credential'],agent_protocol=1)
  app.state.store.finish(node['device_id'],node['credential'],task['id'],Result(lease_id=job['lease_id'],outcome='process_succeeded',exit_code=0,duration_ms=1,output=MARKER))
  before=len(c.get('/api/events',headers=alice).json());r=c.get(url,headers=alice)
  assert r.status_code==200 and r.json()['review_required'] and not r.json()['repository_changed'] and not r.json()['remote_execution']
  assert MARKER not in r.text and task['id'] in r.json()['markdown'] and 'не доказанную' in r.text
  assert len(c.get('/api/events',headers=alice).json())==before
  assert c.get(url,headers=bob).status_code==404 and c.get(url,headers=admin).status_code==404 and c.get(url,headers=auditor).status_code==403
