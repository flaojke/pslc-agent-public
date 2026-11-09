"""Clean extracted Full/Lite installations. No production .local, keys or models copied."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,shutil,subprocess,sys,tempfile,zipfile
from scripts.verify_release import verify
ROOT=Path(__file__).resolve().parents[1];version=json.loads((ROOT/'package.json').read_text())['version']
base=Path(tempfile.mkdtemp(prefix='release-clean-'+version+'-',dir=ROOT/'.local'));result={'version':version,'directory':str(base),'variants':{}}
release=ROOT/'artifacts/releases'/version;manifest=json.loads((release/'distribution-manifest.json').read_text())
assert len({x['code_sha256'] for x in manifest['packages']})==1
shell=shutil.which('pwsh') or shutil.which('powershell');assert shell
for variant in ('Full','Lite'):
 row=next(x for x in manifest['packages'] if x['variant']==variant);source=release/row['name']
 with source.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==row['sha256']
 target=base/variant;target.mkdir()
 with zipfile.ZipFile(source) as archive:
  assert sum(x.file_size for x in archive.infolist())<6*1024**3
  for item in archive.infolist():
   name=PurePosixPath(item.filename)
   assert not name.is_absolute() and '..' not in name.parts and '\\' not in item.filename and ':' not in item.filename and name.parts[0]=='PSLC-Agent-'+version
   archive.extract(item,target)
 root=target/('PSLC-Agent-'+version);report=verify(root);assert report['status']=='verified'
 assert (root/'models/Qwen3.5-4B-Q4_K_M.gguf').is_file()==(variant=='Full')
 print(json.dumps({'phase':'fresh_install','variant':variant,'archive_verified':True}),flush=True)
 run=subprocess.run([shell,'-NoProfile','-File',str(root/'scripts/install-release.ps1'),'-PythonExecutable',sys.executable],cwd=root,capture_output=True,text=True,timeout=420,creationflags=0x08000000 if os.name=='nt' else 0)
 if run.returncode:
  error='package_unavailable' if 'No matching distribution' in run.stderr or 'Could not find a version' in run.stderr else 'network_tls' if 'CERTIFICATE_VERIFY_FAILED' in run.stderr else 'installer_failed'
  raise RuntimeError(variant+':'+error)
 python=root/'.venv/Scripts/python.exe'
 check=subprocess.run([str(python),'-c','import sys,fastapi,sqlalchemy,psutil,cryptography,httpx;print(sys.version.split()[0])'],cwd=root,capture_output=True,text=True,timeout=30,creationflags=0x08000000)
 assert check.returncode==0,'Fresh dependencies import failed'
 result['variants'][variant]={'root':str(root),'python':str(python),'archive_sha256':row['sha256'],'archive_bytes':row['bytes'],'code_sha256':row['code_sha256'],'fresh_dependency_install':True,'python_version':check.stdout.strip(),'initial_model_present':variant=='Full'}
 print(json.dumps({'phase':'fresh_install_passed','variant':variant,'python':check.stdout.strip()}),flush=True)
(ROOT/'.local/clean-distributions.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
(ROOT/'docs/qa/local-model/clean-installs.json').write_text(json.dumps({'scope':'actual clean Windows venv/install-release.ps1, no production state','version':version,'same_code':True,'variants':{k:{key:value for key,value in row.items() if key not in ('root','python')} for k,row in result['variants'].items()}},indent=2),encoding='utf-8')
print(json.dumps({'clean_variants_verified':2,'source_state_copied':False,'weights_in_lite_initially':False}),flush=True)
