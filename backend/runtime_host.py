"""Owned native runtime watchdog. No application payload or logs are written."""
import argparse,os,signal,subprocess,sys,time,ctypes
import psutil

def windows_job(process):
 from ctypes import wintypes
 class Basic(ctypes.Structure):
  _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',wintypes.DWORD),('Min',ctypes.c_size_t),('Max',ctypes.c_size_t),('Active',wintypes.DWORD),('Affinity',ctypes.c_size_t),('Priority',wintypes.DWORD),('Scheduling',wintypes.DWORD)]
 class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
 class Extended(ctypes.Structure):_fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
 k=ctypes.WinDLL('kernel32',use_last_error=True)
 k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];k.CreateJobObjectW.restype=wintypes.HANDLE
 k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];k.SetInformationJobObject.restype=wintypes.BOOL
 k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];k.AssignProcessToJobObject.restype=wintypes.BOOL
 job=k.CreateJobObjectW(None,None);info=Extended();info.Basic.Flags=0x2000
 if not job or not k.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(job,wintypes.HANDLE(int(process._handle))):
  if job:k.CloseHandle(wintypes.HANDLE(job))
  raise RuntimeError('runtime_ownership_failed')
 return job
def linux_parent_death():
 if sys.platform.startswith('linux'):
  libc=ctypes.CDLL(None);libc.prctl(1,signal.SIGTERM)
  if os.getppid()==1:os._exit(1)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--parent',type=int,required=True);parser.add_argument('--parent-created',type=float,required=True);parser.add_argument('command',nargs=argparse.REMAINDER);a=parser.parse_args()
 child=None;job=None;stopped=False
 def stop(*_):
  nonlocal stopped
  stopped=True
 signal.signal(signal.SIGTERM,stop)
 if os.name!='nt':signal.signal(signal.SIGINT,stop)
 try:
  if not a.command or not psutil.pid_exists(a.parent) or abs(psutil.Process(a.parent).create_time()-a.parent_created)>.01:return 2
  kwargs={'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True,'preexec_fn':linux_parent_death}
  child=subprocess.Popen(a.command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
  if os.name=='nt':job=windows_job(child)
  while not stopped and child.poll() is None:
   try:alive=psutil.pid_exists(a.parent) and abs(psutil.Process(a.parent).create_time()-a.parent_created)<.01
   except psutil.Error:alive=False
   if not alive:break
   time.sleep(.5)
  return child.poll() or 0
 except Exception:return 2
 finally:
  if child and child.poll() is None:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
  if job:ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(job))
if __name__=='__main__':raise SystemExit(main())
