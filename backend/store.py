from contextlib import contextmanager
from threading import RLock,local
import hashlib
import json
import secrets
import time
import uuid
from sqlalchemy import or_, UniqueConstraint, Boolean, Column, Float, Integer, JSON, MetaData, String, Table, create_engine, select, update, func
from sqlalchemy.pool import StaticPool
from .catalog import Catalog, CatalogError, manifest_hash, installer_argv
from .migrations import migrate

SCHEMA_VERSION = 9
LOCAL_OWNER = 'local-development'
metadata = MetaData()
guard = Table("dispatch_guard", metadata, Column("id", Integer, primary_key=True))
version = Table("schema_version", metadata, Column("id", Integer, primary_key=True))
devices = Table("devices", metadata,
    Column("id", String(64), primary_key=True), Column("name", String(64)), Column("os_family", String(16)),
    Column("agent_protocol", Integer, nullable=False, default=0, server_default="0"),
    Column("device_kind", String(16), nullable=False, default="unknown", server_default="unknown"),
    Column("credential_hash", String(64)), Column("revoked", Boolean, default=False),
    Column("commands_enabled", Boolean, default=False), Column("installations_enabled", Boolean, default=False, server_default="false", nullable=False), Column("diagnostics_enabled", Boolean, default=False, server_default="false", nullable=False), Column("last_seen", Float))
enrollment = Table("enrollment", metadata, Column("digest", String(64), primary_key=True), Column("expires", Float), Column("used", Boolean),Column("profile",JSON,nullable=True))
tasks = Table("tasks", metadata,
    Column("id", String(64), primary_key=True), Column("owner_id", String(64), nullable=False,default=LOCAL_OWNER,server_default=LOCAL_OWNER), Column("device_id", String(64)), Column("session_id", String(64)),
    Column("recovery_of", String(64), nullable=True),Column("schedule_run_id",String(64),nullable=True),
    Column("kind", String(16)), Column("runtime", String(16)), Column("state", String(40)),
    Column("plan_hash", String(64)), Column("expires", Float), Column("created", Float),
    Column("lease_id", String(128), nullable=True), Column("lease_until", Float, nullable=True),
    Column("summary", JSON, nullable=True), Column("package_meta", JSON, nullable=True))
task_recoveries = Table("task_recoveries", metadata,
    Column("original_task_id",String(64),primary_key=True),Column("device_id",String(64),nullable=False),Column("owner_id",String(64),nullable=False),
    Column("original_plan_hash",String(64),nullable=False),Column("program_hash",String(64),nullable=False),Column("program",JSON,nullable=False),
    Column("verification_hash",String(64),nullable=True),Column("receipt",JSON,nullable=True),Column("latest_recovery_id",String(64),nullable=True),
    Column("historical",Boolean,nullable=False,default=False,server_default="false"))

events = Table("events", metadata,
    Column("id", String(64), primary_key=True), Column("actor_id", String(64),nullable=False,default=LOCAL_OWNER,server_default=LOCAL_OWNER), Column("task_id", String(64), nullable=True),
    Column("device_id", String(64), nullable=True), Column("kind", String(48)), Column("at", Float), Column("wait_ms", Integer, default=0))
workflows = Table("workflows", metadata,
    Column("id", String(64), primary_key=True), Column("owner_id", String(64),nullable=False,default=LOCAL_OWNER,server_default=LOCAL_OWNER), Column("session_id", String(64)), Column("request_hash", String(64)),
    Column("state", String(40)), Column("source", String(32)), Column("plan_hash", String(64), nullable=True),
    Column("created", Float), Column("expires", Float), Column("decision", Boolean, nullable=True),
    Column("execution_mode",String(16),nullable=False,default="sequential",server_default="sequential"),
    Column("target_snapshot",JSON,nullable=True),Column("conversation_id",String(64),nullable=True))
workflow_steps = Table("workflow_steps", metadata,
    Column("workflow_id", String(64), primary_key=True), Column("ordinal", Integer, primary_key=True),
    Column("task_id", String(64), unique=True), Column("verification_hash", String(64), nullable=True))

