import json,hashlib,time,threading
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from fastapi.testclient import TestClient
from backend.config import Settings,ROOT
from backend.local_inference import LocalModels
from backend.model_connection import ModelConnection,Selection,Candidate,Revision
from backend.secret_store import SecretStore
from backend.store import Problem
from backend.integrations import IntegrationError,Integrations
from backend.app import create_app

KEY='PUBLIC-CPU-PROFILE-KEY-938273'
ORIGIN='http://127.0.0.1:8787'
def fixture(tmp,transport=None,ready=False):
 content=b'GGUF-fixture-weights-only'*128
 rows=[]
 for n in (4,2):
  row={'id':f'qwen3.5-{n}b-q4','title':f'Qwen{n}B fixture','filename':f'Qwen{n}B.gguf','bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'repo':'fixture/models','revision':'a'*40,'license':'Apache-2.0'}
  row['url']='https://huggingface.co/'+row['repo']+'/resolve/'+row['revision']+'/'+row['filename'];rows.append(row)
 catalog=tmp/'catalog.json';catalog.write_text(json.dumps({'default':rows[0]['id'],'models':rows}))
 s=Settings('public-unused-local-test-token','sqlite:///'+(tmp/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,
  model_config_path=str(tmp/'secrets/config.enc'),local_models_enabled=True,local_models_dir=str(tmp/'models'),local_runtime_dir=str(tmp/'runtime'),local_catalog_path=str(catalog))
 if ready:
  Path(s.local_models_dir).mkdir()
  for r in rows:(Path(s.local_models_dir)/r['filename']).write_bytes(content)
 return s,content
def wait(m,mid):
 thread=m.jobs[mid]['thread'];thread.join(timeout=5);assert not thread.is_alive();return next(r for r in m.status()['models'] if r['id']==mid)

def test_download_hash_progress_ready_and_restart(tmp_path):
 s,content=fixture(tmp_path);calls=[]
 def handler(r):calls.append(r);return httpx.Response(200,content=content)
 m=LocalModels(s,httpx.MockTransport(handler));assert m.status()['models'][0]['state']=='missing'
 m.request_download(m.default);assert wait(m,m.default)['state']=='ready'
 assert m.path(m.default).read_bytes()==content
 assert LocalModels(s).verified_model(m.default) and len(calls)==1
 assert m.request_download(m.default)['status']=='ready'

@pytest.mark.parametrize('mode',['corrupt','oversize','redirect','range'])
def test_download_rejection_and_retry(tmp_path,mode):
 s,content=fixture(tmp_path);current=[mode]
 def handler(r):
  if current[0]=='redirect':return httpx.Response(302,headers={'location':'https://evil.invalid/model'})
  if current[0]=='range':return httpx.Response(206,content=content,headers={'content-range':'bytes 9-10/200'})
  return httpx.Response(200,content=content+b'x' if current[0]=='oversize' else b'x'*len(content) if current[0]=='corrupt' else content)
 m=LocalModels(s,httpx.MockTransport(handler))
 if mode=='range':
  m.root.mkdir();m.path(m.default).with_suffix('.gguf.partial').write_bytes(content[:5])
 m.request_download(m.default);assert wait(m,m.default)['state']=='failed';assert not m.verified_model(m.default)
 current[0]='ok';m.request_download(m.default);assert wait(m,m.default)['state']=='ready'

def test_interrupted_stream_resume_and_valid_content_range(tmp_path):
 s,content=fixture(tmp_path);state=[0];calls=[]
 class Broken(httpx.SyncByteStream):
  def __iter__(self):yield content[:10];raise httpx.ReadError('fixture transient error')
 def handler(r):
  calls.append(r)
  if state[0]==0:state[0]=1;return httpx.Response(200,stream=Broken())
  # A partial shorter than the downloader chunk may be buffered by HTTPX; handle either offset.
  start=int(r.headers.get('range','bytes=0-').split('=')[1].split('-')[0])
  return httpx.Response(206 if start else 200,content=content[start:],headers={'content-range':f'bytes {start}-{len(content)-1}/{len(content)}'})
 m=LocalModels(s,httpx.MockTransport(handler));m.request_download(m.default);assert wait(m,m.default)['state']=='failed'
 m.request_download(m.default);assert wait(m,m.default)['state']=='ready'

def test_cancel_is_not_ready_and_single_download(tmp_path):
 s,content=fixture(tmp_path);entered=threading.Event();release=threading.Event()
 class Slow(httpx.SyncByteStream):
  def __iter__(self):entered.set();release.wait(3);yield content
 m=LocalModels(s,httpx.MockTransport(lambda r:httpx.Response(200,stream=Slow())));m.request_download(m.default);assert entered.wait(2)
 with pytest.raises(Problem):m.request_download('qwen3.5-2b-q4')
 m.cancel_download(m.default);release.set();assert wait(m,m.default)['state']=='cancelled' and not m.verified_model(m.default)

def test_modified_model_and_extra_runtime_files_rejected(tmp_path):
 s,content=fixture(tmp_path,ready=True);m=LocalModels(s);assert m.verified_model(m.default)
 m.path(m.default).write_bytes(b'x'*len(content));assert not m.verified_model(m.default,force=True)
 with pytest.raises(IntegrationError) as e:m.ensure_running(m.default)
 assert str(e.value)=='local_model_missing'
 with pytest.raises(IntegrationError):m.runtime_executable()

def test_cpu_one_generation_bounded_queue_and_release(tmp_path,monkeypatch):
 s,_=fixture(tmp_path);m=LocalModels(s);monkeypatch.setattr(m,'ensure_running',lambda _:None)
 active=[0];peak=[0];guard=threading.Lock();release=threading.Event();entered=threading.Event()
 def run(_):
  with m.generation(m.default):
   with guard:active[0]+=1;peak[0]=max(peak[0],active[0]);entered.set()
   release.wait(3)
   with guard:active[0]-=1
 with ThreadPoolExecutor(max_workers=9) as pool:
  futures=[pool.submit(run,i) for i in range(9)];assert entered.wait(1)
  deadline=time.monotonic()+2
  while m.queued<8 and time.monotonic()<deadline:time.sleep(.01)
  with pytest.raises(IntegrationError) as e:
   with m.generation(m.default):pass
  assert str(e.value)=='local_model_busy'
  release.set()
  for f in futures:f.result(timeout=4)
 assert peak[0]==1 and m.queued==0

def test_v1_external_and_disabled_migrate_without_local_fallback(tmp_path):
 s,_=fixture(tmp_path,ready=True)
 SecretStore(s.model_config_path).write({'version':1,'enabled':True,'url':'https://webui.invalid/api','key':KEY,'model':'qwen-external','revision':'b'*32})
 m=ModelConnection(s);assert m.snapshot().model_backend=='external' and m.snapshot().model_token==KEY
 m.local.warmup=lambda _:None
 m.select(Selection(provider='local',model_id=m.local.default,revision=m.revision))
 assert m.snapshot().model_backend=='local' and m.profiles['legacy']['key']==KEY
 m.select(Selection(provider='openwebui',profile_id='legacy',revision=m.revision))
 assert m.snapshot().model_token==KEY
 m.select(Selection(provider='disabled',revision=m.revision))
 clone=ModelConnection(s);assert not clone.snapshot().model_url and clone.profiles['legacy']['key']==KEY
 assert KEY.encode() not in Path(s.model_config_path).read_bytes()

def test_fresh_local_default_corrupt_config_and_revision(tmp_path):
 s,_=fixture(tmp_path);m=ModelConnection(s);assert m.snapshot().model_backend=='local'
 i=Integrations(s);i.model_connection=m;assert i.status()['model']=='local_unavailable'
 with pytest.raises(Problem):m.select(Selection(provider='local',model_id=m.local.default,revision=m.revision))
 SecretStore(s.model_config_path).write({'bad':'format'})
 broken=ModelConnection(s);assert broken.error and not broken.snapshot().model_url and broken.local.desired is None

def test_admin_csrf_and_metadata_do_not_leak_local_runtime_key(tmp_path):
 s,_=fixture(tmp_path);app=create_app(s);c=TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN})
 assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
 r=c.get('/api/settings/providers');assert r.status_code==200 and app.state.model_connection.local.token not in r.text
 assert c.post('/api/settings/local-models/qwen3.5-4b-q4/download',headers={'Origin':'https://evil.invalid'}).status_code==403
 assert c.post('/api/users',json={'username':'staff','password':'public-password','roles':['operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False}).status_code==201
 c.post('/auth/logout');c.post('/auth/login',json={'username':'staff','password':'public-password'})
 for path in ('/api/settings/local-models/qwen3.5-4b-q4/download','/api/settings/local-models/qwen3.5-4b-q4/cancel','/api/settings/local-models/check'):
  assert c.post(path).status_code==403
 assert c.get('/api/settings/providers').status_code==403
 assert app.state.model_connection.local.jobs=={}

def test_saved_openwebui_profiles_switch_without_losing_keys(tmp_path):
 s,_=fixture(tmp_path)
 def provider(r):return httpx.Response(200,json={'data':[{'id':'qwen-fixture'},{'id':'gemma-fixture'}]})
 m=ModelConnection(s,httpx.MockTransport(provider))
 for mid in ('qwen-fixture','gemma-fixture'):
  data=Candidate(server_url='https://webui.invalid',api_key=KEY,model_id=mid,revision=m.revision);m.test(data);m.save(data)
 assert len(m.profiles)==2
 first=next(pid for pid,p in m.profiles.items() if p['model']=='qwen-fixture')
 m.select(Selection(provider='openwebui',profile_id=first,revision=m.revision));assert m.snapshot().model_id=='qwen-fixture'
 assert len(ModelConnection(s).profiles)==2
 assert KEY not in json.dumps(m.providers())

def test_local_model_client_enforces_token_limit_and_no_external_keys(tmp_path,monkeypatch):
 s,_=fixture(tmp_path);calls=[]
 def provider(r):
  calls.append(r);body=json.loads(r.content);assert body['max_tokens']==2048 and body['chat_template_kwargs']=={'enable_thinking':False};return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
 m=ModelConnection(s,httpx.MockTransport(provider));monkeypatch.setattr(m.local,'ensure_running',lambda _:None)
 i=Integrations(s,httpx.MockTransport(provider));i.model_connection=m;snap=m.snapshot()
 with i.model_client(snap) as c:
  from backend.integrations import bounded_json
  bounded_json(c,'POST',snap.model_url+'/chat/completions',headers={'Authorization':'Bearer '+snap.model_token},json={'model':snap.model_id,'messages':[],'max_tokens':6000})
 assert len(calls)==1

@pytest.mark.parametrize('same_birth',[False,True])
def test_stop_never_enumerates_reused_pid_children(tmp_path,monkeypatch,same_birth):
 s,_=fixture(tmp_path);m=LocalModels(s);seen=[]
 class Owned:
  pid=41001
  def poll(self):return None
  def terminate(self):seen.append('own-handle')
  def wait(self,timeout):return 0
 class Child:
  def terminate(self):seen.append('own-child')
 class Root:
  def create_time(self):return 100 if same_birth else 200
  def children(self,recursive):seen.append('enumerated');return [Child()]
 m.process=Owned();m.process_birth=100
 monkeypatch.setattr('backend.local_inference.psutil.Process',lambda pid:Root())
 monkeypatch.setattr('backend.local_inference.psutil.wait_procs',lambda children,timeout:(children,[]))
 m._stop()
 assert seen==(['enumerated','own-child','own-handle'] if same_birth else ['own-handle'])

def test_eight_long_model_keys_roundtrip_in_bounded_protected_store(tmp_path):
 s,_=fixture(tmp_path)
 ids=['large-profile-'+str(i) for i in range(8)]
 provider=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[{'id':mid} for mid in ids]}))
 m=ModelConnection(s,provider)
 for index,mid in enumerate(ids):
  key='PUBLIC-FIXTURE-'+str(index)+'K'*7800
  data=Candidate(server_url='https://webui.invalid',api_key=key,model_id=mid,revision=m.revision);m.test(data);m.save(data)
 reloaded=ModelConnection(s,provider);assert len(reloaded.profiles)==8
 assert all(p['key'].startswith('PUBLIC-FIXTURE-') and len(p['key'])>7800 for p in reloaded.profiles.values())
 assert reloaded.secret.path.stat().st_size<131072
 assert 'PUBLIC-FIXTURE-' not in json.dumps(reloaded.providers())
