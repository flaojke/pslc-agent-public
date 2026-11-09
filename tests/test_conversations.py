from recovery_fixture import strip_recovery_snapshot
from recovery_fixture import legacy_version
import hashlib,json,time
from dataclasses import replace
from pathlib import Path
from threading import Event
import httpx,pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update,text
from backend.app import create_app
from backend.config import Settings
from backend.schemas import Enroll,WorkflowRequest,Decision,Result
from backend.store import Store,devices,accounts,version,SCHEMA_VERSION,Volatile,Problem
from backend.operations import backup,restore
from backend.targets import TargetScope,resolve_scope,FleetPlanner
from backend.planner import Planner
from backend.integrations import Integrations

TOKEN='conversation-public-fixture-token'
ORIGIN='http://127.0.0.1:8787'
MARKER='RAW-DIALOGUE-MARKER-859410'
def settings(tmp_path,**kw):
    return Settings(TOKEN,'sqlite:///'+(tmp_path/'case.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,**kw)
def login(c,name='admin',password='admin'):
    assert c.post('/auth/login',json={'username':name,'password':password}).status_code==200
def new(c,title='Проверка доступности'):
    r=c.post('/api/conversations',json={'title':title});assert r.status_code==201,r.text;return r.json()
def wait(c,cid):
    for _ in range(200):
        value=c.get('/api/conversations/'+cid).json()
        if value['turns'] and value['turns'][-1]['state'] not in ('queued','generating'):return value
        time.sleep(.01)
    pytest.fail('generation did not finish')
def node(store,name='pc-01',os='Windows',kind='unknown'):
    result=store.enroll(Enroll(agent_protocol=1,enrollment_token=store.ticket()['enrollment_token'],name=name,os_family=os))
    with store.tx() as conn:conn.execute(update(devices).where(devices.c.id==result['device_id']).values(device_kind=kind))
    return result

def test_dialogue_context_is_owned_volatile_and_continuable(tmp_path):
    seen=[]
    def handle(request):
        body=json.loads(request.content);seen.append(body['messages'])
        return httpx.Response(200,json={'choices':[{'message':{'content':'Гипотеза: '+body['messages'][-1]['content']}}]})
    s=settings(tmp_path,model_url='https://fixture.invalid/v1',model_id='fixture-dialogue')
    app=create_app(s,httpx.MockTransport(handle))
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);a=new(c);b=new(c,'Другое дело')
        for i,q in enumerate((MARKER,'Как проверить эту гипотезу?')):
            r=c.post('/api/conversations/'+a['id']+'/messages',json={'request_id':'turn-id-000'+str(i),'question':q});assert r.status_code==202,r.text
            value=wait(c,a['id']);assert value['turns'][-1]['state']=='completed'
        assert len(seen)==2 and MARKER in json.dumps(seen[1],ensure_ascii=False)
        assert c.get('/api/conversations/'+b['id']).json()['turns']==[]
        r=c.post('/api/users',json={'username':'other','password':'fixture-password','roles':['operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False});assert r.status_code==201
        c.post('/auth/logout');login(c,'other','fixture-password')
        assert c.get('/api/conversations/'+a['id']).status_code==404
        assert c.get('/api/conversations').json()==[]
    report=backup(s.database_url,tmp_path/'metadata.json')
    assert MARKER.encode() not in (tmp_path/'metadata.json').read_bytes()
    assert MARKER.encode() not in (tmp_path/'case.sqlite3').read_bytes()
    app2=create_app(s)
    with TestClient(app2,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);value=c.get('/api/conversations/'+a['id']).json()
        assert len(value['turns'])==2 and all(t['temporary'] is None for t in value['turns'])

def test_cancel_late_reply_and_duplicate_id(tmp_path):
    entered=Event();release=Event()
    def handle(request):
        entered.set();assert release.wait(5)
        return httpx.Response(200,json={'choices':[{'message':{'content':MARKER}}]})
    app=create_app(settings(tmp_path,model_url='https://fixture.invalid/v1',model_id='fixture-dialogue'),httpx.MockTransport(handle))
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);cid=new(c)['id'];url='/api/conversations/'+cid
        body={'request_id':'turn-cancel-001','question':'Уточни проблему'}
        assert c.post(url+'/messages',json=body).status_code==202
        assert entered.wait(2)
        assert c.post(url+'/messages',json=body).status_code==202
        assert c.post(url+'/messages',json={**body,'question':'Другое'}).status_code==409
        assert c.post(url+'/messages',json={'request_id':'other-id-001','question':'Ещё'}).status_code==409
        assert c.post(url+'/messages/turn-cancel-001/cancel').status_code==200
        release.set();time.sleep(.05)
        value=c.get(url).json();assert len(value['turns'])==1 and value['turns'][0]['state']=='cancelled'
        assert value['turns'][0]['temporary']['answer'] is None

def test_classification_scope_and_node_type_confirmation(tmp_path):
    app=create_app(settings(tmp_path))
    win=node(app.state.store,'win-server','Windows','server');lin=node(app.state.store,'linux-pc','Linux','workstation');unknown=node(app.state.store)
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c)
        r=c.post('/api/targets/resolve',json={'kind':'workstations'}).json();assert r['snapshot']==[lin['device_id']]
        assert c.post('/api/targets/resolve',json={'kind':'manual','ids':['not-allowed']}).status_code==404
        p=c.post('/api/devices/'+unknown['device_id']+'/configuration-plans',json={'name':'pc-01','device_kind':'server'}).json()
        r=c.post('/api/devices/'+unknown['device_id']+'/configuration-plans/'+p['id']+'/decision',json={'approve':True,'plan_hash':p['plan_hash']});assert r.status_code==200
        p=c.post('/api/devices/'+unknown['device_id']+'/configuration-plans',json={'name':'pc-01'}).json()
        assert p['before']['device_kind']=='server' and 'device_kind' not in p['changes']

def test_group_plan_all_targets_limits_and_partial_isolation(tmp_path):
    s=Settings(TOKEN,'sqlite:///'+(tmp_path/'group.sqlite3').as_posix());store=Store(s)
    nodes=[node(store,'pc-'+str(i)) for i in range(7)]
    inv=store.list_devices();planner=FleetPlanner(Planner(Integrations(s)),inv)
    req=WorkflowRequest(request_id='group-request-01',session_id='group-session',question='Проверь состояние всех машин',target_ids=[n['device_id'] for n in nodes])
    flow=store.workflow.request(req,planner);assert len(flow['steps'])==7 and flow['source']=='approved_template'
    assert all(store.poll(n['device_id'],n['credential'],agent_protocol=1) is None for n in nodes)
    store.workflow.decision(flow['id'],Decision(approve=True,plan_hash=flow['plan_hash']))
    first=[store.poll(n['device_id'],n['credential'],agent_protocol=1) for n in nodes]
    assert sum(j is not None for j in first)==5
    n=nodes[0];store.finish(n['device_id'],n['credential'],first[0]['task_id'],Result(lease_id=first[0]['lease_id'],outcome='failed'))
    assert store.poll(nodes[5]['device_id'],nodes[5]['credential'],agent_protocol=1) is not None
    assert store.workflow.get(flow['id'])['state'] in ('running','queued')

def test_five_hundred_target_snapshot_not_truncated(tmp_path):
    s=Settings(TOKEN,'sqlite:///'+(tmp_path/'fleet.sqlite3').as_posix());store=Store(s)
    nodes=[node(store,'fleet-'+str(i)) for i in range(500)]
    ids=[n['device_id'] for n in nodes];inv=store.list_devices()
    flow=store.workflow.request(WorkflowRequest(request_id='fleet-request-500',session_id='fleet-session',question='Проверь состояние всех машин',target_ids=ids),FleetPlanner(Planner(Integrations(s)),inv))
    assert len(flow['steps'])==500 and set(flow['target_snapshot'])==set(ids)
    added={t['device_id'] for t in flow['steps']};assert added==set(ids)

def test_schema6_upgrade_and_metadata_restore_preserve_identity(tmp_path):
    s=settings(tmp_path);app=create_app(s)
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);new(c);n=node(app.state.store)
        with app.state.store.tx() as conn:
            identity=conn.scalar(select(devices.c.credential_hash));verifier=conn.scalar(select(accounts.c.password_hash))
    store=app.state.store
    with store.engine.begin() as conn:
        conn.execute(text('DROP TABLE conversation_turns'));conn.execute(text('DROP TABLE conversations'))
        conn.execute(text('ALTER TABLE devices DROP COLUMN device_kind'))
        for col in ('execution_mode','target_snapshot','conversation_id'):conn.execute(text('ALTER TABLE workflows DROP COLUMN '+col))
        legacy_version(conn,'UPDATE schema_version SET id=6')
    store.engine.dispose();upgraded=Store(s)
    with upgraded.tx() as conn:
        assert conn.scalar(select(version.c.id))==SCHEMA_VERSION
        assert conn.scalar(select(devices.c.credential_hash))==identity
        assert conn.scalar(select(accounts.c.password_hash))==verifier
    assert upgraded.list_devices()[0]['device_kind']=='unknown'
    path=tmp_path/'snapshot.json';b=backup(s.database_url,path)
    value=json.loads(path.read_bytes())
    for table in ('conversations','conversation_turns'):del value['tables'][table]
    for row in value['tables']['devices']:del row['device_kind']
    for row in value['tables']['workflows']:
        for col in ('execution_mode','target_snapshot','conversation_id'):del row[col]
    strip_recovery_snapshot(value);value['schema_version']=6;value['tables']['schema_version']=[{'id':6}]
    path.write_text(json.dumps(value),encoding='utf-8')
    target='sqlite:///'+(tmp_path/'restored.sqlite3').as_posix()
    restore(path,target,hashlib.sha256(path.read_bytes()).hexdigest())
    restored=Store(replace(s,database_url=target))
    assert restored.poll(n['device_id'],n['credential'],agent_protocol=1) is None

def test_ram_byte_quota_and_no_partial_overflow():
    v=Volatile(10,max_bytes=20);v.put('a','1234',5)
    with pytest.raises(Problem):v.put('b','x'*50,5)
    assert v.get('a')=='1234' and v.get('b') is None
    v.drop('a');assert sum(v.sizes.values())==0


def test_generation_capacity_and_delete_discard_late_answer(tmp_path):
    release=Event()
    def handle(request):
        release.wait(5)
        return httpx.Response(200,json={'choices':[{'message':{'content':MARKER}}]})
    app=create_app(settings(tmp_path,model_url='https://fixture.invalid/v1',model_id='fixture-dialogue'),httpx.MockTransport(handle))
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);ids=[new(c)['id'] for _ in range(6)]
        try:
            for i,cid in enumerate(ids[:5]):
                assert c.post('/api/conversations/'+cid+'/messages',json={'request_id':'capacity-turn-'+str(i),'question':'Проверка'}).status_code==202
            assert c.post('/api/conversations/'+ids[5]+'/messages',json={'request_id':'capacity-turn-6','question':'Проверка'}).status_code==429
            assert c.post('/api/conversations/'+ids[0]+'/delete',json={}).status_code==200
            assert c.get('/api/conversations/'+ids[0]).status_code==404
            release.set()
            for cid in ids[1:5]:wait(c,cid)
            assert app.state.conversations.texts.get('capacity-turn-0') is None
            assert app.state.conversations.contexts.get(ids[0]) is None
        finally:release.set()


