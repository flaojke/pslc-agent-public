"""Configuration-file snapshots, strict local files only. Never directories or journals."""
import base64,ctypes,os,stat,tempfile
from pathlib import Path
from backend.recovery_types import fingerprint
class RecoveryError(Exception):
 def __init__(self,code='recovery_unavailable'):self.code=code;super().__init__(code)
MAX_BYTES=8*1024*1024
def b64(data):return base64.b64encode(data).decode('ascii')
def unb64(value):return base64.b64decode(value,validate=True)
def _ancestors(p):
 for q in (p,*p.parents):
  if q.is_symlink() or (hasattr(q,'is_junction') and q.is_junction()):raise RecoveryError('recovery_unsafe_path')
  if q.exists() and os.name=='nt' and q.stat().st_file_attributes&0x400:raise RecoveryError('recovery_unsafe_path')
def file_path(value,protected=()):
 p=Path(value)
 if not p.is_absolute() or len(str(p))>1024 or any(ord(c)<32 for c in str(p)):raise RecoveryError('recovery_unsafe_path')
 _ancestors(p)
 resolved=p.resolve(strict=False)
 if p!=resolved:raise RecoveryError('recovery_unsafe_path')
 for raw in protected:
  own=Path(raw).resolve()
  if resolved==own or resolved.is_relative_to(own):raise RecoveryError('recovery_protected_path')
 if p.suffix.lower() not in {'.json','.ini','.conf','.config','.yaml','.yml','.xml','.toml','.properties','.cfg'} and p.name!='hosts':raise RecoveryError('recovery_config_type_required')
 if p.suffix.lower() in {'.log','.evtx','.etl','.journal','.dmp','.dump','.trace'} or any(x.lower() in {'logs','log','winevt','journal'} for x in p.parts):raise RecoveryError('recovery_logs_forbidden')
 if not p.parent.is_dir() or not os.access(p.parent,os.W_OK):raise RecoveryError('recovery_unwritable_parent')
 if os.name=='nt':
  if str(p).startswith(chr(92)*2) or ':' in str(p)[2:] or ctypes.windll.kernel32.GetDriveTypeW(p.anchor)!=3:raise RecoveryError('recovery_remote_path')
 else:
  # Conservative filesystem support, including the disposable Linux container used by QA.
  try:
   mounts=[]
   for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
    left,right=line.split(' - ',1);mount=left.split()[4].replace('\\040',' ');kind=right.split()[0]
    if resolved.is_relative_to(Path(mount)):mounts.append((len(mount),kind))
   if not mounts or max(mounts)[1] not in {'ext2','ext3','ext4','xfs','btrfs','tmpfs','overlay'}:raise RecoveryError('recovery_unsupported_filesystem')
  except (OSError,ValueError):raise RecoveryError('recovery_unsupported_filesystem') from None
 return p
def _win_sd(p,value=None):
 from ctypes import wintypes as w
 dll=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 dll.GetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD)]
 dll.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[w.LPVOID,w.DWORD,w.DWORD,ctypes.POINTER(w.LPWSTR),ctypes.POINTER(w.DWORD)]
 dll.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(w.LPVOID),ctypes.POINTER(w.DWORD)]
 dll.SetFileSecurityW.argtypes=[w.LPCWSTR,w.DWORD,w.LPVOID];kernel.LocalFree.argtypes=[w.LPVOID]
 if value is None:
  size=w.DWORD();dll.GetFileSecurityW(str(p),7,None,0,ctypes.byref(size))
  if not 1<=size.value<=65536:raise RecoveryError('recovery_metadata_unavailable')
  buf=ctypes.create_string_buffer(size.value)
  if not dll.GetFileSecurityW(str(p),7,buf,size.value,ctypes.byref(size)):raise RecoveryError('recovery_metadata_unavailable')
  text=w.LPWSTR()
  if not dll.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf,1,7,ctypes.byref(text),None):raise RecoveryError('recovery_metadata_unavailable')
  try:return text.value
  finally:kernel.LocalFree(text)
 descriptor=w.LPVOID()
 if not dll.ConvertStringSecurityDescriptorToSecurityDescriptorW(value,1,ctypes.byref(descriptor),None):raise RecoveryError('recovery_metadata_unavailable')
 try:
  if not dll.SetFileSecurityW(str(p),7,descriptor):raise RecoveryError('recovery_metadata_unavailable')
 finally:kernel.LocalFree(descriptor)
def _win_preflight(p):
 from ctypes import wintypes as w
 dll=ctypes.WinDLL('kernel32',use_last_error=True)
 dll.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];dll.CreateFileW.restype=w.HANDLE
 dll.CloseHandle.argtypes=[w.HANDLE]
 handle=dll.CreateFileW(str(p),0xC00F0000,7,None,3,0,None)
 if handle==w.HANDLE(-1).value:raise RecoveryError('recovery_metadata_unwritable')
 dll.CloseHandle(handle)
 # Reject named alternate streams, whose contents would not be captured.
 class Stream(ctypes.Structure):_fields_=[('size',ctypes.c_longlong),('name',w.WCHAR*296)]
 dll.FindFirstStreamW.argtypes=[w.LPCWSTR,ctypes.c_int,ctypes.POINTER(Stream),w.DWORD];dll.FindFirstStreamW.restype=w.HANDLE
 dll.FindNextStreamW.argtypes=[w.HANDLE,ctypes.POINTER(Stream)];dll.FindClose.argtypes=[w.HANDLE]
 stream=Stream();handle=dll.FindFirstStreamW(str(p),0,ctypes.byref(stream),0)
 if handle==w.HANDLE(-1).value:raise RecoveryError('recovery_metadata_unavailable')
 try:
  while True:
   if stream.name!='::$DATA':raise RecoveryError('recovery_alternate_stream')
   if not dll.FindNextStreamW(handle,ctypes.byref(stream)):
    if ctypes.get_last_error()!=38:raise RecoveryError('recovery_metadata_unavailable')
    break
 finally:dll.FindClose(handle)
