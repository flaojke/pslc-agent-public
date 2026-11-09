"""Node-local encrypted configuration baselines. No forward source/stdout is serialized."""
import ctypes,hashlib,json,os,re,secrets,tempfile,time,shutil
from pathlib import Path
from typing import Literal
from threading import RLock
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field,model_validator
from backend.secret_store import SecretStore,_dpapi
from backend.recovery_types import Strict,RestoreProgram,RecoveryResources,RecoveryReceipt,Hash,Id,validate_pair,fingerprint
from .recovery_files import RecoveryError,snapshot_file,restore_file,file_fingerprint,unb64
from .recovery_system import service,snapshot_registry,restore_registry
MAX_CAPSULE=16*1024*1024
class Capsule(Strict):
 version:Literal[1]=1
 device_id:Id
 owner_id:Id
 original_task_id:Id
 original_plan_hash:Hash
 program:RestoreProgram
 resources:RecoveryResources
 expires:float
 state:str
 baselines:list[dict]=Field(min_length=1,max_length=8)
 post:list[Hash|None]=Field(min_length=1,max_length=8)
 @model_validator(mode='after')
 def binding(self):
  validate_pair(self.program.model_dump(),[r.model_dump() for r in self.resources.resources])
  if self.device_id!=self.program.device_id or self.original_task_id!=self.program.original_task_id or len(self.baselines)!=len(self.resources.resources) or len(self.post)!=len(self.baselines):raise ValueError('invalid_capsule')
  if self.state not in ('prepared','executing','ready','incomplete','restored','conflict','partial'):raise ValueError('invalid_capsule_state')
  return self

