"""Admin onboarding and exact server settings; never changes remote local opt-ins."""
from pathlib import PureWindowsPath
import hashlib,json,time,secrets
from sqlalchemy import select,update
from pydantic import Field
from typing import Literal
from .schemas import Strict
from .store import devices,tasks,Volatile,Problem,digest

class NodeChanges(Strict):
 device_kind:Literal["unknown","workstation","server"]|None=None
 name:str=Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_.-]+$')
 commands_enabled:bool=False
 installations_enabled:bool=False
 diagnostics_enabled:bool=False
class OnboardingProfile(NodeChanges):
 os_family:Literal['Windows','Linux']
class Onboarding(OnboardingProfile):
 state_path:str=Field(min_length=1,max_length=240)
 ca_file:str=Field(default='',max_length=240)

CAPS={'command':'commands_enabled','install':'installations_enabled','investigate':'diagnostics_enabled'}
def node_state(row):return {**{key:row[key] for key in ('id','name','commands_enabled','installations_enabled','diagnostics_enabled','revoked')},'device_kind':row.get('device_kind','unknown')}
def state_hash(row):return digest(json.dumps(node_state(row),sort_keys=True,separators=(',',':')))

class Nodes:
 def __init__(self,store,ops):self.store=store;self.ops=ops;self.plans=Volatile(128)
 def allowed(self,did=None):
  self.ops.manage()
  principal=self.ops.auth.known(self.ops.p.id)
  if 'platform_admin' not in principal.roles:raise Problem('operator_forbidden',403)
  if did and not principal.device(did):raise Problem('not_found',404)
  if did is None and '*' not in principal.device_ids:raise Problem('operator_forbidden',403)
 def onboard(self,data):
  self.allowed()
  windows=data.os_family=='Windows'
  for path in (data.state_path,data.ca_file):
   if path and (not (PureWindowsPath(path).is_absolute() if windows else path.startswith('/')) or any(c in path for c in ('\n','\r','\0'))):raise Problem('absolute_agent_path_required',422)
  profile={k:v for k,v in data.model_dump().items() if k not in ('state_path','ca_file')}
  ticket=self.store.ticket(self.ops.p.id,profile)
  config={'server':self.store.settings.public_origin.rstrip('/'),'state':data.state_path,'ca_file':data.ca_file,**{key:profile[key] for key in CAPS.values()}}
  return {**ticket,'profile':profile,'config':config,'filename':'pslc-agent-'+data.os_family.lower()+'.json','requires_local_installation':True,'local_opt_in_independent':True}
 def plan(self,did,data):
  self.allowed(did)
  with self.store.tx() as c:row=c.execute(select(devices).where(devices.c.id==did)).mappings().first()
  if not row or row['revoked']:raise Problem('device_unavailable',404)
  from .demo import is_virtual
  if is_virtual(row['name'])!=is_virtual(data.name) or is_virtual(row['name']) and any(getattr(data,k) for k in ('commands_enabled','installations_enabled','diagnostics_enabled')):raise Problem('virtual_device_readonly',422)
  value={'owner_id':self.ops.p.id,'device_id':did,'before_hash':state_hash(row),'before':node_state(row),'changes':data.model_dump(exclude_none=True)}
  ph=digest(json.dumps(value,sort_keys=True,separators=(',',':')));pid=secrets.token_hex(16);expires=time.time()+300
  with self.store.lock:self.plans.put(pid,{**value,'plan_hash':ph,'expires':expires},300)
  return {'id':pid,**value,'plan_hash':ph,'expires':expires,'local_opt_in_independent':True}
 def decide(self,did,pid,data):
  self.allowed(did)
  with self.store.tx() as c:
   self.allowed(did)
   plan=self.plans.get(pid)
   if not plan or plan['owner_id']!=self.ops.p.id or plan['device_id']!=did:raise Problem('configuration_plan_unavailable',404)
   if plan['plan_hash']!=data.plan_hash:raise Problem('invalid_confirmation',409)
   if not data.approve:self.plans.drop(pid);return {'status':'rejected'}
   row=c.execute(select(devices).where(devices.c.id==did)).mappings().first()
   if not row or row['revoked'] or state_hash(row)!=plan['before_hash']:raise Problem('device_configuration_changed',409)
   changes=plan['changes'];c.execute(update(devices).where(devices.c.id==did).values(**changes));self.plans.drop(pid)
   # Pending server permission is rechecked independently from local execution permission.
   for kind,field in CAPS.items():
    if changes[field]:continue
    for task in c.execute(select(tasks).where(tasks.c.device_id==did,tasks.c.kind==kind,tasks.c.state.in_(('queued','awaiting_confirmation')))).mappings().all():
     state={'command':'commands_disabled','install':'installations_disabled','investigate':'diagnostics_disabled'}[kind]
     c.execute(update(tasks).where(tasks.c.id==task['id']).values(state=state));self.store.plans.drop(task['id']);self.store.event(c,state,task)
   if not changes['diagnostics_enabled']:
    for tid in c.execute(select(tasks.c.id).where(tasks.c.device_id==did,tasks.c.kind=='investigate')).scalars():self.store.investigations.cancel_in_tx(c,tid)
   self.store.event(c,'device_configuration_changed',device_id=did,actor_id=self.ops.p.id);self.store.workflow.refresh(c)
   return {'status':'applied','local_opt_in_independent':True,'device':node_state({**row,**changes})}