def test_scope_change_invalidates_unconfirmed_plan_and_expiry_is_explicit(tmp_path):
    app=create_app(settings(tmp_path));n=node(app.state.store,'scope-pc','Linux','workstation')
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);cid=new(c)['id'];url='/api/conversations/'+cid
        assert c.put(url,json={'title':'Проверка','scope':{'kind':'workstations'}}).status_code==200
        p=c.post(url+'/plans',json={'request_id':'scope-plan-01','question':'Проверь состояние всех машин'})
        assert p.status_code==200,p.text
        flow=c.get(url).json()['turns'][-1]['workflow'];assert flow['state']=='awaiting_confirmation'
        r=c.put(url,json={'title':'Проверка','scope':{'kind':'none'}});assert r.status_code==200,r.text
        assert c.post('/api/workflows/'+flow['id']+'/decision',json={'approve':True,'plan_hash':flow['plan_hash']}).status_code==409
        assert app.state.store.poll(n['device_id'],n['credential'],agent_protocol=1) is None
        assert c.post(url+'/messages',json={'request_id':'expire-turn-01','question':MARKER}).status_code==202
        wait(c,cid);app.state.conversations.texts.drop('expire-turn-01');app.state.conversations.contexts.drop(cid)
        value=c.get(url).json();turn=value['turns'][-1]
        assert turn['temporary'] is None and turn['temporary_status']=='expired_or_absent' and value['context_expires_in']==0


