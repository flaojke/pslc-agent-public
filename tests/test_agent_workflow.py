from recovery_fixture import command_contract,simulated_recovery,paired_fixture,legacy_version
from backend.store import SCHEMA_VERSION
import json
import httpx
import pytest
from backend.config import Settings
from backend.integrations import Integrations, IntegrationError
from backend.planner import Planner, PlanningError, ActionPlan

TOKEN='test-operator-token-with-sufficient-length'
INVENTORY=[{'id':'node-id-001','name':'pc-01','os_family':'Windows','commands_enabled':True,'installations_enabled':True}]

def planner(proposal=None):
    if proposal is None:
        return Planner(Integrations(Settings(TOKEN,'sqlite:///:memory:')))
    def handle(request):
        body=json.loads(request.content)
        assert body['stream'] is False and body['model']=='test-planner'
        assert TOKEN not in request.content.decode()
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(proposal,ensure_ascii=False)}}]})
    settings=Settings(TOKEN,'sqlite:///:memory:',model_url='https://model.internal/v1',model_id='test-planner')
    return Planner(Integrations(settings,httpx.MockTransport(handle)))

@pytest.mark.parametrize('question',['Проверь состояние узла pc-01','Покажи статус pc-01.','Проверь состояние node-id-001'])
def test_local_health(question):
    proposal,source=planner().propose(question,INVENTORY,[])
    assert isinstance(proposal,ActionPlan) and source=='local_rules'
    assert proposal.steps[0].device_id=='node-id-001'


def test_local_install_and_explicit_command():
    plan,_=planner().propose('Установи tool версии 1.2 на pc-01',INVENTORY,[])
    assert plan.steps[0].kind=='install' and plan.steps[0].package_version=='1.2'
    question="Выполни python на pc-01:\nprint('ok')\nПроверка python:\nprint('ready')\nОжидается:\nready"
    plan,_=planner().propose(question,INVENTORY,[])
    assert plan.status=='clarification' # No undeclared resource is invented for the legacy free-text command.

@pytest.mark.parametrize('question',['Проверь состояние','Сделай всё хорошо','Проверь состояние unknown','Проверь состояние pc-01 и pc-02'])
def test_local_clarification(question):
    assert planner().propose(question,INVENTORY,[])[0].status=='clarification'


def test_duplicate_name_requires_id():
    inventory=INVENTORY+[dict(INVENTORY[0],id='second-id')]
    assert planner().propose('Проверь состояние pc-01',inventory,[])[0].status=='clarification'
    assert planner().propose('Проверь состояние node-id-001',inventory,[])[0].status=='plan'


def test_configured_model_and_unmentioned_target():
    p=planner({'status':'plan','steps':[{'kind':'health','device_id':'node-id-001'}]})
    plan,source=p.propose('Проверь состояние pc-01',INVENTORY,[])
    assert plan.status=='plan' and source=='configured_model'
    assert p.propose('Проверь все машины',INVENTORY,[])[0].status=='clarification'

@pytest.mark.parametrize('proposal',[
    {'status':'plan','steps':[]},
    {'status':'plan','steps':[{'kind':'health','device_id':'node-id-001'}]*6},
    {'status':'plan','steps':[{'kind':'health','device_id':'node-id-001','ignore_policy':True}]},
    {'status':'plan','steps':[{'kind':'reboot','device_id':'node-id-001'}]},
    {'status':'plan','steps':[{'kind':'command','device_id':'node-id-001','runtime':'python','code':'1'}]},
    {'status':'plan','steps':[{'kind':'health','device_id':'invented'}]},
    {'status':'plan','steps':[{'kind':'install','device_id':'node-id-001','package_id':'x','package_version':'1','url':'https://evil.test'}]},
])
def test_untrusted_model_rejected(proposal):
    with pytest.raises(PlanningError):planner(proposal).propose('Проверь состояние pc-01',INVENTORY,[])


def test_model_redirect_is_not_fallback():
    settings=Settings(TOKEN,'sqlite:///:memory:',model_url='https://model.internal/v1',model_id='test-planner')
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(302,headers={'location':'https://other.test'})
    with pytest.raises(IntegrationError):Planner(Integrations(settings,httpx.MockTransport(handler))).propose('Проверь состояние pc-01',INVENTORY,[])
    assert len(calls)==1
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import time
from fastapi.testclient import TestClient
from sqlalchemy import select, update, inspect
from backend.app import create_app
from backend.store import devices, tasks, workflows, workflow_steps, version, Store
from backend.schemas import Enroll
from agent.executor import execute, verify_job

