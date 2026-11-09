"""Operator API facade. Core agent dispatch remains a separate trust boundary."""
from collections import Counter
from sqlalchemy import select
from .identity import current_principal,LOCAL_ID
from .store import tasks,workflows,workflow_steps,events,Problem

class AuthorizedStore:
    def __init__(self,store,auth):
        self.store=store;self.auth=auth;self.workflow=ScopedWorkflow(self);self.investigations=ScopedInvestigation(self)
    @property
    def p(self):
        principal=current_principal.get()
        if principal is None:raise Problem('operator_unauthorized',401)
        return principal
    def execute(self):
        if not self.p.operator():raise Problem('operator_forbidden',403)
    def manage(self):
        if 'platform_admin' not in self.p.roles:raise Problem('operator_forbidden',403)
    def device(self,did):
        if not self.p.device(did):raise Problem('not_found',404)
    def visible_task(self,t):return self.p.device(t['device_id']) and (t['owner_id']==self.p.id or self.p.audit())
    def task(self,tid,own=False):
        result=self.store.get_task(tid)
        if not self.visible_task(result) or own and result['owner_id']!=self.p.id:raise Problem('not_found',404)
        if result['owner_id']!=self.p.id:result['plan']=None
        return result
    def get_task(self,tid):return self.task(tid)
    def list_devices(self):return [d for d in self.store.list_devices() if self.p.device(d['id'])]
    def task_query(self):
        query=select(tasks)
        if '*' not in self.p.device_ids:query=query.where(tasks.c.device_id.in_(self.p.device_ids))
        if not self.p.audit():query=query.where(tasks.c.owner_id==self.p.id)
        return query
    def list_tasks(self):
        with self.store.tx() as c:
            self.store.maintain(c)
            return [dict(row) for row in c.execute(self.task_query().order_by(tasks.c.created.desc()).limit(200)).mappings()]
    def create_task(self,data):
        self.execute();self.device(data.device_id);return self.store.create_task(data,self.p.id)
    def create_installation(self,data):
        self.execute();self.device(data.device_id);return self.store.create_installation(data,self.p.id)
    def ticket(self):
        self.manage()
        if '*' not in self.p.device_ids:raise Problem('operator_forbidden',403)
        return self.store.ticket(self.p.id)
    def revoke(self,did):self.manage();self.device(did);return self.store.revoke(did,self.p.id)
    def decision(self,tid,data):self.execute();self.task(tid,own=True);return self.store.decision(tid,data)
    def cancel(self,tid):self.execute();self.task(tid,own=True);return self.store.cancel(tid)
    def reconcile(self,tid,data):self.execute();self.task(tid,own=True);return self.store.reconcile(tid,data)
    def output(self,tid):
        self.task(tid,own=True)
        with self.store.lock:value=self.store.outputs.get(tid)
        return {'output':value,'status':'available_in_memory' if value is not None else 'expired_or_absent'}
    def get_events(self):
        with self.store.tx() as c:
            allowed={row['id'] for row in c.execute(self.task_query()).mappings()}
            rows=[]
            for event in c.execute(select(events).order_by(events.c.at.desc())).mappings():
                if event['task_id']:
                    visible=event['task_id'] in allowed
                elif event['device_id']:
                    visible=self.p.device(event['device_id']) and (self.p.audit() or event['actor_id']==self.p.id)
                else:visible=event['actor_id']==self.p.id or self.p.audit() and '*' in self.p.device_ids
                if visible:rows.append(dict(event))
                if len(rows)>=300:break
            return rows
    def statistics(self):
        visible_events=self.get_events()
        with self.store.tx() as c:active=[t for t in self.store.active_rows(c) if self.visible_task(t)]
        keys=[t['session_id'] if t['owner_id']==self.p.id else t['owner_id'][:12]+':'+t['session_id'] for t in active]
        return {'counts':dict(Counter(e['kind'] for e in visible_events)),'active_fleet':len(active),'active_per_session':dict(Counter(keys)),
                'limits':{'session':5,'fleet':20,'devices':500},'scope':'visible_to_operator'}
    def packages(self):return self.store.catalog.listing()
    def chat(self,question,integrations):
        with self.store.tx() as c:self.store.event(c,'question_requested',actor_id=self.p.id)
        return integrations.ask(question)
    def knowledge(self,query,integrations):
        if self.auth.s.auth_mode=='oidc':return {'status':'delegated_credentials_required','items':[]}
        return integrations.search(query)

class ScopedWorkflow:
    def __init__(self,parent):self.parent=parent
    def visible(self,w):
        p=self.parent.p
        return (w['owner_id']==p.id or p.audit()) and all(p.device(t['device_id']) for t in w['steps'])
    def get(self,wid,own=False):
        value=self.parent.store.workflow.get(wid)
        if not self.visible(value) or own and value['owner_id']!=self.parent.p.id:raise Problem('not_found',404)
        if value['owner_id']!=self.parent.p.id:value['preview']=None
        return value
    def listing(self):
        p=self.parent.p;s=self.parent.store
        query=select(workflows)
        if not p.audit():query=query.where(workflows.c.owner_id==p.id)
        if '*' not in p.device_ids:
            forbidden=select(workflow_steps.c.workflow_id).join(tasks,tasks.c.id==workflow_steps.c.task_id).where(tasks.c.device_id.not_in(p.device_ids))
            query=query.where(workflows.c.id.not_in(forbidden))
        with s.tx() as c:
            s.maintain(c)
            result=[]
            for row in c.execute(query.order_by(workflows.c.created.desc()).limit(30)).mappings():
                value=s.workflow.public(c,row,include_preview=False)
                if self.visible(value):result.append(value)
            return result
    def request(self,data,planner):self.parent.execute();return self.parent.store.workflow.request(data,planner,self.parent.p.id)
    def decision(self,wid,data):self.parent.execute();self.get(wid,own=True);return self.parent.store.workflow.decision(wid,data)
    def cancel(self,wid):self.parent.execute();self.get(wid,own=True);return self.parent.store.workflow.cancel(wid)

class ScopedInvestigation:
    def __init__(self,parent):self.parent=parent
    def get(self,tid):
        task=self.parent.task(tid);result=self.parent.store.investigations.get(tid)
        if task['owner_id']!=self.parent.p.id:result.update(assessment=None,assessment_expires=None,temporary_status='owner_only')
        return result
    def cancel(self,tid):self.parent.execute();self.parent.task(tid,own=True);return self.parent.store.investigations.cancel(tid)
