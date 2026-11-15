"""Deterministic source distribution: allowlisted source + compiled web, no local state."""
from pathlib import Path
import hashlib,json,os,sys,zipfile,re
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
REQUIRED_BUILD_INPUTS=(
 'frontend/index.html','vite.config.ts','tsconfig.json','package.json','pnpm-lock.yaml',
 'pyproject.toml','requirements.lock.txt','requirements.agent.txt','requirements.build-windows.lock.txt',
 'deploy/Dockerfile.agent','deploy/Dockerfile.cpu-qa','.dockerignore',
 'scripts/linux_vm.py','scripts/start-linux-vm.ps1','scripts/status-linux-vm.ps1','scripts/stop-linux-vm.ps1','scripts/start-release.ps1',
 'agent/entrypoint.py','agent/__init__.py','scripts/build_agent.py','scripts/native_agent_smoke.py','scripts/install_bootstrap.py','backend/runtime_host.py','backend/runtime_process.py')
EXPLICIT_INPUTS=(*REQUIRED_BUILD_INPUTS,'.agents/skills/.openspec-target')

def explicit_inputs(root):
 root=Path(root).resolve();result={}
 for name in EXPLICIT_INPUTS:
  p=root/name
  if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('invalid_build_input:'+name)
  if not p.is_file():
   if name in REQUIRED_BUILD_INPUTS:raise ValueError('missing_build_input:'+name)
   continue
  result[name]=p.read_bytes()
 return result

def validate_source_inputs(files):
 import shlex
 missing=set(REQUIRED_BUILD_INPUTS)-set(files)
 if missing:raise ValueError('missing_build_input:'+sorted(missing)[0])
 for name in ('deploy/Dockerfile.agent','deploy/Dockerfile.cpu-qa'):
  for line in files[name].decode('utf-8').splitlines():
   if not line.strip().upper().startswith('COPY '):continue
   body=line.strip()[5:].strip()
   if body.startswith('['):args=json.loads(body)
   else:args=shlex.split(body)
   if any(a.startswith('--from=') for a in args):continue
   args=[a for a in args if not a.startswith('--')]
   for src in args[:-1]:
    src=src.removeprefix('./').rstrip('/')
    if src=='.' and name=='deploy/Dockerfile.linux-vm':continue # Manager curates this context independently.
    if not src or src.startswith('/') or '..' in Path(src).parts or ':' in src:raise ValueError('unsafe_docker_copy')
    if src not in files and not any(p.startswith(src+'/') for p in files):raise ValueError('missing_docker_copy:'+src)
 return True


