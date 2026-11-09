"""End-to-end rollback only on isolated DB + disposable temporary config file."""
import json,hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select,text
from backend.config import Settings
from backend.app import create_app
from backend.store import task_recoveries,tasks,devices
from backend.operations import backup
from agent.executor import execute
ORIGIN='http://127.0.0.1:8787'
def fixture(tmp):
 s=Settings('public-manual-rollback-fixture-token','sqlite:///'+(tmp/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,system_preferences_path=str(tmp/'settings/preferences.json'))
 app=create_app(s);c=TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN})
 return s,app,c
def login(c,name='admin',password='admin'):assert c.post('/auth/login',json={'username':name,'password':password}).status_code==200
def node(c,protocol=1):
 ticket=c.post('/api/enrollment-tokens').json()
 r=c.post('/agent/enroll',json={'enrollment_token':ticket['enrollment_token'],'name':'temporary-qa-node','os_family':'Windows','commands_enabled':True,'agent_protocol':protocol});assert r.status_code==200,r.text;return r.json()
def command(c,n,file,after='after',session='qa'):
 payload={'device_id':n['device_id'],'session_id':session,'kind':'command','runtime':'python','code':'from pathlib import Path;Path('+repr(str(file))+').write_text('+repr(after)+',encoding="utf-8");print("RAW-ROLLBACK-COMMAND-MARKER")','verification':{'runtime':'python','code':'from pathlib import Path;print("ok" if Path('+repr(str(file))+').read_text(encoding="utf-8")=='+repr(after)+' else "bad")','expected_output':'ok'},'recovery':{'resources':[{'type':'configuration_file','path':str(file)}]}}
 return c.post('/api/tasks',json=payload)
def confirm(c,t,group=False,approve=True):
 path='/api/workflows/' if group else '/api/tasks/'
 r=c.post(path+t['id']+'/decision',json={'approve':approve,'plan_hash':t['plan_hash']});assert r.status_code==200,r.text;return r.json()
def run(c,n,tmp,protocol=1):
 headers={'Authorization':'Bearer '+n['credential'],'X-PSLC-Agent-Protocol':str(protocol)}
 r=c.post('/agent/'+n['device_id']+'/poll',headers=headers);assert r.status_code==200,r.text;job=r.json()['job']
 if not job:return None
 state=tmp/'identity/state.json';work=state.parent/'agent-work';work.mkdir(parents=True,exist_ok=True)
 result=execute(job['payload'],True,str(work),task_context={'device_id':n['device_id'],'task_id':job['task_id'],'plan_hash':job['plan_hash'],'state_path':str(state)})
 result['lease_id']=job['lease_id'];response=c.post('/agent/'+n['device_id']+'/tasks/'+job['task_id']+'/result',headers=headers,json=result)
 assert response.status_code==200,response.text
 return job,response.json()
def test_manual_rollback_full_cycle_confirmation_private_metadata_restart(tmp_path):
 s,app,c=fixture(tmp_path);config=tmp_path/'configuration';config.mkdir();file=config/'app.json';file.write_text('before',encoding='utf-8')
 with c:
  login(c);n=node(c);r=command(c,n,file);assert r.status_code==201,r.text;t=r.json();assert t['plan']['anti_program']['op']=='restore-baseline-v1'
  with app.state.store.tx() as con:assert not con.execute(select(task_recoveries)).first()
  confirm(c,t);job,result=run(c,n,tmp_path);assert result['state']=='verified',result
  detail=c.get('/api/tasks/'+t['id']).json();assert detail['recovery']['status']=='ready' and file.read_text()=='after'
  raw=tmp_path/'metadata.json';backup(s.database_url,raw);assert b'RAW-ROLLBACK-COMMAND-MARKER' not in raw.read_bytes() and str(file).encode() not in raw.read_bytes()
  # Other employee cannot prepare/inspect another owner's rollback.
  user=c.post('/api/users',json={'username':'other','password':'public-other-fixture','roles':['operator','auditor'],'device_ids':['*']});assert user.status_code==201
  with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as foreign:
   login(foreign,'other','public-other-fixture')
   assert foreign.post('/api/tasks/'+t['id']+'/rollback-plans',json={'request_id':'foreign-request','session_id':'qa'}).status_code==404
  request={'request_id':'manual-undo-request','session_id':'qa'}
  r=c.post('/api/tasks/'+t['id']+'/rollback-plans',json=request);assert r.status_code==200,r.text;undo=r.json()
  assert file.read_text()=='after' and c.post('/api/tasks/'+t['id']+'/rollback-plans',json=request).json()['id']==undo['id']
  assert c.post('/api/workflows/'+undo['id']+'/decision',json={'approve':True,'plan_hash':'0'*64}).status_code==409
  confirm(c,undo,True);job,result=run(c,n,tmp_path);assert result['state']=='restored',result
  assert file.read_text()=='before'
  detail=c.get('/api/tasks/'+t['id']).json();assert detail['state']=='verified' and detail['recovery']['historical'] and detail['recovery']['status']=='restored'
  assert c.post('/agent/'+n['device_id']+'/tasks/'+job['task_id']+'/result',headers={'Authorization':'Bearer '+n['credential']},json={'lease_id':job['lease_id'],'outcome':'restored'}).status_code==200
  assert c.get('/api/overview').json()['statistics']['active_fleet']==0
 # Restart cannot replay forward. A new, separately approved restore remains an idempotent no-op.
 restarted=create_app(s)
 with TestClient(restarted,base_url=ORIGIN,headers={'Origin':ORIGIN}) as fresh:
  login(fresh);assert run(fresh,n,tmp_path) is None
  r=fresh.post('/api/tasks/'+t['id']+'/rollback-plans',json={'request_id':'after-restart-undo','session_id':'qa'});assert r.status_code==200,r.text
  confirm(fresh,r.json(),True);assert run(fresh,n,tmp_path)[1]['state']=='restored'