def test_followup_receives_owned_computed_results_without_raw_output(tmp_path):
    seen=[]
    def handle(request):
        seen.append(json.loads(request.content)['messages'][0]['content'])
        return httpx.Response(200,json={'choices':[{'message':{'content':'Наблюдение требует интерпретации.'}}]})
    app=create_app(settings(tmp_path,model_url='https://fixture.invalid/v1',model_id='fixture-dialogue'),httpx.MockTransport(handle));store=app.state.store;n=node(store)
    with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
        login(c);cid=new(c)['id'];url='/api/conversations/'+cid
        c.put(url,json={'title':'Состояние','scope':{'kind':'manual','ids':[n['device_id']]}})
        assert c.post(url+'/plans',json={'request_id':'facts-plan-01','question':'Проверь состояние всех машин'}).status_code==200
        flow=c.get(url).json()['turns'][-1]['workflow']
        assert c.post('/api/workflows/'+flow['id']+'/decision',json={'approve':True,'plan_hash':flow['plan_hash']}).status_code==200
        job=store.poll(n['device_id'],n['credential'],agent_protocol=1)
        store.finish(n['device_id'],n['credential'],job['task_id'],Result(lease_id=job['lease_id'],outcome='ok',exit_code=0,cpu_percent=5,memory_percent=31,disk_free_gb=99,output=MARKER))
        assert c.post(url+'/messages',json={'request_id':'facts-followup-01','question':'Что означают результаты?'}).status_code==202
        value=wait(c,cid);assert value['turns'][-1]['state']=='completed'
        assert 'verified_steps' in seen[-1] and 'memory_percent' in seen[-1] and '31' in seen[-1] and MARKER not in seen[-1]


