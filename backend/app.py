from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
from pathlib import Path
import secrets
from fastapi import APIRouter, Depends, FastAPI, Header, Request, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,FileResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from .config import ROOT, Settings, load_settings
from .integrations import IntegrationError, Integrations
from .schemas import Chat, Decision, Enroll, Reconcile, Result, TaskInput, InstallTaskInput
from .catalog import CatalogError
from .store import Problem, Store
from .planner import Planner, PlanningError
from .schemas import WorkflowRequest
from .identity import OperatorAuth,current_principal
from .authorization import AuthorizedStore
from .operations import status as operation_status
from .readiness import report as readiness_report
from .feedback import export as export_improvement
from .transport import TransportBoundary
from .nodes import Nodes,Onboarding,NodeChanges
from .discovery import Discovery,ScanRequest
from .agent_export import AgentExport
from .model_connection import ModelConnection,Candidate,Revision,Selection
from .demo import DemoNode
from .conversations import Conversations,NewConversation,EditConversation,DeleteConversation,Message,PreparePlan
from .recovery_types import RecoveryRequest
from .scheduling import Scheduler
from .schedule_types import RuleInput,RuleUpdate,RuleDecision,RuleRevision,ManualRun
from .targets import TargetScope,resolve_scope,FleetPlanner
from .rbac import capabilities,catalogue,require_global
from .system_preferences import SystemPreferences,PreferenceUpdate
from .product_guides import render as render_guide,markdown as guide_markdown
from .system_info import info as system_info
from .accounts import Login,NewAccount,EditAccount,PasswordChange
from .knowledge import Knowledge,Connection,Retrieval,Draft,Review