def test_reject_unknown_drift_protocol_and_no_contract_fail_closed(tmp_path):
 s,app,c=fixture(tmp_path);config=tmp_path/'configuration';config.mkdir();file=config/'app.json';file.write_text('before',encoding='utf-8')
 with c:
  login(c);legacy=node(c,0)
  assert command(c,legacy,file).json()['error']=='agent_upgrade_required'
  assert c.post('/api/tasks',json={'device_id':legacy['device_id'],'session_id':'qa','kind':'command','code':'print(1)'}).status_code==422
  n=node(c);t=command(c,n,file).json();confirm(c,t);run(c,n,tmp_path)
  u=c.post('/api/tasks/'+t['id']+'/rollback-plans',json={'request_id':'reject-undo-request','session_id':'qa'}).json();confirm(c,u,True,False);confirm(c,u,True,False)
  assert run(c,n,tmp_path) is None and file.read_text()=='after'
  # Reconciliation is required before rollback of an uncertain running task.
  with app.state.store.tx() as con:con.execute(tasks.update().where(tasks.c.id==t['id']).values(state='outcome_unknown'))
  r=c.post('/api/tasks/'+t['id']+'/rollback-plans',json={'request_id':'unknown-undo-request','session_id':'qa'});assert r.json()['error']=='rollback_reconciliation_required'
  assert c.post('/api/tasks/'+t['id']+'/reconcile',json={'outcome':'failed','externally_checked_not_running':True}).status_code==200
  file.write_text('independent admin change',encoding='utf-8')
  u=c.post('/api/tasks/'+t['id']+'/rollback-plans',json={'request_id':'drift-undo-request','session_id':'qa'}).json();confirm(c,u,True)
  assert run(c,n,tmp_path)[1]['state']=='recovery_conflict' and file.read_text()=='independent admin change'

def test_group_rollback_runs_in_reverse_order_per_node_and_explains_readonly(tmp_path,monkeypatch):
 from backend.schemas import WorkflowRequest
 from backend.planner import ActionPlan
 s,app,c=fixture(tmp_path);config=tmp_path/'configuration';config.mkdir();file=config/'app.json';file.write_text('before',encoding='utf-8')
 with c:
  login(c);n=node(c)
  def step(value):
   return {'kind':'command','device_id':n['device_id'],'runtime':'python','code':'from pathlib import Path;Path('+repr(str(file))+').write_text('+repr(value)+',encoding="utf-8")','verification':{'runtime':'python','code':'print("ok")','expected_output':'ok'},'recovery':{'resources':[{'type':'configuration_file','path':str(file)}]}}
  class Planner:
   def propose(self,*args):return ActionPlan.model_validate({'status':'plan','steps':[step('one'),step('two'),{'kind':'health','device_id':n['device_id']}]}),'fixture'
  chat=c.post('/api/conversations',json={'title':'QA historical evidence'}).json();c.put('/api/conversations/'+chat['id'],json={'title':'QA historical evidence','scope':{'kind':'all','ids':[]}})
  w=app.state.store.workflow.request(WorkflowRequest(conversation_id=chat['id'],request_id='group-original-fixture',session_id='qa',question='Explicit reversible fixture',target_ids=[n['device_id']]),Planner())
  confirm(c,w,True)
  assert run(c,n,tmp_path)[1]['state']=='verified' and file.read_text()=='one'
  assert run(c,n,tmp_path)[1]['state']=='verified' and file.read_text()=='two'
  assert run(c,n,tmp_path)[1]['state']=='verified'
  response=c.post('/api/workflows/'+w['id']+'/rollback-plans',json={'request_id':'group-reverse-fixture','session_id':'qa'});assert response.status_code==200,response.text
  undo=response.json();assert undo['preview']['read_only_steps_without_changes']==1
  assert [x['payload']['original_task_id'] for x in undo['preview']['steps']]==[w['steps'][1]['task_id'],w['steps'][0]['task_id']]
  confirm(c,undo,True)
  assert run(c,n,tmp_path)[1]['state']=='restored' and file.read_text()=='one'
  assert run(c,n,tmp_path)[1]['state']=='restored' and file.read_text()=='before'
  assert c.get('/api/workflows/'+undo['id']).json()['state']=='verified'
  contexts=[]
  def ask(messages,context):contexts.append(context);return {'status':'ok','answer':'Synthetic metadata-only context verification'}
  monkeypatch.setattr(app.state.conversations.integrations,'ask_messages',ask)
  c.post('/api/conversations/'+chat['id']+'/messages',json={'request_id':'historical-context-question','question':'Какое состояние после отката?'})
  import time
  for _ in range(200):
   if contexts:break
   time.sleep(.01)
  assert contexts and contexts[0]['latest_plan']['historical_steps']==2 and contexts[0]['latest_plan']['verified_steps']==1
  assert sum(o['historical'] for o in contexts[0]['observations'])==2

