"""Owner-only conversations. Raw dialogue is bounded RAM, never SQL metadata."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from pydantic import Field,field_validator
from sqlalchemy import select,update,func
from .schemas import Strict,WorkflowRequest
from .store import conversations,conversation_turns,workflows,tasks,Problem,Volatile,uid,digest,ACTIVE
from .targets import TargetScope,resolve_scope,FleetPlanner
from .integrations import IntegrationError

TTL=1800
class NewConversation(Strict):
    title:str=Field(default='Новая беседа',min_length=1,max_length=80)
    @field_validator('title')
    @classmethod
    def clean_title(cls,value):
        if not value.strip() or any(ord(c)<32 for c in value):raise ValueError('invalid_title')
        return value.strip()
class EditConversation(NewConversation):
    scope:TargetScope|None=None
class DeleteConversation(Strict):
    cancel_tasks:bool=False
class Message(Strict):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')
    question:str=Field(min_length=1,max_length=8000)
class PreparePlan(Strict):
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')
    question:str=Field(default='',max_length=8000)

class Conversations:
    def __init__(self,store,ops,integrations,planner):
        self.store=store;self.ops=ops;self.integrations=integrations;self.planner=planner
        self.contexts=Volatile(128,16*1024*1024);self.texts=Volatile(2048,32*1024*1024)
        self.pool=ThreadPoolExecutor(max_workers=5,thread_name_prefix='pslc-dialogue')
        self.slots=threading.BoundedSemaphore(5);self.closed=False
        with store.tx() as c:
            c.execute(update(conversation_turns).where(conversation_turns.c.state.in_(('queued','generating'))).values(state='context_lost'))
    def _row(self,c,cid,owner):
        row=c.execute(select(conversations).where(conversations.c.id==cid,conversations.c.owner_id==owner,conversations.c.deleted==False)).mappings().first()
        if not row:raise Problem('not_found',404)
        return row
    def _inventory(self,owner):
        p=self.ops.auth.known(owner)
        if not p.operator():raise Problem('operator_forbidden',403)
        return [d for d in self.store.list_devices() if p.device(d['id'])]
    def listing(self):
        owner=self.ops.p.id
        with self.store.tx() as c:
            rows=c.execute(select(conversations).where(conversations.c.owner_id==owner,conversations.c.deleted==False).order_by(conversations.c.updated.desc()).limit(100)).mappings().all()
            return [dict(row) for row in rows]
    def create(self,data):
        self.ops.execute();owner=self.ops.p.id
        with self.store.tx() as c:
            if c.scalar(select(func.count()).select_from(conversations).where(conversations.c.owner_id==owner,conversations.c.deleted==False))>=100:raise Problem('conversation_limit',429)
            row={'id':uid(),'owner_id':owner,'title':data.title,'created':time.time(),'updated':time.time(),'deleted':False,'target_scope':{'kind':'none','ids':[]}}
            c.execute(conversations.insert().values(**row));self.store.event(c,'conversation_created',actor_id=owner)
        return self.get(row['id'])
    def edit(self,cid,data):
        self.ops.execute();owner=self.ops.p.id
        if data.scope is not None:resolve_scope(data.scope,self._inventory(owner))
        with self.store.tx() as c:
            current=self._row(c,cid,owner)
            if data.scope is not None:
                pending=c.scalar(select(func.count()).select_from(conversation_turns).where(conversation_turns.c.conversation_id==cid,conversation_turns.c.state.in_(('queued','generating'))))
                if pending:raise Problem('conversation_busy',409)
                if current['target_scope']!=data.scope.model_dump():
                    for wid in c.execute(select(workflows.c.id).where(workflows.c.conversation_id==cid,workflows.c.state=='awaiting_confirmation')).scalars():self.store.workflow.cancel(wid)
            values={'title':data.title,'updated':time.time()}
            if data.scope is not None:values['target_scope']=data.scope.model_dump()
            c.execute(update(conversations).where(conversations.c.id==cid).values(**values))
        return self.get(cid)
    def get(self,cid):
        owner=self.ops.p.id
        with self.store.tx() as c:
            row=dict(self._row(c,cid,owner));self.store.maintain(c)
            result=[]
            for turn in c.execute(select(conversation_turns).where(conversation_turns.c.conversation_id==cid).order_by(conversation_turns.c.created.desc()).limit(60)).mappings():
                value=dict(turn);raw=self.texts.get(turn['id'])
                value['temporary']=raw;value['temporary_status']='available' if raw else 'expired_or_absent'
                flow=c.execute(select(workflows).where(workflows.c.id==turn['workflow_id'])).mappings().first() if turn['workflow_id'] else None
                value['workflow']=self.store.workflow.public(c,flow) if flow else None
                result.append(value)
            row['turn_count']=c.scalar(select(func.count()).select_from(conversation_turns).where(conversation_turns.c.conversation_id==cid));row['turns_truncated']=row['turn_count']>60
            row['turns']=list(reversed(result));row['context_expires_in']=TTL if self.contexts.get(cid) else 0
            return row
    def remove(self,cid,data):
        self.ops.execute();owner=self.ops.p.id
        with self.store.tx() as c:
            self._row(c,cid,owner)
            linked=c.execute(select(workflows).where(workflows.c.conversation_id==cid)).mappings().all()
            active=[w for w in linked if w['state'] in ('planning','awaiting_confirmation','queued','running','cancel_requested','outcome_unknown')]
            if active and not data.cancel_tasks:raise Problem('conversation_has_active_tasks',409)
            for w in active:self.store.workflow.cancel(w['id'])
            for turn in c.execute(select(conversation_turns.c.id).where(conversation_turns.c.conversation_id==cid)).scalars():self.texts.drop(turn)
            c.execute(update(conversation_turns).where(conversation_turns.c.conversation_id==cid,conversation_turns.c.state.in_(('queued','generating'))).values(state='cancelled'))
            self.contexts.drop(cid)
            c.execute(update(conversations).where(conversations.c.id==cid).values(deleted=True,updated=time.time()))
            self.store.event(c,'conversation_deleted',actor_id=owner)
        return {'status':'deleted','cancel_requested':bool(active),'audit_metadata_preserved':True}
    def send(self,cid,data):
        self.ops.execute();owner=self.ops.p.id
        with self.store.tx() as c:
            row=self._row(c,cid,owner)
            ph=digest(json.dumps({'conversation':cid,'question':data.question,'scope':row['target_scope']},sort_keys=True,ensure_ascii=False))
            existing=c.execute(select(conversation_turns).where(conversation_turns.c.id==data.request_id)).mappings().first()
            if existing:
                if existing['conversation_id']!=cid:raise Problem('not_found',404)
                if existing['request_hash']!=ph:raise Problem('request_id_conflict')
                return self.get(cid)
            if c.scalar(select(func.count()).select_from(conversation_turns).where(conversation_turns.c.conversation_id==cid,conversation_turns.c.state.in_(('queued','generating')))):raise Problem('conversation_busy',409)
            if not self.slots.acquire(blocking=False):raise Problem('conversation_capacity',429)
            try:
                previous=self.contexts.get(cid) or []
                messages=previous+[{'role':'user','content':data.question}]
                truncated=False
                while len(messages)>40 or len(json.dumps(messages,ensure_ascii=False).encode())>65536:
                    if len(messages)==1:raise Problem('conversation_context_too_large',422)
                    messages.pop(0);truncated=True
                self.contexts.put(cid,messages,TTL)
                self.texts.put(data.request_id,{'question':data.question,'answer':None,'expires':time.time()+TTL},TTL)
                c.execute(conversation_turns.insert().values(id=data.request_id,conversation_id=cid,request_hash=ph,state='queued',created=time.time(),workflow_id=None,source='model_pending',summary={'message_count':len(messages),'context_truncated':truncated}))
                c.execute(update(conversations).where(conversations.c.id==cid).values(updated=time.time()))
                self.store.event(c,'question_requested',actor_id=owner)
                scope=dict(row['target_scope'])
            except Exception:
                self.slots.release();self.texts.drop(data.request_id);raise
        try:self.pool.submit(self._generate,cid,data.request_id,owner,messages,scope)
        except RuntimeError:
            self.slots.release()
            with self.store.tx() as c:c.execute(update(conversation_turns).where(conversation_turns.c.id==data.request_id).values(state='context_lost'))
            raise Problem('conversation_unavailable',503) from None
        return self.get(cid)
    def _generate(self,cid,tid,owner,messages,scope):
        try:
            inventory=resolve_scope(TargetScope.model_validate(scope),self._inventory(owner))
            with self.store.tx() as c:
                row=c.execute(select(conversation_turns).where(conversation_turns.c.id==tid)).mappings().one()
                if self.closed or row['state']!='queued':return
                c.execute(update(conversation_turns).where(conversation_turns.c.id==tid).values(state='generating'))
            context={'selected_count':len(inventory),'shown_count':min(len(inventory),12),'devices':[{k:d[k] for k in ('id','name','os_family','device_kind','online')} for d in inventory[:12]],'actions_require_separate_plan':True}
            # Only server-computed metadata from this owned case; never stdout or collector evidence.
            allowed_ids={d['id'] for d in inventory}
            with self.store.tx() as c:
                latest=c.execute(select(workflows).where(workflows.c.conversation_id==cid,workflows.c.owner_id==owner).order_by(workflows.c.created.desc())).mappings().first()
                observations=self.store.workflow.rows(c,latest['id']) if latest else []
                observations=[r for r in observations if r['device_id'] in allowed_ids]
                recovery=self.store.recovery.metadata_many(c,[r['id'] for r in observations])
                context['latest_plan']={'state':latest['state'],'total_steps':len(observations),'verified_steps':sum(r['state'] in ('verified','already_installed') and not recovery.get(r['id'],{}).get('historical',False) for r in observations),'historical_steps':sum(recovery.get(r['id'],{}).get('historical',False) for r in observations),'shown_steps':min(20,len(observations))} if latest else None
                context['observations']=[{'device_id':r['device_id'],'kind':r['kind'],'state':r['state'],'summary':r['summary'],'historical':recovery.get(r['id'],{}).get('historical',False),'recovery_status':recovery.get(r['id'],{}).get('status','not_recorded')} for r in observations[-20:]]
                context['proofs_are_point_in_time']=True
            bounded=list(messages)
            while len(json.dumps(bounded,ensure_ascii=False).encode())>24000 and len(bounded)>1:bounded.pop(0)
            reply=self.integrations.ask_messages(bounded,context)
            self.ops.auth.known(owner)
            with self.store.tx() as c:
                row=c.execute(select(conversation_turns).where(conversation_turns.c.id==tid)).mappings().one()
                if self.closed or row['state']!='generating':return
                self._row(c,cid,owner)
                answer=reply.get('answer','')[:16000]
                history=self.contexts.get(cid) or []
                history=history+[{'role':'assistant','content':answer}]
                truncated=False
                while len(history)>40 or len(json.dumps(history,ensure_ascii=False).encode())>65536:history.pop(0);truncated=True
                self.contexts.put(cid,history,TTL)
                raw=self.texts.get(tid)
                if raw:self.texts.put(tid,{**raw,'answer':answer},TTL)
                c.execute(update(conversation_turns).where(conversation_turns.c.id==tid).values(state='completed' if reply['status']=='connected' else 'not_configured',source='configured_model' if reply['status']=='connected' else 'not_configured',summary={'message_count':len(history),'context_truncated':truncated}))
                c.execute(update(conversations).where(conversations.c.id==cid).values(updated=time.time()))
        except Exception:
            if not self.closed:
                with self.store.tx() as c:
                    row=c.execute(select(conversation_turns).where(conversation_turns.c.id==tid)).mappings().first()
                    if row and row['state'] in ('queued','generating'):c.execute(update(conversation_turns).where(conversation_turns.c.id==tid).values(state='failed',summary={'code':'generation_failed'}))
        finally:self.slots.release()
    def cancel(self,cid,tid):
        self.ops.execute()
        with self.store.tx() as c:
            self._row(c,cid,self.ops.p.id)
            row=c.execute(select(conversation_turns).where(conversation_turns.c.id==tid,conversation_turns.c.conversation_id==cid)).mappings().first()
            if not row:raise Problem('not_found',404)
            if row['state'] in ('queued','generating'):
                c.execute(update(conversation_turns).where(conversation_turns.c.id==tid).values(state='cancelled'))
                raw=self.texts.get(tid)
                if raw:self.texts.put(tid,{**raw,'answer':None},TTL)
        return self.get(cid)
    def plan(self,cid,data):
        self.ops.execute();owner=self.ops.p.id
        inventory=self._inventory(owner)
        with self.store.tx() as c:
            row=self._row(c,cid,owner);selected=resolve_scope(TargetScope.model_validate(row['target_scope']),inventory)
            if not selected:raise Problem('target_selection_required',422)
            history=list(self.contexts.get(cid) or [])
            question=data.question.strip()
            if not question:
                users=[m['content'] for m in history if m['role']=='user']
                question=users[0] if len(users)==1 else '\n'.join('Пользователь: '+q for q in users)[-8000:]
            if not question:raise Problem('conversation_context_expired',409)
        request=WorkflowRequest(request_id=data.request_id,session_id=cid,question=question,target_ids=[d['id'] for d in selected],conversation_id=cid)
        flow=self.store.workflow.request(request,FleetPlanner(self.planner,inventory),owner)
        with self.store.tx() as c:
            self._row(c,cid,owner)
            turn_id='plan-'+data.request_id
            if not c.execute(select(conversation_turns.c.id).where(conversation_turns.c.id==turn_id)).first():
                self.texts.put(turn_id,{'question':question,'answer':None,'expires':time.time()+TTL},TTL)
                if data.question.strip():
                    history=(self.contexts.get(cid) or [])+[{'role':'user','content':data.question.strip()}]
                    while len(history)>40 or len(json.dumps(history,ensure_ascii=False).encode())>65536:history.pop(0)
                    self.contexts.put(cid,history,TTL)
                c.execute(conversation_turns.insert().values(id=turn_id,conversation_id=cid,request_hash=digest(data.request_id),state='plan',created=time.time(),workflow_id=flow['id'],source=flow['source'],summary=None))
                c.execute(update(conversations).where(conversations.c.id==cid).values(updated=time.time()))
        return self.get(cid)
    def purge(self):
        with self.store.lock:self.contexts.purge();self.texts.purge()
    def close(self):
        with self.store.lock:
            self.closed=True;self.contexts.data.clear();self.contexts.sizes.clear();self.texts.data.clear();self.texts.sizes.clear()
        self.pool.shutdown(wait=False,cancel_futures=True)