def test_five_steps_per_five_hundred_nodes_and_unknown_peer_progress(tmp_path):
    from backend.planner import ActionPlan,HealthStep
    class Five:
        def propose(self,q,inventory,packages):
            return ActionPlan(status='plan',steps=[HealthStep(kind='health',device_id=inventory[0]['id']) for _ in range(5)]),'fixture'
    s=Settings(TOKEN,'sqlite:///'+(tmp_path/'wide.sqlite3').as_posix());store=Store(s)
    nodes=[node(store,'wide-'+str(i)) for i in range(500)]
    ids=[n['device_id'] for n in nodes]
    flow=store.workflow.request(WorkflowRequest(request_id='wide-request-500',session_id='wide-session',question='Пять проверок',target_ids=ids),FleetPlanner(Five(),store.list_devices()))
    assert len(flow['steps'])==2500 and len(flow['target_snapshot'])==500
    store.workflow.decision(flow['id'],Decision(approve=True,plan_hash=flow['plan_hash']))
    first=store.poll(nodes[0]['device_id'],nodes[0]['credential'],agent_protocol=1)
    assert first is not None
    with store.tx() as c:
        from backend.store import tasks
        c.execute(update(tasks).where(tasks.c.id==first['task_id']).values(state='outcome_unknown'))
    assert store.poll(nodes[0]['device_id'],nodes[0]['credential'],agent_protocol=1) is None
    assert store.poll(nodes[1]['device_id'],nodes[1]['credential'],agent_protocol=1) is not None
    current=store.workflow.get(flow['id'])
    blocked=[t for t in current['steps'] if t['device_id']==nodes[0]['device_id']]
    assert [t['state'] for t in blocked]==['outcome_unknown']+['cancelled']*4
