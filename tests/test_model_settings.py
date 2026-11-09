import json,threading
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.app import create_app
from backend.model_connection import ModelConnection,Candidate,Revision
from backend.store import Problem
from backend.operations import backup
from backend.secret_store import SecretStore

KEY='PUBLIC-MODEL-SETTINGS-FIXTURE-KEY-28295'
RAW='UNTRUSTED-UPSTREAM-RESPONSE-MARKER-9284'
ORIGIN='http://127.0.0.1:8787'
def settings(tmp):
 return Settings('public-unused-model-settings-token','sqlite:///'+(tmp/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,model_config_path=str(tmp/'secrets/connection.enc'))
def provider(calls,mode='ok'):
 def handler(r):
  calls.append(r);assert r.headers['authorization']=='Bearer '+KEY
  if mode=='auth':return httpx.Response(401,text=KEY+RAW)
  if mode=='redirect':return httpx.Response(302,headers={'Location':'https://evil.invalid/steal'})
  if mode=='timeout':raise httpx.ReadTimeout('SECRET '+KEY)
  if mode=='huge':return httpx.Response(200,content=b'x'*524289)
  if mode=='invalid':return httpx.Response(200,text=RAW)
  if r.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':'qwen-fixture'},{'id':'gemma-fixture'},{'id':KEY},{'id':'<script>'+RAW}]})
  assert r.url.path=='/api/chat/completions'
  body=json.loads(r.content);assert body['tools']==[] and body['tool_ids']==[] and KEY not in r.content.decode()
  content=RAW if mode=='bad-json' else '{"status":"ok"}'
  return httpx.Response(200,json={'choices':[{'message':{'content':content}}]})
 return httpx.MockTransport(handler)
def candidate(m,**kw):
 return Candidate(server_url='https://openwebui.invalid',api_key=KEY,model_id='qwen-fixture',revision=m.view()['revision'],**kw)
def api_fixture(tmp,mode='ok'):
 calls=[];s=settings(tmp);app=create_app(s,provider(calls,mode));c=TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN});assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
 return s,app,c,calls

def test_admin_config_restart_rotation_disable_and_metadata_backup(tmp_path):
 s,app,c,calls=api_fixture(tmp_path)
 with c:
  rev=c.get('/api/settings/model').json()['revision']
  body={'server_url':'https://openwebui.invalid','api_key':KEY,'model_id':'qwen-fixture','revision':rev}
  assert c.post('/api/settings/model/models',json=body).json()['model_ids']==['gemma-fixture','qwen-fixture']
  result=c.post('/api/settings/model/check',json=body).json();assert result['status']=='json_probe_passed' and not result['real_task_quality_verified']
  r=c.put('/api/settings/model',json=body);assert r.status_code==200 and KEY not in r.text
  view=r.json();assert view['has_key'] and view['server_url']=='https://openwebui.invalid/api'
  assert c.put('/api/settings/model',json=body).status_code==409
  m=app.state.model_connection;assert m.snapshot().model_token==KEY
  assert m.secret.path.read_bytes().find(KEY.encode())<0
  copy=ModelConnection(s,provider([]));assert copy.snapshot().model_token==KEY and copy.view()['last_check'] is None
  blank={**body,'api_key':'','revision':view['revision']}
  assert c.post('/api/settings/model/models',json=blank).status_code==200
  count=len(calls)
  assert c.post('/api/settings/model/models',json={**blank,'server_url':'https://other.invalid'}).status_code==422 and len(calls)==count
  assert c.request('DELETE','/api/settings/model',json={'revision':view['revision']}).status_code==200
  assert not ModelConnection(replace(s,model_url='https://env.invalid/api',model_id='env',model_token=KEY)).snapshot().model_url
  assert KEY.encode() not in (tmp_path/'qa.sqlite3').read_bytes()
 app.state.store.engine.dispose();backup(s.database_url,tmp_path/'backup.json');assert KEY.encode() not in (tmp_path/'backup.json').read_bytes()

@pytest.mark.parametrize('mode,code',[('auth','openwebui_auth_failed'),('redirect','openwebui_redirect_rejected'),('timeout','openwebui_timeout'),('huge','openwebui_response_too_large'),('invalid','openwebui_invalid_response')])
def test_fixed_errors_preserve_config_no_secret_echo(tmp_path,mode,code):
 _,app,c,calls=api_fixture(tmp_path,mode)
 with c:
  view=c.get('/api/settings/model').json();body=candidate(app.state.model_connection).model_dump()
  r=c.post('/api/settings/model/check',json=body)
  assert r.json()['error']==code and KEY not in r.text and RAW not in r.text
  assert c.get('/api/settings/model').json()==view and len(calls)==1

