"""Server-local encrypted secrets. No portable export, database, or logging."""
import base64,ctypes,csv,io,json,os,secrets,subprocess,tempfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class SecretUnavailable(Exception):pass

def _dpapi(data,decrypt=False):
 from ctypes import wintypes
 class Blob(ctypes.Structure):
  _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
 buf=ctypes.create_string_buffer(data);src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
 dll=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 fn=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
 fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
 fn.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p]
 if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out)):raise SecretUnavailable()
 try:return ctypes.string_at(out.data,out.size)
 finally:kernel.LocalFree(out.data)

class SecretStore:
 def __init__(self,path,max_bytes=32768):
  if type(max_bytes)!=int or not 1024<=max_bytes<=131072:raise ValueError("invalid_secret_size_limit")
  self.path=Path(path);self.max_bytes=max_bytes
 def _directory(self):
  p=self.path.parent
  if p.is_symlink() or self.path.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise SecretUnavailable()
  p.mkdir(parents=True,exist_ok=True)
  if os.name=='nt':
   text=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True,creationflags=0x08000000)
   sid=list(csv.reader(io.StringIO(text.strip())))[0][-1]
   if not sid.startswith('S-1-'):raise SecretUnavailable()
   r=subprocess.run(['icacls',str(p),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True,creationflags=0x08000000)
   if r.returncode:raise SecretUnavailable()
  else:os.chmod(p,0o700)
 def _key(self,create):
  p=self.path.parent/'master.key'
  if p.is_symlink():raise SecretUnavailable()
  if not p.exists():
   if not create:raise SecretUnavailable()
   fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
   with os.fdopen(fd,'wb') as f:f.write(secrets.token_bytes(32))
  if p.stat().st_mode&0o077:raise SecretUnavailable()
  key=p.read_bytes()
  if len(key)!=32:raise SecretUnavailable()
  return key
 def write(self,value):
  tmp=None
  try:
   self._directory();raw=json.dumps(value,separators=(',',':'),ensure_ascii=False).encode()
   if len(raw)>self.max_bytes-1024:raise SecretUnavailable()
   if os.name=='nt':payload=b'DP1'+_dpapi(raw)
   else:
    nonce=secrets.token_bytes(12);payload=b'AG1'+nonce+AESGCM(self._key(True)).encrypt(nonce,raw,b'PSLC-model-v1')
   fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=self.path.parent)
   with os.fdopen(fd,'wb') as f:
    if os.name!='nt':os.fchmod(f.fileno(),0o600)
    f.write(payload);f.flush();os.fsync(f.fileno())
   os.replace(tmp,self.path);tmp=None
  except Exception:raise SecretUnavailable() from None
  finally:
   if tmp is not None:
    try:os.unlink(tmp)
    except OSError:pass
 def read(self):
  try:
   if self.path.is_symlink() or self.path.parent.is_symlink() or self.path.stat().st_size>self.max_bytes:raise SecretUnavailable()
   raw=self.path.read_bytes()
   if os.name=='nt' and raw.startswith(b'DP1'):plain=_dpapi(raw[3:],True)
   elif os.name!='nt' and raw.startswith(b'AG1'):
    if self.path.stat().st_mode&0o077:raise SecretUnavailable()
    plain=AESGCM(self._key(False)).decrypt(raw[3:15],raw[15:],b'PSLC-model-v1')
   else:raise SecretUnavailable()
   return json.loads(plain)
  except Exception:raise SecretUnavailable() from None
