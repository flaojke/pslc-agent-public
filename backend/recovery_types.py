"""Versioned recovery contract. Program is metadata, never arbitrary source or paths."""
import hashlib,json,re
from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field,model_validator,TypeAdapter
class Strict(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
Hash=Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
Id=Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
class ConfigurationFile(Strict):
 type:Literal['configuration_file']
 path:str=Field(min_length=1,max_length=1024)
 metadata:Literal['config-metadata-v1']='config-metadata-v1'
class ServiceRuntime(Strict):
 type:Literal['service_runtime']
 name:str=Field(pattern=r'^[A-Za-z0-9_.@ -]{1,128}$')
class RegistryValue(Strict):
 type:Literal['registry_value']
 hive:Literal['HKLM','HKCU']
 key:str=Field(min_length=1,max_length=512)
 name:str=Field(max_length=128)
 @model_validator(mode='after')
 def key_shape(self):
  if any(ord(c)<32 for c in self.key+self.name) or any(x in ('','.','..') for x in self.key.split(chr(92))):raise ValueError('invalid_registry_resource')
  return self
class PackagePresence(Strict):
 type:Literal['package_presence']
 package_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
 version:str=Field(pattern=r'^[a-zA-Z0-9_.+~-]{1,64}$')
 manifest_hash:Hash
Resource=Annotated[ConfigurationFile|ServiceRuntime|RegistryValue|PackagePresence,Field(discriminator='type')]
RESOURCES=TypeAdapter(list[Resource])
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def fingerprint(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
class RecoveryResources(Strict):
 resources:list[Resource]=Field(min_length=1,max_length=8)
 @model_validator(mode='after')
 def unique(self):
  keys=[canonical(r.model_dump()).casefold() for r in self.resources]
  if len(keys)!=len(set(keys)):raise ValueError('duplicate_recovery_resource')
  return self
class TypeCounts(Strict):
 configuration_file:int=Field(default=0,ge=0,le=8)
 service_runtime:int=Field(default=0,ge=0,le=8)
 registry_value:int=Field(default=0,ge=0,le=8)
 package_presence:int=Field(default=0,ge=0,le=1)
 @model_validator(mode='after')
 def total(self):
  if not 1<=sum(self.model_dump().values())<=8:raise ValueError('invalid_resource_count')
  return self
class RestoreProgram(Strict):
 version:Literal[1]=1
 op:Literal['restore-baseline-v1']='restore-baseline-v1'
 device_id:Id
 original_task_id:Id
 resource_set_hash:Hash
 type_counts:TypeCounts
class RecoveryReceipt(Strict):
 version:Literal[1]=1
 device_id:Id
 original_task_id:Id
 original_plan_hash:Hash
 program_hash:Hash
 resource_set_hash:Hash
 capsule_id:Hash
 expires:float=Field(gt=0)
 state:Literal['ready','incomplete','restored','conflict','partial','unavailable','expired','not_needed']
 baseline_hash:Hash|None=None
 post_hash:Hash|None=None
 resource_count:int=Field(ge=1,le=8)
 restored_count:int=Field(default=0,ge=0,le=8)
 @model_validator(mode='after')
 def bound(self):
  if self.restored_count>self.resource_count or self.state=='restored' and self.restored_count!=self.resource_count:raise ValueError('invalid_restored_count')
  return self
class RecoveryProof(Strict):
 phase:Literal['forward','rollback']
 receipt:RecoveryReceipt
class RecoveryRequest(Strict):
 request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')
 session_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
def make_program(resources,device_id,task_id):
 values=RecoveryResources.model_validate({'resources':resources},strict=True)
 kinds=[r.type for r in values.resources]
 return RestoreProgram(device_id=device_id,original_task_id=task_id,resource_set_hash=fingerprint([r.model_dump() for r in values.resources]),type_counts=TypeCounts(**{key:kinds.count(key) for key in TypeCounts.model_fields})).model_dump()
def validate_pair(program,resources):
 p=RestoreProgram.model_validate(program,strict=True)
 if make_program(resources,p.device_id,p.original_task_id)!=p.model_dump():raise ValueError('recovery_binding_mismatch')
 return p