def test_role_and_csrf_do_not_call_provider(tmp_path):
 _,app,c,calls=api_fixture(tmp_path)
 with c:
  assert c.post('/api/users',json={'username':'operator','password':'public-fixture-password','roles':['operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False}).status_code==201
  body=candidate(app.state.model_connection).model_dump()
  assert c.post('/api/settings/model/check',json=body,headers={'Origin':'https://evil.invalid'}).status_code==403
  c.post('/auth/logout');c.post('/auth/login',json={'username':'operator','password':'public-fixture-password'})
  for method,path in [('GET','/api/settings/model'),('POST','/api/settings/model/models'),('POST','/api/settings/model/check'),('PUT','/api/settings/model')]:
   assert c.request(method,path,json=body if method!='GET' else None).status_code==403
  assert calls==[]

def test_corrupt_secret_fails_closed_and_write_failure_preserves_active(tmp_path,monkeypatch):
 s=settings(tmp_path);m=ModelConnection(s,provider([]));body=candidate(m);m.test(body);m.save(body)
 m.secret.path.write_bytes(b'corrupt')
 broken=ModelConnection(replace(s,model_url='https://env.invalid/api',model_id='env',model_token=KEY))
 assert not broken.snapshot().model_url and broken.view()['status']=='configuration_unavailable'
 body=candidate(m);m.test(body)
 from backend.secret_store import SecretUnavailable
 def fail(*a):raise SecretUnavailable()
 monkeypatch.setattr(m.secret,'write',fail)
 with pytest.raises(Problem) as err:m.disable(Revision(revision=body.revision))
 assert err.value.code=='model_secret_unavailable' and m.snapshot().model_token==KEY

@pytest.mark.parametrize('url',['http://10.0.0.1','https://u:password@example.invalid','https://x.invalid/api?key=secret','https://x.invalid/#secret','https://x.invalid/v1','https://x.invalid:invalid','file:///etc/passwd'])
def test_invalid_urls_do_not_send_key(tmp_path,url):
 calls=[];m=ModelConnection(settings(tmp_path),provider(calls));body=candidate(m).model_copy(update={'server_url':url})
 with pytest.raises(Problem):m.test(body)
 assert not calls

def test_bad_json_not_falsely_verified_and_unlisted_selection_blocked(tmp_path):
 m=ModelConnection(settings(tmp_path),provider([],mode='bad-json'));body=candidate(m)
 assert m.test(body,probe=True)['status']=='json_contract_failed'
 with pytest.raises(Problem):m.save(body.model_copy(update={'model_id':'invented'}))
 assert m.view()['status']=='not_configured'

def test_concurrent_save_and_revocation_after_probe(tmp_path):
 calls=[];m=ModelConnection(settings(tmp_path),provider(calls));body=candidate(m);m.test(body)
 def save(_):
  try:m.save(body);return 'saved'
  except Problem as e:return e.code
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,range(2)))
 assert sorted(results)==['model_configuration_changed','saved']
 body=candidate(m);counter=[0]
 def guard():
  counter[0]+=1
  if counter[0]>1:raise Problem('operator_forbidden',403)
 with pytest.raises(Problem):m.test(body,authorize=guard)
 assert m.catalogs=={}

def test_runtime_chat_and_planner_use_new_snapshot_without_restart(tmp_path):
 _,app,c,calls=api_fixture(tmp_path)
 with c:
  body=candidate(app.state.model_connection).model_dump()
  c.post('/api/settings/model/models',json=body);assert c.put('/api/settings/model',json=body).status_code==200
  assert c.get('/api/overview').json()['integrations']['model']=='configured_unverified'
  r=c.post('/api/chat',json={'question':'Read-only connectivity check'});assert r.status_code==200
  assert calls[-1].url.path=='/api/chat/completions' and json.loads(calls[-1].content)['model']=='qwen-fixture'
  previous=len(calls)
  r=c.post('/api/agent/requests',json={'request_id':'model-fixture-request-001','question':'Fixture plan request','session_id':'qa-session'})
  assert r.status_code==422 and r.json()['error']=='invalid_model_plan' and len(calls)==previous+1 and calls[-1].url.path=='/api/chat/completions'


def test_key_rotation_and_expired_catalog(tmp_path,monkeypatch):
 m=ModelConnection(settings(tmp_path),provider([]));body=candidate(m);m.test(body);m.save(body)
 newkey='PUBLIC-ROTATED-KEY-MARKER-39382'
 def rotated(r):
  assert r.headers['authorization']=='Bearer '+newkey
  return httpx.Response(200,json={'data':[{'id':'qwen-fixture'}]})
 m.transport=httpx.MockTransport(rotated)
 body=candidate(m).model_copy(update={'api_key':newkey})
 m.test(body)
 with monkeypatch.context() as patch:
  patch.setattr('backend.model_connection.time',lambda:10**12)
  with pytest.raises(Problem) as err:m.save(body)
  assert err.value.code=='reload_model_list'
 m.save(body)
 assert ModelConnection(settings(tmp_path)).snapshot().model_token==newkey
 assert KEY.encode() not in m.secret.path.read_bytes() and newkey.encode() not in m.secret.path.read_bytes()
