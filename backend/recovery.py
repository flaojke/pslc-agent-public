"""Exact paired plans and owner-bound manual rollback, using the existing dispatcher."""
import time,secrets,json
from pathlib import PureWindowsPath,PurePosixPath
from sqlalchemy import select,update
from .store import tasks,devices,workflows,workflow_steps,task_recoveries,Problem,uid,digest,ACTIVE
from .recovery_types import RecoveryResources,RestoreProgram,RecoveryReceipt,make_program,validate_pair,fingerprint,canonical
MUTATING=('command','install','rollback')
BLOCKING=ACTIVE+('queued','awaiting_confirmation')
class Recovery:
 def __init__(self,store):self.store=store
 def admit(self,c,payload):
  if payload['kind'] not in MUTATING:return
  node=c.execute(select(devices).where(devices.c.id==payload['device_id'])).mappings().first()
  if not node or node['revoked']:raise Problem('device_unavailable',404)
  if node['agent_protocol']<1:raise Problem('agent_upgrade_required',409)
  if payload['kind']=='command' and not node['commands_enabled']:raise Problem('commands_disabled',403)
  if payload['kind']=='install' and not node['installations_enabled']:raise Problem('installations_disabled',403)
  if payload['kind']=='rollback':
   original=c.execute(select(tasks).where(tasks.c.id==payload.get('original_task_id'))).mappings().first()
   row=c.execute(select(task_recoveries).where(task_recoveries.c.original_task_id==payload.get('original_task_id'))).mappings().first()
   if not original or not row or original['owner_id']!=payload['owner_id'] or original['device_id']!=payload['device_id']:raise Problem('not_found',404)
   if original['state'] in BLOCKING:raise Problem('rollback_reconciliation_required',409)
   if row['program']!=payload['anti_program'] or row['original_plan_hash']!=payload['original_plan_hash'] or not row['receipt'] or fingerprint(row['receipt'])!=payload['receipt_hash']:raise Problem('recovery_changed',409)
   if row['receipt']['expires']<=time.time() or row['receipt']['state'] in ('not_needed','expired','unavailable'):raise Problem('recovery_unavailable',409)
   if original['kind']=='command' and not node['commands_enabled']:raise Problem('commands_disabled',403)
   if original['kind']=='install' and not node['installations_enabled']:raise Problem('installations_disabled',403)
   if original['kind']=='install':
    from .catalog import manifest_hash,CatalogError
    meta=original['package_meta']
    try:
     current=self.store.catalog.get(meta['id'],meta['version'],node['os_family'])
     if manifest_hash(current)!=meta['manifest_hash']:raise CatalogError('catalog_changed')
    except CatalogError:raise Problem('catalog_changed',409) from None
   if row['latest_recovery_id']:
    latest=c.execute(select(tasks).where(tasks.c.id==row['latest_recovery_id'])).mappings().first()
    if latest and latest['id']!=payload.get('task_id') and latest['state'] in BLOCKING:raise Problem('recovery_in_progress',409)
   return
  resources=RecoveryResources.model_validate(payload.get('recovery'),strict=True)
  validate_pair(payload['anti_program'],[r.model_dump() for r in resources.resources])
  for r in resources.resources:
   if r.type=='configuration_file':
    p=PureWindowsPath(r.path) if node['os_family']=='Windows' else PurePosixPath(r.path)
    if not p.is_absolute() or any(ord(ch)<32 for ch in r.path) or p.suffix.lower() not in ('.json','.ini','.conf','.config','.yaml','.yml','.xml','.toml','.properties','.cfg') and p.name!='hosts':raise Problem('unsupported_recovery_resource',422)
   elif r.type=='registry_value' and node['os_family']!='Windows':raise Problem('unsupported_recovery_resource',422)
   elif r.type=='package_presence' and payload['kind']!='install':raise Problem('unsupported_recovery_resource',422)
 def attach(self,payload,tid):
  if payload['kind'] not in ('command','install'):return
  if payload['kind']=='install':
   manifest=payload['manifest']
   if not manifest.get('recovery'):raise Problem('package_recovery_required',409)
   resources=[{'type':'package_presence','package_id':manifest['id'],'version':manifest['version'],'manifest_hash':payload['manifest_hash']}]
   payload['recovery']={'resources':resources}
  else:
   resources=RecoveryResources.model_validate(payload.get('recovery'),strict=True).model_dump()['resources']
   payload['recovery']={'resources':resources}
  payload['task_id']=tid;payload['anti_program']=make_program(resources,payload['device_id'],tid)
 def approved(self,c,t,payload):
  if t['kind'] not in MUTATING:return
  self.admit(c,payload)
  if t['kind']=='rollback':
   c.execute(update(task_recoveries).where(task_recoveries.c.original_task_id==t['recovery_of']).values(latest_recovery_id=t['id']))
   return
  program=RestoreProgram.model_validate(payload['anti_program'],strict=True).model_dump()
  existing=c.execute(select(task_recoveries).where(task_recoveries.c.original_task_id==t['id'])).mappings().first()
  if existing:raise Problem('recovery_already_recorded',409)
  c.execute(task_recoveries.insert().values(original_task_id=t['id'],device_id=t['device_id'],owner_id=t['owner_id'],original_plan_hash=t['plan_hash'],program_hash=fingerprint(program),program=program,verification_hash=fingerprint(payload['verification']) if payload.get('verification') else None,receipt=None,latest_recovery_id=None,historical=False))
 def metadata_many(self,c,ids):
  rows=list(c.execute(select(task_recoveries).where(task_recoveries.c.original_task_id.in_(ids))).mappings()) if ids else []
  latest_ids=[r['latest_recovery_id'] for r in rows if r['latest_recovery_id']]
  latest={r['id']:r for r in c.execute(select(tasks.c.id,tasks.c.state,workflow_steps.c.workflow_id).outerjoin(workflow_steps,tasks.c.id==workflow_steps.c.task_id).where(tasks.c.id.in_(latest_ids))).mappings()} if latest_ids else {}
  result={}
  for row in rows:
   receipt=row['receipt'];state='unavailable' if receipt is None else receipt['state'] if receipt['state'] in ('restored','not_needed') else 'expired' if receipt['expires']<=time.time() else receipt['state'];last=latest.get(row['latest_recovery_id'])
   result[row['original_task_id']]={'status':state,'program':row['program'],'program_hash':row['program_hash'],'expires':receipt['expires'] if receipt else None,'historical':row['historical'],'latest_recovery_id':row['latest_recovery_id'],'latest_recovery_state':last['state'] if last else None,'latest_recovery_workflow_id':last['workflow_id'] if last else None}
  return result
 def metadata(self,c,tid):return self.metadata_many(c,[tid]).get(tid,{'status':'not_recorded'})
 def accept(self,c,t,data):
  original_id=t['recovery_of'] if t['kind']=='rollback' else t['id']
  row=c.execute(select(task_recoveries).where(task_recoveries.c.original_task_id==original_id)).mappings().first()
  proof=data.recovery
  if not row:return 'recovery_unavailable'
  if not proof:
   if data.outcome in ('outcome_unknown','recovery_required','recovery_unavailable'):return data.outcome
   return 'recovery_unavailable' if data.outcome in ('process_succeeded','installed','already_installed','reboot_required','restored') else None
  receipt=proof.receipt
  program=RestoreProgram.model_validate(row['program'],strict=True)
  capsule=fingerprint({'device_id':row['device_id'],'task_id':original_id,'plan_hash':row['original_plan_hash'],'program_hash':row['program_hash']})
  if receipt.device_id!=t['device_id'] or receipt.original_task_id!=original_id or receipt.original_plan_hash!=row['original_plan_hash'] or receipt.program_hash!=row['program_hash'] or receipt.resource_set_hash!=program.resource_set_hash or receipt.capsule_id!=capsule or receipt.resource_count!=sum(program.type_counts.model_dump().values()) or receipt.expires>time.time()+7*86400+600 or proof.phase!=('rollback' if t['kind']=='rollback' else 'forward'):raise Problem('invalid_recovery_proof',422)
  if row['receipt'] and (receipt.expires!=row['receipt']['expires'] or receipt.baseline_hash!=row['receipt'].get('baseline_hash') or receipt.post_hash!=row['receipt'].get('post_hash')):raise Problem('invalid_recovery_proof',422)
  if receipt.state not in ('unavailable','expired','not_needed') and receipt.baseline_hash is None:raise Problem('invalid_recovery_proof',422)
  if receipt.state=='restored' and receipt.post_hash is None:raise Problem('invalid_recovery_proof',422)
  c.execute(update(task_recoveries).where(task_recoveries.c.original_task_id==original_id).values(receipt=receipt.model_dump(),historical=bool(row['historical'] or t['kind']=='rollback' and receipt.state=='restored' and data.outcome=='restored')))
  if t['kind']=='rollback':
   expected={'restored':'restored','conflict':'recovery_conflict','partial':'recovery_partial'}.get(receipt.state,'recovery_unavailable')
   return expected if data.outcome==expected else 'verification_failed'
  if data.outcome=='already_installed' and receipt.state=='not_needed':return None
  if receipt.state!='ready':return 'outcome_unknown'
  return None
 def prepare(self,ops,target_id,data,group=False):
  ops.execute();owner=ops.p.id
  binding={'target':target_id,'group':group,'session_id':data.session_id,'owner_id':owner}
  rh=fingerprint(binding);wid=data.request_id
  with self.store.tx() as c:
   self.store.maintain(c)
   existing=c.execute(select(workflows).where(workflows.c.id==wid)).mappings().first()
   if existing:
    if existing['owner_id']!=owner:raise Problem('not_found',404)
    if existing['request_hash']!=rh:raise Problem('request_id_conflict',409)
    return self.store.workflow.public(c,existing)
   if group:
    original=c.execute(select(workflows).where(workflows.c.id==target_id,workflows.c.owner_id==owner)).mappings().first()
    if not original:raise Problem('not_found',404)
    selected=list(reversed(self.store.workflow.rows(c,target_id)))
   else:
    original=c.execute(select(tasks).where(tasks.c.id==target_id,tasks.c.owner_id==owner)).mappings().first()
    if not original:raise Problem('not_found',404)
    selected=[original]
   if any(t['kind']=='rollback' for t in selected):raise Problem('rollback_of_rollback_forbidden',409)
   readonly=sum(t['kind'] not in ('command','install') for t in selected)
   if any(t['state'] in BLOCKING for t in selected):raise Problem('rollback_reconciliation_required',409)
   selected=[t for t in selected if t['kind'] in ('command','install')]
   if not selected:raise Problem('rollback_not_needed',409)
   now=time.time();drafts=[];previews=[];noops=0
   for index,original in enumerate(selected):
    ops.device(original['device_id']);self.store.authorize_owner(owner,original['device_id'])
    row=c.execute(select(task_recoveries).where(task_recoveries.c.original_task_id==original['id'])).mappings().first()
    if not row or not row['receipt']:raise Problem('recovery_unavailable',409)
    if row['receipt']['state']=='not_needed' and original['state']=='already_installed':
     if not group:raise Problem('rollback_not_needed',409)
     noops+=1;continue
    tid=uid();payload={'version':1,'kind':'rollback','owner_id':owner,'device_id':original['device_id'],'session_id':data.session_id,'workflow_id':wid,'task_id':tid,'original_task_id':original['id'],'original_kind':original['kind'],'original_plan_hash':original['plan_hash'],'anti_program':row['program'],'capsule_id':row['receipt']['capsule_id'],'receipt_hash':fingerprint(row['receipt']),'timeout_seconds':2100 if original['kind']=='install' else 180}
    self.admit(c,payload)
    task={'id':tid,'owner_id':owner,'device_id':original['device_id'],'session_id':data.session_id,'kind':'rollback','runtime':'native','state':'awaiting_confirmation','plan_hash':fingerprint(payload),'expires':now+self.store.settings.plan_ttl,'created':now+index*.000001,'package_meta':None,'recovery_of':original['id']}
    drafts.append((task,payload));previews.append({'index':index,'task_id':tid,'device_name':c.scalar(select(devices.c.name).where(devices.c.id==original['device_id'])),'payload':payload,'effect':'Восстановление только объявленных ресурсов по локальному baseline; проверка drift и равенства','execution_context':'Та же identity и независимые opt-in. Без повышения прав.'})
   if not drafts:raise Problem('rollback_not_needed',409)
   self.store.plans.purge();self.store.workflow.previews.purge()
   if len(self.store.plans.data)+len(drafts)>self.store.plans.capacity or len(self.store.workflow.previews.data)>=self.store.workflow.previews.capacity:raise Problem('volatile_capacity_reached',429)
   required=sum(len(json.dumps(payload,ensure_ascii=False).encode()) for _,payload in drafts)
   if self.store.plans.max_bytes is not None and sum(self.store.plans.sizes.values())+required>self.store.plans.max_bytes:raise Problem('volatile_capacity_reached',429)
   preview={'status':'plan','steps':previews,'read_only_steps_without_changes':readonly,'already_installed_steps_without_changes':noops}
   ph=fingerprint({'workflow_id':wid,'session_id':data.session_id,'preview':preview})
   c.execute(workflows.insert().values(id=wid,owner_id=owner,session_id=data.session_id,request_hash=rh,state='awaiting_confirmation',source='manual_rollback',plan_hash=ph,created=now,expires=now+self.store.settings.plan_ttl,decision=None,execution_mode='group',target_snapshot=list(dict.fromkeys(t['device_id'] for t,_ in drafts)),conversation_id=None))
   for index,(t,payload) in enumerate(drafts):
    self.store.plans.put(t['id'],payload,self.store.settings.plan_ttl)
    c.execute(tasks.insert().values(**t));c.execute(workflow_steps.insert().values(workflow_id=wid,ordinal=index,task_id=t['id'],verification_hash=None))
   self.store.workflow.previews.put(wid,preview,self.store.settings.plan_ttl);self.store.event(c,'confirmation_requested',drafts[0][0])
   return self.store.workflow.get(wid)
