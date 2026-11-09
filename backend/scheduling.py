"""Owned, reviewed, accounts-only recurring work over the existing exact dispatcher."""
import copy,secrets,time
from datetime import datetime,timezone,timedelta
from threading import Event,Thread,BoundedSemaphore,current_thread
from sqlalchemy import select,update,func,or_
from .config import ROOT
from .identity import Identity
from .store import schedules,schedule_runs,tasks,workflows,workflow_steps,accounts,Problem,ACTIVE
from .schedule_types import RuleInput,RuleDecision,Calendar,Template,RunBrief
from .schedule_clock import next_slot,slot_key
from .schedule_vault import ScheduleVault,canonical,template_hash,definition_fingerprint
from .secret_store import SecretUnavailable
from .targets import TargetScope,resolve_scope,FleetPlanner,ExpandedPlan
from .planner import ActionPlan
from .schemas import WorkflowRequest,Decision
from .recovery_types import fingerprint
BLOCKING=('claimed','planning','awaiting_confirmation','queued','running','outcome_unknown','cancel_requested')
RUN_ALLOWED=('claimed','planning','awaiting_confirmation','queued','running','outcome_unknown')
class Scheduler:
 def __init__(self,store,ops,planner):
  self.store=store;self.ops=ops;self.auth=ops.auth;self.planner=planner
  self.vault=ScheduleVault(store.settings.schedule_vault_path or ROOT/'.local/schedules')
  self.stop=Event();self.slots=BoundedSemaphore(5);self.clock=None;self.workers=set()
 def principal(self,owner):
  if self.auth.s.auth_mode!='accounts':raise Problem('schedule_accounts_required',409)
  with self.store.tx() as c:
   row=c.execute(select(accounts).where(accounts.c.id==owner)).mappings().first()
   if not row or row['disabled']:raise Problem('owner_access_revoked',403)
   principal=self.auth.accounts.principal(Identity(owner,(),time.time()+30,row['auth_version']))
   if not principal.operator():raise Problem('owner_access_revoked',403)
   return principal
 def write_allowed(self):
  self.ops.execute()
  if self.auth.s.auth_mode!='accounts':raise Problem('schedule_accounts_required',409)
 def visible(self,row,p):
  ids=row["consented_ids"] or (row["target_scope"]["ids"] if row["target_scope"]["kind"]=="manual" else [])
  return all(p.device(d) for d in ids) if ids else "*" in p.device_ids
 def row(self,c,sid,own=True):
  row=c.execute(select(schedules).where(schedules.c.id==sid,schedules.c.deleted==False)).mappings().first()
  if not row:raise Problem('not_found',404)
  p=self.ops.p
  if row['owner_id']!=p.id and (own or not p.audit() or not self.visible(row,p)):raise Problem('not_found',404)
  return row
 def inventory(self,p):return [d for d in self.store.list_devices() if p.device(d['id']) and not d['revoked']]
 def selected(self,row,p,strict=True):
  scope=TargetScope.model_validate(row['target_scope'],strict=True);inventory=self.inventory(p)
  if not strict and scope.kind=='manual':return [d for d in inventory if d['id'] in scope.ids]
  return resolve_scope(scope,inventory)
 def review(self,row,p):
  try:template=self.vault.load(row)
  except SecretUnavailable:raise Problem("schedule_template_unavailable",503) from None
  selected=self.selected(row,p)
  if not selected:raise Problem('schedule_no_targets',422)
  if row['mode']=='script' and len({d['os_family'] for d in selected})!=1:raise Problem('schedule_script_same_os',422)
  ids=sorted(d['id'] for d in selected)
  sha=definition_fingerprint(row,ids)
  eligible,skips=self.eligible(selected,p,template,ids)
  return {'definition_hash':sha,'device_ids':ids,'devices':[{'id':d['id'],'name':d['name'],'os_family':d['os_family'],'device_kind':d.get('device_kind','unknown')} for d in selected],
   'eligible_count':len(eligible),'skipped':skips,'automatic':row['mode']!='prompt','template_hash':row['template_hash'],
   'limits':{'workers':5,'session':5,'fleet':20},'new_devices_require_review':True}
 def eligible(self,selected,p,template,consent):
  eligible=[];skips=[]
  for d in selected:
   reason=None
   if not p.device(d['id']):reason='access_revoked'
   elif d['id'] not in consent:reason='outside_consent'
   elif d['revoked']:reason='device_unavailable'
   elif template.mode=='script':
    if not d['commands_enabled']:reason='commands_disabled'
    elif d['agent_protocol']<1:reason='agent_upgrade_required'
    elif template.script.runtime in ('powershell','cmd') and d['os_family']!='Windows':reason='unsupported_platform'
   elif template.mode=='errors':
    if not d['diagnostics_enabled']:reason='diagnostics_disabled'
    elif d['os_family']=='Linux' and not template.linux_unit:reason='unit_required'
   if reason:skips.append({'device_id':d['id'],'reason':reason})
   else:eligible.append(d)
  return eligible,skips
 def public(self,row,detail=False):
  value=dict(row);value['state']='enabled' if row['enabled'] else 'paused';value['owned']=row['owner_id']==self.ops.p.id;value['template']=None;value['review']=None;value['review_error']=None
  if detail and value['owned'] and self.ops.p.operator():
   try:value['template']=self.vault.load(row).model_dump();value['review']=self.review(row,self.ops.p)
   except SecretUnavailable:value['review_error']='template_unavailable'
   except Problem as e:value['review_error']='template_unavailable' if e.code=='schedule_template_unavailable' else e.code
  return value
 def listing(self):
  if not self.ops.p.operator() and not self.ops.p.audit():raise Problem('operator_forbidden',403)
  with self.store.tx() as c:
   self.store.maintain(c)
   rows=c.execute(select(schedules).where(schedules.c.deleted==False).order_by(schedules.c.updated.desc()).limit(100)).mappings()
   return [self.public(r) for r in rows if r['owner_id']==self.ops.p.id or self.ops.p.audit() and self.visible(r,self.ops.p)]
 def get(self,sid):
  with self.store.tx() as c:
   row=self.row(c,sid,own=False);value=self.public(row,True)
   value['runs']=[dict(r) for r in c.execute(select(schedule_runs).where(schedule_runs.c.schedule_id==sid,schedule_runs.c.created>=time.time()-30*86400).order_by(schedule_runs.c.created.desc()).limit(100)).mappings()]
   return value
 def save(self,data,sid=None):
  self.write_allowed();now=time.time();old=None
  with self.store.tx() as c:
   if sid:
    old=self.row(c,sid)
    if data.expected_revision!=old['revision']:raise Problem('schedule_changed',409)
    revision=old['revision']+1
   else:
    if c.scalar(select(func.count()).select_from(schedules).where(schedules.c.deleted==False))>=100 or c.scalar(select(func.count()).select_from(schedules).where(schedules.c.deleted==False,schedules.c.owner_id==self.ops.p.id))>=20:raise Problem('schedule_limit',409)
    sid=secrets.token_hex(16);revision=1
   # Resolve explicitly now, so invalid/manual out-of-scope IDs cannot become durable rules.
   selected=resolve_scope(data.targets,self.inventory(self.ops.p))
   if not selected:raise Problem('schedule_no_targets',422)
   if data.template.mode=='script' and len({d['os_family'] for d in selected})!=1:raise Problem('schedule_script_same_os',422)
   try:sha=self.vault.save(sid,revision,self.ops.p.id,data.template)
   except SecretUnavailable:raise Problem('schedule_template_unavailable',503) from None
   row={'id':sid,'owner_id':self.ops.p.id,'title':data.title,'revision':revision,'mode':data.template.mode,'calendar':data.calendar.model_dump(),'target_scope':data.targets.model_dump(),'template_hash':sha,
    'consented_ids':[],'definition_hash':None,'consent_id':None,'enabled':False,'deleted':False,'created':old['created'] if old else now,'updated':now,'next_due':None}
   if old:c.execute(update(schedules).where(schedules.c.id==sid).values(**row))
   else:c.execute(schedules.insert().values(**row))
   self.store.event(c,'schedule_saved',actor_id=self.ops.p.id);self.store.maintain(c)
  self.vault.cleanup(sid,self.vault.path(sid,revision,sha))
  return self.get(sid)
 def decide(self,sid,data):
  self.write_allowed()
  with self.store.tx() as c:
   row=self.row(c,sid)
   if row['revision']!=data.expected_revision:raise Problem('schedule_changed',409)
   review=self.review(row,self.ops.p)
   if data.definition_hash!=review['definition_hash']:raise Problem('invalid_confirmation',409)
   if not data.consent:raise Problem('schedule_consent_required',422)
   if row['enabled'] and row['definition_hash']==review['definition_hash'] and row['consented_ids']==review['device_ids']:return self.get(sid)
   if row['mode']=='script' and review['skipped']:raise Problem('schedule_targets_not_ready',409)
   following=next_slot(row['calendar'],time.time())
   if following is None:raise Problem('schedule_no_future_slot',422)
   # Fresh consent nonce prevents a paused then resumed rule from reviving old queued work.
   c.execute(update(schedules).where(schedules.c.id==sid).values(consented_ids=review['device_ids'],definition_hash=review['definition_hash'],consent_id=secrets.token_hex(16),enabled=True,next_due=following[0],updated=time.time()))
   self.store.event(c,'schedule_consent_granted',actor_id=self.ops.p.id)
  return self.get(sid)
 def pause(self,sid,data,delete=False):
  self.write_allowed()
  with self.store.tx() as c:
   row=self.row(c,sid)
   if row['revision']!=data.expected_revision:raise Problem('schedule_changed',409)
   c.execute(update(schedules).where(schedules.c.id==sid).values(enabled=False,deleted=delete,definition_hash=None,consent_id=None,consented_ids=[],next_due=None,updated=time.time()))
   self.store.maintain(c);self.store.event(c,'schedule_deleted' if delete else 'schedule_paused',actor_id=self.ops.p.id)
  if delete:self.vault.cleanup(sid);return {'deleted':True}
  return self.get(sid)
 def authorize(self,run_id,owner,did=None,task_id=None):
  if self.stop.is_set():raise Problem("scheduler_stopped",409)
  with self.store.tx() as c:
   cache=getattr(self.store.tx_context,'schedule_authorizations',{})
   key=(run_id,owner)
   if key in cache:p,run,row=cache[key]
   else:
    run=c.execute(select(schedule_runs).where(schedule_runs.c.id==run_id)).mappings().first()
    row=c.execute(select(schedules).where(schedules.c.id==run['schedule_id'])).mappings().first() if run else None
    if not run or not row or run['owner_id']!=owner or row['owner_id']!=owner or not row['enabled'] or row['deleted'] or row['revision']!=run['revision'] or row['definition_hash']!=run['definition_hash'] or row['consent_id']!=run['consent_id'] or run['state'] not in RUN_ALLOWED:raise Problem('owner_access_revoked',403)
    if definition_fingerprint(row,row['consented_ids'])!=row['definition_hash']:raise Problem('owner_access_revoked',403)
    p=self.principal(owner)
    try:self.vault.load(row)
    except SecretUnavailable:raise Problem('schedule_template_unavailable',409) from None
    if not set(run['target_snapshot']).issubset(row['consented_ids']):raise Problem('owner_access_revoked',403)
    cache[key]=(p,run,row)
    if hasattr(self.store.tx_context,'schedule_authorizations'):self.store.tx_context.schedule_authorizations=cache
   if did is not None and (not p.device(did) or did not in run['target_snapshot']):raise Problem('owner_access_revoked',403)
   if task_id:
    task=c.execute(select(tasks).join(workflow_steps,workflow_steps.c.task_id==tasks.c.id).where(tasks.c.id==task_id,tasks.c.schedule_run_id==run_id,workflow_steps.c.workflow_id==run['workflow_id'])).mappings().first()
    if not task or task['owner_id']!=owner or task['device_id']!=did:raise Problem('owner_access_revoked',403)
   return p
 def fixed_steps(self,template,selected,at):
  steps=[]
  end=datetime.fromtimestamp(at,timezone.utc);start=end-timedelta(hours=24)
  for d in selected:
   step={'device_id':d['id'],'kind':'health'}
   if template.mode=='script':step={'device_id':d['id'],'kind':'command',**template.script.model_dump()}
   elif template.mode=='errors':
    scope={'start_utc':start.isoformat().replace('+00:00','Z'),'end_utc':end.isoformat().replace('+00:00','Z'),'max_events':100}
    scope.update({'source':'windows_events','channels':['Application','System']} if d['os_family']=='Windows' else {'source':'systemd','unit':template.linux_unit})
    step={'device_id':d['id'],'kind':'investigate','scope':scope}
   steps.append(step)
  return ExpandedPlan.model_validate({'status':'plan','steps':steps},strict=True)
 def confirmation_kind(self,run_id):
  with self.store.tx() as c:
   mode=c.scalar(select(schedules.c.mode).join(schedule_runs,schedule_runs.c.schedule_id==schedules.c.id).where(schedule_runs.c.id==run_id))
   return 'confirmation_requested' if mode=='prompt' else 'schedule_plan_prepared'
 def automatic(self,run_id,wid):
  with self.store.tx() as c:
   run=c.execute(select(schedule_runs).where(schedule_runs.c.id==run_id)).mappings().one()
   if run['workflow_id']!=wid:raise Problem('invalid_confirmation',409)
   p=self.authorize(run_id,run['owner_id'])
   row=c.execute(select(schedules).where(schedules.c.id==run['schedule_id'])).mappings().one();template=self.vault.load(row)
   if template.mode=='prompt':raise Problem('workflow_confirmation_required',409)
   inventory={d['id']:d for d in self.store.list_devices()}
   selected=[inventory[did] for did in run['target_snapshot']]
   expected=self.fixed_steps(template,selected,run['created']).steps;actual=self.store.workflow.rows(c,wid)
   if len(actual)!=len(expected):raise Problem('invalid_confirmation',409)
   for task,step in zip(actual,expected):
    payload=self.store.plans.get(task['id'])
    if not payload or payload.get('schedule_run_id')!=run_id:raise Problem('invalid_confirmation',409)
    raw=step.model_dump()
    if any(payload.get(k)!=v for k,v in raw.items()):raise Problem('invalid_confirmation',409)
   return True
 def refresh(self,c):
  for r in c.execute(select(schedule_runs).join(workflows,workflows.c.id==schedule_runs.c.workflow_id).where(or_(schedule_runs.c.state.in_(BLOCKING),workflows.c.state.in_(BLOCKING)))).mappings().all():
   w=c.execute(select(workflows.c.state).where(workflows.c.id==r['workflow_id'])).scalar()
   if w=='planner_failed':w='plan_failed'
   if w and w!=r['state']:
    brief={**r['summary']}
    if w not in BLOCKING:brief['code']='completed' if w in ('verified','partial') else 'stopped'
    c.execute(update(schedule_runs).where(schedule_runs.c.id==r['id']).values(state=w,summary=brief,updated=time.time()))
 def overlap(self,c,sid):
  self.store.maintain(c)
  return c.execute(select(schedule_runs.c.id).outerjoin(workflows,workflows.c.id==schedule_runs.c.workflow_id).where(schedule_runs.c.schedule_id==sid,or_(schedule_runs.c.state.in_(BLOCKING),workflows.c.state.in_(BLOCKING)))).first() is not None
 def claim(self,c,row,key,now):
  existing=c.execute(select(schedule_runs).where(schedule_runs.c.schedule_id==row['id'],schedule_runs.c.slot_key==key)).mappings().first()
  if existing:return dict(existing),False
  if not row['enabled'] or row['deleted'] or not row['definition_hash'] or not row['consent_id']:raise Problem('schedule_paused',409)
  overlap=self.overlap(c,row['id']);rid=secrets.token_hex(16)
  run={'id':rid,'schedule_id':row['id'],'owner_id':row['owner_id'],'revision':row['revision'],'slot_key':key,'definition_hash':row['definition_hash'],'consent_id':row['consent_id'],'target_snapshot':[],'workflow_id':None,
   'state':'skipped_overlap' if overlap else 'claimed','created':now,'updated':now,'summary':RunBrief(code='overlap' if overlap else '').model_dump()}
  c.execute(schedule_runs.insert().values(**run));return run,not overlap
 def manual(self,sid,data):
  self.write_allowed()
  with self.store.tx() as c:row=self.row(c,sid);run,new=self.claim(c,row,'manual:'+data.request_id,time.time())
  if new:self.launch(run['id'])
  return self.run(sid,run['id'])
 def run(self,sid,rid):
  with self.store.tx() as c:
   self.row(c,sid,own=False);self.store.maintain(c)
   run=c.execute(select(schedule_runs).where(schedule_runs.c.id==rid,schedule_runs.c.schedule_id==sid)).mappings().first()
   if not run:raise Problem('not_found',404)
   value=dict(run);value['workflow']=None
   if run['workflow_id'] and c.execute(select(workflows.c.id).where(workflows.c.id==run['workflow_id'])).first():value['workflow']=self.ops.workflow.get(run['workflow_id'])
   return value
 def finish_state(self,rid,state,code=''):
  with self.store.tx() as c:
   r=c.execute(select(schedule_runs).where(schedule_runs.c.id==rid)).mappings().first()
   if r:
    w=c.scalar(select(workflows.c.state).where(workflows.c.id==r['workflow_id'])) if r['workflow_id'] else None
    if w in BLOCKING:state=w
    c.execute(update(schedule_runs).where(schedule_runs.c.id==rid).values(state=state,summary={**r['summary'],'code':code},updated=time.time()))
 def execute(self,rid):
  try:
   with self.store.tx() as c:
    run=c.execute(select(schedule_runs).where(schedule_runs.c.id==rid)).mappings().one()
    if run['state']!='claimed':return
    row=c.execute(select(schedules).where(schedules.c.id==run['schedule_id'])).mappings().one()
    p=self.authorize(rid,run['owner_id']);template=self.vault.load(row)
    selected=self.selected(row,p,strict=False);eligible,skips=self.eligible(selected,p,template,row['consented_ids'])
    # Removed/revoked IDs are not silently omitted from metadata.
    seen={d['id'] for d in selected}
    for did in row['consented_ids']:
     if did not in seen:skips.append({'device_id':did,'reason':'access_revoked' if not p.device(did) else 'device_unavailable'})
    if not eligible:
     c.execute(update(schedule_runs).where(schedule_runs.c.id==rid).values(state='no_targets',summary=RunBrief(target_count=len(selected),eligible_count=0,skipped=skips,code='no_targets').model_dump(),updated=time.time()));return
    wid=secrets.token_hex(16);target_ids=[d['id'] for d in eligible]
    brief=RunBrief(target_count=len(selected),eligible_count=len(eligible),skipped=skips).model_dump()
    c.execute(update(schedule_runs).where(schedule_runs.c.id==rid).values(state='planning',workflow_id=wid,target_snapshot=target_ids,summary=brief,updated=time.time()))
   # No SQL lock held during LLM/network work.
   if self.stop.is_set():raise Problem('scheduler_stopped',409)
   if template.mode=='prompt':planner=FleetPlanner(self.planner,self.store.list_devices());question=template.prompt
   else:
    plan=self.fixed_steps(template,eligible,run['created'])
    class Fixed:
     def propose(self,*args):return plan,'schedule_template'
    planner=Fixed();question='Согласованный шаблон расписания '+row['id']
   data=WorkflowRequest(request_id=wid,session_id='schedule-'+row['id'],question=question,target_ids=target_ids)
   flow=self.store.workflow.request(data,planner,run['owner_id'],automation_run_id=rid)
   if template.mode!='prompt' and flow['state']=='awaiting_confirmation':
    if self.stop.is_set():raise Problem('scheduler_stopped',409)
    self.store.workflow.decision(wid,Decision(approve=True,plan_hash=flow['plan_hash']),automatic_run_id=rid)
   with self.store.tx() as c:self.store.maintain(c);self.refresh(c)
  except (SecretUnavailable,Problem,ValueError) as e:
   code=e.code if isinstance(e,Problem) else 'schedule_template_unavailable' if isinstance(e,SecretUnavailable) else 'plan_failed'
   state='template_unavailable' if code=='schedule_template_unavailable' else 'access_revoked' if code in ('owner_access_revoked','operator_forbidden','operator_unauthorized') else 'stopped' if code=='scheduler_stopped' else 'plan_failed'
   self.finish_state(rid,state,'template_unavailable' if state=='template_unavailable' else 'access_revoked' if state=='access_revoked' else 'stopped' if state=='stopped' else 'plan_failed')
  except Exception:self.finish_state(rid,'plan_failed','plan_failed')
 def launch(self,rid):
  if self.stop.is_set():self.finish_state(rid,'interrupted','interrupted');return
  if not self.slots.acquire(blocking=False):self.finish_state(rid,'worker_busy','worker_busy');return
  def work():
   try:self.execute(rid)
   finally:
    self.slots.release()
    with self.store.lock:self.workers.discard(current_thread())
  thread=Thread(target=work,daemon=True,name='pslc-schedule-worker')
  with self.store.lock:self.workers={t for t in self.workers if t.is_alive()};self.workers.add(thread)
  thread.start()
 def tick(self,now=None,startup=False):
  if self.auth.s.auth_mode!='accounts' or self.stop.is_set():return
  now=time.time() if now is None else now;launch=[]
  with self.store.tx() as c:
   self.store.maintain(c)
   if startup:
    for run in c.execute(select(schedule_runs).where(schedule_runs.c.state.in_(('claimed','planning')))).mappings().all():
     if not run['workflow_id'] or not c.execute(select(workflows.c.id).where(workflows.c.id==run['workflow_id'])).first():c.execute(update(schedule_runs).where(schedule_runs.c.id==run['id']).values(state='interrupted',summary={**run['summary'],'code':'interrupted'},updated=now))
   for row in c.execute(select(schedules).where(schedules.c.enabled==True,schedules.c.deleted==False,schedules.c.next_due<=now).order_by(schedules.c.next_due)).mappings().all():
    missed=startup or now-row['next_due']>90
    run,new=self.claim(c,row,slot_key(row['calendar'],row['next_due']),now)
    if new and missed:c.execute(update(schedule_runs).where(schedule_runs.c.id==run['id']).values(state='missed',summary=RunBrief(code='schedule_missed').model_dump()))
    elif new:launch.append(run['id'])
    following=next_slot(row['calendar'],now)
    c.execute(update(schedules).where(schedules.c.id==row['id']).values(next_due=following[0] if following else None))
  for rid in launch:self.launch(rid)
 def start(self):
  if self.clock is not None or self.auth.s.auth_mode!='accounts':return
  self.tick(startup=True)
  def clock():
   while not self.stop.wait(1):
    try:self.tick()
    except Exception:pass # Typed API readiness reports DB health; never emit template/exception text.
  self.clock=Thread(target=clock,daemon=True,name='pslc-schedule-clock');self.clock.start()
 def close(self):
  self.stop.set()
  if self.clock:self.clock.join(timeout=2)
  # Bounded model requests may finish after stop; their automatic admission rechecks stop/consent.