investigation_jobs = Table("investigation_jobs", metadata,
    Column("task_id", String(64), primary_key=True), Column("scope", JSON), Column("scope_hash", String(64)),
    Column("analysis_state", String(40)), Column("analysis_meta", JSON), Column("created", Float))

knowledge_records = Table("knowledge_records", metadata,
    Column("id",String(64),primary_key=True),Column("source_id",String(32)),Column("source_version",Integer),Column("space",String(64)),
    Column("os_family",String(16)),Column("service",String(64)),Column("applies_version",String(64)),Column("task_id",String(64),nullable=True),
    Column("owner_id",String(64)),Column("reviewer_id",String(64),nullable=True),Column("state",String(24)),Column("revision",Integer),Column("updated",Float))

accounts = Table("accounts",metadata,
    Column("id",String(64),primary_key=True),Column("username",String(64),unique=True,nullable=False),Column("password_hash",String(255),nullable=False),
    Column("roles",JSON,nullable=False),Column("device_ids",JSON,nullable=False),Column("knowledge_spaces",JSON,nullable=False),
    Column("disabled",Boolean,nullable=False),Column("auth_version",Integer,nullable=False),Column("created",Float,nullable=False))

conversations = Table("conversations",metadata,
    Column("id",String(64),primary_key=True),Column("owner_id",String(64),nullable=False),
    Column("title",String(80),nullable=False),Column("created",Float,nullable=False),Column("updated",Float,nullable=False),
    Column("deleted",Boolean,nullable=False,default=False),Column("target_scope",JSON,nullable=False))
conversation_turns = Table("conversation_turns",metadata,
    Column("id",String(64),primary_key=True),Column("conversation_id",String(64),nullable=False),
    Column("request_hash",String(64),nullable=False),Column("state",String(32),nullable=False),
    Column("created",Float,nullable=False),Column("workflow_id",String(64),nullable=True),
    Column("source",String(32),nullable=False),Column("summary",JSON,nullable=True))


schedules = Table("schedules",metadata,
 Column("id",String(32),primary_key=True),Column("owner_id",String(64),nullable=False),Column("title",String(80),nullable=False),
 Column("revision",Integer,nullable=False),Column("mode",String(16),nullable=False),Column("calendar",JSON,nullable=False),
 Column("target_scope",JSON,nullable=False),Column("template_hash",String(64),nullable=False),Column("consented_ids",JSON,nullable=False),
 Column("definition_hash",String(64)),Column("consent_id",String(32)),Column("enabled",Boolean,nullable=False),Column("deleted",Boolean,nullable=False),
 Column("created",Float,nullable=False),Column("updated",Float,nullable=False),Column("next_due",Float))
schedule_runs = Table("schedule_runs",metadata,
 Column("id",String(32),primary_key=True),Column("schedule_id",String(32),nullable=False),Column("owner_id",String(64),nullable=False),
 Column("consent_id",String(32),nullable=False),Column("revision",Integer,nullable=False),Column("slot_key",String(128),nullable=False),Column("definition_hash",String(64),nullable=False),
 Column("target_snapshot",JSON,nullable=False),Column("workflow_id",String(64)),Column("state",String(40),nullable=False),
 Column("created",Float,nullable=False),Column("updated",Float,nullable=False),Column("summary",JSON,nullable=False),
 UniqueConstraint("schedule_id","slot_key",name="schedule_slot_once"))

ACTIVE = ("running", "cancel_requested", "outcome_unknown")
TERMINAL = ("verified", "process_succeeded", "failed", "timeout", "output_limit", "prerequisite_missing", "commands_disabled", "cancelled", "rejected", "plan_expired", "catalog_changed", "already_installed", "reboot_required", "verification_failed", "integrity_failed", "download_failed", "installations_disabled", "unsupported_platform", "partial", "diagnostics_disabled", "access_revoked", "recovery_required", "recovery_unavailable", "recovery_conflict", "recovery_partial", "restored")

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def uid():
    return str(uuid.uuid4())

