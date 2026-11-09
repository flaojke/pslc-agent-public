"""Administrative Open WebUI configuration and bounded, non-operational probes."""
from dataclasses import replace
from pathlib import Path
from threading import RLock,BoundedSemaphore
from time import monotonic,time
from urllib.parse import urlsplit,urlunsplit
import hashlib,json,re,secrets,ssl
import httpx
from pydantic import Field
from typing import Literal
from .schemas import Strict
from .integrations import validate_endpoint,IntegrationError
from .secret_store import SecretStore,SecretUnavailable
from .store import Problem

ID=re.compile(r'[A-Za-z0-9_./:@+~-]{1,128}\Z')
class Candidate(Strict):
 server_url:str=Field(min_length=1,max_length=2048)
 api_key:str=Field(default='',max_length=8192)
 model_id:str=Field(default='',max_length=128)
 revision:str=Field(min_length=1,max_length=64)
class Revision(Strict):
 revision:str=Field(min_length=1,max_length=64)

def normalize(value):
 try:
  value=value.strip().rstrip('/')
  validate_endpoint(value);p=urlsplit(value)
  if p.path.endswith('/v1') or '%' in p.netloc or '\\' in value or any(ord(c)<33 for c in value):raise ValueError()
  port=p.port
  host=p.hostname.lower().encode('idna').decode('ascii')
  host='['+host+']' if ':' in host else host
  netloc=host+((':'+str(port)) if port else '')
  path=p.path.rstrip('/');path=path if path.endswith('/api') else path+'/api'
  return urlunsplit((p.scheme.lower(),netloc,path,'',''))
 except (ValueError,AttributeError,IntegrationError):raise Problem('invalid_model_endpoint',422) from None

def valid_key(value):
 if not value or len(value)>8192 or any(ord(c)<33 or ord(c)>126 for c in value):raise Problem('invalid_model_key',422)
 return value

def remote(client,method,url,**kwargs):
 started=monotonic()
 try:
  with client.stream(method,url,**kwargs) as response:
   if response.status_code in (401,403):raise Problem('openwebui_auth_failed',502)
   if 300<=response.status_code<400:raise Problem('openwebui_redirect_rejected',502)
   if response.status_code>=400:raise Problem('openwebui_unavailable',502)
   data=bytearray()
   for chunk in response.iter_bytes():
    if len(data)+len(chunk)>524288:raise Problem('openwebui_response_too_large',502)
    if monotonic()-started>30:raise Problem('openwebui_timeout',502)
    data.extend(chunk)
   if monotonic()-started>30:raise Problem('openwebui_timeout',502)
   return json.loads(data)
 except httpx.TimeoutException:raise Problem('openwebui_timeout',502) from None
 except httpx.HTTPError:raise Problem('openwebui_unavailable',502) from None
 except (ValueError,RecursionError):raise Problem('openwebui_invalid_response',502) from None

def model_ids(client,url,key):
 data=remote(client,'GET',url+'/models',headers={'Authorization':'Bearer '+key})
 if not isinstance(data,dict) or not isinstance(data.get('data'),list) or len(data['data'])>2000:raise Problem('openwebui_invalid_response',502)
 return sorted({r['id'] for r in data['data'] if isinstance(r,dict) and isinstance(r.get('id'),str) and ID.fullmatch(r['id']) and key not in r['id']})

def json_probe(client,url,key,model):
 data=remote(client,'POST',url+'/chat/completions',headers={'Authorization':'Bearer '+key},json={
  'model':model,'stream':False,'temperature':0,'max_tokens':256,'response_format':{'type':'json_object'},
  'tools':[],'tool_ids':[],'messages':[{'role':'system','content':'Return ONLY the exact JSON object {"status":"ok"}. Do not call tools or execute actions.'},{'role':'user','content':'JSON contract connectivity probe; no operational context.'}]})
 try:
  choice=data['choices'][0]
  return choice.get('finish_reason')!='length' and not choice['message'].get('tool_calls') and json.loads(choice['message']['content'])=={'status':'ok'}
 except (ValueError,KeyError,TypeError,IndexError,RecursionError):return False

class Selection(Strict):
 provider:Literal['local','openwebui','disabled']
 model_id:str=Field(default='',max_length=64)
 profile_id:str=Field(default='',max_length=32)
 revision:str=Field(min_length=1,max_length=64)

