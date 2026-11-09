"""Pinned CPU models, explicit downloads and one owned bounded inference process."""
from contextlib import contextmanager
from pathlib import Path
from threading import RLock,Lock,Event,Thread,BoundedSemaphore
import hashlib,json,os,platform,re,secrets,shutil,socket,subprocess,sys,time
from urllib.parse import urlsplit,urljoin
import httpx,psutil
from .config import ROOT
from .store import Problem
from .integrations import IntegrationError

class Cancelled(Exception):pass
def hash_file(path,cancel=None):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  while True:
   if cancel and cancel.is_set():raise Cancelled()
   b=f.read(4*1024*1024)
   if not b:break
   h.update(b)
 return h.hexdigest()
def inside(root,path):
 p=Path(path)
 if p.is_symlink() or not p.resolve().is_relative_to(Path(root).resolve()):raise Problem('unsafe_model_path',422)
 return p
class LocalModels:
 def __init__(self,settings,transport=None):
  self.s=settings;self.enabled=settings.local_models_enabled;self.transport=transport
  self.root=Path(settings.local_models_dir or ROOT/'models');self.runtime_root=Path(settings.local_runtime_dir or ROOT/'runtime')
  self.lock=RLock();self.generation_lock=Lock();self.queue=BoundedSemaphore(9);self.queued=0;self.jobs={};self.verified={};self.closed=Event()
  self.process=None;self.process_birth=None;self.running_id=None;self.runtime_state='stopped';self.runtime_error=None;self.desired=None
  self.token=secrets.token_urlsafe(32)
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
  self.url='http://127.0.0.1:'+str(self.port)+'/v1'
  data=json.loads(Path(settings.local_catalog_path or ROOT/'config/local-models.json').read_text(encoding='utf-8'))
  self.default=data['default'];self.catalog={}
  for row in data['models']:
   if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',row['id']) or Path(row['filename']).name!=row['filename'] or not row['filename'].endswith('.gguf') or not re.fullmatch('[a-f0-9]{64}',row['sha256']) or not 0<row['bytes']<=8*1024**3:raise ValueError('invalid_local_catalog')
   expected='https://huggingface.co/'+row['repo']+'/resolve/'+row['revision']+'/'+row['filename']
   if row['url']!=expected or not re.fullmatch('[a-f0-9]{40}',row['revision']):raise ValueError('invalid_local_catalog')
   self.catalog[row['id']]=row
  if self.default not in self.catalog:raise ValueError('invalid_local_catalog')
  self.runtime_manifest=json.loads(Path(settings.local_runtime_manifest_path or ROOT/'config/local-runtime.json').read_text(encoding='utf-8'))
 def entry(self,mid):
  if mid not in self.catalog:raise Problem('unknown_local_model',404)
  return self.catalog[mid]
 def path(self,mid):return inside(self.root,self.root/self.entry(mid)['filename'])
 def verified_model(self,mid,force=False):
  p=self.path(mid);row=self.entry(mid)
  if not p.is_file() or p.stat().st_size!=row['bytes']:return False
  stat=p.stat();finger=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
  with self.lock:cached=self.verified.get(mid)==finger
  if force or not cached:
   if hash_file(p)!=row['sha256']:return False
   with self.lock:self.verified[mid]=finger
  return True
 def status(self):
  rows=[]
  for mid,row in self.catalog.items():
   with self.lock:job=dict(self.jobs.get(mid,{}))
   ready=self.verified_model(mid)
   partial=self.path(mid).with_suffix('.gguf.partial')
   state='ready' if ready else job.get('state') if job else 'interrupted' if partial.exists() else 'missing'
   rows.append({'id':mid,'title':row['title'],'bytes':row['bytes'],'sha256':row['sha256'],'license':row['license'],'state':state,
    'downloaded_bytes':row['bytes'] if ready else job.get('downloaded_bytes',partial.stat().st_size if partial.exists() else 0),'error':job.get('error')})
  with self.lock:
   if self.process and self.process.poll() is not None and self.runtime_state=='ready':self.runtime_state='failed';self.runtime_error='local_runtime_exited'
   runtime={'state':self.runtime_state,'model_id':self.running_id,'error':self.runtime_error,'context_tokens':8192,'parallel_generations':1,'queued':self.queued,'cpu_only':True}
  return {'enabled':self.enabled,'default':self.default,'models':rows,'runtime':runtime}
 def request_download(self,mid):
  if not self.enabled:raise Problem('local_models_disabled',409)
  row=self.entry(mid)
  with self.lock:
   if any(j.get('thread') and j['thread'].is_alive() for j in self.jobs.values()):raise Problem('model_download_busy',409)
   if self.verified_model(mid):return {'status':'ready'}
   if self.root.is_symlink():raise Problem('unsafe_model_path',422)
   self.root.mkdir(parents=True,exist_ok=True)
   if shutil.disk_usage(self.root).free<row['bytes']+256*1024**2:raise Problem('model_disk_space',409)
   cancel=Event();job={'state':'downloading','downloaded_bytes':0,'error':None,'cancel':cancel}
   thread=Thread(target=self._download,args=(mid,job),daemon=True,name='pslc-model-download')
   job['thread']=thread;self.jobs[mid]=job;thread.start()
  return {'status':'downloading'}
 def cancel_download(self,mid):
  self.entry(mid)
  with self.lock:
   job=self.jobs.get(mid)
   if job and job['thread'].is_alive():job['cancel'].set();job['state']='cancelling'
  return {'status':'cancel_requested'}
 def _set(self,job,**values):
  with self.lock:job.update(values)
 def _download(self,mid,job):
  row=self.entry(mid);target=self.path(mid);partial=inside(self.root,target.with_suffix('.gguf.partial'));cancel=job['cancel']
  try:
   start=partial.stat().st_size if partial.exists() else 0
   if start>row['bytes']:raise Problem('model_integrity_failed',422)
   if start<row['bytes']:
    begun=time.monotonic();url=row['url'];headers={'Range':'bytes='+str(start)+'-'} if start else {}
    with httpx.Client(transport=self.transport,timeout=httpx.Timeout(10,read=5),follow_redirects=False,trust_env=False) as client:
     for hop in range(6):
      if cancel.is_set() or self.closed.is_set():raise Cancelled()
      with client.stream('GET',url,headers=headers) as response:
       if 300<=response.status_code<400:
        dest=urljoin(url,response.headers.get('location',''));p=urlsplit(dest)
        host=p.hostname or ''
        if p.scheme!='https' or p.username or p.password or not (host=='huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.hf.co') or host.endswith('.xethub.hf.co')):raise Problem('model_redirect_rejected',502)
        url=dest;continue
       response.raise_for_status()
       if start and response.status_code==206:
        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('content-range',''))
        if not match or int(match[1])!=start or int(match[3])!=row['bytes']:raise Problem('model_range_invalid',502)
       elif start:start=0
       done=start
       with partial.open('ab' if start else 'wb') as file:
        for chunk in response.iter_bytes(1024*1024):
         if cancel.is_set() or self.closed.is_set():raise Cancelled()
         if time.monotonic()-begun>1800:raise Problem('model_download_timeout',504)
         done+=len(chunk)
         if done>row['bytes']:raise Problem('model_integrity_failed',422)
         file.write(chunk);self._set(job,downloaded_bytes=done)
        file.flush();os.fsync(file.fileno())
       break
     else:raise Problem('model_redirect_rejected',502)
   self._set(job,state='verifying')
   if partial.stat().st_size!=row['bytes'] or hash_file(partial,cancel)!=row['sha256']:raise Problem('model_integrity_failed',422)
   if cancel.is_set() or self.closed.is_set():raise Cancelled()
   os.replace(partial,target);self._set(job,state='ready',downloaded_bytes=row['bytes'])
   if self.desired==mid:
    try:
     with self.generation_lock:self.ensure_running(mid)
    except IntegrationError:pass
  except Cancelled:self._set(job,state='cancelled')
  except Problem as e:
   if e.code=='model_integrity_failed':
    try:partial.unlink(missing_ok=True)
    except OSError:pass
   self._set(job,state='failed',error=e.code)
  except (OSError,httpx.HTTPError,ValueError):self._set(job,state='failed',error='model_download_failed')
 def runtime_executable(self):
  machine=platform.machine().lower()
  if machine not in ('amd64','x86_64'):raise IntegrationError('local_runtime_platform')
  key='windows-x64' if os.name=='nt' else 'linux-x64'
  spec=self.runtime_manifest['platforms'].get(key)
  if not spec:raise IntegrationError('local_runtime_missing')
  root=inside(self.runtime_root,self.runtime_root/key)
  if root.exists() and any(p.is_symlink() for p in root.rglob('*')):raise IntegrationError('local_runtime_integrity')
  actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()} if root.exists() else set()
  if actual!={r['path'] for r in spec['files']}:raise IntegrationError('local_runtime_integrity')
  for row in spec['files']:
   p=inside(root,root/row['path'])
   if not p.is_file() or p.stat().st_size!=row['size'] or hash_file(p)!=row['sha256']:raise IntegrationError('local_runtime_integrity')
  exe=inside(root,root/spec['executable'])
  if os.name!='nt' and not os.access(exe,os.X_OK):os.chmod(exe,0o755)
  return exe
 def set_desired(self,mid):
  self.desired=mid
  if mid is None and self.generation_lock.acquire(False):
   try:self._stop()
   finally:self.generation_lock.release()
 def _stop(self):
  with self.lock:process=self.process;birth=self.process_birth;self.process=None;self.process_birth=None;self.running_id=None;self.runtime_state='stopped'
  if process and process.poll() is None:
   # Windows venv may launch a second Python process. Stop only this owned tree.
   try:
    root=psutil.Process(process.pid)
    children=root.children(recursive=True) if birth is not None and abs(root.create_time()-birth)<.01 else []
   except psutil.Error:children=[]
   for child in reversed(children):
    try:child.terminate()
    except psutil.Error:pass
   process.terminate()
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
   _,remaining=psutil.wait_procs(children,timeout=5)
   for child in remaining:
    try:child.kill()
    except psutil.Error:pass
 def ensure_running(self,mid):
  if not self.enabled:raise IntegrationError('local_models_disabled')
  if self.closed.is_set():raise IntegrationError('local_runtime_stopped')
  with self.lock:
   if self.process and self.process.poll() is None and self.running_id==mid and self.runtime_state=='ready':return
  self._stop()
  with self.lock:self.runtime_state='loading';self.runtime_error=None
  try:
   if not self.verified_model(mid,force=True):raise IntegrationError('local_model_missing')
   exe=self.runtime_executable();weights=self.path(mid)
   args=[str(exe),'-m',str(weights.resolve()),'--host','127.0.0.1','--port',str(self.port),'--alias',mid,
    '-ngl','0','-c','8192','-np','1','-t',str(self.s.local_model_threads),'-tb',str(self.s.local_model_threads),
    '--cache-ram','0','--no-context-shift','--chat-template-kwargs','{"enable_thinking":false}','--log-disable','--no-webui','--timeout','120']
   parent=psutil.Process()
   host=[sys.executable,'-m','backend.runtime_host','--parent',str(parent.pid),'--parent-created',str(parent.create_time()),*args]
   allowed={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','HOME','LANG','LC_ALL'}
   env={k:v for k,v in os.environ.items() if k.upper() in allowed};env['LLAMA_API_KEY']=self.token;env['PYTHONIOENCODING']='utf-8'
   if os.name!='nt':env['LD_LIBRARY_PATH']=str(exe.parent.resolve())
   try:
    check=subprocess.run([str(exe),'--version'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,**({'creationflags':0x08000000} if os.name=='nt' else {}))
    if check.returncode:raise IntegrationError('local_runtime_dependencies')
   except subprocess.TimeoutExpired:raise IntegrationError('local_runtime_dependencies') from None
   kwargs={'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True}
   process=subprocess.Popen(host,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
   try:birth=psutil.Process(process.pid).create_time()
   except psutil.Error:birth=None
   with self.lock:self.process=process;self.process_birth=birth;self.running_id=mid
   deadline=time.monotonic()+90
   with httpx.Client(timeout=2,trust_env=False) as c:
    while time.monotonic()<deadline and not self.closed.is_set():
     if process.poll() is not None:raise IntegrationError('local_runtime_start_failed')
     try:
      r=c.get(self.url+'/models',headers={'Authorization':'Bearer '+self.token})
      if r.status_code==200 and any(x.get('id')==mid for x in r.json().get('data',[])):
       with self.lock:self.runtime_state='ready'
       return
     except (httpx.HTTPError,ValueError):pass
     time.sleep(.2)
   raise IntegrationError('local_runtime_timeout')
  except (IntegrationError,OSError,Problem) as e:
   code=str(e) if isinstance(e,IntegrationError) else 'local_runtime_start_failed'
   self._stop()
   with self.lock:self.runtime_state='failed';self.runtime_error=code
   raise IntegrationError(code) from None
 @contextmanager
 def generation(self,mid):
  if not self.queue.acquire(False):raise IntegrationError('local_model_busy')
  acquired=False
  with self.lock:self.queued+=1
  try:
   acquired=self.generation_lock.acquire(timeout=30)
   if not acquired:raise IntegrationError('local_model_queue_timeout')
   with self.lock:self.queued-=1
   self.ensure_running(mid)
   yield
  finally:
   if not acquired:
    with self.lock:self.queued-=1
   if acquired:
    if self.desired is None:self._stop()
    self.generation_lock.release()
   self.queue.release()
 def warmup(self,mid):
  if self.enabled and self.verified_model(mid):
   def run():
    try:
     with self.generation_lock:
      if self.desired==mid:self.ensure_running(mid)
    except IntegrationError:pass
   Thread(target=run,daemon=True,name='pslc-model-warmup').start()
 def close(self):
  self.closed.set()
  with self.lock:
   for job in self.jobs.values():job['cancel'].set()
  self._stop()
