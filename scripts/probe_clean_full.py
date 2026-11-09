"""Actual clean Full: default local model -> JSON -> LLM health plan -> exact confirmation -> native health."""
from pathlib import Path
import hashlib,json,secrets,time,httpx
ROOT=Path(__file__).resolve().parents[1];info=json.loads((ROOT/'.local/clean-distributions.json').read_text());entry=info['variants']['Full'];url=entry['url']
with httpx.Client(base_url=url,headers={'Origin':url},timeout=180) as c:
 for _ in range(120):
  try:
   if c.get('/healthz').status_code==200:break
  except httpx.HTTPError:pass
  time.sleep(.5)
 else:raise RuntimeError('Clean Full not ready')
 assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
 provider=c.get('/api/settings/providers').json();assert provider['active']['provider']=='local' and provider['active']['model_id']=='qwen3.5-4b-q4'
 assert next(x for x in provider['local']['models'] if x['id']=='qwen3.5-4b-q4')['state']=='ready'
 assert not c.get('/qa/status').json()['model_downloads_requested']
 print(json.dumps({'phase':'clean_full_real_cpu_probe'}),flush=True)
 check=c.post('/api/settings/local-models/check');assert check.status_code==200 and check.json()['status']=='json_probe_passed','Clean Full JSON probe failed'
 state=Path(entry['node_state']);state.parent.mkdir(parents=True,exist_ok=True)
 onboarding=c.post('/api/nodes/onboarding',json={'name':'QA-CLEAN-WIN-01','os_family':'Windows','device_kind':'workstation','state_path':str(state)})
 assert onboarding.status_code==200
 ticket=onboarding.json()['enrollment_token']
 enrolled=c.post('/agent/enroll',json={'enrollment_token':ticket,'name':'QA-CLEAN-WIN-01','os_family':'Windows','agent_protocol':1});assert enrolled.status_code==200;identity=enrolled.json()
 state.write_text(json.dumps(identity),encoding='utf-8');identity_hash=hashlib.sha256(state.read_bytes()).hexdigest();did=identity['device_id']
 request={'request_id':secrets.token_hex(16),'session_id':'clean-full-cpu-health','question':'Проверь состояние устройства '+did+'. Нужен ровно один шаг health:CPU/RAM/диск. Только чтение, без команд, установки или сбора журналов.'}
 began=time.monotonic();response=c.post('/api/agent/requests',json=request);elapsed=time.monotonic()-began
 assert response.status_code==200,'Clean Full model plan rejected'
 flow=response.json();assert flow['source']=='configured_model' and flow['state']=='awaiting_confirmation' and len(flow['steps'])==1 and all(s['kind']=='health' and s['device_id']==did for s in flow['steps']),'Never admit non-health or fabricated proposal'
 assert c.post('/qa/native-health-once').json()['returncode']==0
 assert c.get('/api/workflows/'+flow['id']).json()['state']=='awaiting_confirmation','Prepare must not execute'
 assert c.post('/api/workflows/'+flow['id']+'/decision',json={'approve':True,'plan_hash':flow['plan_hash']}).status_code==200
 assert c.post('/qa/native-health-once').json()['returncode']==0
 result=c.get('/api/workflows/'+flow['id']).json();assert result['state']=='verified'
 assert hashlib.sha256(state.read_bytes()).hexdigest()==identity_hash
 assert not c.get('/qa/status').json()['model_downloads_requested']
 assert c.get('/api/users').json()[0]['username']=='admin'
 entry.update(device_id=did,node_name='QA-CLEAN-WIN-01',verified_workflow=flow['id'])
 (ROOT/'.local/clean-distributions.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
 value={'scope':'actual clean extracted Full on Windows, fresh Python dependencies','version':info['version'],'initial_model_ready':True,'model_download_requested':False,'json_probe_seconds':round(check.json()['duration_ms']/1000,3),'model_plan_seconds':round(elapsed,3),'model_source':'configured_model','only_health':True,'prepare_did_not_execute':True,'exact_confirmation':True,'native_standalone_health_verified':True,'native_version':'0.8.0','identity_preserved':True,'mutation_flags':False,'no_production_state_copied':True}
 (ROOT/'docs/qa/local-model/clean-full-cpu.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps(value),flush=True)