def snapshot_file(resource,protected=()):
 p=file_path(resource['path'],protected)
 if not p.exists():return {'exists':False}
 st=p.stat()
 if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>MAX_BYTES:raise RecoveryError('recovery_unsupported_file')
 if not os.access(p,os.R_OK|os.W_OK):raise RecoveryError('recovery_metadata_unwritable')
 extra={}
 if os.name=='nt':
  if st.st_file_attributes&~(0x1|0x2|0x4|0x20|0x80|0x100|0x2000):raise RecoveryError('recovery_unsupported_attributes')
  _win_preflight(p);security=_win_sd(p)
  # Test restoration rights on an empty sibling before accepting a baseline.
  # WRITE_OWNER alone does not prove another owner's SID can be assigned to a new file.
  probe=None;probe_security=None
  try:
   fd,probe=tempfile.mkstemp(prefix='.pslc-metadata-probe-',dir=p.parent);os.close(fd);probe_path=Path(probe)
   probe_security=_win_sd(probe_path);_win_sd(probe_path,security)
   if _win_sd(probe_path)!=security:raise RecoveryError('recovery_metadata_unrestorable')
  finally:
   if probe:
    try:
     if probe_security:_win_sd(Path(probe),probe_security)
    finally:os.unlink(probe)
  extra={'security':security,'attributes':st.st_file_attributes}
 else:
  if os.geteuid()!=st.st_uid or st.st_gid not in {os.getegid(),*os.getgroups()}:
   try:caps=int(next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')),16);chown_cap=bool(caps&1) and (st.st_uid==os.geteuid() or bool(caps&8))
   except Exception:chown_cap=False
   if not chown_cap:raise RecoveryError('recovery_owner_unrestorable')
  names=os.listxattr(p,follow_symlinks=False)
  if any(not name.startswith('user.') and name!='system.posix_acl_access' for name in names):raise RecoveryError('recovery_metadata_unrestorable')
  attrs={name:b64(os.getxattr(p,name,follow_symlinks=False)) for name in names}
  if sum(len(k)+len(v) for k,v in attrs.items())>65536:raise RecoveryError('recovery_metadata_limit')
  extra={'uid':st.st_uid,'gid':st.st_gid,'xattrs':attrs}
 fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
 try:
  opened=os.fstat(fd)
  if (opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns)!=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns):raise RecoveryError('recovery_file_changed')
  with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(MAX_BYTES+1)
  after=os.fstat(fd)
 finally:os.close(fd)
 if len(data)>MAX_BYTES or (st.st_size,st.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RecoveryError('recovery_file_changed')
 return {'exists':True,'data':b64(data),'mode':stat.S_IMODE(st.st_mode),'mtime_ns':st.st_mtime_ns,**extra}
def file_fingerprint(value):
 if not value['exists']:return fingerprint({'exists':False})
 import hashlib
 return fingerprint({**{k:v for k,v in value.items() if k!='data'},'content_hash':hashlib.sha256(unb64(value['data'])).hexdigest()})
def restore_file(resource,baseline,protected=()):
 p=file_path(resource['path'],protected)
 if not baseline['exists']:
  if p.exists():p.unlink()
  return
 data=unb64(baseline['data'])
 if len(data)>MAX_BYTES:raise RecoveryError('recovery_file_limit')
 temporary=None
 try:
  fd,temporary=tempfile.mkstemp(prefix='.pslc-restore-',dir=p.parent)
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  q=Path(temporary)
  if os.name!='nt':
   os.chown(q,baseline['uid'],baseline['gid']);os.chmod(q,baseline['mode'])
   wanted=baseline['xattrs']
   for name in os.listxattr(q,follow_symlinks=False):
    if name not in wanted:os.removexattr(q,name,follow_symlinks=False)
   for name,value in wanted.items():os.setxattr(q,name,unb64(value),follow_symlinks=False)
  else:_win_sd(q,baseline['security'])
  os.utime(q,ns=(baseline['mtime_ns'],baseline['mtime_ns']))
  if p.exists() and os.name=='nt':ctypes.windll.kernel32.SetFileAttributesW(str(p),0x80)
  os.replace(q,p);temporary=None
  if os.name=='nt' and not ctypes.windll.kernel32.SetFileAttributesW(str(p),baseline['attributes']):raise RecoveryError('recovery_metadata_unavailable')
 finally:
  if temporary:
   try:os.unlink(temporary)
   except OSError:pass
