"""Fixed worker protocol and Windows launch containment; no import-time OS changes."""
import os

POLICY_ERROR='local_runtime_policy_blocked'
WORKER_POLICY_BLOCKED=77
WORKER_FAILED=2
POLICY_STATUSES=frozenset((0xC0E90002,0xC0000428))

def policy_launch_error(error):
 return isinstance(error,OSError) and getattr(error,'winerror',None) in (4551,577)

def policy_native_status(code):
 return type(code)==int and (code&0xffffffff) in POLICY_STATUSES

def worker_result(native_code):
 # Application-defined exit77 is not a policy error. The worker alone grants77.
 return WORKER_POLICY_BLOCKED if policy_native_status(native_code) else 0 if native_code==0 else WORKER_FAILED

def worker_error(code,default):
 return POLICY_ERROR if code==WORKER_POLICY_BLOCKED or policy_native_status(code) else default

def windows_error_mode():
 """Call only in the dedicated single-purpose worker before creating children."""
 import ctypes
 from ctypes import wintypes
 k=ctypes.WinDLL('kernel32',use_last_error=True)
 k.GetErrorMode.argtypes=[];k.GetErrorMode.restype=wintypes.UINT
 k.SetErrorMode.argtypes=[wintypes.UINT];k.SetErrorMode.restype=wintypes.UINT
 required=0x0001|0x0002
 k.SetErrorMode(k.GetErrorMode()|required)
 if k.GetErrorMode()&required!=required:raise OSError('runtime_error_mode_failed')

def windows_own_job():
 """Join before Popen; retain returned noninherited handle until OS teardown.
 Closing the last handle inside a member process would terminate that process.
 """
 import ctypes
 from ctypes import wintypes
 class Basic(ctypes.Structure):
  _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',wintypes.DWORD),('Min',ctypes.c_size_t),('Max',ctypes.c_size_t),('Active',wintypes.DWORD),('Affinity',ctypes.c_size_t),('Priority',wintypes.DWORD),('Scheduling',wintypes.DWORD)]
 class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
 class Extended(ctypes.Structure):
  _fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
 k=ctypes.WinDLL('kernel32',use_last_error=True)
 k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];k.CreateJobObjectW.restype=wintypes.HANDLE
 k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];k.SetInformationJobObject.restype=wintypes.BOOL
 k.GetCurrentProcess.argtypes=[];k.GetCurrentProcess.restype=wintypes.HANDLE
 k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];k.AssignProcessToJobObject.restype=wintypes.BOOL
 k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
 job=k.CreateJobObjectW(None,None);info=Extended();info.Basic.Flags=0x2000
 if not job:raise OSError('runtime_ownership_failed')
 if not k.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(job,k.GetCurrentProcess()):
  k.CloseHandle(job);raise OSError('runtime_ownership_failed')
 return job