@contextmanager
def workflow_env(tmp_path,model=False,commands=True,installs=False):
    catalog=tmp_path/'packages.json'
    package={'id':'fixture-tool','name':'Fixture','version':'1.2.3','os_family':'Windows','installer':'msi',
        'source_url':'https://fixtures.invalid/tool.msi','sha256':'a'*64,'timeout_seconds':600,
        'verification_argv':['fixture-probe'],'expected_output':'1.2.3','uninstall_instructions':'Fixture only; no real installation.','recovery':{'uninstall_argv':['fixture-uninstall'],'absence_argv':['fixture-absence'],'absence_expected_output':'absent'}}
    catalog.write_text(json.dumps({'schema_version':1,'packages':[package]}))
    settings=Settings(TOKEN,'sqlite:///'+(tmp_path/'workflow.sqlite3').as_posix(),package_catalog_path=str(catalog),
        model_url='https://model.internal/v1' if model else '',model_id='fixture-planner' if model else '')
    response={}
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(response)}}]})
    app=create_app(settings,httpx.MockTransport(handler))
    with TestClient(app) as c:
        c.headers['Authorization']='Bearer '+TOKEN
        identity=app.state.store.enroll(Enroll(agent_protocol=1,enrollment_token=app.state.store.ticket()['enrollment_token'],name='pc-01',os_family='Windows',commands_enabled=commands,installations_enabled=installs))
        yield {'c':c,'store':app.state.store,'node':identity,'settings':settings,'response':response,'catalog':catalog,'package':package,'config_resource':str(tmp_path/'proof.json')}


def prepare(env,question='Проверь состояние узла pc-01',rid='request-0001',session='main-session'):
    return env['c'].post('/api/agent/requests',json={'request_id':rid,'session_id':session,'question':question})

def decision(env,w,approve=True,plan_hash=None):
    return env['c'].post('/api/workflows/'+w['id']+'/decision',json={'plan_hash':plan_hash or w['plan_hash'],'approve':approve})

def poll_workflow(env,node=None):
    n=node or env['node']
    return env['c'].post('/agent/'+n['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1','Authorization':'Bearer '+n['credential']}).json()['job']

def finish_workflow(env,job,result=None,node=None):
    n=node or env['node']
    result=result or {'outcome':'ok','exit_code':0,'cpu_percent':2,'memory_percent':30,'disk_free_gb':100}
    if job['payload']['kind'] in ('command','install') and 'recovery' not in result:result={**result,'recovery':simulated_recovery(job,'not_needed' if result.get('outcome')=='already_installed' else 'ready')}
    return env['c'].post('/agent/'+n['device_id']+'/tasks/'+job['task_id']+'/result',headers={'Authorization':'Bearer '+n['credential']},json={'lease_id':job['lease_id'],**result})

def detail(env,w):return env['c'].get('/api/workflows/'+w['id']).json()

def model_steps(env,steps):env['response'].update(status='plan',steps=steps)

def health_step(env):return {'kind':'health','device_id':env['node']['device_id']}

def command_step(env,code="print('ok')",expected='ready',check="print('ready')"):
    return {**command_contract(env['config_resource']),'kind':'command','device_id':env['node']['device_id'],'runtime':'python','code':code,
            'verification':{'runtime':'python','code':check,'expected_output':expected}}


def test_main_health_confirmation_replay_and_result(tmp_path):
    with workflow_env(tmp_path) as e:
        w=prepare(e).json()
        assert w['state']=='awaiting_confirmation' and w['source']=='local_rules'
        assert w['preview']['steps'][0]['payload']['device_id']==e['node']['device_id']
        assert poll_workflow(e) is None
        assert prepare(e).json()['id']==w['id'] and len(e['c'].get('/api/tasks').json())==1
        assert prepare(e,'Другой запрос').status_code==409
        assert decision(e,w,plan_hash='0'*64).status_code==409
        assert e['c'].post('/api/tasks/'+w['steps'][0]['task_id']+'/decision',json={'approve':True,'plan_hash':e['store'].get_task(w['steps'][0]['task_id'])['plan_hash']}).json()['error']=='workflow_confirmation_required'
        assert decision(e,w).json()['state']=='queued'
        assert decision(e,w).status_code==200
        assert decision(e,w,False).status_code==409
        job=poll_workflow(e);assert verify_job(job,e['node']['device_id'])
        changed=dict(job,payload=dict(job['payload'],device_id='other'))
        assert not verify_job(changed,e['node']['device_id'])
        result=execute(job['payload'])
        assert finish_workflow(e,job,result).json()['state']=='verified'
        assert detail(e,w)['state']=='verified' and detail(e,w)['preview'] is None
        assert finish_workflow(e,job,result).status_code==200
        counts=e['store'].statistics()['counts'];assert counts['confirmed']==1 and counts['question_requested']==1 and counts['confirmation_requested']==1


