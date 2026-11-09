"""Explicit virtual health device. Never imports or runs the host executor."""
from pathlib import Path
import secrets
from .schemas import Enroll,Result
from .secret_store import SecretStore,SecretUnavailable

PREFIX='DEMO-VIRTUAL-'
def is_virtual(name):return name.startswith(PREFIX)
class DemoNode:
 def __init__(self,store):
  self.store=store;self.path=store.settings.demo_config_path;self.identity=None
  if self.path and (Path(self.path).exists() or Path(self.path).is_symlink()):
   try:
    value=SecretStore(self.path).read()
    if set(value)!={'device_id','credential'}:raise ValueError()
    with store.tx() as c:node=store.check_agent(c,value['device_id'],value['credential'])
    if not is_virtual(node['name']):raise ValueError()
    self.identity=value
   except Exception:pass  # fail closed, never create or undo revocation
 def create(self):
  from .store import Problem,devices
  from sqlalchemy import select
  if not self.path:raise Problem('demo_not_configured',409)
  if Path(self.path).exists() or Path(self.path).is_symlink():
   if not self.identity:raise Problem('demo_identity_unavailable',409)
   return {'device_id':self.identity['device_id'],'virtual':True,'created':False}
  with self.store.tx() as c:
   if any(is_virtual(n) for n in c.execute(select(devices.c.name)).scalars()):raise Problem('demo_already_exists',409)
   ticket=self.store.ticket()
   identity=self.store.enroll(Enroll(enrollment_token=ticket['enrollment_token'],name=PREFIX+'WIN-01',os_family='Windows'),_virtual=True)
   try:SecretStore(self.path).write(identity)
   except SecretUnavailable:raise Problem('demo_identity_unavailable',503) from None
  self.identity=identity
  return {'device_id':identity['device_id'],'virtual':True,'created':True}
 def tick(self):
  from .store import Problem
  if not self.identity:return
  value=self.identity
  try:
   task=self.store.poll(value['device_id'],value['credential'])
   if not task:return
   if task['payload'].get('kind')=='health':
    result=Result(lease_id=task['lease_id'],outcome='ok',exit_code=0,duration_ms=1,cpu_percent=12.5,memory_percent=37.0,disk_free_gb=128.0,
     output='DEMO SIMULATION: fixed virtual metrics; no physical host sampled.')
   else:result=Result(lease_id=task['lease_id'],outcome='failed',exit_code=1,duration_ms=0,output='Virtual read-only health simulator: execution is unsupported.')
   self.store.finish(value['device_id'],value['credential'],task['task_id'],result)
  except Problem:pass
