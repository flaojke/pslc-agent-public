"""Offline detached release verification. Never downloads or auto-enrolls."""
from pathlib import Path,PurePosixPath
import argparse,base64,hashlib,json,os,platform,re,shutil,stat,subprocess,sys,uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from pydantic import BaseModel,ConfigDict,Field
from .runtime import instance_lock

class File(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 path:str=Field(min_length=1,max_length=240)
 size:int=Field(ge=0,le=1073741824)
 sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
class Manifest(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 schema_version:int=Field(ge=1,le=1)
 version:str=Field(pattern=r'^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$')
 platform:str=Field(pattern=r'^(windows|linux)-x64$')
 key_id:str=Field(pattern=r'^[a-f0-9]{16}$')
 files:list[File]=Field(min_length=1,max_length=10000)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def host_platform():return ('windows' if os.name=='nt' else 'linux')+'-x64' if platform.machine().lower() in ('amd64','x86_64') else 'unsupported'
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()
def safe(root,relative):
 p=PurePosixPath(relative)
 if p.is_absolute() or '\\' in relative or ':' in relative or any(part in ('','..','.') for part in relative.split('/')):raise ValueError('unsafe_release_path')
 candidate=root.joinpath(*p.parts)
 for part in [candidate,*candidate.parents]:
  if part==root.parent:break
  if part.is_symlink() or (part.exists() and getattr(part.lstat(),'st_file_attributes',0)&0x400):raise ValueError('release_link_forbidden')
 if not candidate.resolve().is_relative_to(root.resolve()):raise ValueError('release_path_escape')
 return candidate

def inventory(root):
 root=Path(root).resolve();items=[]
 for p in root.rglob('*'):
  safe(root,p.relative_to(root).as_posix())
  if p.is_file() and p.relative_to(root).as_posix() not in ('manifest.json','manifest.sig'):
   if len(items)>=10000:raise ValueError('release_too_many_files')
   items.append({'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,'sha256':sha(p)})
 if sum(x['size'] for x in items)>1073741824:raise ValueError('release_too_large')
 return sorted(items,key=lambda x:x['path'])

def sign(root,version,platform_name,key_file):
 root=Path(root).resolve();key=serialization.load_pem_private_key(Path(key_file).read_bytes(),password=None)
 if not isinstance(key,Ed25519PrivateKey):raise ValueError('ed25519_required')
 public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
 value=Manifest(schema_version=1,version=version,platform=platform_name,key_id=hashlib.sha256(public).hexdigest()[:16],files=inventory(root)).model_dump()
 payload=canonical(value);(root/'manifest.json').write_bytes(payload);(root/'manifest.sig').write_bytes(base64.b64encode(key.sign(payload)))
 return hashlib.sha256(payload).hexdigest()

def verify(root,trusted_key,expected_platform=None):
 root=Path(root)
 if root.is_symlink():raise ValueError('release_link_forbidden')
 root=root.resolve()
 for name in ('manifest.json','manifest.sig'):safe(root,name)
 if (root/'manifest.json').stat().st_size>2097152 or (root/'manifest.sig').stat().st_size>256:raise ValueError('manifest_too_large')
 payload=(root/'manifest.json').read_bytes();value=Manifest.model_validate_json(payload,strict=True)
 key=serialization.load_pem_public_key(Path(trusted_key).read_bytes())
 if not isinstance(key,Ed25519PublicKey):raise ValueError('ed25519_required')
 raw=key.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
 if value.key_id!=hashlib.sha256(raw).hexdigest()[:16]:raise ValueError('untrusted_release_key')
 key.verify(base64.b64decode((root/'manifest.sig').read_bytes(),validate=True),payload)
 if payload!=canonical(value.model_dump()):raise ValueError('noncanonical_manifest')
 if value.platform!=(expected_platform or host_platform()):raise ValueError('wrong_platform')
 actual=inventory(root)
 if actual!=sorted([x.model_dump() for x in value.files],key=lambda x:x['path']):raise ValueError('release_file_mismatch')
 return value,hashlib.sha256(payload).hexdigest()

def stage(source,release_root,trusted_key):
 value,digest=verify(source,trusted_key);root=Path(release_root).resolve();root.mkdir(parents=True,exist_ok=True)
 folder=safe(root,'versions/'+value.version)
 if folder.exists():
  _,existing=verify(folder,trusted_key)
  if existing!=digest:raise ValueError('version_immutable')
  return folder,digest
 folder.parent.mkdir(exist_ok=True);temporary=safe(root,'versions/stage-'+uuid.uuid4().hex)
 shutil.copytree(Path(source).resolve(),temporary,symlinks=False)
 _,copied=verify(temporary,trusted_key)
 if copied!=digest:raise ValueError('staged_digest_mismatch')
 temporary.rename(folder);return folder,digest

def active(root):
 root=Path(root).resolve();p=safe(root,'current.json')
 if not p.exists():return None
 if p.stat().st_size>512:raise ValueError('invalid_current_pointer')
 value=json.loads(p.read_bytes())
 if set(value)!={'version','digest'} or not re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}',value['version']) or not re.fullmatch(r'[a-f0-9]{64}',value['digest']):raise ValueError('invalid_current_pointer')
 return value

def activate(release_root,version,trusted_key,expected_digest,state_path):
 root=Path(release_root).resolve();folder=safe(root,'versions/'+version)
 with instance_lock(state_path):
  value,digest=verify(folder,trusted_key)
  if digest!=expected_digest or value.version!=version:raise ValueError('expected_release_mismatch')
  previous=active(root)
  if previous and tuple(map(int,version.split('.')))<tuple(map(int,previous['version'].split('.'))):raise ValueError('downgrade_forbidden')
  if previous and previous['version']==version and previous['digest']!=digest:raise ValueError('version_immutable')
  pointer=safe(root,'current-'+uuid.uuid4().hex+'.json');pointer.write_bytes(canonical({'version':version,'digest':digest}));os.replace(pointer,safe(root,'current.json'))
 return {'version':version,'digest':digest,'identity_preserved':True}

def launch(release_root,trusted_key,config):
 root=Path(release_root).resolve();pointer=active(root)
 if not pointer:raise ValueError('no_active_release')
 folder=safe(root,'versions/'+pointer['version']);manifest,digest=verify(folder,trusted_key)
 if digest!=pointer['digest']:raise ValueError('active_digest_mismatch')
 binary=safe(folder,'pslc-agent.exe' if os.name=='nt' else 'pslc-agent')
 if not any(x.path==binary.name for x in manifest.files):raise ValueError('release_entrypoint_missing')
 return subprocess.run([str(binary),'--config',str(Path(config).resolve())],shell=False).returncode

def main():
 p=argparse.ArgumentParser(description='PSLC signed offline delivery')
 sub=p.add_subparsers(dest='command',required=True)
 k=sub.add_parser('qa-keygen');k.add_argument('--directory',required=True)
 s=sub.add_parser('sign');s.add_argument('--bundle',required=True);s.add_argument('--version',required=True);s.add_argument('--platform',required=True);s.add_argument('--key-file',required=True)
 for name in ('verify','stage','activate','launch'):
  q=sub.add_parser(name);q.add_argument('--trusted-key',required=True)
  if name in ('verify','stage'):q.add_argument('--bundle',required=True)
  if name!='verify':q.add_argument('--release-root',required=True)
  if name=='activate':q.add_argument('--version',required=True);q.add_argument('--expected-digest',required=True);q.add_argument('--state',required=True)
  if name=='launch':q.add_argument('--config',required=True)
 a=p.parse_args()
 try:
  if a.command=='qa-keygen':
   root=Path(a.directory).resolve();root.mkdir(parents=True,exist_ok=True)
   if any((root/x).exists() for x in ('QA-ONLY-private.pem','QA-ONLY-public.pem')):raise ValueError('key_exists')
   key=Ed25519PrivateKey.generate();private=root/'QA-ONLY-private.pem';private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));os.chmod(private,0o600)
   (root/'QA-ONLY-public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo));print('QA ONLY keypair created. Not corporate trust.')
  elif a.command=='sign':print(sign(a.bundle,a.version,a.platform,a.key_file))
  elif a.command=='verify':value,digest=verify(a.bundle,a.trusted_key);print(json.dumps({'version':value.version,'digest':digest,'files':len(value.files)}))
  elif a.command=='stage':folder,digest=stage(a.bundle,a.release_root,a.trusted_key);print(json.dumps({'version_path':str(folder),'digest':digest}))
  elif a.command=='activate':print(json.dumps(activate(a.release_root,a.version,a.trusted_key,a.expected_digest,a.state)))
  elif a.command=='launch':raise SystemExit(launch(a.release_root,a.trusted_key,a.config))
 except Exception:raise SystemExit('Release rejected: signature, files, platform, identity lock or expected digest check failed.') from None
if __name__=='__main__':main()