def test_rejection_and_clarification_create_no_dispatch(tmp_path):
    with workflow_env(tmp_path) as e:
        clarification=prepare(e,'Проверь состояние',rid='clarify-0001').json()
        assert clarification['state']=='clarification' and not clarification['steps']
        assert prepare(e,'Проверь состояние',rid='clarify-0001').json()['id']==clarification['id']
        w=prepare(e).json();assert decision(e,w,False).json()['state']=='rejected'
        assert decision(e,w,False).status_code==200
        assert poll_workflow(e) is None
        assert e['store'].statistics()['counts']['rejected']==1

@pytest.mark.parametrize('mode',['expire','revoke','tamper'])
def test_plan_gates_before_confirm(tmp_path,mode):
    with workflow_env(tmp_path) as e:
        w=prepare(e).json()
        if mode=='expire':
            e['store'].workflow.previews.data[w['id']]=(time.time()-1,{})
        elif mode=='revoke':e['store'].revoke(e['node']['device_id'])
        else:e['store'].plans.get(w['steps'][0]['task_id'])['timeout_seconds']=99
        assert decision(e,w).status_code==409
        assert detail(e,w)['state'] in ('plan_expired','policy_changed')
        assert e['store'].statistics()['active_fleet']==0


def test_preparation_atomic_if_later_step_disabled(tmp_path):
    with workflow_env(tmp_path,model=True,commands=False) as e:
        model_steps(e,[health_step(e),command_step(e)])
        assert prepare(e).status_code==403
        assert e['c'].get('/api/tasks').json()==[]
        assert e['c'].get('/api/workflows').json()[0]['state']=='planner_failed'


def test_sequential_safe_command_postcheck_and_nonretention(tmp_path):
    marker='TRANSIENT-WORKFLOW-MARKER-572918'
    destination=tmp_path/'proof.json'
    code=f"from pathlib import Path;Path({str(destination)!r}).write_text({marker!r});print({marker!r})"
    check=f"from pathlib import Path;print(Path({str(destination)!r}).read_text())"
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[command_step(e,code,marker,check),health_step(e)])
        w=prepare(e,'Выполни проверяемую задачу на pc-01 '+marker).json()
        assert len(w['preview']['steps'])==2
        decision(e,w);job=poll_workflow(e)
        assert poll_workflow(e) is None
        result=execute(job['payload'],True,str(tmp_path),task_context={'state_path':str(tmp_path/'native-identity/state.json'),'device_id':job['payload']['device_id'],'task_id':job['task_id'],'plan_hash':job['plan_hash']})
        assert result['command_verification']['matched'] and destination.read_text()==marker
        assert finish_workflow(e,job,result).json()['state']=='verified'
        second=poll_workflow(e);assert second and second['payload']['kind']=='health'
        finish_workflow(e,second)
        assert detail(e,w)['state']=='verified'
        assert marker not in e['c'].get('/api/tasks').text+e['c'].get('/api/events').text+e['c'].get('/api/workflows').text
    assert marker.encode() not in (tmp_path/'workflow.sqlite3').read_bytes()

@pytest.mark.parametrize('mode',['mismatch','missing','foreign','incomplete_health'])
def test_unverified_step_stops_chain(tmp_path,mode):
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[health_step(e) if mode=='incomplete_health' else command_step(e,expected='wanted'),health_step(e)])
        w=prepare(e).json();decision(e,w);job=poll_workflow(e)
        if mode=='incomplete_health':result={'outcome':'ok','exit_code':0}
        elif mode=='missing':result={'outcome':'process_succeeded','exit_code':0}
        else:
            result=execute(job['payload'],True,str(tmp_path),task_context={'state_path':str(tmp_path/'native-identity/state.json'),'device_id':job['payload']['device_id'],'task_id':job['task_id'],'plan_hash':job['plan_hash']})
            if mode=='foreign':
                result['outcome']='process_succeeded';result['command_verification'].update(verification_hash='0'*64,matched=True)
        assert finish_workflow(e,job,result).json()['state']=='verification_failed'
        assert detail(e,w)['state']=='stopped' and detail(e,w)['steps'][1]['state']=='cancelled'
        assert poll_workflow(e) is None

