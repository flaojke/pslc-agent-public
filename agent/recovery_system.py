"""Narrow service-runtime and Windows registry-value providers. No shell or elevation."""
import ctypes,os,re,subprocess,time
from backend.recovery_types import fingerprint
from .recovery_files import RecoveryError,b64,unb64

def _win_service(name,action=None):
 from ctypes import wintypes as w
 dll=ctypes.WinDLL('advapi32',use_last_error=True)
 dll.OpenSCManagerW.argtypes=[w.LPCWSTR,w.LPCWSTR,w.DWORD];dll.OpenSCManagerW.restype=w.HANDLE
 dll.OpenServiceW.argtypes=[w.HANDLE,w.LPCWSTR,w.DWORD];dll.OpenServiceW.restype=w.HANDLE
 dll.CloseServiceHandle.argtypes=[w.HANDLE]
 dll.QueryServiceStatusEx.argtypes=[w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD)]
 dll.StartServiceW.argtypes=[w.HANDLE,w.DWORD,w.LPVOID]
 dll.ControlService.argtypes=[w.HANDLE,w.DWORD,w.LPVOID]
 class Status(ctypes.Structure):
  _fields_=[('type',w.DWORD),('state',w.DWORD),('accepted',w.DWORD),('exit',w.DWORD),('specific',w.DWORD),('checkpoint',w.DWORD),('wait',w.DWORD),('pid',w.DWORD),('flags',w.DWORD)]
 scm=dll.OpenSCManagerW(None,None,1)
 if not scm:raise RecoveryError('recovery_service_unavailable')
 service=None
 try:
  service=dll.OpenServiceW(scm,name,4|16|32)
  if not service:raise RecoveryError('recovery_service_permissions')
  def state():
   status=Status();size=w.DWORD()
   if not dll.QueryServiceStatusEx(service,0,ctypes.byref(status),ctypes.sizeof(status),ctypes.byref(size)):raise RecoveryError('recovery_service_unavailable')
   return status.state
  old=state()
  if old not in (1,4):raise RecoveryError('recovery_service_unstable')
  desired=4 if action=='running' else 1
  if action and old!=desired:
   if action=='running':ok=dll.StartServiceW(service,0,None)
   else:
    buf=Status();ok=dll.ControlService(service,1,ctypes.byref(buf))
   if not ok:raise RecoveryError('recovery_service_failed')
   deadline=time.monotonic()+10
   while state()!=desired:
    if time.monotonic()>deadline:raise RecoveryError('recovery_service_timeout')
    time.sleep(.1)
  final=state()
  if final not in (1,4):raise RecoveryError('recovery_service_unstable')
  return {'state':'running' if final==4 else 'stopped'}
 finally:
  if service:dll.CloseServiceHandle(service)
  dll.CloseServiceHandle(scm)

def service(resource,action=None):
 name=resource['name']
 if not re.fullmatch(r'[A-Za-z0-9_.@ -]{1,128}',name) or name!=name.strip():raise RecoveryError('recovery_service_invalid')
 if os.name=='nt':return _win_service(name,action)
 if os.geteuid()!=0 or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}',name):raise RecoveryError('recovery_service_permissions')
 def run(args):
  try:
   p=subprocess.run(['systemctl',*args,'--',name],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=12,check=False)
   if p.returncode or len(p.stdout)>4096:raise RecoveryError('recovery_service_unavailable')
   return p.stdout.decode('ascii','strict')
  except (OSError,subprocess.TimeoutExpired,UnicodeError):raise RecoveryError('recovery_service_unavailable') from None
 if action:run(['start' if action=='running' else 'stop'])
 text=run(['show','--property=LoadState','--property=ActiveState'])
 values=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
 if values.get('LoadState')!='loaded' or values.get('ActiveState') not in ('active','inactive'):raise RecoveryError('recovery_service_unstable')
 return {'state':'running' if values['ActiveState']=='active' else 'stopped'}

def _reg(resource):
 if os.name!='nt':raise RecoveryError('recovery_registry_platform')
 import winreg
 hive=winreg.HKEY_LOCAL_MACHINE if resource['hive']=='HKLM' else winreg.HKEY_CURRENT_USER
 key=resource['key'];name=resource['name']
 return winreg,hive,key,name
def _registry_value_size(handle,name):
 from ctypes import wintypes as w
 dll=ctypes.WinDLL('advapi32',use_last_error=True);fn=dll.RegQueryValueExW
 fn.argtypes=[w.HANDLE,w.LPCWSTR,w.LPVOID,ctypes.POINTER(w.DWORD),w.LPVOID,ctypes.POINTER(w.DWORD)];fn.restype=w.LONG
 size=w.DWORD();kind=w.DWORD();status=fn(w.HANDLE(int(handle)),name,None,ctypes.byref(kind),None,ctypes.byref(size))
 if status==2:return None
 if status!=0 or size.value>65536:raise RecoveryError('recovery_registry_limit')
 return size.value
def snapshot_registry(resource):
 reg,hive,key,name=_reg(resource);handle=None
 try:
  handle=reg.OpenKey(hive,key,0,reg.KEY_READ|reg.KEY_WRITE)
 except FileNotFoundError:
  parent,_,leaf=key.rpartition(chr(92))
  if not parent:raise RecoveryError('recovery_registry_parent')
  try:
   with reg.OpenKey(hive,parent,0,reg.KEY_READ|reg.KEY_CREATE_SUB_KEY):pass
  except OSError:raise RecoveryError('recovery_registry_permissions') from None
  return {'key_exists':False,'value_exists':False}
 except OSError:raise RecoveryError('recovery_registry_permissions') from None
 try:
  count=reg.QueryInfoKey(handle)
  if count[0]+count[1]>256:raise RecoveryError('recovery_registry_limit')
  value={'key_exists':True,'key_shape':fingerprint({'children':count[0],'values':count[1]})}
  if _registry_value_size(handle,name) is None:return {**value,'value_exists':False}
  try:data,kind=reg.QueryValueEx(handle,name)
  except FileNotFoundError:return {**value,'value_exists':False}
  if kind not in (reg.REG_NONE,reg.REG_SZ,reg.REG_EXPAND_SZ,reg.REG_BINARY,reg.REG_DWORD,reg.REG_MULTI_SZ,reg.REG_QWORD):raise RecoveryError('recovery_registry_type')
  encoded=b64(data if data is not None else b'') if isinstance(data,bytes) or kind==reg.REG_NONE else data
  import json
  if len(json.dumps(encoded).encode())>65536:raise RecoveryError('recovery_registry_limit')
  return {**value,'value_exists':True,'kind':kind,'data':encoded}
 finally:reg.CloseKey(handle)
def restore_registry(resource,baseline):
 reg,hive,key,name=_reg(resource)
 if baseline['value_exists']:
  with reg.CreateKeyEx(hive,key,0,reg.KEY_READ|reg.KEY_WRITE) as handle:
   data=unb64(baseline['data']) if baseline['kind'] in (reg.REG_BINARY,reg.REG_NONE) else baseline['data']
   reg.SetValueEx(handle,name,0,baseline['kind'],data)
 else:
  try:
   with reg.OpenKey(hive,key,0,reg.KEY_READ|reg.KEY_WRITE) as handle:
    try:reg.DeleteValue(handle,name)
    except FileNotFoundError:pass
    count=reg.QueryInfoKey(handle)
   if not baseline['key_exists']:
    if count[0] or count[1]:raise RecoveryError('recovery_registry_conflict')
    reg.DeleteKey(hive,key)
  except FileNotFoundError:pass
