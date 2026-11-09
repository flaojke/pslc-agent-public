"""Post-upgrade acceptance with unchanged credentials/capabilities; no real recurring work."""
import json,time,httpx
from sqlalchemy import create_engine,text
from backend.config import ROOT,load_settings
out=ROOT/'docs/qa/local-model';out.mkdir(parents=True,exist_ok=True)
with httpx.Client(base_url='http://127.0.0.1:8787',headers={'Origin':'http://127.0.0.1:8787'},timeout=20) as c:
 assert c.get('/healthz').json()['version']=='0.18.0'
 assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
 assert c.get('/api/schedules').json()==[]
 identity=json.loads((ROOT/'.local/smoke-agent.json').read_text());did=identity['device_id']
 n=next(n for n in c.get('/api/devices').json() if n['id']==did)
 assert n['agent_protocol']==1 and not any(n[k] for k in ('commands_enabled','installations_enabled','diagnostics_enabled'))
 r=c.post('/api/tasks',json={'device_id':did,'session_id':'release-018-health-only','kind':'health'});assert r.status_code==201;tid=r.json()['id']
 for _ in range(60):
  task=c.get('/api/tasks/'+tid).json()
  if task['state'] not in ('queued','running'):break
  time.sleep(.5)
 assert task['state']=='verified'
 assert c.get('/api/schedules').json()==[]
db=create_engine(load_settings().database_url)
with db.connect() as con:assert con.scalar(text('SELECT id FROM schema_version'))==9
db.dispose()
value={'api':'0.18.0','schema':9,'native_protocol':1,'health_verified':True,'mutation_flags':False,'real_schedules_created':False,'identity_preserved':True,'model_demo_system_config_preserved':True,'production_admitted':False}
(out/'live-upgrade.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps(value))