@pytest.mark.parametrize('after_confirm',[False,True])
def test_workflow_catalog_invalidates(tmp_path,after_confirm):
    with workflow_env(tmp_path,installs=True) as e:
        w=prepare(e,'Установи fixture-tool версии 1.2.3 на pc-01').json()
        assert 'sha256' in w['preview']['steps'][0]['payload']['manifest']
        if after_confirm:decision(e,w)
        e['package']['sha256']='b'*64
        e['catalog'].write_text(json.dumps({'schema_version':1,'packages':[e['package']]}))
        assert detail(e,w)['state']=='policy_changed' and poll_workflow(e) is None
        assert decision(e,w).status_code==(200 if after_confirm else 409)


def test_installer_opt_in_and_unapproved_package(tmp_path):
    with workflow_env(tmp_path) as e:
        assert prepare(e,'Установи fixture-tool версии 1.2.3 на pc-01').status_code==403
        assert prepare(e,'Установи unknown версии 1 на pc-01',rid='unknown-0001').status_code==409
        assert e['c'].get('/api/tasks').json()==[]


def test_lease_unknown_and_late_proof_does_not_resume(tmp_path):
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[health_step(e),health_step(e)])
        w=prepare(e).json();decision(e,w);job=poll_workflow(e)
        with e['store'].tx() as c:c.execute(update(tasks).where(tasks.c.id==job['task_id']).values(lease_until=time.time()-1))
        assert detail(e,w)['state']=='outcome_unknown' and poll_workflow(e) is None
        assert e['store'].statistics()['active_fleet']==1
        finish_workflow(e,job)
        assert detail(e,w)['steps'][0]['state']=='verified' and detail(e,w)['state']=='stopped'
        assert poll_workflow(e) is None


def test_cancel_running_does_not_fake_rollback(tmp_path):
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[health_step(e),health_step(e)])
        w=prepare(e).json();decision(e,w);job=poll_workflow(e)
        cancelled=e['c'].post('/api/workflows/'+w['id']+'/cancel').json()
        assert cancelled['state']=='cancel_requested' and cancelled['steps'][0]['state']=='cancel_requested'
        assert cancelled['steps'][1]['state']=='cancelled' and poll_workflow(e) is None
        finish_workflow(e,job)
        assert detail(e,w)['state']=='stopped'


def test_restart_expires_pending_preserves_metadata_and_unknown(tmp_path):
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[health_step(e),health_step(e)])
        w=prepare(e).json();decision(e,w);job=poll_workflow(e)
        n=e['node'];settings=e['settings']
    app=create_app(settings)
    with TestClient(app) as c:
        c.headers['Authorization']='Bearer '+TOKEN
        state=c.get('/api/workflows/'+w['id']).json()
        assert state['state']=='outcome_unknown' and state['preview'] is None
        assert state['steps'][0]['state']=='outcome_unknown' and state['steps'][1]['state'] in ('cancelled','plan_expired')
        assert c.post('/agent/'+n['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1','Authorization':'Bearer '+n['credential']}).json()['job'] is None
        assert app.state.store.statistics()['active_fleet']==1
        assert c.post('/api/agent/requests',json={'request_id':w['id'],'session_id':'main-session','question':'Проверь состояние узла pc-01'}).json()['id']==w['id']


def test_additive_v2_upgrade_preserves_identity(tmp_path):
    settings=Settings(TOKEN,'sqlite:///'+(tmp_path/'previous.sqlite3').as_posix())
    old=Store(settings)
    n=old.enroll(Enroll(agent_protocol=1,enrollment_token=old.ticket()['enrollment_token'],name='existing-node',os_family='Windows'))
    with old.engine.begin() as c:
        workflow_steps.drop(c);workflows.drop(c);legacy_version(c,'UPDATE schema_version SET id=2')
    old.engine.dispose()
    current=Store(settings)
    assert {'workflows','workflow_steps'}<=set(inspect(current.engine).get_table_names())
    with current.tx() as c:
        assert c.scalar(select(version.c.id))==SCHEMA_VERSION
        current.check_agent(c,n['device_id'],n['credential'])
    assert current.list_devices()[0]['installations_enabled'] is False
    current.engine.dispose()


