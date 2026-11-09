from recovery_fixture import paired_fixture
import json
import os
import shutil
import sys
from dataclasses import replace
import httpx
import pytest
from fastapi.testclient import TestClient
from agent.executor import execute, run_bounded, verify_job
from backend.app import create_app
from backend.config import Settings
from backend.integrations import IntegrationError, Integrations, validate_endpoint
from backend.store import digest


def test_health_and_command_opt_in():
    health=execute({"kind":"health"})
    assert health['outcome']=='ok' and 0<=health['memory_percent']<=100
    assert execute({"kind":"command","runtime":"python","code":"raise Exception()"})['outcome']=='commands_disabled'

@pytest.mark.parametrize('runtime,code',[
    ('python',"print('SAFE-RUNTIME-CHECK')"),
    ('node',"console.log('SAFE-RUNTIME-CHECK')"),
    ('cmd',"echo SAFE-RUNTIME-CHECK"),
    ('powershell',"Write-Output SAFE-RUNTIME-CHECK"),
])
def test_safe_runtime(runtime,code,tmp_path):
    payload,context=paired_fixture({"kind":"command","runtime":runtime,"code":code,"timeout_seconds":10},tmp_path)
    result=execute(payload,True,str(tmp_path),task_context=context)
    if result['outcome']=='prerequisite_missing':
        pytest.skip('Runtime is not present on this test OS: '+runtime)
    assert result['outcome']=='process_succeeded'
    assert 'SAFE-RUNTIME-CHECK' in result['output']


def test_missing_runtime_and_invalid_input(monkeypatch,tmp_path):
    monkeypatch.setattr(shutil,'which',lambda name:None)
    payload,context=paired_fixture({"kind":"command","runtime":"node","code":"1"},tmp_path)
    assert execute(payload,True,str(tmp_path),task_context=context)['outcome']=='prerequisite_missing'
    assert execute({"kind":"command","runtime":"other","code":"1"},True)['outcome']=='recovery_required'


def test_bounded_output_and_timeout():
    r=run_bounded([sys.executable,'-c',"print('x'*1000000)"],5,max_output=4096)
    assert r['outcome']=='output_limit' and len(r['output'])<=4096
    r=run_bounded([sys.executable,'-c','import time;time.sleep(20)'],1)
    assert r['outcome']=='timeout' and r['duration_ms']<7000


def test_timeout_kills_standard_child_tree():
    import psutil
    code="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);print(p.pid,flush=True);time.sleep(30)"
    r=run_bounded([sys.executable,'-c',code],1)
    assert r['outcome']=='timeout'
    pid=int(r['output'].strip())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_plan_integrity():
    payload={"kind":"command","device_id":"device","code":"print(1)","runtime":"python"}
    encoded=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    job={"payload":payload,"plan_hash":digest(encoded)}
    assert verify_job(job,'device')
    assert not verify_job(job,'other')
    payload['code']='print(2)'
    assert not verify_job(job,'device')


def test_endpoint_rejects_credentials_and_insecure_remote():
    for url in ['http://remote.example/v1','https://user:secret@example.com/v1','https://example.com/v1?token=x','file:///secret']:
        with pytest.raises(IntegrationError): validate_endpoint(url)
    validate_endpoint('https://models.internal/v1')
    validate_endpoint('http://127.0.0.1:9999/v1')


def test_model_contract_and_nonretention(tmp_path):
    calls=[]
    marker='TEMPORARY-MODEL-MARKER-92371'
    def transport(req):
        calls.append(req)
        assert req.url.path=='/v1/chat/completions'
        assert json.loads(req.content)['model']=='configured-qwen'
        return httpx.Response(200,json={"choices":[{"message":{"content":marker}}]})
    db=tmp_path/'integration.sqlite3'
    settings=Settings('test-operator-secret-long-enough','sqlite:///'+db.as_posix(),model_url='https://model.internal/v1',model_id='configured-qwen')
    with TestClient(create_app(settings,httpx.MockTransport(transport))) as c:
        c.headers['Authorization']='Bearer '+settings.operator_token
        assert c.post('/api/chat',json={"question":marker}).json()['answer']==marker
        assert marker not in c.get('/api/events').text
        assert marker not in c.get('/api/tasks').text
    assert len(calls)==1 and marker.encode() not in db.read_bytes()


def test_no_model_fallback_on_redirect():
    calls=[]
    def transport(req):
        calls.append(req);return httpx.Response(302,headers={'location':'https://unapproved.example/steal'})
    s=Settings('test-operator-secret-long-enough','sqlite:///:memory:',model_url='https://model.internal/v1',model_id='gemma')
    with pytest.raises(IntegrationError): Integrations(s,httpx.MockTransport(transport)).ask('test')
    assert len(calls)==1


def test_confluence_read_only_allowlist_and_links():
    calls=[]
    def transport(req):
        calls.append(req)
        assert req.method=='GET' and req.url.path=='/wiki/rest/api/content/search'
        cql=req.url.params['cql']
        assert 'space in ("IT")' in cql and 'text ~ "a\\"b"' in cql
        return httpx.Response(200,json={'results':[
            {'id':'1','title':'Runbook','space':{'key':'IT'},'version':{'number':2},'_links':{'webui':'/wiki/spaces/IT/pages/1'}},
            {'id':'2','title':'Private','space':{'key':'PRIVATE'},'_links':{'webui':'/private'}},
            {'id':'3','title':'Unsafe','space':{'key':'IT'},'_links':{'webui':'https://unapproved.example/'}}
        ]})
    s=Settings('test-operator-secret-long-enough','sqlite:///:memory:',confluence_url='https://knowledge.internal/wiki',confluence_token='fixture',confluence_spaces=('IT',))
    result=Integrations(s,httpx.MockTransport(transport)).search('a"b')
    assert len(calls)==1 and len(result['items'])==1
    assert result['items'][0]['url']=='https://knowledge.internal/wiki/spaces/IT/pages/1'


def test_integration_output_limit():
    s=Settings('test-operator-secret-long-enough','sqlite:///:memory:',model_url='https://model.internal/v1',model_id='qwen')
    t=httpx.MockTransport(lambda req:httpx.Response(200,content=b'x'*600000))
    with pytest.raises(IntegrationError,match='response_too_large'): Integrations(s,t).ask('test')
