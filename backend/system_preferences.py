"""Three bounded display preferences. Separate protected config, never transport/policy."""
import hashlib,json,os,tempfile
from pathlib import Path
from threading import RLock
from typing import Literal
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pydantic import Field,field_validator
from .schemas import Strict
from .secret_store import SecretStore
from .store import Problem

class Preferences(Strict):
 schema_version:Literal[1]=1
 instance_label:str=Field(default='Локальный стенд',min_length=1,max_length=80)
 support_contact:str=Field(default='',max_length=200)
 display_timezone:str=Field(default='Europe/Moscow',min_length=1,max_length=80)
 @field_validator('instance_label','support_contact','display_timezone')
 @classmethod
 def text(cls,value):
  if value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('invalid_preference_text')
  return value
 @field_validator('display_timezone')
 @classmethod
 def timezone(cls,value):
  try:ZoneInfo(value)
  except (ZoneInfoNotFoundError,ValueError):raise ValueError('invalid_timezone') from None
  return value
class PreferenceUpdate(Preferences):
 expected_revision:str=Field(pattern='^[a-f0-9]{64}$')

class SystemPreferences:
 def __init__(self,path):self.path=Path(path);self.lock=RLock()
 def _read(self):
  try:
   if self.path.is_symlink() or self.path.parent.is_symlink():raise ValueError()
   if not self.path.exists():return Preferences()
   if self.path.stat().st_size>4096:raise ValueError()
   if os.name!='nt' and self.path.stat().st_mode&0o077:raise ValueError()
   return Preferences.model_validate_json(self.path.read_bytes(),strict=True)
  except Exception:raise Problem('system_preferences_unavailable',503) from None
 @staticmethod
 def revision(value):return hashlib.sha256(json.dumps(value.model_dump(),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 def view(self):
  with self.lock:
   value=self._read();return {**value.model_dump(),'revision':self.revision(value)}
 def save(self,data,authorize):
  with self.lock:
   authorize();old=self._read()
   if data.expected_revision!=self.revision(old):raise Problem('system_preferences_changed',409)
   value=Preferences.model_validate(data.model_dump(exclude={'expected_revision'}),strict=True)
   tmp=None
   try:
    SecretStore(self.path)._directory()
    authorize()
    fd,tmp=tempfile.mkstemp(prefix='.preferences-',dir=self.path.parent)
    with os.fdopen(fd,'wb') as f:
     if os.name!='nt':os.fchmod(f.fileno(),0o600)
     f.write(json.dumps(value.model_dump(),ensure_ascii=False,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
    os.replace(tmp,self.path);tmp=None
   except Problem:raise
   except Exception:raise Problem('system_preferences_unavailable',503) from None
   finally:
    if tmp is not None:
     try:os.unlink(tmp)
     except OSError:pass
   return self.view()