def test_workflows_share_session_and_fleet_limits(tmp_path):
    with workflow_env(tmp_path) as e:
        nodes=[]
        for i in range(21):
            n=e['store'].enroll(Enroll(agent_protocol=1,enrollment_token=e['store'].ticket()['enrollment_token'],name='fleet-'+str(i),os_family='Windows'))
            nodes.append(n)
            w=prepare(e,'Проверь состояние fleet-'+str(i),rid='fleet-request-'+str(i),session='same' if i<6 else 'session-'+str(i)).json()
            assert decision(e,w).status_code==200
        jobs=[poll_workflow(e,n) for n in nodes]
        assert sum(x is not None for x in jobs)==20 and jobs[5] is None
        extra=e['store'].enroll(Enroll(agent_protocol=1,enrollment_token=e['store'].ticket()['enrollment_token'],name='fleet-extra',os_family='Windows'))
        w=prepare(e,'Проверь состояние fleet-extra',rid='fleet-extra-request',session='extra').json();decision(e,w)
        assert poll_workflow(e,extra) is None
        finish_workflow(e,jobs[0],node=nodes[0])
        assert poll_workflow(e,nodes[5]) is not None and e['store'].statistics()['active_fleet']==20


def test_agent_command_timeout_unknown_and_verification_no_opt_in(tmp_path):
    p={'kind':'command','workflow_id':'wf','runtime':'python','code':'import time;time.sleep(5)','timeout_seconds':1,
       'verification':{'runtime':'python','code':"print('ready')",'expected_output':'ready'}}
    assert execute(p,False,str(tmp_path))['outcome']=='commands_disabled'
    p,context=paired_fixture(p,tmp_path)
    assert execute(p,True,str(tmp_path),task_context=context)['outcome']=='outcome_unknown'
    assert execute(dict(p,verification=None),True,str(tmp_path))['outcome']=='recovery_required'

def test_workflow_installation_proof_and_reboot_gate(tmp_path):
    with workflow_env(tmp_path,model=True,installs=True) as e:
        install={'kind':'install','device_id':e['node']['device_id'],'package_id':'fixture-tool','package_version':'1.2.3'}
        model_steps(e,[install,health_step(e)])
        w=prepare(e,'Установи fixture-tool версии 1.2.3 на pc-01, затем проверь состояние').json()
        decision(e,w);job=poll_workflow(e);p=job['payload']
        proof={'package_id':'fixture-tool','version':'1.2.3','manifest_hash':p['manifest_hash'],'artifact_sha256':p['manifest']['sha256'],'version_matched':True,'reboot_required':True}
        result=finish_workflow(e,job,{'outcome':'reboot_required','exit_code':3010,'installation':proof})
        assert result.json()['state']=='reboot_required' and detail(e,w)['state']=='stopped'
        assert poll_workflow(e) is None


def test_unknown_command_result_is_idempotent(tmp_path):
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[command_step(e),health_step(e)])
        w=prepare(e).json();decision(e,w);job=poll_workflow(e)
        finish_workflow(e,job,{'outcome':'outcome_unknown'})
        before=e['store'].statistics()
        finish_workflow(e,job,{'outcome':'outcome_unknown'})
        assert e['store'].statistics()==before and before['active_fleet']==1
        assert detail(e,w)['state']=='outcome_unknown' and poll_workflow(e) is None


def test_confirmed_permissions_revalidated_and_pending_restart(tmp_path):
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[command_step(e)])
        w=prepare(e).json();decision(e,w)
        with e['store'].tx() as c:c.execute(update(devices).where(devices.c.id==e['node']['device_id']).values(commands_enabled=False))
        assert poll_workflow(e) is None and detail(e,w)['state']=='policy_changed'
        pending=prepare(e,rid='pending-health-request').json() # model still proposes command, now disabled
        assert pending.get('error')=='commands_disabled'
        model_steps(e,[health_step(e)])
        waiting=prepare(e,rid='waiting-health-request').json();settings=e['settings']
    app=create_app(settings)
    with TestClient(app) as c:
        c.headers['Authorization']='Bearer '+TOKEN
        assert c.get('/api/workflows/'+waiting['id']).json()['state']=='plan_expired'

def test_pending_history_does_not_restore_raw_preview(tmp_path):
    marker='PENDING-PREVIEW-ONLY-79253'
    with workflow_env(tmp_path,model=True) as e:
        model_steps(e,[command_step(e,code=f"print({marker!r})",expected=marker,check=f"print({marker!r})")])
        w=prepare(e).json()
        assert marker in json.dumps(w)
        history=e['c'].get('/api/workflows')
        assert marker not in history.text and history.json()[0]['preview'] is None
        assert marker in e['c'].get('/api/workflows/'+w['id']).text
        assert poll_workflow(e) is None
