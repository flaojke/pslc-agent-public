"""Windows launch policy fixtures; no security changes, real loader dialogs or raw logs."""
import ctypes,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
import psutil,pytest
from fastapi.testclient import TestClient
from backend import runtime_host as host
from backend.runtime_process import POLICY_ERROR,WORKER_POLICY_BLOCKED,policy_launch_error,policy_native_status,worker_error,worker_result
from backend.local_inference import LocalModels
from backend.integrations import IntegrationError,Integrations
from backend.model_connection import ModelConnection
from backend.app import create_app
from test_local_inference import fixture,ORIGIN
ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('status',[0xC0E90002,0xC0000428,0xC0E90002-2**32,0xC0000428-2**32])
def test_exact_native_policy_status_maps_to_fixed_worker_code(status):
 assert policy_native_status(status) and worker_result(status)==77 and worker_error(77,'generic')==POLICY_ERROR

@pytest.mark.parametrize('status',[0,1,2,77,4551,577,0xC0000005,None])
def test_other_native_codes_are_not_policy_errors(status):
 assert not policy_native_status(status)
 assert worker_result(status)==(0 if status==0 else 2)

@pytest.mark.parametrize('code',[4551,577,5,193])
def test_creation_error_winerror_is_distinct_from_native_exit(code):
 error=OSError('PUBLIC-OS-FIXTURE-NOT-EXPOSED');error.winerror=code
 assert policy_launch_error(error)==(code in (4551,577))
 assert not policy_launch_error(RuntimeError(str(code)))

@pytest.mark.parametrize('creation_error,native_code,expected',[(4551,None,77),(577,None,77),(193,None,2),(None,0xC0E90002,77),(None,0xC0000428,77),(None,77,2),(None,4551,2),(None,0,0)])
def test_worker_maps_only_recognized_source_errors(monkeypatch,creation_error,native_code,expected):
 order=[]
 monkeypatch.setattr(host.sys,'argv',['runtime_host','--parent','1','--parent-created','1','--probe','fixture'])
 monkeypatch.setattr(host,'parent_alive',lambda *_:True)
 monkeypatch.setattr(host.signal,'signal',lambda *_:None)
 monkeypatch.setattr(host,'windows_own_job',lambda:order.append('own') or 123)
 monkeypatch.setattr(host,'windows_error_mode',lambda:order.append('mode'))
 class Child:
  def poll(self):return native_code
 def popen(*args,**kwargs):
  order.append('spawn')
  if creation_error:
   error=OSError('PUBLIC-NATIVE-DETAIL');error.winerror=creation_error;raise error
  return Child()
 monkeypatch.setattr(host.subprocess,'Popen',popen)
 assert host.main()==expected
 assert order==(['own','mode','spawn'] if os.name=='nt' else ['spawn'])


def worker_args(command,probe=True,timeout=5,parent=None):
 parent=parent or psutil.Process()
 return [sys.executable,'-m','backend.runtime_host','--parent',str(parent.pid),'--parent-created',str(parent.create_time()),*(['--probe','--probe-timeout',str(timeout)] if probe else []),*command]

def public_env():return {k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','LANG')}

def launch(args):return subprocess.Popen(args,cwd=ROOT,env=public_env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**({'creationflags':0x08000000} if os.name=='nt' else {}))

def wait_file(path,process,seconds=15):
 deadline=time.monotonic()+seconds
 while not path.is_file():
  assert process.poll() is None,'owned fixture process stopped before ready'
  assert time.monotonic()<deadline,'owned fixture was not ready'
  time.sleep(.03)

def pulse_command(tmp_path):
 heartbeat=tmp_path/'heartbeat'
 leaf="import sys,time;from pathlib import Path;p=Path(sys.argv[1]);\nwhile True:p.write_text(str(time.monotonic()));time.sleep(.02)"
 code="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(leaf)+","+repr(str(heartbeat))+"]);time.sleep(60)"
 return [sys.executable,'-c',code],heartbeat

@pytest.mark.skipif(os.name!='nt',reason='Win32 inherited error mode')
def test_actual_safe_child_inherits_mode_and_api_mode_is_unchanged(tmp_path):
 k=ctypes.WinDLL('kernel32');k.GetErrorMode.restype=ctypes.c_uint
 before=k.GetErrorMode();out=tmp_path/'mode.json'
 code="import ctypes,json;from pathlib import Path;k=ctypes.WinDLL('kernel32');k.GetErrorMode.restype=ctypes.c_uint;Path("+repr(str(out))+").write_text(json.dumps({'mode':k.GetErrorMode()}))"
 p=launch(worker_args([sys.executable,'-c',code]));assert p.wait(timeout=15)==0
 assert json.loads(out.read_text())['mode']&3==3 and k.GetErrorMode()==before

