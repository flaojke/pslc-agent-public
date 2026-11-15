"""Import-safe model-free inventory/extraction. Installer execution is explicit."""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,stat,subprocess,sys,tempfile,zipfile
from scripts.verify_release import verify
from scripts.build_release import public_member,verify_model_free_archive
ROOT=Path(__file__).resolve().parents[1]

def model_free_inventory(manifest):
 version=manifest.get('version');rows=manifest.get('packages')
 if not isinstance(version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',version) or not isinstance(rows,list) or len(rows)!=1:raise ValueError('model_free_inventory_required')
 row=rows[0]
 if row.get('variant')!='Lite' or row.get('weights_included') is not False or manifest.get('model_weights_included') is not False or manifest.get('source_contains_weights') is not False or row.get('name')!='PSLC-Agent-'+version+'-Lite.zip':raise ValueError('model_free_inventory_required')
 if not isinstance(row.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']) or type(row.get('bytes')) is not int or row['bytes']<=0 or row.get('code_sha256')!=manifest.get('code_sha256'):raise ValueError('invalid_distribution_inventory')
 return version,rows

def prepare(*,root=ROOT,release_dir=None,install=False,runner=None):
 root=Path(root).resolve();version=json.loads((root/'package.json').read_text(encoding='utf-8'))['version'];release=Path(release_dir or root/'artifacts/releases'/version).resolve();manifest=json.loads((release/'distribution-manifest.json').read_text(encoding='utf-8'));declared,rows=model_free_inventory(manifest)
 if declared!=version:raise ValueError('distribution_version_mismatch')
 for row in rows:
  source=release/row['name']
  if source.is_symlink() or source.stat().st_size!=row['bytes']:raise ValueError('invalid_distribution_archive')
  checked=verify_model_free_archive(source,version=version,expected_sha256=row['sha256'])
  if checked['variant']!='Lite':raise ValueError('model_free_inventory_required')
 temporary=root/'.local';temporary.mkdir(exist_ok=True);base=Path(tempfile.mkdtemp(prefix='release-clean-'+version+'-',dir=temporary));result={'version':version,'directory':str(base),'variants':{},'installer_executed':False,'weights_initially_present':False}
 for row in rows:
  source=release/row['name'];target=base/row['variant'];target.mkdir()
  with zipfile.ZipFile(source) as archive:
   members=archive.infolist()
   if len(members)>10000 or sum(x.file_size for x in members)>2*1024**3 or len({x.filename.lower() for x in members})!=len(members):raise ValueError('distribution_extract_limit')
   for item in members:
    parts=item.filename.split('/',1)
    if len(parts)!=2 or parts[0]!='PSLC-Agent-'+version or item.is_dir() or stat.S_IFMT(item.external_attr>>16)==stat.S_IFLNK:raise ValueError('unsafe_distribution_member')
    public_member(parts[1]);archive.extract(item,target)
  extracted=target/('PSLC-Agent-'+version);report=verify(extracted)
  if report['status']!='verified':raise ValueError('extracted_distribution_invalid')
  verify_model_free_archive(source,version=version,expected_sha256=row['sha256'])
  data={'root':str(extracted),'archive_sha256':row['sha256'],'archive_bytes':row['bytes'],'code_sha256':row['code_sha256'],'initial_model_present':False,'fresh_dependency_install':False}
  if install:
   command=runner or subprocess.run;shell=shutil.which('pwsh') or shutil.which('powershell')
   if not shell:raise ValueError('powershell_unavailable')
   run=command([shell,'-NoProfile','-File',str(extracted/'scripts/install-release.ps1'),'-PythonExecutable',sys.executable],cwd=extracted,capture_output=True,text=True,timeout=420,creationflags=0x08000000 if os.name=='nt' else 0)
   if run.returncode:raise ValueError('clean_installer_failed')
   python=extracted/'.venv/Scripts/python.exe';check=command([str(python),'-c','import sys,fastapi,sqlalchemy,psutil,cryptography,httpx;print(sys.version.split()[0])'],cwd=extracted,capture_output=True,text=True,timeout=30,creationflags=0x08000000 if os.name=='nt' else 0)
   if check.returncode:raise ValueError('clean_dependencies_failed')
   data.update(python=str(python),fresh_dependency_install=True,python_version=check.stdout.strip());result['installer_executed']=True
  result['variants'][row['variant']]=data
 return result

def main():
 parser=argparse.ArgumentParser(description='Validate/extract current model-free Lite. --install explicitly runs a clean installer; no model download or API/runtime startup.')
 parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--install',action='store_true');args=parser.parse_args()
 result=prepare(root=args.root,install=args.install)
 if args.install:
  (args.root/'.local/clean-distributions.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps(result),flush=True)
if __name__=='__main__':main()
