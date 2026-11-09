"""Metadata-only sequential workflows over the existing bounded dispatcher."""
import json
import secrets
import time
from threading import BoundedSemaphore
from sqlalchemy import select, update
from .catalog import CatalogError, manifest_hash, installer_argv
from .planner import ActionPlan, PlanningError
from .integrations import IntegrationError
from .diagnostic_types import admit_scope, scope_hash
from .store import workflows, workflow_steps, tasks, devices, investigation_jobs, LOCAL_OWNER, Problem, Volatile, digest, uid, ACTIVE

SUCCESS=('verified','already_installed','restored')
PENDING=('awaiting_confirmation','queued')
LIVE=('awaiting_confirmation','queued','running')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


class Workflow:
    def __init__(self,store):
        self.store=store
        self.previews=Volatile(capacity=128)
        self.planning_slots=BoundedSemaphore(5)

    def link(self,c,tid):
        return c.execute(select(workflow_steps).where(workflow_steps.c.task_id==tid)).mappings().first()

    def rows(self,c,wid):
        return c.execute(select(tasks,workflow_steps.c.ordinal,workflow_steps.c.verification_hash).join(
            workflow_steps,tasks.c.id==workflow_steps.c.task_id).where(workflow_steps.c.workflow_id==wid).order_by(workflow_steps.c.ordinal)).mappings().all()

    def check_payload(self,c,payload):
        self.store.authorize_owner(payload.get('owner_id',LOCAL_OWNER),payload['device_id'],payload.get('schedule_run_id'))
        d=c.execute(select(devices).where(devices.c.id==payload['device_id'])).mappings().first()
        if not d or d['revoked']: raise Problem('device_unavailable')
        if payload['kind']=='command' and not d['commands_enabled']: raise Problem('commands_disabled',403)
        if payload['kind']=='investigate':
            if not d['diagnostics_enabled']:raise Problem('diagnostics_disabled',403)
            try:
                scope=admit_scope(payload['scope'],d['os_family'])
                if scope_hash(scope)!=payload['scope_hash']:raise ValueError()
            except ValueError:raise Problem('invalid_diagnostic_scope',422) from None
        if payload['kind']=='install':
            if not d['installations_enabled']: raise Problem('installations_disabled',403)
            try:
                approved=self.store.catalog.get(payload['manifest']['id'],payload['manifest']['version'],d['os_family'])
                if manifest_hash(approved)!=payload['manifest_hash']: raise CatalogError('catalog_changed')
            except CatalogError: raise Problem('catalog_changed') from None
        self.store.recovery.admit(c,payload)
        return d

    def refresh(self,c,restarted=False):
        for w in c.execute(select(workflows).where(workflows.c.state.in_(LIVE+('planning','outcome_unknown','cancel_requested')))).mappings().all():
            wid=w['id'];rows=self.rows(c,wid)
            if w['state']=='planning':
                if restarted: c.execute(update(workflows).where(workflows.c.id==wid).values(state='planner_failed'))
                continue
            if w.get('execution_mode')=='group':
                self.refresh_group(c,w);continue
            if w['state'] in ('outcome_unknown','cancel_requested'):
                if rows and all(t['state'] not in ACTIVE for t in rows):
                    c.execute(update(workflows).where(workflows.c.id==wid).values(state='stopped'))
                    self.store.event(c,'workflow_stopped',rows[0])
                continue
            state=None
            if any(t['state']=='outcome_unknown' for t in rows): state='outcome_unknown'
            elif any(t['state']=='cancel_requested' for t in rows): state='cancel_requested'
            elif any(t['state']=='plan_expired' for t in rows): state='plan_expired'
            elif any(t['state']=='catalog_changed' for t in rows): state='policy_changed'
            elif any(t['state'] not in SUCCESS+PENDING+('running',) for t in rows): state='stopped'
            elif rows and all(t['state'] in SUCCESS for t in rows):
                pending=any(t['kind']=='investigate' and self.store.investigations.metadata(c,t['id'])['analysis_state'] in ('pending','analyzing') for t in rows)
                if pending:
                    c.execute(update(workflows).where(workflows.c.id==wid).values(state='running'));continue
                state='verified'
            if state is None and any(t['state'] in PENDING for t in rows) and self.previews.get(wid) is None: state='plan_expired'
            if state is None:
                try:
                    for t in rows:
                        if t['state'] not in PENDING: continue
                        payload=self.store.plans.get(t['id'])
                        if payload is None: raise Problem('plan_expired')
                        if digest(canonical(payload))!=t['plan_hash']: raise Problem('plan_changed')
                        self.check_payload(c,payload)
                except Problem as exc: state='plan_expired' if exc.code=='plan_expired' else 'policy_changed'
            if state:
                c.execute(update(workflows).where(workflows.c.id==wid).values(state=state))
                for t in rows:
                    if t['state'] in PENDING:
                        c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='plan_expired' if state=='plan_expired' else 'cancelled'))
                        self.store.plans.drop(t['id'])
                self.previews.drop(wid)
                self.store.event(c,'workflow_'+state,rows[0] if rows else None)
            elif w['state']!='awaiting_confirmation':
                c.execute(update(workflows).where(workflows.c.id==wid).values(state='running' if any(t['state']=='running' for t in rows) else 'queued'))

    def refresh_group(self,c,w):
        rows=self.rows(c,w['id'])
        if not rows:return
        if w['state']=='awaiting_confirmation':
            reason=None
            if self.previews.get(w['id']) is None:reason='plan_expired'
            for t in rows:
                if reason:break
                payload=self.store.plans.get(t['id'])
                try:
                    if payload is None:raise Problem('plan_expired')
                    if digest(canonical(payload))!=t['plan_hash']:raise Problem('plan_changed')
                    self.check_payload(c,payload)
                except Problem as e:reason='plan_expired' if e.code=='plan_expired' else 'policy_changed'
            if reason:
                for t in rows:
                    if t['state'] in PENDING:c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='plan_expired' if reason=='plan_expired' else 'cancelled'));self.store.plans.drop(t['id'])
                c.execute(update(workflows).where(workflows.c.id==w['id']).values(state=reason));self.previews.drop(w['id'])
                self.store.event(c,'workflow_'+reason,rows[0])
            return
        if w['state']=='cancel_requested':
            if all(t['state'] not in ACTIVE for t in rows):c.execute(update(workflows).where(workflows.c.id==w['id']).values(state='cancelled'))
            return
        for t in rows:
            if t['state'] not in PENDING:continue
            predecessors=[x for x in rows if x['device_id']==t['device_id'] and x['ordinal']<t['ordinal']]
            if any(x['state'] not in SUCCESS+PENDING+('running',) for x in predecessors):
                c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='cancelled'));self.store.plans.drop(t['id']);continue
            payload=self.store.plans.get(t['id'])
            try:
                if payload is None:raise Problem('plan_expired')
                if digest(canonical(payload))!=t['plan_hash']:raise Problem('plan_changed')
                self.check_payload(c,payload)
            except Problem as e:
                state=e.code if e.code in ('commands_disabled','installations_disabled','diagnostics_disabled','catalog_changed','plan_expired') else 'access_revoked'
                c.execute(update(tasks).where(tasks.c.id==t['id']).values(state=state));self.store.plans.drop(t['id']);self.store.event(c,state,t)
        rows=self.rows(c,w['id'])
        states=[t['state'] for t in rows]
        analyzing=any(t['kind']=='investigate' and self.store.investigations.metadata(c,t['id'])['analysis_state'] in ('pending','analyzing') for t in rows)
        if any(s in PENDING+('running',) for s in states) or analyzing:
            state='running' if any(s in ('running','outcome_unknown') for s in states) or analyzing else 'queued'
        elif 'outcome_unknown' in states:state='outcome_unknown'
        elif all(s in SUCCESS for s in states):state='verified'
        elif any(s in SUCCESS for s in states):state='partial'
        else:state='stopped'
        if state!=w['state']:
            c.execute(update(workflows).where(workflows.c.id==w['id']).values(state=state))
            self.store.event(c,'workflow_'+state,rows[0])
        if state in ('verified','partial','stopped'):self.previews.drop(w['id'])

    def permits(self,c,t):
        link=self.link(c,t['id'])
        if link is None: return True
        w=c.execute(select(workflows).where(workflows.c.id==link['workflow_id'])).mappings().one()
        return w['state'] in ('queued','running') and all(x['state'] in SUCCESS for x in self.rows(c,w['id']) if x['ordinal']<link['ordinal'] and (w.get('execution_mode')!='group' or x['device_id']==t['device_id']))

    def public(self,c,w,include_preview=True):
        result={k:w[k] for k in ('id','owner_id','session_id','state','source','plan_hash','created','expires','decision')}
        result.update(execution_mode=w.get('execution_mode','sequential'),target_snapshot=w.get('target_snapshot'),conversation_id=w.get('conversation_id'))
        result['steps']=[{**{k:t[k] for k in ('device_id','kind','runtime','state','summary','package_meta','recovery_of')},'index':t['ordinal'],
                          'task_id':t['id']} for t in self.rows(c,w['id'])]
        recovery=self.store.recovery.metadata_many(c,[s['recovery_of'] or s['task_id'] for s in result['steps']])
        for step in result['steps']:
            step['recovery']=recovery.get(step['recovery_of'] or step['task_id'],{'status':'not_recorded'})
            if step['kind']=='investigate':step['investigation']=self.store.investigations.metadata(c,step['task_id'])
        preview=self.previews.get(w['id'])
        result['preview']=preview if include_preview and preview and w['state'] in LIVE+('clarification','unsupported') else None
        result['preview_expires']=w['expires']
        return result

    def get(self,wid):
        with self.store.tx() as c:
            self.store.maintain(c)
            w=c.execute(select(workflows).where(workflows.c.id==wid)).mappings().first()
            if not w: raise Problem('not_found',404)
            return self.public(c,w)

    def listing(self):
        with self.store.tx() as c:
            self.store.maintain(c)
            return [self.public(c,w,include_preview=False) for w in c.execute(select(workflows).order_by(workflows.c.created.desc()).limit(30)).mappings()]

    def request(self,data,planner,owner_id=LOCAL_OWNER,automation_run_id=None):
        if data.target_ids is not None and (not data.target_ids or len(set(data.target_ids))!=len(data.target_ids) or any(not 1<=len(did)<=64 for did in data.target_ids)):raise Problem('invalid_target_selection',422)
        request_hash=digest(canonical({'owner_id':owner_id,'session_id':data.session_id,'question':data.question,'target_ids':data.target_ids,'conversation_id':data.conversation_id}))
        with self.store.tx() as c:
            existing=c.execute(select(workflows).where(workflows.c.id==data.request_id)).mappings().first()
            if existing:
                if existing['owner_id']!=owner_id:raise Problem('not_found',404)
                if existing['request_hash']!=request_hash: raise Problem('request_id_conflict')
                self.store.maintain(c)
                return self.public(c,c.execute(select(workflows).where(workflows.c.id==data.request_id)).mappings().one())
            if data.conversation_id:
                from .store import conversations
                if not c.execute(select(conversations.c.id).where(conversations.c.id==data.conversation_id,conversations.c.owner_id==owner_id,conversations.c.deleted==False)).first():raise Problem('not_found',404)
            c.execute(workflows.insert().values(execution_mode='group' if data.target_ids is not None else 'sequential',target_snapshot=data.target_ids,conversation_id=data.conversation_id,id=data.request_id,owner_id=owner_id,session_id=data.session_id,request_hash=request_hash,state='planning',source='pending',
                plan_hash=None,created=time.time(),expires=time.time()+self.store.settings.plan_ttl,decision=None))
            self.store.event(c,'schedule_requested' if automation_run_id else 'question_requested',actor_id=owner_id)
        inserted=[]
        try:
            if not self.planning_slots.acquire(blocking=False): raise Problem('planner_capacity',429)
            try:
                principal=self.store.scheduler.authorize(automation_run_id,owner_id) if automation_run_id else self.store.authorization.known(owner_id) if self.store.authorization else None
                inventory=[d for d in self.store.list_devices() if not d['revoked'] and (principal is None or principal.device(d['id']))]
                if data.target_ids is not None:
                    if not data.target_ids or len(set(data.target_ids))!=len(data.target_ids):raise Problem('invalid_target_selection',422)
                    known={d['id']:d for d in inventory}
                    if any(did not in known for did in data.target_ids):raise Problem('target_unavailable',404)
                    inventory=[known[did] for did in data.target_ids]
                try: packages=self.store.catalog.listing()['packages']
                except CatalogError: packages=[]
                proposal,source=planner.propose(data.question,inventory,packages)
            finally: self.planning_slots.release()
            if not isinstance(proposal,ActionPlan):
                with self.store.tx() as c:
                    self.previews.put(data.request_id,proposal.model_dump(),self.store.settings.plan_ttl)
                    c.execute(update(workflows).where(workflows.c.id==data.request_id).values(state=proposal.status,source=source))
                return self.get(data.request_id)
            with self.store.tx() as c:
                drafts=[];previews=[];now=time.time()
                for index,step in enumerate(proposal.steps):
                    raw=step.model_dump();kind=raw['kind'];did=raw['device_id']
                    d=c.execute(select(devices).where(devices.c.id==did)).mappings().first()
                    if not d or d['revoked']: raise Problem('device_unavailable')
                    payload={'owner_id':owner_id,'kind':kind,'device_id':did,'session_id':data.session_id,'workflow_id':data.request_id,'version':3,'timeout_seconds':30}
                    if automation_run_id:payload['schedule_run_id']=automation_run_id
                    package_meta=None;verification_hash=None;runtime='python'
                    if kind=='command':
                        payload.update(runtime=raw['runtime'],code=raw['code'],timeout_seconds=raw['timeout_seconds'],verification=raw['verification'],recovery=raw['recovery'])
                        verification_hash=digest(canonical(raw['verification']));runtime=raw['runtime']
                    elif kind=='investigate':
                        try:scope=admit_scope(raw['scope'],d['os_family'])
                        except ValueError:raise Problem('invalid_diagnostic_scope',422) from None
                        payload.update(scope=scope.model_dump(),scope_hash=scope_hash(scope),timeout_seconds=30)
                        runtime=scope.source
                    elif kind=='install':
                        try: package=self.store.catalog.get(raw['package_id'],raw['package_version'],d['os_family'])
                        except CatalogError: raise Problem('package_not_approved') from None
                        payload.update(manifest=package.model_dump(),manifest_hash=manifest_hash(package),timeout_seconds=package.timeout_seconds,
                                       install_argv=installer_argv(package,'{installer}'))
                        package_meta={'id':package.id,'version':package.version,'os_family':package.os_family,'sha256':package.sha256,'manifest_hash':manifest_hash(package)}
                        runtime=package.installer
                    tid=uid();self.store.recovery.attach(payload,tid)
                    self.check_payload(c,payload)
                    row={'id':tid,'owner_id':owner_id,'device_id':did,'session_id':data.session_id,'kind':kind,'runtime':runtime,'state':'awaiting_confirmation',
                         'plan_hash':digest(canonical(payload)),'expires':now+self.store.settings.plan_ttl,'created':now+index*.000001,'package_meta':package_meta,'schedule_run_id':automation_run_id}
                    drafts.append((row,payload,verification_hash))
                    previews.append({'index':index,'task_id':tid,'device_name':d['name'],'os_family':d['os_family'],'payload':payload,
                        'effect':'Сбор CPU/RAM/диска без изменения настроек' if kind=='health' else 'Установка утверждённого пакета' if kind=='install' else 'Чтение ограниченного интервала журналов; причина ещё не установлена' if kind=='investigate' else 'Команда и проверка могут менять состояние узла',
                        'execution_context':'Учётная запись процесса агента; отдельная рабочая папка; повышение прав не выполняется'})
                self.store.plans.purge();self.previews.purge()
                if len(self.store.plans.data)+len(drafts)>self.store.plans.capacity or len(self.previews.data)>=self.previews.capacity:
                    raise Problem('volatile_capacity_reached',429)
                preview={'status':'plan','steps':previews}
                plan_hash=digest(canonical({'workflow_id':data.request_id,'session_id':data.session_id,'preview':preview}))
                for index,(row,payload,vhash) in enumerate(drafts):
                    self.store.plans.put(row['id'],payload,self.store.settings.plan_ttl);inserted.append(row['id'])
                    c.execute(tasks.insert().values(**row))
                    if row['kind']=='investigate':
                        c.execute(investigation_jobs.insert().values(task_id=row['id'],scope=payload['scope'],scope_hash=payload['scope_hash'],analysis_state='awaiting_collection',analysis_meta={'policy_version':'incident-v1'},created=now))
                    c.execute(workflow_steps.insert().values(workflow_id=data.request_id,ordinal=index,task_id=row['id'],verification_hash=vhash))
                self.previews.put(data.request_id,preview,self.store.settings.plan_ttl)
                c.execute(update(workflows).where(workflows.c.id==data.request_id).values(state='awaiting_confirmation',source=source,plan_hash=plan_hash,expires=now+self.store.settings.plan_ttl))
                self.store.event(c,self.store.scheduler.confirmation_kind(automation_run_id) if automation_run_id else 'confirmation_requested',drafts[0][0])
            return self.get(data.request_id)
        except (Problem,PlanningError,IntegrationError):
            for tid in inserted:self.store.plans.drop(tid)
            self.previews.drop(data.request_id)
            with self.store.tx() as c:
                c.execute(update(workflows).where(workflows.c.id==data.request_id).values(state='planner_failed'))
            raise

    def decision(self,wid,data,automatic_run_id=None):
        with self.store.tx() as c:self.store.maintain(c)
        with self.store.tx() as c:
            self.store.maintain(c)
            w=c.execute(select(workflows).where(workflows.c.id==wid)).mappings().first()
            if not w: raise Problem('not_found',404)
            if not w['plan_hash'] or not secrets.compare_digest(w['plan_hash'],data.plan_hash): raise Problem('invalid_confirmation')
            if w['decision'] is not None:
                if w['decision']!=data.approve: raise Problem('decision_already_recorded')
                return self.public(c,w)
            if w['state']!='awaiting_confirmation' or w['expires']<=time.time() or self.previews.get(wid) is None:
                raise Problem('plan_not_confirmable')
            rows=self.rows(c,wid)
            if automatic_run_id:self.store.scheduler.automatic(automatic_run_id,wid)
            preview=self.previews.get(wid)
            if digest(canonical({'workflow_id':wid,'session_id':w['session_id'],'preview':preview}))!=w['plan_hash']: raise Problem('invalid_confirmation')
            for t in rows:
                payload=self.store.plans.get(t['id'])
                if payload is None:raise Problem('plan_expired')
                self.check_payload(c,payload)
            for t in rows:
                if data.approve:self.store.recovery.approved(c,t,self.store.plans.get(t['id']))
                c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='queued' if data.approve else 'rejected'))
                if not data.approve:self.store.plans.drop(t['id'])
            c.execute(update(workflows).where(workflows.c.id==wid).values(state='queued' if data.approve else 'rejected',decision=data.approve))
            self.store.event(c,'schedule_admitted' if automatic_run_id and data.approve else 'confirmed' if data.approve else 'rejected',rows[0],wait_ms=int((time.time()-w['created'])*1000))
            if not data.approve:self.previews.drop(wid)
        return self.get(wid)

    def cancel(self,wid):
        with self.store.tx() as c:
            w=c.execute(select(workflows).where(workflows.c.id==wid)).mappings().first()
            if not w: raise Problem('not_found',404)
            rows=self.rows(c,wid)
            if w['state'] not in LIVE+('outcome_unknown','cancel_requested'): return self.public(c,w)
            active=False
            for t in rows:
                if t['kind']=='investigate':self.store.investigations.cancel_in_tx(c,t['id'])
                if t['state'] in PENDING:
                    c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='cancelled'));self.store.plans.drop(t['id'])
                elif t['state'] in ACTIVE:
                    active=True
                    if t['state']!='outcome_unknown':c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='cancel_requested'))
            c.execute(update(workflows).where(workflows.c.id==wid).values(state='cancel_requested' if active else 'cancelled'))
            self.previews.drop(wid);self.store.event(c,'workflow_cancelled',rows[0] if rows else None)
        return self.get(wid)