def create_app(settings: Settings, transport=None, frontend_directory=None):
    settings.validate()
    runtime_mode="pilot-network" if settings.pilot_network_admitted else "local-development"
    store=Store(settings)
    integrations=Integrations(settings,transport)
    model_connection=ModelConnection(settings,transport)
    integrations.model_connection=model_connection
    store.integrations=integrations
    planner=Planner(integrations)
    auth=OperatorAuth(settings,transport,store)
    store.authorization=auth
    ops=AuthorizedStore(store,auth)
    knowledge_service=Knowledge(store,integrations,auth)
    nodes=Nodes(store,ops)
    discovery=Discovery(nodes);agent_export=AgentExport(nodes)
    preferences=SystemPreferences(settings.system_preferences_path or ROOT/".local/system/preferences.json")
    demo=DemoNode(store)
    conversations_service=Conversations(store,ops,integrations,planner)
    scheduler=Scheduler(store,ops,planner);store.scheduler=scheduler

    @asynccontextmanager
    async def lifespan(app):
        model_connection.warmup()
        await asyncio.to_thread(scheduler.start)
        async def purge():
            while True:
                with store.lock:
                    store.plans.purge(); store.outputs.purge(); store.workflow.previews.purge(); store.investigations.evidence.purge(); store.investigations.assessments.purge()
                with auth.lock:
                    auth.identities.purge();auth.sessions.purge();auth.states.purge()
                with knowledge_service.lock:knowledge_service.credentials.purge();knowledge_service.answers.purge()
                conversations_service.purge();discovery.purge();agent_export.purge()
                await asyncio.to_thread(demo.tick)
                await asyncio.sleep(1)
        cleaner=asyncio.create_task(purge())
        try:
            yield
        finally:
            cleaner.cancel()
            with suppress(asyncio.CancelledError): await cleaner
            await asyncio.to_thread(scheduler.close)
            conversations_service.close();discovery.close();agent_export.close()
            await asyncio.to_thread(model_connection.close)
            store.engine.dispose()

    app=FastAPI(title="PSLC Agent",version="0.18.1",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store
    app.state.auth=auth
    app.state.knowledge=knowledge_service
    app.state.nodes=nodes
    app.state.discovery=discovery;app.state.agent_export=agent_export
    app.state.model_connection=model_connection
    app.state.preferences=preferences
    app.state.demo=demo
    app.state.conversations=conversations_service
    app.state.scheduler=scheduler

    @app.middleware("http")
    async def local_boundary(request:Request,call_next):
        context_token=None
        if request.url.path.startswith('/api/'):
            try:principal=auth.authenticate(request)
            except Problem as error:return JSONResponse({'error':error.code},error.status)
            context_token=current_principal.set(principal)
        try:response=await call_next(request)
        finally:
            if context_token is not None:current_principal.reset(context_token)
        response.headers["Cache-Control"]="no-store"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.exception_handler(Problem)
    async def problem_handler(request,exc):
        return JSONResponse({"error":exc.code},exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request,exc):
        return JSONResponse({"error":"invalid_request"},422)

    @app.exception_handler(IntegrationError)
    async def integration_handler(request,exc):
        return JSONResponse({"error":str(exc)},502)

    @app.exception_handler(PlanningError)
    async def planning_handler(request,exc):
        return JSONResponse({"error":str(exc)},422)

    def operator():
        if current_principal.get() is None:raise Problem("operator_unauthorized",401)

    @app.get("/auth/config")
    def auth_config():return {"mode":settings.auth_mode,"boundary":runtime_mode}

    @app.post("/auth/login")
    def account_login(request:Request,data:Login):return auth.account_login(request,data)

    @app.get("/auth/start")
    def login():return auth.start()

    @app.get("/auth/callback")
    def callback(request:Request):return auth.callback(request)

    @app.post("/auth/logout")
    def logout(request:Request):
        try:knowledge_service.disconnect() if current_principal.get() else None
        finally:
            # Logout route is outside API middleware; resolve session before clearing it.
            try:
                p=auth.authenticate(request)
                with knowledge_service.lock:
                    knowledge_service.credentials.drop(p.id)
                    for key,(_,value) in list(knowledge_service.answers.data.items()):
                        if value["owner"]==p.id:knowledge_service.answers.drop(key)
            except Problem:pass
        return auth.logout(request)

    def agent_token(authorization: str=Header(default="")):
        if not authorization.startswith("Bearer "): raise Problem("agent_unauthorized",401)
        return authorization[7:]

    @app.get("/healthz")
    def health(): return {"service":"PSLC Agent","version":"0.18.1","mode":runtime_mode}

    api=APIRouter(prefix="/api",dependencies=[Depends(operator)])

    @api.get("/me")
    def me():
        p=ops.p
        return {"id":p.id,"roles":sorted(p.roles),"device_scope":"all" if "*" in p.device_ids else "selected","auth_mode":settings.auth_mode,"capabilities":capabilities(p,settings.auth_mode)}

    @api.post('/tasks/{tid}/rollback-plans')
    def task_rollback(tid:str,data:RecoveryRequest):return store.recovery.prepare(ops,tid,data)
    @api.post('/workflows/{wid}/rollback-plans')
    def workflow_rollback(wid:str,data:RecoveryRequest):return store.recovery.prepare(ops,wid,data,group=True)

    @api.get('/access/catalog')
    def access_catalog():return catalogue(ops.p,settings.auth_mode)
    @api.get('/settings/system')
    def system_settings():return preferences.view()
    @api.put('/settings/system')
    def save_system_settings(data:PreferenceUpdate):
        result=preferences.save(data,lambda:require_global(ops,'configure_system'))
        with store.tx() as c:store.event(c,'system_preferences_changed',actor_id=ops.p.id)
        return result
    @api.get('/system/about')
    def about():return system_info(settings,store,ops.p,app.version)
    @api.get('/help/{gid}')
    def help_guide(gid:str):
        if gid not in ('user','administrator','deployment','models'):raise Problem('not_found',404)
        try:return render_guide(gid)
        except (OSError,ValueError,KeyError):raise Problem('product_guides_unavailable',503) from None

    @api.get("/overview")
    def overview():
        return {"mode":runtime_mode,"integrations":integrations.status(),"statistics":ops.statistics()}

    def model_admin():
        ops.manage();require_global(ops,'manage_models')

    @api.get('/settings/providers')
    def model_providers():model_admin();return model_connection.providers()
    @api.post('/settings/providers/active')
    def select_model(data:Selection):return model_connection.select(data,authorize=model_admin)
    @api.post('/settings/local-models/{mid}/download')
    def download_model(mid:str):model_admin();return model_connection.local.request_download(mid)
    @api.post('/settings/local-models/{mid}/cancel')
    def cancel_model_download(mid:str):model_admin();return model_connection.local.cancel_download(mid)
    @api.post('/settings/local-models/check')
    def check_local_model():
        model_admin()
        s=model_connection.snapshot()
        if s.model_backend!='local':raise Problem('local_model_not_active',409)
        from .integrations import bounded_json
        import json,time
        start=time.monotonic()
        with integrations.model_client(s) as client:
            data=bounded_json(client,'POST',s.model_url+'/chat/completions',budget_seconds=120,headers={'Authorization':'Bearer '+s.model_token},
                json={'model':s.model_id,'messages':[{'role':'system','content':'Return ONLY the JSON object {"status":"ok"}.'},{'role':'user','content':'JSON connectivity probe.'}],
                      'stream':False,'max_tokens':256,'temperature':0,'response_format':{'type':'json_object'},'tools':[]})
        model_admin()
        try:passed=json.loads(data['choices'][0]['message']['content'])=={'status':'ok'}
        except (KeyError,ValueError,TypeError,IndexError):passed=False
        return {'status':'json_probe_passed' if passed else 'json_contract_failed','model_id':s.model_id,'provider':'local','cpu_only':True,'duration_ms':round((time.monotonic()-start)*1000),'real_task_quality_verified':False}

    @api.get('/settings/model')
    def model_settings():model_admin();return model_connection.view()
    @api.post('/settings/model/models')
    def available_models(data:Candidate):return model_connection.test(data,authorize=model_admin)
    @api.post('/settings/model/check')
    def check_model(data:Candidate):return model_connection.test(data,probe=True,authorize=model_admin)
    @api.put('/settings/model')
    def save_model(data:Candidate):return model_connection.save(data,authorize=model_admin)
    @api.delete('/settings/model')
    def disconnect_model(data:Revision):return model_connection.disable(data,authorize=model_admin)

    @api.get("/devices")
    def list_devices(): return ops.list_devices()

    @api.post("/enrollment-tokens")
    def issue(): return ops.ticket()

    @api.post("/devices/{did}/revoke")
    def revoke(did:str):
        ops.revoke(did); return {"status":"revoked"}

    @api.get("/packages")
    def packages():
        try: return ops.packages()
        except CatalogError: raise Problem("catalog_unavailable",503) from None

    @api.post("/installations",status_code=201)
    def installation(data:InstallTaskInput): return ops.create_installation(data)

    @api.get("/tasks")
    def list_tasks(): return ops.list_tasks()

    @api.post("/tasks",status_code=201)
    def create_task(data:TaskInput): return ops.create_task(data)

    @api.get("/tasks/{tid}")
    def task_detail(tid:str): return ops.get_task(tid)

    @api.post("/tasks/{tid}/decision")
    def decide(tid:str,data:Decision): return ops.decision(tid,data)

    @api.post("/tasks/{tid}/cancel")
    def cancel(tid:str):
        ops.cancel(tid); return {"status":"cancel_requested"}

    @api.post("/tasks/{tid}/reconcile")
    def reconcile(tid:str,data:Reconcile):
        ops.reconcile(tid,data); return {"status":"operator_reconciled"}

    @api.get("/tasks/{tid}/output")
    def output(tid:str):
        return ops.output(tid)

    @api.get("/events")
    def events(): return ops.get_events()

    @api.post("/agent/requests")
    def agent_request(data:WorkflowRequest):
        import re
        match=re.match(r"^найди\s+(?:инструкцию|знания|решение)\s+(.+)$",data.question.strip(),re.I|re.S)
        if match:
            if len(match[1])>200:raise Problem('query_too_long',422)
            return {'kind':'knowledge','result':knowledge_service.retrieve(match[1])}
        if data.target_ids is not None:
            return ops.workflow.request(data,FleetPlanner(planner,ops.list_devices()))
        return ops.workflow.request(data,planner)

    @api.get("/workflows")
    def workflow_list(): return ops.workflow.listing()

    @api.get("/workflows/{wid}")
    def workflow_detail(wid:str): return ops.workflow.get(wid)

    @api.post("/workflows/{wid}/decision")
    def workflow_decision(wid:str,data:Decision): return ops.workflow.decision(wid,data)

    @api.post("/workflows/{wid}/cancel")
    def workflow_cancel(wid:str): return ops.workflow.cancel(wid)

    @api.get("/tasks/{tid}/investigation")
    def investigation(tid:str): return ops.investigations.get(tid)

    @api.post("/tasks/{tid}/investigation/cancel")
    def cancel_investigation(tid:str): return ops.investigations.cancel(tid)

    @api.post("/chat")
    def chat(data:Chat):
        return ops.chat(data.question,integrations)

    @api.get("/knowledge")
    def knowledge(q:str=""):
        if len(q)>200: raise Problem("query_too_long",422)
        return knowledge_service.search(q)

    @api.post('/knowledge/connection')
    def knowledge_connect(data:Connection):return knowledge_service.connect(data)
    @api.delete('/knowledge/connection')
    def knowledge_disconnect():return knowledge_service.disconnect()
    @api.post('/knowledge/retrieve')
    def retrieve_knowledge(data:Retrieval):return knowledge_service.retrieve(data.question)
    @api.get('/knowledge/answers/{aid}')
    def knowledge_answer(aid:str):return knowledge_service.answer(aid)
    @api.get('/knowledge/records')
    def knowledge_records():return knowledge_service.records()
    @api.post('/knowledge/records')
    def knowledge_draft(data:Draft):return knowledge_service.draft(data)
    @api.post('/knowledge/records/{rid}/review')
    def knowledge_review(rid:str,data:Review):return knowledge_service.review(rid,data)

    @api.get('/operations/status')
    def operations_status():
        if 'platform_admin' not in ops.p.roles:raise Problem('operator_forbidden',403)
        return {**operation_status(store),'integrations':integrations.status()}

    @app.get('/readyz')
    def ready():
        operation_status(store)
        return {'status':'ready','scope':'local-api-and-database','external_acceptance':'pending'}

    @api.get('/readiness')
    def readiness():
        ops.manage();return readiness_report(integrations.s,store)

    @api.get('/tasks/{tid}/improvement')
    def improvement(tid:str):return export_improvement(ops,tid)

    def account_admin():
        ops.manage()
        if settings.auth_mode!='accounts':raise Problem('accounts_not_enabled',409)
        require_global(ops,'manage_users')
    @api.get('/users')
    def list_users():account_admin();return auth.accounts.list()
    @api.post('/users',status_code=201)
    def create_user(data:NewAccount):account_admin();return auth.accounts.create(data,ops.p.id)
    @api.put('/users/{aid}')
    def edit_user(aid:str,data:EditAccount):account_admin();return auth.accounts.edit(aid,data,ops.p.id)
    @api.post('/me/password')
    def password(data:PasswordChange):
        if settings.auth_mode!='accounts':raise Problem('accounts_not_enabled',409)
        return auth.accounts.password(ops.p.id,data)

    @api.get('/discovery')
    def discovery_info():return discovery.info()
    @api.post('/discovery',status_code=202)
    def discovery_start(data:ScanRequest):return discovery.start(data)
    @api.get('/discovery/{sid}')
    def discovery_get(sid:str):return discovery.get(sid)
    @api.post('/discovery/{sid}/cancel')
    def discovery_cancel(sid:str):return discovery.cancel(sid)
    @api.get('/nodes/bundles/{sid}')
    def node_bundle(sid:str):
        value=agent_export.build(sid)
        return FileResponse(value['path'],filename=value['filename'],media_type='application/zip',headers={'X-Checksum-SHA256':value['sha256'],'X-Agent-Trust':value['trust']},background=BackgroundTask(agent_export.cleanup,value['directory']))
    @api.post('/nodes/onboarding')
    def node_onboarding(data:Onboarding):return agent_export.register(nodes.onboard(data))
    @api.post('/devices/{did}/configuration-plans')
    def node_configuration(did:str,data:NodeChanges):return nodes.plan(did,data)
    @api.post('/devices/{did}/configuration-plans/{pid}/decision')
    def node_configuration_decision(did:str,pid:str,data:Decision):return nodes.decide(did,pid,data)

    @api.get('/schedules')
    def schedule_list():return scheduler.listing()
    @api.post('/schedules',status_code=201)
    def schedule_create(data:RuleInput):return scheduler.save(data)
    @api.get('/schedules/{sid}')
    def schedule_get(sid:str):return scheduler.get(sid)
    @api.put('/schedules/{sid}')
    def schedule_edit(sid:str,data:RuleUpdate):return scheduler.save(data,sid)
    @api.post('/schedules/{sid}/decision')
    def schedule_decide(sid:str,data:RuleDecision):return scheduler.decide(sid,data)
    @api.post('/schedules/{sid}/pause')
    def schedule_pause(sid:str,data:RuleRevision):return scheduler.pause(sid,data)
    @api.post('/schedules/{sid}/delete')
    def schedule_delete(sid:str,data:RuleRevision):return scheduler.pause(sid,data,delete=True)
    @api.post('/schedules/{sid}/runs',status_code=202)
    def schedule_now(sid:str,data:ManualRun):return scheduler.manual(sid,data)
    @api.get('/schedules/{sid}/runs/{rid}')
    def schedule_run(sid:str,rid:str):return scheduler.run(sid,rid)

    @api.get('/model/active')
    def active_model():
        snap=integrations.s
        return {'id':snap.model_id,'provider':snap.model_backend,'status':integrations.status()['model']}

    @api.get('/conversations')
    def conversation_list():return conversations_service.listing()
    @api.post('/conversations',status_code=201)
    def conversation_create(data:NewConversation):return conversations_service.create(data)
    @api.get('/conversations/{cid}')
    def conversation_get(cid:str):return conversations_service.get(cid)
    @api.put('/conversations/{cid}')
    def conversation_edit(cid:str,data:EditConversation):return conversations_service.edit(cid,data)
    @api.post('/conversations/{cid}/delete')
    def conversation_delete(cid:str,data:DeleteConversation):return conversations_service.remove(cid,data)
    @api.post('/conversations/{cid}/messages',status_code=202)
    def conversation_message(cid:str,data:Message):return conversations_service.send(cid,data)
    @api.post('/conversations/{cid}/messages/{tid}/cancel')
    def conversation_cancel(cid:str,tid:str):return conversations_service.cancel(cid,tid)
    @api.post('/conversations/{cid}/plans')
    def conversation_plan(cid:str,data:PreparePlan):return conversations_service.plan(cid,data)
    @api.post('/targets/resolve')
    def target_resolve(data:TargetScope):
        ops.execute()
        targets=resolve_scope(data,ops.list_devices())
        return {'count':len(targets),'devices':targets,'snapshot':[d['id'] for d in targets]}

    app.include_router(api)

    @app.post("/agent/enroll")
    def enroll(data:Enroll): return store.enroll(data)

    @app.post("/agent/{did}/poll")
    def poll(did:str,token:str=Depends(agent_token),agent_protocol:int=Header(default=0,ge=0,le=1,alias="X-PSLC-Agent-Protocol")):
        return {"job":store.poll(did,token,agent_protocol)}

    @app.post("/agent/{did}/tasks/{tid}/result")
    def result(did:str,tid:str,data:Result,background:BackgroundTasks,token:str=Depends(agent_token)):
        value=store.finish(did,token,tid,data)
        if data.diagnostics is not None:background.add_task(store.investigations.analyze,tid,integrations)
        return value

    dist=Path(frontend_directory).resolve() if frontend_directory else ROOT/"frontend/dist"
    if frontend_directory and not dist.is_relative_to(ROOT):raise ValueError("frontend_directory_outside_project")
    if dist.exists(): app.mount("/",StaticFiles(directory=dist,html=True),name="workspace")
    app.add_middleware(TransportBoundary,settings=settings)
    return app
