"""Owned native runtime watchdog/probe. No application payload or logs are written."""
import argparse,os,signal,subprocess,sys,time,ctypes
import psutil
from .runtime_process import windows_own_job,windows_error_mode,policy_launch_error,worker_result,WORKER_POLICY_BLOCKED,WORKER_FAILED

def linux_parent_death():
 if sys.platform.startswith('linux'):
  libc=ctypes.CDLL(None);libc.prctl(1,signal.SIGTERM)
  if os.getppid()==1:os._exit(1)

def parent_alive(pid,birth):
 try:return psutil.pid_exists(pid) and abs(psutil.Process(pid).create_time()-birth)<.01
 except psutil.Error:return False

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--parent',type=int,required=True);parser.add_argument('--parent-created',type=float,required=True)
 parser.add_argument('--probe',action='store_true');parser.add_argument('--probe-timeout',type=float,default=5);parser.add_argument('command',nargs=argparse.REMAINDER);a=parser.parse_args()
 child=None;owned_job=None;stopped=False
 def stop(*_):
  nonlocal stopped
  stopped=True
 signal.signal(signal.SIGTERM,stop)
 if os.name!='nt':signal.signal(signal.SIGINT,stop)
 try:
  if not a.command or not parent_alive(a.parent,a.parent_created) or a.probe and not 0<a.probe_timeout<=5:return WORKER_FAILED
  # Set mode only here, never in the multithreaded API. Ownership precedes Popen.
  if os.name=='nt':owned_job=windows_own_job();windows_error_mode()
  kwargs={'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True,'preexec_fn':linux_parent_death}
  child=subprocess.Popen(a.command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
  deadline=time.monotonic()+a.probe_timeout if a.probe else None
  while not stopped and child.poll() is None:
   if not parent_alive(a.parent,a.parent_created) or deadline is not None and time.monotonic()>=deadline:break
   time.sleep(.1)
  code=child.poll()
  return worker_result(code) if code is not None else WORKER_FAILED
 except OSError as error:return WORKER_POLICY_BLOCKED if policy_launch_error(error) else WORKER_FAILED
 except Exception:return WORKER_FAILED
 finally:
  if child and child.poll() is None:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
  # Windows owns the self-job handle lifetime. Closing it here would kill this
  # worker before it returns its fixed code. OS teardown closes/kills descendants.
if __name__=='__main__':raise SystemExit(main())