class ModelConnection:
 def __init__(self,settings,transport=None):
  from .local_inference import LocalModels
  self.base=settings;self.transport=transport;self.lock=RLock();self.slots=BoundedSemaphore(2)
  self.secret=SecretStore(settings.model_config_path,max_bytes=131072) if settings.model_config_path else None
  self.revision=secrets.token_hex(16);self.source='none';self.error=False;self.catalogs={};self.last_check=None
  self.local=LocalModels(settings,transport);self.local_id=self.local.default
  self.profiles={};self.active='local' if settings.local_models_enabled else 'disabled'
  if settings.model_url:
   self.profiles['legacy']={'url':settings.model_url,'key':settings.model_token,'model':settings.model_id,'title':settings.model_id}
   self.active='legacy';self.source='environment'
  if self.secret and (self.secret.path.exists() or self.secret.path.is_symlink()):
   try:
    data=self.secret.read()
    if data.get('version')==1:
     if set(data)!={'version','enabled','url','key','model','revision'} or type(data['enabled']) is not bool:raise ValueError()
     self.profiles={'legacy':{'url':normalize(data['url']),'key':valid_key(data['key']),'model':data['model'],'title':data['model']}} if data['enabled'] else {}
     self.active='legacy' if data['enabled'] else 'disabled'
    elif data.get('version')==2:
     if set(data)!={'version','active','local_id','profiles','revision'}:raise ValueError()
     self.profiles=data['profiles'];self.active=data['active'];self.local_id=data['local_id']
     if not isinstance(self.profiles,dict) or len(self.profiles)>8 or self.local_id not in self.local.catalog:raise ValueError()
     for pid,p in self.profiles.items():
      if not re.fullmatch('[A-Za-z0-9_-]{1,32}',pid) or set(p)!={'url','key','model','title'}:raise ValueError()
      p['url']=normalize(p['url']);valid_key(p['key'])
      if not isinstance(p['title'],str) or len(p['title'])>128:raise ValueError()
     if self.active not in ('local','disabled') and self.active not in self.profiles:raise ValueError()
    else:raise ValueError()
    if not re.fullmatch('[a-f0-9]{32}',data['revision']):raise ValueError()
    for p in self.profiles.values():
     if not ID.fullmatch(p['model']):raise ValueError()
    self.revision=data['revision'];self.source='interface'
   except Exception:self.profiles={};self.active='disabled';self.error=True;self.source='unavailable'
  self.local.set_desired(self.local_id if self.active=='local' else None)
 def snapshot(self):
  with self.lock:
   if self.active=='local':
    return replace(self.base,model_url=self.local.url,model_id=self.local_id,model_token=self.local.token,model_backend='local')
   p=self.profiles.get(self.active)
   return replace(self.base,model_url=p['url'] if p else '',model_token=p['key'] if p else '',model_id=p['model'] if p else '',model_backend='external')
 def editor(self):
  return self.profiles.get(self.active) or next(reversed(self.profiles.values()),None) if self.profiles else None
 def view(self):
  with self.lock:
   p=self.editor() or {}
   try:url=normalize(p.get('url','')) if p.get('url') else ''
   except Problem:url=''
   return {'server_url':url,'model_id':p.get('model','') if ID.fullmatch(p.get('model','')) else '',
    'has_key':bool(p.get('key')),'revision':self.revision,'source':self.source,
    'status':'configuration_unavailable' if self.error else 'configured_unverified' if p else 'not_configured',
    'last_check':self.last_check,'real_task_quality_verified':False}
 def providers(self):
  with self.lock:
   active={'provider':'local' if self.active=='local' else 'disabled' if self.active=='disabled' else 'openwebui',
     'model_id':self.local_id if self.active=='local' else self.profiles.get(self.active,{}).get('model',''),
     'profile_id':self.active if self.active in self.profiles else ''}
   profiles=[{'id':pid,'title':p['title'],'server_url':p['url'],'model_id':p['model'],'has_key':bool(p['key'])} for pid,p in self.profiles.items()]
   revision=self.revision
  return {'revision':revision,'active':active,'profiles':profiles,'local':self.local.status(),'configuration_error':self.error,'real_task_quality_verified':False}
 def client(self):
  return httpx.Client(transport=self.transport,timeout=httpx.Timeout(15,connect=5),follow_redirects=False,trust_env=False,
    verify=ssl.create_default_context(cafile=self.base.model_ca_file or None))
 def candidate(self,data):
  if data.revision!=self.revision:raise Problem('model_configuration_changed',409)
  url=normalize(data.server_url);key=data.api_key;p=self.editor() or {}
  if not key:
   try:old=normalize(p.get('url',''))
   except Problem:old=''
   if url!=old or not p.get('key'):raise Problem('new_key_required',422)
   key=p['key']
  key=valid_key(key)
  if key in url or key in data.model_id:raise Problem('invalid_model_endpoint',422)
  if data.model_id and not ID.fullmatch(data.model_id):raise Problem('invalid_model_id',422)
  return url,key,data.model_id
 def signature(self,url,key):return hashlib.sha256((url+'\0'+key).encode()).hexdigest()
 def test(self,data,probe=False,authorize=lambda:None):
  authorize()
  with self.lock:url,key,model=self.candidate(data);rev=self.revision
  if probe and not model:raise Problem('model_selection_required',422)
  if not self.slots.acquire(False):raise Problem('model_check_busy',429)
  try:
   try:
    with self.client() as c:
     ids=model_ids(c,url,key)
     if probe and model not in ids:raise Problem('model_not_available',422)
     passed=json_probe(c,url,key,model) if probe else False
   except (OSError,httpx.InvalidURL):raise Problem('openwebui_unavailable',502) from None
   authorize()
   with self.lock:
    if rev!=self.revision:raise Problem('model_configuration_changed',409)
    self.catalogs={k:v for k,v in self.catalogs.items() if v[0]>time()}
    if len(self.catalogs)>=128:self.catalogs.pop(next(iter(self.catalogs)))
    self.catalogs[self.signature(url,key)]=(time()+300,ids)
   return {'status':('json_probe_passed' if passed else 'json_contract_failed') if probe else 'model_list_received',
    'model_ids':ids,'model_id':model,'revision':rev,'checked_at':time(),'real_task_quality_verified':False}
  finally:self.slots.release()
 def _persist(self,active,local_id,profiles,authorize):
  authorize();rev=secrets.token_hex(16)
  try:
   if self.secret is None:raise SecretUnavailable()
   self.secret.write({'version':2,'active':active,'local_id':local_id,'profiles':profiles,'revision':rev})
  except SecretUnavailable:raise Problem('model_secret_unavailable',503) from None
  self.profiles=profiles;self.active=active;self.local_id=local_id;self.revision=rev;self.source='interface';self.error=False;self.catalogs.clear();self.last_check=None
  self.local.set_desired(local_id if active=='local' else None)
 def save(self,data,authorize=lambda:None):
  authorize()
  with self.lock:
   url,key,model=self.candidate(data)
   if not model:raise Problem('model_selection_required',422)
   cached=self.catalogs.get(self.signature(url,key))
   if not cached or cached[0]<time() or model not in cached[1]:raise Problem('reload_model_list',409)
   profiles={pid:dict(p) for pid,p in self.profiles.items()}
   pid=next((pid for pid,p in profiles.items() if p['url']==url and p['model']==model),None)
   if pid is None:
    if len(profiles)>=8:raise Problem('model_profile_limit',409)
    pid=secrets.token_hex(8)
   profiles[pid]={'url':url,'key':key,'model':model,'title':model}
   self._persist(pid,self.local_id,profiles,authorize);return self.view()
 def disable(self,data,authorize=lambda:None):
  authorize()
  with self.lock:
   if data.revision!=self.revision:raise Problem('model_configuration_changed',409)
   profiles={pid:dict(p) for pid,p in self.profiles.items()};editor=self.editor()
   pid=next((pid for pid,p in self.profiles.items() if p is editor),None)
   if pid:profiles.pop(pid)
   active='disabled' if self.active==pid or self.active=='disabled' else self.active
   self._persist(active,self.local_id,profiles,authorize);return self.view()
 def select(self,data,authorize=lambda:None):
  authorize()
  with self.lock:
   if data.revision!=self.revision:raise Problem('model_configuration_changed',409)
   local_id=self.local_id
   if data.provider=='local':
    if not self.local.enabled:raise Problem('local_models_disabled',409)
    self.local.entry(data.model_id)
    if not self.local.verified_model(data.model_id,force=True):raise Problem('local_model_missing',409)
    active='local';local_id=data.model_id
   elif data.provider=='openwebui':
    if data.profile_id not in self.profiles:raise Problem('model_profile_missing',404)
    active=data.profile_id
   else:active='disabled'
   self._persist(active,local_id,{pid:dict(p) for pid,p in self.profiles.items()},authorize)
  if active=='local':self.local.warmup(local_id)
  return self.providers()
 def warmup(self):
  with self.lock:mid=self.local_id if self.active=='local' else None
  if mid:self.local.warmup(mid)
 def close(self):self.local.close()
