"""Local employees: verifier-only persistence and live grants, no AD dependency."""
import hashlib,secrets,re,time
from sqlalchemy import select,update,func
from pydantic import Field,model_validator
from .schemas import Strict
from .identity import Grant,Principal,Identity,LOCAL_ID
from .store import accounts,Problem

class Login(Strict):
 username:str=Field(min_length=1,max_length=64)
 password:str=Field(min_length=1,max_length=128)
class NewAccount(Grant):
 username:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')
 password:str=Field(min_length=8,max_length=128)
class EditAccount(Grant):
 expected_revision:int=Field(ge=1)
 password:str|None=Field(default=None,min_length=8,max_length=128)
class PasswordChange(Strict):
 old_password:str=Field(min_length=1,max_length=128)
 new_password:str=Field(min_length=8,max_length=128)

def hash_password(password):
 try:encoded=password.encode()
 except UnicodeError:raise Problem('invalid_request',422) from None
 salt=secrets.token_bytes(16);value=hashlib.scrypt(encoded,salt=salt,n=16384,r=8,p=1,maxmem=67108864,dklen=32)
 return 'scrypt$16384$8$1$'+salt.hex()+'$'+value.hex()
def valid_verifier(value):
 if not isinstance(value,str) or not re.fullmatch(r'scrypt\$16384\$8\$1\$[a-f0-9]{32}\$[a-f0-9]{64}',value):raise ValueError('invalid_password_verifier')
 return value
def check_password(password,value):
 try:
  parts=valid_verifier(value).split('$');actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(parts[4]),n=16384,r=8,p=1,maxmem=67108864,dklen=32)
  return secrets.compare_digest(actual,bytes.fromhex(parts[5]))
 except (ValueError,TypeError):return False

def validate_grant(roles,device_ids,spaces):
 grant=Grant.model_validate({'roles':roles,'device_ids':device_ids,'knowledge_spaces':spaces},strict=True)
 if len(set(roles))!=len(roles) or not roles or any(not re.fullmatch(r'[A-Za-z0-9_*-]{1,64}',x) for x in device_ids+spaces):raise ValueError('invalid_account_grant')
 return grant

class LocalAccounts:
 def __init__(self,store):
  self.store=store;self.dummy=hash_password('public-unused-dummy-password')
  with store.tx() as c:
   if c.scalar(select(func.count()).select_from(accounts))==0:
    # Public bootstrap credential explicitly requested by the product owner.
    c.execute(accounts.insert().values(id=LOCAL_ID,username='admin',password_hash=hash_password('admin'),roles=['operator','auditor','reviewer','platform_admin'],device_ids=['*'],knowledge_spaces=[],disabled=False,auth_version=1,created=time.time()))
    store.event(c,'account_bootstrapped',actor_id=LOCAL_ID)
 def public(self,row):return {k:v for k,v in row.items() if k!='password_hash'}
 def login(self,username,password):
  with self.store.tx() as c:row=c.execute(select(accounts).where(accounts.c.username==username.strip().lower())).mappings().first()
  valid=check_password(password,row['password_hash'] if row else self.dummy)
  if not row or not valid or row['disabled']:raise Problem('operator_unauthorized',401)
  return Identity(row['id'],(),time.time()+900,row['auth_version'])
 def principal(self,identity):
  if identity.expires<=time.time():raise Problem('operator_unauthorized',401)
  with self.store.tx() as c:row=c.execute(select(accounts).where(accounts.c.id==identity.id)).mappings().first()
  if not row or row['disabled'] or row['auth_version']!=identity.revision:raise Problem('operator_unauthorized',401)
  try:grant=validate_grant(row['roles'],row['device_ids'],row['knowledge_spaces'])
  except ValueError:raise Problem('operator_forbidden',403) from None
  return Principal(row['id'],frozenset(grant.roles),frozenset(grant.device_ids),frozenset(grant.knowledge_spaces),identity.expires)
 def list(self):
  with self.store.tx() as c:return [self.public(x) for x in c.execute(select(accounts).order_by(accounts.c.created)).mappings()]
 def create(self,data,actor):
  try:validate_grant(data.roles,data.device_ids,data.knowledge_spaces)
  except ValueError:raise Problem('invalid_account_grant',422) from None
  verifier=hash_password(data.password)
  with self.store.tx() as c:
   if c.scalar(select(func.count()).select_from(accounts))>=500:raise Problem('account_limit',409)
   if c.execute(select(accounts.c.id).where(accounts.c.username==data.username.lower())).first():raise Problem('account_exists',409)
   row={'id':secrets.token_hex(32),'username':data.username.lower(),'password_hash':verifier,'roles':data.roles,'device_ids':data.device_ids,'knowledge_spaces':data.knowledge_spaces,'disabled':data.disabled,'auth_version':1,'created':time.time()}
   c.execute(accounts.insert().values(**row));self.store.event(c,'account_created',actor_id=actor)
  return self.public(row)
 def edit(self,aid,data,actor):
  try:validate_grant(data.roles,data.device_ids,data.knowledge_spaces)
  except ValueError:raise Problem('invalid_account_grant',422) from None
  verifier=hash_password(data.password) if data.password else None
  with self.store.tx() as c:
   row=c.execute(select(accounts).where(accounts.c.id==aid)).mappings().first()
   if not row:raise Problem('not_found',404)
   if row['auth_version']!=data.expected_revision:raise Problem('account_changed',409)
   if not row['disabled'] and 'platform_admin' in row['roles'] and '*' in row['device_ids'] and (data.disabled or 'platform_admin' not in data.roles or '*' not in data.device_ids):
    others=c.execute(select(accounts).where(accounts.c.id!=aid,accounts.c.disabled==False)).mappings().all()
    if not any('platform_admin' in x['roles'] and '*' in x['device_ids'] for x in others):raise Problem('last_admin_required',409)
   changes={'roles':data.roles,'device_ids':data.device_ids,'knowledge_spaces':data.knowledge_spaces,'disabled':data.disabled,'auth_version':row['auth_version']+1}
   if verifier:changes['password_hash']=verifier
   c.execute(update(accounts).where(accounts.c.id==aid).values(**changes));self.store.event(c,'account_changed',actor_id=actor)
   result={**row,**changes}
  return self.public(result)
 def password(self,aid,data):
  with self.store.tx() as c:row=c.execute(select(accounts).where(accounts.c.id==aid)).mappings().first()
  if not row or not check_password(data.old_password,row['password_hash']):raise Problem('operator_unauthorized',401)
  verifier=hash_password(data.new_password)
  with self.store.tx() as c:
   result=c.execute(update(accounts).where(accounts.c.id==aid,accounts.c.auth_version==row['auth_version']).values(password_hash=verifier,auth_version=row['auth_version']+1))
   if result.rowcount!=1:raise Problem('account_changed',409)
   self.store.event(c,'account_password_changed',actor_id=aid)
  return {'status':'password_changed','must_sign_in':True}