class Vault:
 def __init__(self,directory,device_id,owner_id,protected=(),clock=time.time,max_capsules=100,max_bytes=1024**3,package_provider=None):
  self.directory=Path(directory);self.device_id=device_id;self.owner_id=owner_id;self.protected=[self.directory,*protected];self.clock=clock;self.max_capsules=max_capsules;self.max_bytes=max_bytes;self.lock=RLock();self.package_provider=package_provider
 def _path(self,cid):
  if not re.fullmatch('[a-f0-9]{64}',cid):raise RecoveryError('recovery_binding_mismatch')
  return self.directory/(cid+'.enc')
 def _read(self,cid,allow_other_owner=False):
  p=self._path(cid)
  try:
   if p.is_symlink() or self.directory.is_symlink() or p.stat().st_size>MAX_CAPSULE:raise ValueError()
   if os.name!='nt' and p.stat().st_mode&0o077:raise ValueError()
   raw=p.read_bytes();helper=SecretStore(p)
   if os.name=='nt' and raw.startswith(b'RW1'):plain=_dpapi(raw[3:],True)
   elif os.name!='nt' and raw.startswith(b'RL1'):plain=AESGCM(helper._key(False)).decrypt(raw[3:15],raw[15:],b'PSLC-recovery-v1')
   else:raise ValueError()
   cap=Capsule.model_validate_json(plain,strict=True)
   if cap.device_id!=self.device_id or (not allow_other_owner and cap.owner_id!=self.owner_id) or self.capsule_id(cap)!=cid:raise ValueError()
   if cap.expires<=self.clock():raise RecoveryError('recovery_expired')
   return cap
  except RecoveryError:raise
  except Exception:raise RecoveryError('recovery_unavailable') from None
 def _inventory(self,keep=None):
  if self.directory.is_symlink():raise RecoveryError('recovery_unavailable')
  self.directory.mkdir(parents=True,exist_ok=True)
  files=list(self.directory.iterdir())
  if len(files)>self.max_capsules+4:raise RecoveryError('recovery_quota')
  capsules=[]
  for p in files:
   if p.name=='master.key':continue
   if p.is_symlink() or not re.fullmatch('[a-f0-9]{64}\\.enc',p.name) or not p.is_file():raise RecoveryError('recovery_unavailable')
   # Expired capsules of this owner can be removed; corrupt/foreign capsules are preserved.
   try:
    if p.stem!=keep:self._read(p.stem,allow_other_owner=True)
   except RecoveryError as exc:
    if exc.code=='recovery_expired':p.unlink();continue
   capsules.append(p)
  return capsules
 def _write(self,cap):
  cid=self.capsule_id(cap);target=self._path(cid);tmp=None
  try:
   for directory in (self.directory,*self.directory.parents):
    if directory.is_symlink() or (hasattr(directory,'is_junction') and directory.is_junction()):raise RecoveryError('recovery_unsafe_path')
   helper=SecretStore(target);helper._directory()
   raw=cap.model_dump_json().encode()
   if len(raw)>MAX_CAPSULE-100:raise RecoveryError('recovery_quota')
   if os.name=='nt':payload=b'RW1'+_dpapi(raw)
   else:
    nonce=secrets.token_bytes(12);payload=b'RL1'+nonce+AESGCM(helper._key(True)).encrypt(nonce,raw,b'PSLC-recovery-v1')
   files=self._inventory(keep=cid);old=target.stat().st_size if target.exists() else 0
   if len(files)+(not target.exists())>self.max_capsules or sum(p.stat().st_size for p in files)-old+len(payload)>self.max_bytes or shutil.disk_usage(self.directory).free<len(payload)*2+1048576:raise RecoveryError('recovery_quota')
   fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=self.directory)
   with os.fdopen(fd,'wb') as f:
    if os.name!='nt':os.fchmod(f.fileno(),0o600)
    f.write(payload);f.flush();os.fsync(f.fileno())
   os.replace(tmp,target);tmp=None
   return cid
  except RecoveryError:raise
  except Exception:raise RecoveryError('recovery_unavailable') from None
  finally:
   if tmp is not None:
    try:os.unlink(tmp)
    except OSError:pass
 @staticmethod
 def capsule_id(cap):return fingerprint({'device_id':cap.device_id,'task_id':cap.original_task_id,'plan_hash':cap.original_plan_hash,'program_hash':fingerprint(cap.program.model_dump())})
 def snapshot(self,r,baseline=None):
  if r['type']=='configuration_file':return snapshot_file(r,self.protected)
  if r['type']=='service_runtime':return service(r)
  if r['type']=='registry_value':return snapshot_registry(r)
  if r['type']=='package_presence':
   if self.package_provider is None:
    from .recovery_package import PackageProvider
    self.package_provider=PackageProvider()
   if baseline:self.package_provider.load_baseline(baseline)
   return self.package_provider.snapshot(r)
  raise RecoveryError('recovery_provider_unavailable')
 def state_hash(self,r,value):return file_fingerprint(value) if r['type']=='configuration_file' else fingerprint(value)
 def restore_resource(self,r,baseline):
  if r['type']=='configuration_file':restore_file(r,baseline,self.protected)
  elif r['type']=='service_runtime':service(r,baseline['state'])
  elif r['type']=='registry_value':restore_registry(r,baseline)
  elif r['type']=='package_presence' and self.package_provider:self.package_provider.restore(r,baseline)
  else:raise RecoveryError('recovery_provider_unavailable')
 def prepare(self,program,resources,plan_hash):
  with self.lock:
   p=validate_pair(program,resources)
   if p.device_id!=self.device_id:raise RecoveryError('recovery_binding_mismatch')
   values=RecoveryResources(resources=resources);baseline=[];size=0
   for r in values.resources:
    b=self.snapshot(r.model_dump());baseline.append(b)
    if r.type=='package_presence' and b.get('presence')!='absent':raise RecoveryError('recovery_package_restore_unsupported')
    if r.type=='configuration_file' and b.get('exists'):size+=len(unb64(b['data']))
    if size>8*1024*1024:raise RecoveryError('recovery_quota')
   cap=Capsule(device_id=self.device_id,owner_id=self.owner_id,original_task_id=p.original_task_id,original_plan_hash=plan_hash,program=p,resources=values,expires=float(self.clock()+7*86400),state='prepared',baselines=baseline,post=[None]*len(baseline))
   cid=self.capsule_id(cap)
   if self._path(cid).exists():raise RecoveryError('recovery_forward_replay_blocked')
   self._write(cap);return cid
 def started(self,cid):
  with self.lock:
   cap=self._read(cid)
   if cap.state!='prepared':raise RecoveryError('recovery_forward_replay_blocked')
   for r,b in zip(cap.resources.resources,cap.baselines):
    resource=r.model_dump()
    if self.state_hash(resource,self.snapshot(resource,b))!=self.state_hash(resource,b):raise RecoveryError('recovery_drift_before_forward')
   cap.state='executing';self._write(cap)
 def finished(self,cid):
  with self.lock:
   cap=self._read(cid)
   if cap.state not in ('prepared','executing'):raise RecoveryError('recovery_forward_replay_blocked')
   for i,r in enumerate(cap.resources.resources):
    try:cap.post[i]=self.state_hash(r.model_dump(),self.snapshot(r.model_dump()))
    except Exception:cap.post[i]=None
   cap.state='ready' if all(cap.post) else 'incomplete';self._write(cap)
   return self.receipt(cap)
 def receipt(self,cap,restored_count=0):
  resources=[r.model_dump() for r in cap.resources.resources]
  baseline=[self.state_hash(r,b) for r,b in zip(resources,cap.baselines)]
  return RecoveryReceipt(device_id=cap.device_id,original_task_id=cap.original_task_id,original_plan_hash=cap.original_plan_hash,program_hash=fingerprint(cap.program.model_dump()),resource_set_hash=cap.program.resource_set_hash,capsule_id=self.capsule_id(cap),expires=cap.expires,state=cap.state if cap.state not in ('prepared','executing') else 'incomplete',baseline_hash=fingerprint(baseline),post_hash=fingerprint(cap.post),resource_count=len(resources),restored_count=restored_count).model_dump()
 def rollback(self,cid,program,original_plan_hash,on_restore=None):
  with self.lock:
   cap=self._read(cid)
   if cap.program.model_dump()!=program or cap.original_plan_hash!=original_plan_hash:raise RecoveryError('recovery_binding_mismatch')
   resources=[r.model_dump() for r in cap.resources.resources]
   expected=[self.state_hash(r,b) for r,b in zip(resources,cap.baselines)]
   current=[]
   try:
    for r,b in zip(resources,cap.baselines):current.append(self.state_hash(r,self.snapshot(r,b)))
   except Exception:raise RecoveryError('recovery_unavailable') from None
   if any(x!=base and (post is None or x!=post) for x,base,post in zip(current,expected,cap.post)):
    cap.state='conflict';self._write(cap);return self.receipt(cap,sum(x==base for x,base in zip(current,expected)))
   restored=0;cap.state='partial'
   for i,(r,baseline) in enumerate(zip(resources,cap.baselines)):
    try:
     latest=self.state_hash(r,self.snapshot(r,baseline))
     if latest!=expected[i] and (cap.post[i] is None or latest!=cap.post[i]):
      cap.state='partial' if restored else 'conflict';self._write(cap);return self.receipt(cap,restored)
     if latest!=expected[i]:
      if on_restore:on_restore()
      self.restore_resource(r,baseline)
     if self.state_hash(r,self.snapshot(r,baseline))!=expected[i]:raise RecoveryError('recovery_verification_failed')
     restored+=1
    except Exception:
     self._write(cap);return self.receipt(cap,restored)
   cap.state='restored';self._write(cap);return self.receipt(cap,restored)