class Problem(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status

class Volatile:
    def __init__(self, capacity=1024, max_bytes=None):
        self.data = {}
        self.capacity = capacity
        self.max_bytes = max_bytes
        self.sizes = {}

    def put(self, key, value, ttl):
        self.purge()
        if len(self.data) >= self.capacity and key not in self.data:
            raise Problem("volatile_capacity_reached", 429)
        size=len(json.dumps(value,ensure_ascii=False).encode()) if self.max_bytes is not None else 0
        if self.max_bytes is not None and sum(self.sizes.values())-self.sizes.get(key,0)+size>self.max_bytes:raise Problem("volatile_capacity_reached",429)
        self.data[key] = (time.time()+ttl, value)
        self.sizes[key] = size

    def purge(self):
        now = time.time()
        for key in list(self.data):
            if self.data[key][0] <= now:
                self.drop(key)

    def get(self, key):
        self.purge()
        item = self.data.get(key)
        return item[1] if item else None

    def drop(self, key):
        self.data.pop(key, None)
        self.sizes.pop(key,None)

class Store:
    def __init__(self, settings):
        settings.validate()
        self.settings = settings
        self.authorization = None
        self.scheduler = None
        kwargs = {"echo": False, "hide_parameters": True}
        if settings.database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
            if settings.database_url.endswith(":memory:"):
                kwargs["poolclass"] = StaticPool
        if settings.database_url.startswith('postgresql'):
            kwargs.update(pool_pre_ping=True,pool_timeout=5,connect_args={'connect_timeout':5,'options':'-c statement_timeout=10000 -c lock_timeout=5000'})
        self.engine = create_engine(settings.database_url, **kwargs)
        self.lock = RLock();self.tx_context=local()
        self.plans, self.outputs = Volatile(8192,64*1024*1024), Volatile()
        self.catalog = Catalog(settings.package_catalog_path)
        migrate(self.engine)
        metadata.create_all(self.engine)
        with self.engine.begin() as c:
            if c.execute(select(guard)).first() is None:
                c.execute(guard.insert().values(id=1))
            if c.execute(select(version)).first() is None:
                c.execute(version.insert().values(id=SCHEMA_VERSION))
        from .recovery import Recovery
        self.recovery=Recovery(self)
        from .workflow import Workflow
        self.workflow = Workflow(self)
        from .investigations import Investigations
        self.investigations = Investigations(self)
        with self.tx() as c:self.investigations.startup(c)
        with self.tx() as c:
            for t in c.execute(select(tasks)).mappings().all():
                if t["state"] in ("running", "cancel_requested"):
                    c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="outcome_unknown"))
                    self.event(c, "outcome_unknown", t)
                elif (t["kind"] in ("command","install","investigate") or t.get("schedule_run_id")) and t["state"] in ("queued", "awaiting_confirmation"):
                    c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="plan_expired"))
                    self.event(c, "confirmation_expired" if t["state"]=="awaiting_confirmation" else "plan_expired", t)

        with self.tx() as c:
            self.workflow.refresh(c, restarted=True)

    @contextmanager
    def tx(self):
        with self.lock:
            current=getattr(self.tx_context,'connection',None)
            if current is not None:
                yield current
                return
            with self.engine.begin() as c:
                self.tx_context.connection=c
                self.tx_context.schedule_authorizations={}
                try:
                    if self.engine.dialect.name == 'postgresql':c.execute(select(guard).where(guard.c.id==1).with_for_update())
                    yield c
                finally:
                    del self.tx_context.connection
                    del self.tx_context.schedule_authorizations

    def event(self, c, kind, task=None, device_id=None, wait_ms=0, actor_id=LOCAL_OWNER):
        c.execute(events.insert().values(id=uid(), actor_id=task.get("owner_id",actor_id) if task else actor_id, kind=kind, at=time.time(), task_id=task["id"] if task else None,
            device_id=task["device_id"] if task else device_id, wait_ms=wait_ms))

    def authorize_owner(self,owner,did,run_id=None,task_id=None):
        if run_id:
            if self.scheduler is None:raise Problem("owner_access_revoked",403)
            return self.scheduler.authorize(run_id,owner,did,task_id)
        if self.authorization is not None:return self.authorization.authorize_owner(owner,did)
        if owner!=LOCAL_OWNER:raise Problem("owner_access_revoked",403)

    def maintain(self, c):
        now=time.time()
        for t in c.execute(select(tasks).where(tasks.c.state.in_(["queued", "awaiting_confirmation", "running", "cancel_requested"]))).mappings().all():
            state=t["state"]
            if state in ("queued","awaiting_confirmation"):
                try:self.authorize_owner(t["owner_id"],t["device_id"],t.get("schedule_run_id"),t["id"])
                except Problem:
                    c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="access_revoked"));self.plans.drop(t["id"]);self.event(c,"owner_access_revoked",t);continue
            if state in ("queued","awaiting_confirmation") and t["kind"]=="install" and self.plans.get(t["id"]) is not None:
                meta=t["package_meta"]
                try:
                    approved=self.catalog.get(meta["id"],meta["version"],meta["os_family"])
                    valid=manifest_hash(approved)==meta["manifest_hash"]
                except CatalogError: valid=False
                if not valid:
                    c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="catalog_changed"))
                    self.plans.drop(t["id"]);self.event(c,"catalog_changed",t);continue
            if state in ("running", "cancel_requested") and t["lease_until"] < now:
                c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="outcome_unknown")); self.event(c,"outcome_unknown",t)
            elif state in ("queued", "awaiting_confirmation") and t["kind"] in ("command","install","rollback") and self.plans.get(t["id"]) is None:
                c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="plan_expired"))
                self.event(c,"confirmation_expired" if state=="awaiting_confirmation" else "plan_expired",t)

        self.investigations.maintain(c)
        self.workflow.refresh(c)
        if self.scheduler is not None:self.scheduler.refresh(c)

    def ticket(self,actor_id=LOCAL_OWNER,profile=None):
        token=secrets.token_urlsafe(32)
        with self.tx() as c:
            c.execute(enrollment.insert().values(digest=digest(token),expires=time.time()+600,used=False,profile=profile))
            self.event(c,"enrollment_issued",actor_id=actor_id)
        return {"enrollment_token":token,"expires_in":600}

    def enroll(self, data, _virtual=False):
        from .demo import is_virtual
        if is_virtual(data.name) and not _virtual:raise Problem("reserved_device_name",422)
        if _virtual and (not is_virtual(data.name) or data.commands_enabled or data.installations_enabled or data.diagnostics_enabled):raise Problem("virtual_device_readonly",422)
        token=secrets.token_urlsafe(32)
        with self.tx() as c:
            row=c.execute(select(enrollment).where(enrollment.c.digest==digest(data.enrollment_token))).mappings().first()
            if not row or row["used"] or row["expires"]<=time.time(): raise Problem("invalid_enrollment",401)
            if c.scalar(select(func.count()).select_from(devices))>=500: raise Problem("device_limit",409)
            profile=row.get('profile')
            if profile:
                from .nodes import OnboardingProfile
                try:profile=OnboardingProfile.model_validate(profile,strict=True).model_dump()
                except ValueError:raise Problem('invalid_enrollment_profile',403) from None
                if profile['os_family']!=data.os_family or any(getattr(data,key) and not profile[key] for key in ('commands_enabled','installations_enabled','diagnostics_enabled')):raise Problem('enrollment_profile_mismatch',403)
            did=uid()
            c.execute(devices.insert().values(id=did,name=profile['name'] if profile else data.name,os_family=data.os_family,credential_hash=digest(token),revoked=False,
                commands_enabled=data.commands_enabled,installations_enabled=data.installations_enabled,diagnostics_enabled=data.diagnostics_enabled,agent_protocol=data.agent_protocol,device_kind=profile.get("device_kind") or "unknown" if profile else "unknown",last_seen=time.time()))
            c.execute(update(enrollment).where(enrollment.c.digest==row["digest"]).values(used=True))
            self.event(c,"device_enrolled",device_id=did)
        return {"device_id":did,"credential":token}

    def check_agent(self,c,did,token):
        d=c.execute(select(devices).where(devices.c.id==did)).mappings().first()
        if not d or d["revoked"] or not secrets.compare_digest(d["credential_hash"],digest(token)):
            raise Problem("agent_unauthorized",401)
        return d

    def list_devices(self):
        with self.tx() as c:
            rows=[]
            for d in c.execute(select(devices)).mappings():
                row={k:v for k,v in d.items() if k!="credential_hash"}
                from .demo import is_virtual
                row["virtual"]=is_virtual(d["name"])
                row["online"] = not d["revoked"] and time.time()-d["last_seen"]<45
                rows.append(row)
            return rows

    def revoke(self,did,actor_id=LOCAL_OWNER):
        with self.tx() as c:
            if c.execute(select(devices.c.id).where(devices.c.id==did)).first() is None: raise Problem("not_found",404)
            c.execute(update(devices).where(devices.c.id==did).values(revoked=True))
            self.event(c,"device_revoked",device_id=did,actor_id=actor_id)
            for tid in c.execute(select(tasks.c.id).where(tasks.c.device_id==did)).scalars():self.investigations.cancel_in_tx(c,tid)

    def create_task(self,data,owner_id=LOCAL_OWNER):
        self.authorize_owner(owner_id,data.device_id)
        payload={"owner_id":owner_id,"kind":data.kind,"runtime":data.runtime,"code":data.code if data.kind=="command" else "", "timeout_seconds":data.timeout_seconds,
            "device_id":data.device_id,"session_id":data.session_id,"version":1}
        if data.kind=="command" and not data.code.strip(): raise Problem("empty_code",422)
        tid=uid(); now=time.time()
        if data.kind=="command":
            payload.update(verification=data.verification.model_dump(),recovery=data.recovery.model_dump())
            self.recovery.attach(payload,tid)
        ph=digest(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False))
        with self.tx() as c:
            d=c.execute(select(devices).where(devices.c.id==data.device_id)).mappings().first()
            if not d or d["revoked"]: raise Problem("device_unavailable",404)
            if data.kind=="command" and not d["commands_enabled"]: raise Problem("commands_disabled",403)
            if data.kind=="command":
                self.recovery.admit(c,payload)
                self.plans.put(tid,payload,self.settings.plan_ttl)
            row={"id":tid,"owner_id":owner_id,"device_id":data.device_id,"session_id":data.session_id,"kind":data.kind,"runtime":data.runtime,
                "state":"awaiting_confirmation" if data.kind=="command" else "queued","plan_hash":ph,"expires":now+self.settings.plan_ttl,"created":now}
            c.execute(tasks.insert().values(**row))
            self.event(c,"confirmation_requested" if data.kind=="command" else "task_created",row)
        return self.get_task(tid)

    def create_installation(self,data,owner_id=LOCAL_OWNER):
        self.authorize_owner(owner_id,data.device_id)
        tid=uid();now=time.time()
        with self.tx() as c:
            d=c.execute(select(devices).where(devices.c.id==data.device_id)).mappings().first()
            if not d or d["revoked"]: raise Problem("device_unavailable",404)
            if not d["installations_enabled"]: raise Problem("installations_disabled",403)
            try: package=self.catalog.get(data.package_id,data.package_version,d["os_family"])
            except CatalogError as e: raise Problem(str(e),409) from None
            mh=manifest_hash(package)
            payload={"owner_id":owner_id,"kind":"install","device_id":data.device_id,"session_id":data.session_id,"version":2,
                "manifest":package.model_dump(),"manifest_hash":mh,"timeout_seconds":package.timeout_seconds,
                "install_argv":installer_argv(package,"{installer}")}
            self.recovery.attach(payload,tid);self.recovery.admit(c,payload)
            ph=digest(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False))
            self.plans.put(tid,payload,self.settings.plan_ttl)
            row={"id":tid,"owner_id":owner_id,"device_id":data.device_id,"session_id":data.session_id,"kind":"install","runtime":package.installer,
                "state":"awaiting_confirmation","plan_hash":ph,"expires":now+self.settings.plan_ttl,"created":now,
                "package_meta":{"id":package.id,"version":package.version,"os_family":package.os_family,"sha256":package.sha256,"manifest_hash":mh}}
            c.execute(tasks.insert().values(**row));self.event(c,"confirmation_requested",row)
        return self.get_task(tid)

    def get_task(self,tid):
        with self.tx() as c:
            self.maintain(c)
            t=c.execute(select(tasks).where(tasks.c.id==tid)).mappings().first()
            if not t: raise Problem("not_found",404)
            result=dict(t)
            link=self.workflow.link(c,tid);result["workflow_id"]=link["workflow_id"] if link else None
            result["recovery"]=self.recovery.metadata(c,t["recovery_of"] or tid)
            result["plan"]=self.plans.get(tid) if t["state"] in ("queued","awaiting_confirmation") else None
            return result

    def list_tasks(self):
        with self.tx() as c:
            self.maintain(c)
            return [dict(t) for t in c.execute(select(tasks).order_by(tasks.c.created.desc()).limit(200)).mappings()]

    def decision(self,tid,data):
        with self.tx() as c:
            self.maintain(c)
        with self.tx() as c:
            self.maintain(c)
            t=c.execute(select(tasks).where(tasks.c.id==tid)).mappings().first()
            if not t: raise Problem("not_found",404)
            if self.workflow.link(c,tid): raise Problem("workflow_confirmation_required")
            if t["state"]!="awaiting_confirmation": raise Problem("not_awaiting_confirmation")
            if t["expires"]<=time.time() or not secrets.compare_digest(t["plan_hash"],data.plan_hash) or self.plans.get(tid) is None:
                raise Problem("invalid_confirmation")
            if data.approve:self.recovery.approved(c,t,self.plans.get(tid))
            c.execute(update(tasks).where(tasks.c.id==tid).values(state="queued" if data.approve else "rejected"))
            self.event(c,"confirmed" if data.approve else "rejected",t,wait_ms=int((time.time()-t["created"])*1000))
            if not data.approve: self.plans.drop(tid)
        return self.get_task(tid)

    def active_rows(self,c):
        pending=select(investigation_jobs.c.task_id).where(investigation_jobs.c.analysis_state.in_(("pending","analyzing")))
        return c.execute(select(tasks).where(or_(tasks.c.state.in_(ACTIVE),tasks.c.id.in_(pending)))).mappings().all()

    def poll(self,did,token,agent_protocol=0):
        with self.tx() as c:
            device=self.check_agent(c,did,token)
            if type(agent_protocol)!=int or agent_protocol not in (0,1):raise Problem("invalid_agent_protocol",422)
            c.execute(update(devices).where(devices.c.id==did).values(last_seen=time.time(),agent_protocol=agent_protocol))
            self.maintain(c)
            active=self.active_rows(c)
            if len(active)>=20 or any(t["device_id"]==did for t in active): return None
            for t in c.execute(select(tasks).where(tasks.c.device_id==did,tasks.c.state=="queued").order_by(tasks.c.created)).mappings().all():
                if sum(x["session_id"]==t["session_id"] and x["owner_id"]==t["owner_id"] for x in active)>=5: continue
                if t["kind"] in ("command","install","rollback") and agent_protocol<1:continue
                if not self.workflow.permits(c,t): continue
                managed=self.workflow.link(c,t["id"]) is not None
                payload=self.plans.get(t["id"]) if managed or t["kind"] in ("command","install","rollback") else {"kind":"health","timeout_seconds":30}
                if payload is None: continue
                if t['kind']=='command' and not device['commands_enabled']:
                    c.execute(update(tasks).where(tasks.c.id==t['id']).values(state='commands_disabled'))
                    self.plans.drop(t['id']);self.event(c,'commands_disabled',t);continue
                if t["kind"]=="install" and not device["installations_enabled"]:
                    c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="installations_disabled"))
                    self.plans.drop(t["id"]);self.event(c,"installations_disabled",t);continue
                if t['kind'] in ('command','install','rollback'):
                    try:self.workflow.check_payload(c,payload)
                    except Problem as error:
                        state=error.code if error.code in TERMINAL else 'access_revoked'
                        c.execute(update(tasks).where(tasks.c.id==t['id']).values(state=state));self.plans.drop(t['id']);self.event(c,state,t);continue
                lease=secrets.token_urlsafe(24)
                c.execute(update(tasks).where(tasks.c.id==t["id"]).values(state="running",lease_id=lease,lease_until=time.time()+max(self.settings.lease_seconds,payload.get("timeout_seconds",30)+(payload.get("verification") or {}).get("timeout_seconds",0)+60)))
                self.event(c,"dispatched",t)
                return {"task_id":t["id"],"lease_id":lease,"plan_hash":t["plan_hash"],"payload":payload}
        return None

    def finish(self,did,token,tid,data):
        with self.tx() as c:
            device=self.check_agent(c,did,token)
            t=c.execute(select(tasks).where(tasks.c.id==tid)).mappings().first()
            if not t or t["device_id"]!=did or t["lease_id"]!=data.lease_id: raise Problem("invalid_lease",403)
            if t["state"] in TERMINAL or (t["summary"] or {}).get("agent_result_received"): return dict(t)
            if t["state"] not in ACTIVE: raise Problem("invalid_state")
            if t["kind"]=="investigate":return self.investigations.accept(c,t,data)
            if t["kind"]=="rollback":
                state=self.recovery.accept(c,t,data) or 'verification_failed'
                receipt=data.recovery.receipt if data.recovery else None
                summary={'outcome':state,'duration_ms':data.duration_ms,'agent_result_received':True,'restored_count':receipt.restored_count if receipt else 0,'resource_count':receipt.resource_count if receipt else 0}
                c.execute(update(tasks).where(tasks.c.id==tid).values(state=state,summary=summary));self.plans.drop(tid);self.event(c,'completed',t);self.workflow.refresh(c)
                return {'state':state,'summary':summary}

            state="verified" if data.outcome=="ok" and t["kind"]=="health" else data.outcome
            if state=="ok": state="failed"
            if state=="process_succeeded" and (t["kind"]!="command" or data.exit_code!=0): state="failed"
            summary={"outcome":state,"exit_code":data.exit_code,"duration_ms":data.duration_ms}
            if t["kind"]=="install":
                meta=t["package_meta"];proof=data.installation
                if proof and (proof.package_id!=meta["id"] or proof.version!=meta["version"] or proof.manifest_hash!=meta["manifest_hash"]):
                    raise Problem("invalid_installation_proof",422)
                if data.outcome in ("installed","already_installed","reboot_required"):
                    valid=proof is not None and proof.version_matched
                    if data.outcome=="already_installed": valid=valid and proof.already_installed and data.exit_code==0
                    else: valid=valid and not proof.already_installed and proof.artifact_sha256==meta["sha256"]
                    if data.outcome=="installed": valid=valid and data.exit_code==0 and not proof.reboot_required
                    if data.outcome=="reboot_required": valid=valid and proof.reboot_required and data.exit_code==3010
                    state=("verified" if data.outcome=="installed" else data.outcome) if valid else "verification_failed"
                elif data.outcome in ("timeout","output_limit","outcome_unknown"): state="outcome_unknown"
                elif data.outcome not in ("failed","verification_failed","integrity_failed","download_failed","prerequisite_missing","installations_disabled","unsupported_platform"): state="verification_failed"
                summary={"outcome":state,"exit_code":data.exit_code,"duration_ms":data.duration_ms,"package_id":meta["id"],"package_version":meta["version"],
                    "version_matched":bool(proof and proof.version_matched),"reboot_required":bool(proof and proof.reboot_required),"agent_result_received":True}
                if proof and proof.version_matched: summary["verified_version"]=meta["version"]
            elif data.outcome not in ("ok","process_succeeded","failed","timeout","output_limit","prerequisite_missing","commands_disabled"):
                state="failed";summary["outcome"]=state
            managed=self.workflow.link(c,tid)
            if t["kind"]=="command":
                proof=data.command_verification
                rec=c.execute(select(task_recoveries).where(task_recoveries.c.original_task_id==tid)).mappings().first()
                verification_hash=rec['verification_hash'] if rec else None
                valid=proof is not None and verification_hash is not None and proof.verification_hash==verification_hash and proof.matched and proof.exit_code==0
                if data.outcome=="process_succeeded":
                    state="verified" if valid and data.exit_code==0 else "verification_failed"
                elif data.outcome in ("timeout","output_limit","outcome_unknown"):
                    state="outcome_unknown"
                elif data.outcome in ("failed","prerequisite_missing","commands_disabled","verification_failed"):
                    state=data.outcome
                else: state="verification_failed"
                summary={"outcome":state,"exit_code":data.exit_code,"duration_ms":data.duration_ms,
                    "verification_hash":verification_hash,"postcondition_matched":bool(valid),"agent_result_received":True}
            if managed and t["kind"]=="health" and not (data.outcome=="ok" and data.exit_code==0 and all(getattr(data,k) is not None for k in ("cpu_percent","memory_percent","disk_free_gb"))):
                state="verification_failed";summary["outcome"]=state
            if t["kind"]=="health":
                for key in ("cpu_percent","memory_percent","disk_free_gb"):
                    value=getattr(data,key)
                    if value is not None: summary[key]=value
            if t['kind'] in ('command','install'):
                recovery_state=self.recovery.accept(c,t,data)
                if recovery_state:state=recovery_state;summary['outcome']=state
            from .demo import is_virtual
            if is_virtual(device["name"]):summary["source"]="demo_simulation"
            if summary.get('verification_hash','absent') is None:summary.pop('verification_hash',None)
            if data.output: self.outputs.put(tid,data.output,self.settings.output_ttl)
            c.execute(update(tasks).where(tasks.c.id==tid).values(state=state,summary=summary))
            self.plans.drop(tid); self.event(c,"completed",t)
            self.workflow.refresh(c)
            if self.scheduler is not None:self.scheduler.refresh(c)
            return {"state":state,"summary":summary}

    def cancel(self,tid):
        with self.tx() as c:
            t=c.execute(select(tasks).where(tasks.c.id==tid)).mappings().first()
            if not t: raise Problem("not_found",404)
            if t["state"] in TERMINAL: return
            state="cancel_requested" if t["state"] in ACTIVE else "cancelled"
            c.execute(update(tasks).where(tasks.c.id==tid).values(state=state)); self.event(c,"cancel_requested",t)
            if state=="cancelled": self.plans.drop(tid)

    def reconcile(self,tid,data):
        with self.tx() as c:
            t=c.execute(select(tasks).where(tasks.c.id==tid)).mappings().first()
            if not t or t["state"] not in ("outcome_unknown","cancel_requested"): raise Problem("not_unknown")
            c.execute(update(tasks).where(tasks.c.id==tid).values(state=data.outcome,summary={"outcome":data.outcome,"source":"operator_external_check"}))
            self.event(c,"operator_reconciled",t); self.plans.drop(tid)

    def get_events(self):
        with self.tx() as c:
            return [dict(x) for x in c.execute(select(events).order_by(events.c.at.desc()).limit(300)).mappings()]

    def statistics(self):
        with self.tx() as c:
            counts={k:n for k,n in c.execute(select(events.c.kind,func.count()).group_by(events.c.kind))}
            active=[t["session_id"] if t["owner_id"]==LOCAL_OWNER else t["owner_id"][:12]+":"+t["session_id"] for t in self.active_rows(c)]
            return {"counts":counts,"active_fleet":len(active),"active_per_session":{s:active.count(s) for s in set(active)},"limits":{"session":5,"fleet":20,"devices":500}}
