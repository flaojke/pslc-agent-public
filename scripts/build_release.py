"""Deterministic source distribution: allowlisted source + compiled web, no local state."""
from pathlib import Path
import hashlib,json,os,sys,zipfile,re
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def collect():
 files={}
 version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
 galleries=sorted(p.name for p in (ROOT/'release-dashboards').glob('*_MSK_v'+version) if p.is_dir())
 current_gallery=galleries[-1] if galleries else None
 safe_qa={'settings-guides','reversible-task-plans','scheduled-agent-work','local-model','compact-historical-messages'}
 directories=('backend','agent','frontend/src','frontend/dist','tests','scripts','openspec','qa','docs','deploy','config','licenses','.agents/skills','release-dashboards')
 allowed={'.py','.ps1','.cjs','.js','.md','.json','.yaml','.yml','.ts','.tsx','.css','.html','.png','.svg','.txt','.service','.pem','.sh'}
 for folder in directories:
  for p in (ROOT/folder).rglob('*'):
   if not p.is_file() or p.suffix not in allowed or p.is_symlink() or any(x in p.parts for x in ('__pycache__','.pytest_cache','node_modules','.local','.git','.venv')):continue
   # No signing keys or local certificates, even inside an allowlisted folder.
   if p.suffix=='.pem':continue
   relative=p.relative_to(ROOT).as_posix();parts=relative.split('/')
   if relative.startswith(('docs/qa/','docs/baseline-v0.2/','docs/design/','docs/roadmap/','release-dashboards/','openspec/changes/')) or relative in ('openspec/MASTER_SPEC.md','docs/ARCHITECTURE.md','docs/ROADMAP.md','docs/SECURITY_APPROVALS.md','docs/REMAINING_WORK.md','docs/GITHUB_HANDOFF.md') or relative.startswith(('docs/VERIFICATION','docs/RELEASE_NOTES_')):continue
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
 return files
def build(out=None):
 version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
 files=collect()
 manifest={'format':'pslc-source-release-v1','version':version,'database_schema':__import__('backend.store',fromlist=['SCHEMA_VERSION']).SCHEMA_VERSION,'agent_version':__import__('agent').__version__,'kind':'local-MVP source distribution with compiled web','production_admitted':False,
  'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]}
 files['release-manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
 out=Path(out or ROOT/'artifacts/releases'/('PSLC-Agent-'+version+'-source.zip'));out.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
  for name,data in sorted(files.items()):
   info=zipfile.ZipInfo('PSLC-Agent-'+version+'/'+name,date_time=(2026,11,8,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
   archive.writestr(info,data)
 digest=hashlib.sha256(out.read_bytes()).hexdigest()
 out.with_suffix('.zip.sha256').write_text(digest+'  '+out.name+'\n',encoding='ascii')
 result={'version':version,'archive':str(out),'sha256':digest,'files':len(files),'size_bytes':out.stat().st_size,'secret_scan':'passed','production_admitted':False}
 out.with_suffix('.release.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 return result
if __name__=='__main__':print(json.dumps(build(sys.argv[1] if len(sys.argv)>1 else None)))
