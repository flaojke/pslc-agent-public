"""Public-only release build and current-SHA fresh native QA. No VM/state import."""
from pathlib import Path
from dataclasses import replace
import argparse,hashlib,importlib.util,json,os,socket,subprocess,sys,threading,uuid,zipfile
PROJECT=Path(__file__).resolve().parents[1]

def digest(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def write(path,value):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 Path(path).write_text(json.dumps(value,indent=2),encoding='utf-8')

def build(out,proof):
 sys.path.insert(0,str(PROJECT))
 from unittest.mock import patch
 from scripts.build_release import collect
 from scripts.build_distributions import build as package
 def forbidden(*args,**kwargs):raise RuntimeError('working_protected_state_access_forbidden')
 with patch('backend.config.load_settings',forbidden),patch('backend.model_connection.ModelConnection',forbidden),patch('backend.secret_store.SecretStore',forbidden):
  public=collect(scan_private=False)
  result=package(public_files=public,out=Path(out))
 value={'format':'pslc-public-release-proof-v1','version':result['version'],'output':str(Path(out).resolve()),'packages':result['packages'],'assets':result['github_assets'],'public_source_files':len(public),'code_sha256':result['code_sha256'],'private_known_key_scan':False,'protected_state_access_guarded':True,'model_cache_read':False,'weights_included':False,'vm_created':False,'published':False}
 write(proof,value);print(json.dumps(value),flush=True)

def extract(release,work,descriptor):
 sys.path.insert(0,str(PROJECT))
 from scripts.build_release import verify_model_free_archive,public_member
 from scripts.verify_release import verify
 release=Path(release).resolve();work=Path(work).resolve()
 owner=json.loads((work/'owner.json').read_text(encoding='utf-8'))
 if owner.get('format')!='pslc-owned-release-qa-v1' or owner.get('project')!=str(PROJECT):raise ValueError('foreign_qa_work')
 m=json.loads((release/'distribution-manifest.json').read_text(encoding='utf-8'));rows=m['packages']
 if len(rows)!=1 or rows[0]['variant']!='Lite' or rows[0]['weights_included'] is not False:raise ValueError('not_model_free_inventory')
 row=rows[0];archive=release/row['name'];version=m['version']
 verify_model_free_archive(archive,version=version,expected_sha256=row['sha256'])
 target=work/('clean-'+uuid.uuid4().hex[:8]);target.mkdir()
 with zipfile.ZipFile(archive) as z:
  if len(z.infolist())>10000 or sum(i.file_size for i in z.infolist())>2*1024**3:raise ValueError('extract_limit')
  for item in z.infolist():
   head,name=item.filename.split('/',1);public_member(name)
   if head!='PSLC-Agent-'+version:raise ValueError('foreign_archive')
   z.extract(item,target)
 root=target/('PSLC-Agent-'+version);checked=verify(root)
 verify_model_free_archive(archive,version=version,expected_sha256=row['sha256'])
 if (root/'.local').exists() or (root/'models').exists():raise ValueError('not_clean_model_free_extract')
 value={'format':'pslc-clean-release-qa-v1','project':str(PROJECT),'root':str(root),'archive':str(archive),'archive_sha256':row['sha256'],'manifest_sha256':digest(root/'release-manifest.json'),'version':version,'identity':uuid.uuid4().hex,'verified':checked,'models_initially_present':False,'state_imported':False}
 write(descriptor,value);print(json.dumps(value),flush=True)

def serve(descriptor):
 d=json.loads(Path(descriptor).read_text(encoding='utf-8'));root=Path(d['root']).resolve()
 if d.get('format')!='pslc-clean-release-qa-v1' or d.get('project')!=str(PROJECT) or not root.is_relative_to(PROJECT/'artifacts/qa'):raise ValueError('foreign_qa_descriptor')
 if digest(d['archive'])!=d['archive_sha256'] or digest(root/'release-manifest.json')!=d['manifest_sha256']:raise ValueError('changed_qa_artifact')
 spec=importlib.util.spec_from_file_location('_pslc_trusted_install_guard',PROJECT/'scripts/install_bootstrap.py')
 guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
 if guard.manifest(root,allow_runtime=True)!=d['manifest_sha256']:raise ValueError('changed_qa_declared_files')
 if not (root/'.local').is_dir() or not (root/'.pslc-install.json').is_file():raise ValueError('qa_clean_install_required')
 if (root/'models').exists() or (root/'.local/model/connection.enc').exists():raise ValueError('qa_must_start_without_models_or_saved_provider')
 for key in list(os.environ):
  if key.startswith(('PSLC_','LLAMA_')) or key in ('PYTHONPATH','PYTHONHOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'):os.environ.pop(key,None)
 os.chdir(root);sys.path.insert(0,str(root))
 import httpx,uvicorn,backend.app,backend.config
 from backend.config import Settings
 if Path(backend.app.__file__).resolve()!=root/'backend/app.py' or backend.config.ROOT.resolve()!=root:raise ValueError('qa_source_not_isolated')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 origin='http://127.0.0.1:'+str(port);local=root/'.local';attempts={'model_download':0,'runtime_process':0,'external_request':0}
 def deny_external(request):attempts['external_request']+=1;raise RuntimeError('external_request_not_part_of_release_smoke')
 original_popen=subprocess.Popen
 def deny_process(*args,**kwargs):attempts['runtime_process']+=1;raise RuntimeError('runtime_process_not_part_of_model_free_smoke')
 subprocess.Popen=deny_process
 settings=Settings('public-unused-clean-qa-token','sqlite:///'+(local/'fresh-qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=origin,knowledge_enabled=False,local_models_enabled=True,model_config_path=str(local/'model/connection.enc'),demo_config_path=str(local/'demo/identity.enc'),system_preferences_path=str(local/'system/preferences.json'),schedule_vault_path=str(local/'schedules'),local_models_dir=str(root/'models'),local_runtime_dir=str(root/'runtime'),local_catalog_path=str(root/'config/local-models.json'),local_runtime_manifest_path=str(root/'config/local-runtime.json'))
 app=backend.app.create_app(settings,httpx.MockTransport(deny_external))
 def deny_download(*args,**kwargs):attempts['model_download']+=1;raise RuntimeError('actual_model_download_not_part_of_release_smoke')
 app.state.model_connection.local.request_download=deny_download
 server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,proxy_headers=False,workers=1,log_level='warning'))
 status_path=Path(descriptor).with_name('server-status.json')
 status={'phase':'starting','url':origin,'pid':os.getpid(),'version':app.version,'artifact_sha256':d['archive_sha256'],'root':str(root),'executable':sys.executable,'source_isolated':True,'attempts':attempts,'state_imported':False}
 write(status_path,status)
 def stop_on_stdin():
  try:sys.stdin.buffer.readline()
  finally:server.should_exit=True
 threading.Thread(target=stop_on_stdin,daemon=True).start()
 try:server.run()
 finally:
  subprocess.Popen=original_popen
  status.update(phase='stopped',attempts=attempts,model_files_present=(root/'models').exists())
  write(status_path,status)

def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True)
 b=s.add_parser('build');b.add_argument('--out',required=True);b.add_argument('--proof',required=True)
 e=s.add_parser('extract');e.add_argument('--release',required=True);e.add_argument('--work',required=True);e.add_argument('--descriptor',required=True)
 v=s.add_parser('serve');v.add_argument('--descriptor',required=True)
 a=p.parse_args()
 try:
  if a.action=='build':build(a.out,a.proof)
  elif a.action=='extract':extract(a.release,a.work,a.descriptor)
  else:serve(a.descriptor)
 except Exception as exc:
  if a.action=='serve':
   write(Path(a.descriptor).with_name('server-error.json'),{'phase':'failed','type':type(exc).__name__,'winerror':getattr(exc,'winerror',None)})
  print(json.dumps({'error':'release_verification_failed','type':type(exc).__name__}),flush=True);return 2
 return 0
if __name__=='__main__':raise SystemExit(main())
