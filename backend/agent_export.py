"""Owner-bound complete native export; no enrollment code or existing identity."""
from pathlib import Path
import copy,hashlib,json,secrets,shutil,tempfile,threading,time,zipfile
from sqlalchemy import select
from agent.delivery import verify
from agent import __version__ as AGENT_VERSION
from .config import ROOT
from .secret_store import SecretStore
from .store import Volatile,Problem,enrollment,digest
MAX_BYTES=512*1024*1024
def remove_export_directory(base):
 p=Path(base)
 if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('unsafe_export_cleanup')
 resolved=p.resolve()
 if resolved.parent!=Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith('pslc-node-export-'):raise ValueError('unsafe_export_cleanup')
 shutil.rmtree(resolved,ignore_errors=True)

class AgentExport:
 def __init__(self,nodes,roots=None,public_pem=None,scope=None):
  self.nodes=nodes;self.handles=Volatile(128,4*1024*1024);self.lock=threading.RLock()
  self.slot=threading.BoundedSemaphore(1);self.transfers={};self.roots=roots;self.public_pem=public_pem;self.scope=scope
 def register(self,result):
  self.nodes.allowed();sid=secrets.token_hex(16);now=time.time()
  row={'owner_id':self.nodes.ops.p.id,'config':copy.deepcopy(result['config']),'profile':copy.deepcopy(result['profile']),'ticket_hash':digest(result['enrollment_token']),'expires':now+result['expires_in']}
  with self.lock:self.handles.put(sid,row,result['expires_in'])
  return {**result,'bundle_id':sid,'bundle_expires':row['expires'],'bundle_contains_enrollment_code':False}
 def get(self,sid):
  self.nodes.allowed()
  with self.lock:
   row=self.handles.get(sid)
   if not row or row['owner_id']!=self.nodes.ops.p.id:raise Problem('export_profile_unavailable',404)
   row=copy.deepcopy(row)
  with self.nodes.store.tx() as c:
   ticket=c.execute(select(enrollment).where(enrollment.c.digest==row['ticket_hash'])).mappings().first()
   if not ticket or ticket['used'] or ticket['expires']<time.time():raise Problem('export_profile_unavailable',409)
  return row
 def resource(self,platform):
  if self.public_pem is not None:return Path(self.roots[platform]),self.public_pem,self.scope or 'test-only'
  p=ROOT/'config/agent-trust.json'
  if p.is_symlink() or p.stat().st_size>8192:raise ValueError('invalid_agent_trust')
  trust=json.loads(p.read_text(encoding='utf-8'));root=ROOT/'resources/agents'/platform/'pslc-agent'
  if not root.is_dir():root=ROOT/'artifacts/native'/AGENT_VERSION/platform/'pslc-agent'
  return root,trust['public_pem'].encode('ascii'),trust['scope']
 def build(self,sid):
  row=self.get(sid)
  if not self.slot.acquire(blocking=False):raise Problem('export_busy',409)
  base=None
  try:
   platform=('windows' if row['profile']['os_family']=='Windows' else 'linux')+'-x64'
   native,pem,scope=self.resource(platform);base=Path(tempfile.mkdtemp(prefix='pslc-node-export-'))
   SecretStore(base/'guard')._directory()
   key=base/'trusted-public.pem';key.write_bytes(pem)
   manifest,manifest_hash=verify(native,key,platform)
   if self.public_pem is None and manifest.version!=AGENT_VERSION:raise ValueError("native_source_version_mismatch")
   if sum(x.size for x in manifest.files)>MAX_BYTES:raise ValueError('export_size_limit')
   expected={x.path:x.sha256 for x in manifest.files}
   expected.update({n:hashlib.sha256((native/n).read_bytes()).hexdigest() for n in ('manifest.json','manifest.sig')})
   template=ROOT/'deploy/export'
   readme=(template/'README.md').read_text(encoding='utf-8')+'\n\nПлатформа: '+platform+'. Native: '+manifest.version+'. Trust: '+scope+'.\nСервер: '+row['config']['server']+'\n'
   extras={'config.json':json.dumps(row['config'],indent=2).encode(),'README.md':readme.encode(),'TRUSTED-public.pem':pem,'native-manifest-digest.txt':(manifest_hash+'\n').encode()}
   for n in ('verify.ps1','start.ps1') if platform.startswith('windows') else ('start.sh',):extras[n]=(template/n).read_bytes()
   n='install-windows-service.ps1' if platform.startswith('windows') else 'pslc-agent.service';extras[n]=(ROOT/'deploy'/n).read_bytes()
   target=base/'PSLC-Agent-node.zip';checks=[]
   with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
    for name,sha in sorted(expected.items()):
     source=native/name;arcname='native/'+name;h=hashlib.sha256()
     info=zipfile.ZipInfo(arcname);info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(0o100755 if name=='pslc-agent' else 0o100644)<<16
     with source.open('rb') as src,archive.open(info,'w') as dst:
      for chunk in iter(lambda:src.read(1024*1024),b''):h.update(chunk);dst.write(chunk)
     if h.hexdigest()!=sha:raise ValueError('native_changed_during_export')
     checks.append(sha+'  '+arcname)
    for name,data in sorted(extras.items()):
     info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(0o100755 if name=='start.sh' else 0o100644)<<16
     archive.writestr(info,data);checks.append(hashlib.sha256(data).hexdigest()+'  '+name)
    archive.writestr('SHA256SUMS','\n'.join(sorted(checks))+'\n')
   h=hashlib.sha256()
   with target.open('rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
   with self.lock:self.transfers[str(base)]=time.time()+900
   return {'path':target,'directory':base,'sha256':h.hexdigest(),'filename':'PSLC-Agent-'+platform+'-node.zip','trust':scope}
  except Exception as error:
   if base:remove_export_directory(base)
   self.slot.release()
   if isinstance(error,Problem):raise
   raise Problem('agent_distribution_unavailable',503) from None
 def cleanup(self,base):
  with self.lock:
   key=str(base)
   if key not in self.transfers:return
   remove_export_directory(base)
   if Path(base).exists():return
   del self.transfers[key];self.slot.release()
 def purge(self):
  with self.lock:self.handles.purge();old=[p for p,expiry in self.transfers.items() if expiry<time.time()]
  for p in old:self.cleanup(p)
 def close(self):
  for p in list(self.transfers):self.cleanup(p)
