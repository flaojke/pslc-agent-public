"""Disposable Windows-only real venv/pip fixtures. No network or real application state."""
from pathlib import Path
import hashlib,importlib.util,json,os,shutil,sqlite3,struct,subprocess,sys,sysconfig,time,zipfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('install_bootstrap_test',ROOT/'scripts/install_bootstrap.py')
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)
SHELL=shutil.which('pwsh') or shutil.which('powershell')
pytestmark=pytest.mark.skipif(os.name!='nt' or not SHELL,reason='Windows PowerShell installer integration')

def fixture_release(tmp_path):
 root=tmp_path/'release with spaces';(root/'scripts').mkdir(parents=True)
 for name in ('install-release.ps1','install_bootstrap.py'):(root/'scripts'/name).write_bytes((ROOT/'scripts'/name).read_bytes())
 setup=(ROOT/'scripts/setup_dev.py').read_text(encoding='utf-8')+"\nif not (ROOT/'.local/allow-setup').is_file(): raise SystemExit(24)\n"
 (root/'scripts/setup_dev.py').write_text(setup,encoding='utf-8')
 (root/'requirements.lock.txt').write_text('pslc-install-fixture==0.1\n')
 (root/'config').mkdir();data=json.loads((ROOT/'config/local-models.json').read_text(encoding='utf-8'))
 for row in data['models']:
  content=('PUBLIC-GGUF-'+row['id']).encode();row.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
 (root/'config/local-models.json').write_text(json.dumps(data),encoding='utf-8')
 rows=[]
 for p in sorted(root.rglob('*')):
  if p.is_file():rows.append({'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 (root/'release-manifest.json').write_text(json.dumps({'format':'pslc-source-release-v1','version':'fixture','files':rows}),encoding='utf-8')
 return root

def call(root,wheelhouse=None,resume=False):
 cmd=[SHELL,'-NoProfile','-File',str(root/'scripts/install-release.ps1'),'-PythonExecutable',sys.executable]
 if resume:cmd+=['-Resume']
 if wheelhouse:cmd+=['-Wheelhouse',str(wheelhouse)]
 p=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=120)
 return p

def wheel(folder):
 folder.mkdir(exist_ok=True)
 files={'pslc_install_fixture/__init__.py':'PUBLIC_FIXTURE=True\n',
 'pslc_install_fixture-0.1.dist-info/METADATA':'Metadata-Version: 2.1\nName: pslc-install-fixture\nVersion: 0.1\n',
 'pslc_install_fixture-0.1.dist-info/WHEEL':'Wheel-Version: 1.0\nGenerator: public-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
 files['pslc_install_fixture-0.1.dist-info/RECORD']=''.join(name+',,\n' for name in files)+'pslc_install_fixture-0.1.dist-info/RECORD,,\n'
 with zipfile.ZipFile(folder/'pslc_install_fixture-0.1-py3-none-any.whl','w') as z:
  for name,data in files.items():z.writestr(name,data)

def error(p,code):
 assert p.returncode!=0 and '"error": "'+code+'"' in p.stdout,(p.returncode,p.stdout[-300:],p.stderr[-300:])

def test_real_offline_failure_integrity_resume_and_preservation(tmp_path):
 root=fixture_release(tmp_path);wheels=tmp_path/'offline wheels';wheels.mkdir()
 p=call(root,wheels);error(p,'dependency_installation_failed')
 receipt=json.loads((root/boot.RECEIPT).read_text());assert receipt['phase']=='venv_ready'
 assert (root/'.venv/Scripts/python.exe').is_file()
 owned=(root/'.venv'/boot.MARKER).read_bytes()
 # Integrity failures must not start pip or replace the existing environment.
 original=(root/'release-manifest.json').read_bytes();(root/'release-manifest.json').write_bytes(original+b' ')
 error(call(root,wheels,True),'release_manifest_changed');(root/'release-manifest.json').write_bytes(original)
 req=(root/'requirements.lock.txt').read_bytes();(root/'requirements.lock.txt').write_bytes(req+b'# changed\n')
 error(call(root,wheels,True),'release_file_changed');(root/'requirements.lock.txt').write_bytes(req)
 assert (root/'.venv'/boot.MARKER).read_bytes()==owned
 # Actual setup_dev body sees pre-existing synthetic local state and must retain it.
 local=root/'.local';(local/'model').mkdir(parents=True);settings=local/'settings.json';settings.write_text('{"public_fixture":"retained"}')
 (local/'model/connection.enc').write_bytes(b'PUBLIC-SYNTHETIC-PROFILE')
 with sqlite3.connect(local/'pslc.sqlite3') as c:c.execute('CREATE TABLE accounts (id TEXT PRIMARY KEY,password_hash TEXT)');c.execute("INSERT INTO accounts VALUES ('fixture','public-fixture-verifier')")
 protected={p.relative_to(local):p.read_bytes() for p in local.rglob('*') if p.is_file()}
 wheel(wheels)
 p=call(root,wheels,True);error(p,'local_setup_failed')
 assert json.loads((root/boot.RECEIPT).read_text())['phase']=='dependencies_ready'
 assert all((local/name).read_bytes()==data for name,data in protected.items())
 (local/'allow-setup').write_text('public-test-gate')
 p=call(root,wheels,True);assert p.returncode==0,p.stderr[-500:]
 assert json.loads((root/boot.RECEIPT).read_text())['phase']=='completed'
 probe=subprocess.run([str(root/'.venv/Scripts/python.exe'),'-I','-c','import pslc_install_fixture;assert pslc_install_fixture.PUBLIC_FIXTURE'],capture_output=True,timeout=20)
 assert probe.returncode==0 and (root/'.venv/Lib/site-packages/pslc_install_fixture/__init__.py').is_file()
 assert all((local/name).read_bytes()==data for name,data in protected.items())
 # Completed Lite resume permits only pinned downloaded GGUF/bounded partials.
 models=root/'models';models.mkdir();catalog=json.loads((root/'config/local-models.json').read_text())
 first,second=catalog['models'];download=models/first['filename'];download.write_bytes(('PUBLIC-GGUF-'+first['id']).encode())
 partial=models/(second['filename']+'.partial');partial.write_bytes(b'GGUF-PART')
 # Completed resume must not rerun setup or provisioning.
 (local/'allow-setup').unlink()
 p=call(root,tmp_path/'missing wheelhouse',True);assert p.returncode==0 and 'already_completed' in p.stdout
 assert all((local/name).read_bytes()==data for name,data in protected.items())
 assert (root/'.venv'/boot.MARKER).read_bytes()==owned
 assert hashlib.sha256(download.read_bytes()).hexdigest()==first['sha256'] and partial.read_bytes()==b'GGUF-PART'
 p=call(root,wheels);error(p,'existing_installation_use_resume_or_update')

def test_foreign_environment_is_never_claimed_or_removed(tmp_path):
 root=fixture_release(tmp_path);env=root/'.venv';env.mkdir();sentinel=env/'foreign.txt';sentinel.write_text('PUBLIC-FOREIGN-ENV')
 error(call(root),'existing_installation_use_resume_or_update')
 error(call(root,resume=True),'resume_receipt_missing')
 assert sentinel.read_text()=='PUBLIC-FOREIGN-ENV' and not (root/boot.RECEIPT).exists()

@pytest.mark.parametrize('problem',['version','architecture'])
def test_unsupported_python_refused_before_filesystem_state(tmp_path,monkeypatch,problem):
 root=fixture_release(tmp_path)
 before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
 if problem=='version':monkeypatch.setattr(boot.sys,'version_info',(3,11,9))
 else:monkeypatch.setattr(boot.struct,'calcsize',lambda _:4)
 with pytest.raises(boot.InstallError,match='python_312_x64_required'):boot.install(root)
 assert {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before

def test_root_binding_and_parallel_lock_refused(tmp_path):
 root=fixture_release(tmp_path);digest=hashlib.sha256((root/'release-manifest.json').read_bytes()).hexdigest()
 rec=boot.identity(root,digest);rec['root']=os.path.normcase(str(tmp_path/'another-root'))
 (root/boot.RECEIPT).write_text(json.dumps(rec))
 error(call(root,resume=True),'installation_root_changed')
 rec['root']=os.path.normcase(str(root.resolve()));(root/boot.RECEIPT).write_text(json.dumps(rec))
 with boot.installation_lock(root):
  with pytest.raises(boot.InstallError,match='installation_busy'):boot.install(root,resume=True)
 assert not (root/'.venv').exists()

def test_foreign_matching_root_but_wrong_environment_marker(tmp_path):
 root=fixture_release(tmp_path);digest=hashlib.sha256((root/'release-manifest.json').read_bytes()).hexdigest();rec=boot.identity(root,digest)
 (root/boot.RECEIPT).write_text(json.dumps(rec));env=root/'.venv';env.mkdir();(env/boot.MARKER).write_text('{"foreign":true}');(env/'keep.txt').write_text('PUBLIC-KEEP')
 error(call(root,resume=True),'foreign_environment_refused')
 assert (env/'keep.txt').read_text()=='PUBLIC-KEEP' and json.loads((env/boot.MARKER).read_text())=={'foreign':True}

@pytest.mark.parametrize('link_name',['.venv','models'])
def test_actual_reparse_environment_refused_without_target_writes(tmp_path,link_name):
 root=fixture_release(tmp_path);target=tmp_path/'foreign target';target.mkdir();(target/'keep.txt').write_text('PUBLIC-TARGET')
 script=tmp_path/'junction.ps1';script.write_text('param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n',encoding='utf-8')
 assert target.resolve().is_relative_to(tmp_path.resolve()) and (root/link_name).absolute().is_relative_to(tmp_path.resolve())
 digest=hashlib.sha256((root/'release-manifest.json').read_bytes()).hexdigest();(root/boot.RECEIPT).write_text(json.dumps(boot.identity(root,digest)))
 subprocess.run([SHELL,'-NoProfile','-File',str(script),str(root/link_name),str(target)],capture_output=True,check=True)
 error(call(root,resume=True),'reparse_path_refused')
 assert list(p.name for p in target.iterdir())==['keep.txt'] and (target/'keep.txt').read_text()=='PUBLIC-TARGET'


def test_atomic_receipt_collision_keeps_preexisting_file(tmp_path,monkeypatch):
 root=tmp_path/'atomic';root.mkdir();pending=root/('.pslc-install.pending-'+('1'*32)+'.json');pending.write_text('PUBLIC-UNRELATED-FILE')
 monkeypatch.setattr(boot.secrets,'token_hex',lambda n:'1'*(2*n))
 with pytest.raises(FileExistsError):boot.atomic_json(root,root/boot.RECEIPT,{'fixture':True})
 assert pending.read_text()=='PUBLIC-UNRELATED-FILE' and not (root/boot.RECEIPT).exists()


def test_release_changed_during_provisioning_never_runs_setup(tmp_path,monkeypatch):
 root=fixture_release(tmp_path);original=boot.command;setup_calls=[]
 def command(args,cwd,error):
  if 'pip' in args:
   p=root/'scripts/setup_dev.py';p.write_bytes(p.read_bytes()+b'\n# changed during provisioning\n');return
  if args[-1]=='scripts/setup_dev.py':setup_calls.append(True)
  return original(args,cwd,error)
 monkeypatch.setattr(boot,'command',command)
 with pytest.raises(boot.InstallError,match='release_file_changed'):boot.install(root)
 assert not setup_calls and not (root/'.local').exists()
 assert json.loads((root/boot.RECEIPT).read_text())['phase']=='dependencies_ready'


def test_forced_helper_exit_stops_owned_provisioning_tree_and_resumes(tmp_path):
 root=fixture_release(tmp_path);local=root/'.local';wheels=tmp_path/'wheels';wheels.mkdir()
 setup=root/'scripts/setup_dev.py'
 source=setup.read_text(encoding='utf-8').replace("if not (ROOT/'.local/allow-setup').is_file(): raise SystemExit(24)", "")
 child_code="import sys,time;from pathlib import Path;p=Path(sys.argv[1]);"+"\nwhile True:p.write_text(str(time.monotonic()));time.sleep(.02)"
 source+="\nimport subprocess,time,os\n"+"if not (d/'finish').exists():\n"+" child=subprocess.Popen([sys.executable,'-c',"+repr(child_code)+",str(d/'heartbeat')],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"+" (d/'ready').write_text(json.dumps({'setup':os.getpid(),'child':child.pid}))\n"+" while not (d/'finish').exists():time.sleep(.02)\n"
 setup.write_text(source,encoding='utf-8')
 data=json.loads((root/'release-manifest.json').read_text())
 for row in data['files']:
  if row['path']=='scripts/setup_dev.py':row.update(size=setup.stat().st_size,sha256=hashlib.sha256(setup.read_bytes()).hexdigest())
 (root/'release-manifest.json').write_text(json.dumps(data),encoding='utf-8')
 error(call(root,wheels),'dependency_installation_failed')
 local.mkdir();(local/'settings.json').write_text('{"public_fixture":"retained"}');wheel(wheels)
 helper=subprocess.Popen([sys.executable,str(root/'scripts/install_bootstrap.py'),'--root',str(root),'--resume','--wheelhouse',str(wheels)],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  until=time.monotonic()+30
  while not (local/'ready').exists() or not (local/'heartbeat').exists():
   assert helper.poll() is None,'fixture helper unexpectedly stopped'
   assert time.monotonic()<until,'fixture setup did not start'
   time.sleep(.05)
  helper.kill();helper.wait(timeout=10)
  # The nested job owns both setup and its child. A resumed helper must not
  # overlap any previous writer even after an ungraceful parent termination.
  before=(local/'heartbeat').read_bytes();time.sleep(.25)
  assert (local/'heartbeat').read_bytes()==before
  (local/'finish').write_text('PUBLIC-COMPLETE')
  p=call(root,wheels,True);assert p.returncode==0,p.stderr[-300:]
  assert json.loads((root/boot.RECEIPT).read_text())['phase']=='completed'
  assert (local/'settings.json').read_text()=='{"public_fixture":"retained"}'
 finally:
  if helper.poll() is None:helper.kill();helper.wait(timeout=10)


@pytest.mark.parametrize('extra',['unknown.gguf','Qwen3.5-4B-Q4_K_M.gguf.partial.json','nested/Qwen3.5-4B-Q4_K_M.gguf','notes.txt'])
def test_runtime_models_do_not_allow_arbitrary_extra_files(tmp_path,extra):
 root=fixture_release(tmp_path);p=root/'models'/extra;p.parent.mkdir(parents=True);p.write_bytes(b'PUBLIC-EXTRA')
 with pytest.raises(boot.InstallError,match='release_extra_or_missing_files'):boot.manifest(root,True)
 assert p.read_bytes()==b'PUBLIC-EXTRA'


@pytest.mark.parametrize('corruption',['final_hash','partial_oversize','declared_full_weight'])
def test_runtime_model_exception_never_weakens_integrity(tmp_path,corruption):
 root=fixture_release(tmp_path);row=json.loads((root/'config/local-models.json').read_text())['models'][0]
 p=root/'models'/row['filename'];p.parent.mkdir();content=('PUBLIC-GGUF-'+row['id']).encode()
 if corruption=='partial_oversize':
  p=p.with_suffix('.gguf.partial');p.write_bytes(b'x'*(row['bytes']+1))
 elif corruption=='final_hash':p.write_bytes(b'x'*len(content))
 else:
  p.write_bytes(content);m=root/'release-manifest.json';data=json.loads(m.read_text());data['files'].append({'path':p.relative_to(root).as_posix(),'size':len(content),'sha256':hashlib.sha256(content).hexdigest()});m.write_text(json.dumps(data))
  assert boot.manifest(root,True)==hashlib.sha256(m.read_bytes()).hexdigest()
  p.write_bytes(b'x'*len(content))
 code='release_file_changed' if corruption=='declared_full_weight' else 'runtime_model_file_changed'
 with pytest.raises(boot.InstallError,match=code):boot.manifest(root,True)