def collect(*,scan_private=True):
 files=explicit_inputs(ROOT)
 version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
 galleries=sorted(p.name for p in (ROOT/'release-dashboards').glob('*_MSK_v'+version) if p.is_dir())
 current_gallery=galleries[-1] if galleries else None
 safe_qa={'settings-guides','reversible-task-plans','scheduled-agent-work','local-model','compact-historical-messages','model-label-refresh','runtime-policy'}
 directories=('backend','agent','frontend/src','frontend/dist','tests','scripts','openspec','qa','docs','deploy','config','licenses','.agents/skills','release-dashboards')
 allowed={'.py','.ps1','.cjs','.js','.md','.json','.yaml','.yml','.ts','.tsx','.css','.html','.png','.svg','.txt','.service','.pem','.sh'}
 for folder in directories:
  for p in (ROOT/folder).rglob('*'):
   if not p.is_file() or p.suffix not in allowed or p.is_symlink() or not p.resolve().is_relative_to(ROOT.resolve()) or any(x in p.parts for x in ('__pycache__','.pytest_cache','node_modules','.local','.git','.venv')):continue
   # No signing keys or local certificates, even inside an allowlisted folder.
   if p.suffix=='.pem':continue
   relative=p.relative_to(ROOT).as_posix();parts=relative.split('/')
   if relative.startswith(('docs/qa/','docs/baseline-v0.2/','docs/design/','docs/roadmap/','release-dashboards/','openspec/changes/','openspec/withdrawn/')) or relative in ('openspec/MASTER_SPEC.md','docs/ARCHITECTURE.md','docs/ROADMAP.md','docs/SECURITY_APPROVALS.md','docs/REMAINING_WORK.md','docs/GITHUB_HANDOFF.md') or relative.startswith(('docs/VERIFICATION','docs/RELEASE_NOTES_')):continue
   if parts[0]=='release-dashboards' and (current_gallery is None or parts[1]!=current_gallery):continue
   if relative.startswith('docs/qa/') and p.suffix in ('.png','.svg') and (len(parts)<4 or parts[2] not in safe_qa or 'live' in p.name.lower()):continue
   files[relative]=p.read_bytes()
 for name in ('LICENSE','NOTICE','SECURITY.md','.gitignore','README.md','AGENTS.md','pyproject.toml','package.json','pnpm-lock.yaml','requirements.lock.txt','requirements.agent.txt','requirements.build-windows.lock.txt','vite.config.ts','tsconfig.json','openspec.ps1','compose.yaml','.dockerignore'):
  p=ROOT/name
  if p.is_file():files[name]=p.read_bytes()
 import importlib.metadata as metadata
 inventory=[]
 for line in (ROOT/'requirements.lock.txt').read_text(encoding='utf-8').splitlines():
  if '==' not in line:continue
  name,expected=line.split('==',1);dist=metadata.distribution(name)
  if dist.version!=expected:raise ValueError('installed_dependency_version_mismatch')
  inventory.append({'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License','See upstream package')})
 for name in ('react','react-dom'):
  p=ROOT/'node_modules'/name/'LICENSE'
  if p.is_file():files['licenses/'+name+'-LICENSE.txt']=p.read_bytes()
 files['dependency-inventory.json']=json.dumps({'python':inventory,'frontend':json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['dependencies'],'distribution':'source plus compiled web; Python dependencies installed separately'},indent=2).encode()
 if 'frontend/dist/index.html' not in files:raise ValueError('build_web_first')
 for asset in re.findall(r'(?:src|href)="(/assets/[^"?]+)"',files['frontend/dist/index.html'].decode()):
  if 'frontend/dist'+asset not in files:raise ValueError('missing_compiled_web_asset')
 if (ROOT/'docs/INSTALL_RELEASE.md').is_file():files['START_HERE.md']=(ROOT/'docs/INSTALL_RELEASE.md').read_bytes()
 if scan_private:
  from backend.config import load_settings
  from backend.model_connection import ModelConnection
  settings=load_settings();private=[settings.operator_token,settings.model_token,settings.oidc_client_secret,settings.confluence_token,ModelConnection(settings).snapshot().model_token]
  private.extend(p['key'] for p in ModelConnection(settings).profiles.values())
  from backend.secret_store import SecretStore
  identity=ROOT/'.local/smoke-agent.json'
  if identity.is_file():
   value=json.loads(identity.read_text(encoding='utf-8'));private.append(value.get('credential',''))
  demo=ROOT/'.local/demo/identity.enc'
  if demo.is_file():
   value=SecretStore(demo).read();private.append(value.get('credential',''))

  for value in private:
   if value and len(value)>=12 and any(value.encode() in b for b in files.values()):raise ValueError('private_value_in_release')
 if any('/.local/' in n or n.endswith(('.sqlite3','.enc','.key','.env')) for n in files):raise ValueError('private_path_in_release')
 validate_source_inputs(files)
 return files
WEIGHT_EXTENSIONS={'.gguf','.safetensors','.ckpt','.onnx','.onnx_data','.pt','.pth','.tflite','.h5','.hdf5'}

def public_member(name):
 from pathlib import PurePosixPath
 if not isinstance(name,str) or not name or any(ord(c)<32 for c in name) or "\\" in name or ':' in name:raise ValueError('unsafe_distribution_member')
 parts=PurePosixPath(name).parts
 if not parts or name.startswith('/') or '/'.join(parts)!=name or any(p in ('.','..') for p in parts):raise ValueError('unsafe_distribution_member')
 if any(p!=p.strip() or p.endswith('.') or any(c in p for c in '<>"|?*') or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p) for p in parts):raise ValueError('unsafe_distribution_member')
 low=[p.lower() for p in parts]
 if any(p in ('models','weights','.local','.git','.venv','node_modules') for p in low) or Path(name).suffix.lower() in WEIGHT_EXTENSIONS|{'.sqlite3','.enc','.key','.env'} or re.fullmatch(r'(?:pytorch_model|ggml-model).*\.bin',low[-1]):raise ValueError('model_weight_in_distribution')
 return name

def weight_header(data):
 return len(data)>=8 and data[:4]==b'GGUF' and not all(32<=b<127 for b in data[4:8])

def validate_public_payload(files):
 seen=set()
 for name,data in files.items():
  public_member(name)
  if name.lower() in seen:raise ValueError('duplicate_distribution_member')
  seen.add(name.lower())
  if name in ('release-manifest.json','distribution.json'):raise ValueError('reserved_distribution_member')
  if type(data) is not bytes:raise ValueError('invalid_distribution_payload')
  if weight_header(data[:8]):raise ValueError('model_weight_in_distribution')
 return True

