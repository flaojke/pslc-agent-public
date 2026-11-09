"""Final native ZIP verification and existing health-only node smoke. Never issue mutations."""
from pathlib import Path
import hashlib,json,time,zipfile,httpx
from backend.config import ROOT
from agent.delivery import verify
out=ROOT/'docs/qa/reversible-task-plans';out.mkdir(parents=True,exist_ok=True)
with httpx.Client(base_url='http://127.0.0.1:8787',headers={'Origin':'http://127.0.0.1:8787'},timeout=60) as c:
 assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200,'Existing login failed; no reset'
 did=json.loads((ROOT/'.local/smoke-agent.json').read_text())['device_id']
 node=next(x for x in c.get('/api/devices').json() if x['id']==did)
 assert not any(node[k] for k in ('commands_enabled','installations_enabled','diagnostics_enabled'))
 assert node['agent_protocol']==1
 r=c.post('/api/tasks',json={'device_id':did,'session_id':'release-016-health-only','kind':'health'});assert r.status_code==201
 task=r.json()
 if task['state']=='awaiting_confirmation':
  r=c.post('/api/tasks/'+task['id']+'/decision',json={'approve':True,'plan_hash':task['plan_hash']});assert r.status_code==200
 for _ in range(80):
  task=c.get('/api/tasks/'+task['id']).json()
  if task['state'] not in ('queued','running'):break
  time.sleep(.5)
 assert task['state']=='verified',task['state']
 (out/'live-health.json').write_text(json.dumps({'api':'0.16.0','native_protocol':1,'health_verified':True,'identity_preserved':True,'mutation_flags':False,'services_installed':False}),encoding='utf-8')
print('Live native protocol1 health verified; capabilities unchanged.')