@pytest.mark.skipif(os.name!='nt',reason='Windows status DWORD')
@pytest.mark.parametrize('status',[0xC0E90002,0xC0000428,77])
def test_actual_safe_exitprocess_child_uses_fixed_mapping(status):
 code='import ctypes;ctypes.windll.kernel32.ExitProcess('+str(status)+')'
 p=launch(worker_args([sys.executable,'-c',code]));assert p.wait(timeout=15)==(2 if status==77 else 77)

@pytest.mark.skipif(os.name!='nt',reason='Windows nested self-job ownership')
@pytest.mark.parametrize('ending',['timeout','worker_killed','parent_killed'])
def test_owned_worker_tree_stops_and_unrelated_process_is_preserved(tmp_path,ending):
 command,heartbeat=pulse_command(tmp_path);foreign=launch([sys.executable,'-c','import time;time.sleep(60)']);owner=None;worker=None
 try:
  if ending=='parent_killed':
   ready=tmp_path/'worker.json'
   # Disposable owner passes its own birth to its worker; no arbitrary PID kill.
   prefix=[sys.executable,'-m','backend.runtime_host']
   code="import json,os,subprocess,sys,time,psutil;from pathlib import Path;p=psutil.Process();child=subprocess.Popen("+repr(prefix)+"+['--parent',str(p.pid),'--parent-created',str(p.create_time())]+"+repr(command)+");Path("+repr(str(ready))+").write_text(json.dumps({'pid':child.pid}));time.sleep(60)"
   owner=launch([sys.executable,'-c',code]);wait_file(ready,owner);wait_file(heartbeat,owner);owner.kill();owner.wait(timeout=10)
  else:
   worker=launch(worker_args(command,probe=ending=='timeout',timeout=2));wait_file(heartbeat,worker)
   if ending=='worker_killed':worker.kill()
   assert worker.wait(timeout=15)!=0
  # Job teardown/watchdog is bounded; once stable the old writer cannot race retry.
  time.sleep(.4);before=heartbeat.read_bytes();time.sleep(.25);assert heartbeat.read_bytes()==before
  assert foreign.poll() is None
 finally:
  for p in (worker,owner,foreign):
   if p and p.poll() is None:p.kill();p.wait(timeout=10)

@pytest.mark.skipif(os.name!='nt',reason='Windows owned probe cleanup')
def test_api_close_cancels_actual_owned_probe(tmp_path):
 s,_=fixture(tmp_path)
 m=LocalModels(s);command,heartbeat=pulse_command(tmp_path);args=worker_args(command,probe=True,timeout=5);errors=[]
 def run():
  try:m._version_probe(args,public_env(),m.default)
  except IntegrationError as e:errors.append(str(e))
 thread=threading.Thread(target=run);thread.start()
 deadline=time.monotonic()+15
 while not heartbeat.is_file():
  assert thread.is_alive() and time.monotonic()<deadline;time.sleep(.03)
 m.close();thread.join(timeout=15);assert not thread.is_alive() and errors==['local_runtime_stopped'] and m.process is None
 before=heartbeat.read_bytes();time.sleep(.25);assert heartbeat.read_bytes()==before


def test_local_policy_error_status_http502_and_no_fallback(tmp_path,monkeypatch):
 s,_=fixture(tmp_path,ready=True);app=create_app(s);m=app.state.model_connection.local
 monkeypatch.setattr(m,'warmup',lambda *_:None)
 calls=[]
 def unavailable(*args,**kwargs):calls.append('external');raise AssertionError('No external fallback')
 monkeypatch.setattr('backend.integrations.Integrations.client',unavailable)
 monkeypatch.setattr(m,'runtime_executable',lambda:Path('fixture-verified-runtime.exe'))
 def blocked(*args):raise IntegrationError(POLICY_ERROR)
 monkeypatch.setattr(m,'_version_probe',blocked)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
  selected=app.state.model_connection.snapshot()
  result=c.post('/api/settings/local-models/check');assert result.status_code==502 and result.json()=={'error':POLICY_ERROR}
  assert m.runtime_state=='failed' and m.runtime_error==POLICY_ERROR and m.process is None
  status=app.state.integrations.status() if hasattr(app.state,'integrations') else Integrations(s)
  if not isinstance(status,dict):status.model_connection=app.state.model_connection;status=status.status()
  assert status['model']==POLICY_ERROR and app.state.model_connection.snapshot()==selected
  active=c.get('/api/model/active');assert active.status_code==200 and active.json()['status']==POLICY_ERROR
  assert m.token not in active.text and 'PUBLIC-NATIVE-DETAIL' not in active.text and not calls