def verify_model_free_archive(path,*,version=None,expected_sha256=None):
 import stat
 path=Path(path)
 if path.stat().st_size>2*1024**3:raise ValueError('distribution_size_limit')
 if expected_sha256:
  with path.open('rb') as f:
   if hashlib.file_digest(f,'sha256').hexdigest()!=expected_sha256:raise ValueError('distribution_digest_mismatch')
 hashes={};sizes={};total=0;prefix=None
 with zipfile.ZipFile(path) as archive:
  names=archive.namelist()
  if len(names)>10000 or len(names)!=len(set(n.lower() for n in names)):raise ValueError('duplicate_distribution_member')
  for item in archive.infolist():
   if item.is_dir() or stat.S_IFMT(item.external_attr>>16)==stat.S_IFLNK:raise ValueError('unsafe_distribution_member')
   parts=item.filename.split('/',1)
   if len(parts)!=2 or not re.fullmatch(r'PSLC-Agent-\d+\.\d+\.\d+',parts[0]):raise ValueError('unsafe_distribution_member')
   prefix=prefix or parts[0]
   if parts[0]!=prefix or version and prefix!='PSLC-Agent-'+version:raise ValueError('distribution_version_mismatch')
   name=public_member(parts[1]);total+=item.file_size
   if total>2*1024**3:raise ValueError('distribution_size_limit')
   h=hashlib.sha256();first=b'';count=0
   with archive.open(item) as src:
    for block in iter(lambda:src.read(1024*1024),b''):
     if len(first)<8:first+=block[:8-len(first)]
     h.update(block);count+=len(block)
   if weight_header(first):raise ValueError('model_weight_in_distribution')
   if count!=item.file_size:raise ValueError('distribution_size_mismatch')
   hashes[name]=h.hexdigest();sizes[name]=count
  if prefix is None or sizes.get('release-manifest.json',0)>8*1024*1024 or sizes.get('distribution.json',0)>131072:raise ValueError('distribution_manifest_limit')
  manifest=json.loads(archive.read(prefix+'/release-manifest.json'))
  if manifest.get('format')!='pslc-source-release-v1' or manifest.get('version')!=prefix.removeprefix('PSLC-Agent-') or manifest.get('variant') not in (None,'Lite') or manifest.get('weights_included',False) is not False:raise ValueError('model_weight_in_distribution')
  rows=manifest['files'];seen=set()
  if not isinstance(rows,list) or len(rows)>10000:raise ValueError('invalid_distribution_manifest')
  for row in rows:
   if not isinstance(row,dict) or set(row)!={'path','size','sha256'} or type(row['size']) is not int or row['size']<0 or not isinstance(row['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError('invalid_distribution_manifest')
   name=public_member(row['path'])
   if name.lower() in seen:raise ValueError('duplicate_distribution_member')
   seen.add(name.lower())
   if hashes.get(name)!=row['sha256'] or sizes.get(name)!=row['size']:raise ValueError('distribution_manifest_mismatch')
  if set(hashes)!={row['path'] for row in rows}|{'release-manifest.json'}:raise ValueError('distribution_manifest_mismatch')
  if 'distribution.json' in hashes:
   distribution=json.loads(archive.read(prefix+'/distribution.json'))
   if distribution.get('variant')!='Lite' or distribution.get('weights_included') is not False or distribution.get('version')!=manifest['version']:raise ValueError('model_weight_in_distribution')
 return {'status':'verified','version':manifest['version'],'files':len(hashes),'weights_included':False,'all_crc_checked':True,'variant':manifest.get('variant','Source')}

def build(out=None,files=None,*,root=None):
 source_root=Path(root or ROOT).resolve()
 if root is not None and files is None and source_root!=ROOT.resolve():raise ValueError('source_root_override_requires_files')
 version=json.loads((source_root/'package.json').read_text(encoding='utf-8'))['version']
 out=Path(out or source_root/'artifacts/releases'/('PSLC-Agent-'+version+'-source.zip'))
 output_paths=(out,out.with_suffix('.zip.sha256'),out.with_suffix('.release.json'))
 if any(p.exists() or p.is_symlink() for p in output_paths):raise ValueError('source_output_exists')
 injected=files is not None
 files=dict(collect() if files is None else files)
 if json.loads(files['package.json'])['version']!=version:raise ValueError('distribution_version_mismatch')
 validate_public_payload(files);validate_source_inputs(files)
 manifest={'format':'pslc-source-release-v1','version':version,'database_schema':__import__('backend.store',fromlist=['SCHEMA_VERSION']).SCHEMA_VERSION,'agent_version':__import__('agent').__version__,'weights_included':False,'kind':'local-MVP source distribution with compiled web','production_admitted':False,
  'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]}
 files['release-manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
 out.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
  for name,data in sorted(files.items()):
   info=zipfile.ZipInfo('PSLC-Agent-'+version+'/'+name,date_time=(2026,11,8,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
   archive.writestr(info,data)
 verify_model_free_archive(out,version=version)
 with out.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
 with out.with_suffix('.zip.sha256').open('x',encoding='ascii') as sidecar:sidecar.write(digest+'  '+out.name+'\n')
 result={'version':version,'archive':str(out),'sha256':digest,'files':len(files),'size_bytes':out.stat().st_size,'secret_scan':'not_run_injected_public_files' if injected else 'passed','production_admitted':False}
 with out.with_suffix('.release.json').open('x',encoding='utf-8') as sidecar:sidecar.write(json.dumps(result,indent=2))
 return result
if __name__=='__main__':print(json.dumps(build(sys.argv[1] if len(sys.argv)>1 else None)))
