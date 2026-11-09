from recovery_fixture import command_contract,simulated_recovery
from recovery_fixture import command_contract
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from backend.app import create_app
from backend.config import Settings
from backend.schemas import Result
from backend.store import devices, enrollment, tasks, digest

TOKEN="test-operator-token-with-sufficient-length"

@pytest.fixture
def settings(tmp_path):
    return Settings(TOKEN,"sqlite:///"+(tmp_path/"metadata.sqlite3").as_posix())

@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        c.headers["Authorization"]="Bearer "+TOKEN
        yield c


def node(c,commands=True):
    ticket=c.post("/api/enrollment-tokens").json()["enrollment_token"]
    r=c.post("/agent/enroll",json={'agent_protocol':1,"enrollment_token":ticket,"name":"test-node","os_family":"Windows","commands_enabled":commands})
    assert r.status_code==200
    return r.json()


def poll(c,n):
    return c.post('/agent/'+n['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1',"Authorization":"Bearer "+n['credential']})


def task(c,n,session="session",command=False):
    r=c.post("/api/tasks",json={**(command_contract() if command else {}),"device_id":n["device_id"],"session_id":session,"kind":"command" if command else "health","runtime":"python","code":"print(42)" if command else ""})
    assert r.status_code==201
    return r.json()


def complete(c,n,t,j,**data):
    return c.post('/agent/'+n['device_id']+'/tasks/'+t['id']+'/result',headers={"Authorization":"Bearer "+n['credential']},json={"lease_id":j['lease_id'],"outcome":"ok",**({"recovery":simulated_recovery(j),"command_verification":{"verification_hash":__import__("backend.recovery_types",fromlist=["fingerprint"]).fingerprint(j["payload"]["verification"]),"matched":True,"exit_code":0}} if j["payload"]["kind"]=="command" else {}),**data})


def test_authentication_and_no_body_echo(client):
    assert client.get("/api/devices",headers={"Authorization":""}).status_code==401
    r=client.post("/api/tasks",json={"raw":"DO-NOT-ECHO-INPUT"})
    assert r.status_code==422 and "DO-NOT" not in r.text
    assert client.get("/healthz").json()["mode"]=="local-development"
    assert client.get("/api/devices").headers["cache-control"]=="no-store"


def test_production_fails_closed(settings):
    with pytest.raises(ValueError,match="production_mode_not_admitted"):
        create_app(replace(settings,local_mode=False))


def test_short_operator_secret_rejected(settings):
    with pytest.raises(ValueError): create_app(replace(settings,operator_token="short"))


def test_enrollment_replay_expiry_and_hashes(client):
    ticket=client.post("/api/enrollment-tokens").json()["enrollment_token"]
    payload={"enrollment_token":ticket,"name":"same-name","os_family":"Linux"}
    first=client.post("/agent/enroll",json=payload).json()
    assert client.post("/agent/enroll",json=payload).status_code==401
    store=client.app.state.store
    with store.tx() as c:
        row=c.execute(select(devices)).mappings().first()
        assert row["credential_hash"]==digest(first["credential"])
        assert first["credential"] not in json.dumps(dict(row))
        c.execute(update(enrollment).values(used=False,expires=0))
    assert client.post("/agent/enroll",json=payload).status_code==401


def test_cap_500(client):
    store=client.app.state.store
    with store.tx() as c:
        c.execute(insert(devices),[{"id":str(i),"name":"n","os_family":"Linux","credential_hash":"h","revoked":False,"commands_enabled":False,"last_seen":0} for i in range(500)])
    ticket=client.post("/api/enrollment-tokens").json()["enrollment_token"]
    assert client.post("/agent/enroll",json={'agent_protocol':1,"enrollment_token":ticket,"name":"n501","os_family":"Linux"}).json()["error"]=="device_limit"


def test_duplicate_names_revocation_and_freshness(client):
    a,b=node(client),node(client)
    assert a['device_id']!=b['device_id']
    with client.app.state.store.tx() as c:
        c.execute(update(devices).where(devices.c.id==a['device_id']).values(last_seen=0))
    found={x['id']:x for x in client.get('/api/devices').json()}
    assert not found[a['device_id']]['online'] and found[b['device_id']]['online']
    assert 'credential_hash' not in found[a['device_id']]
    client.post('/api/devices/'+a['device_id']+'/revoke')
    assert poll(client,a).status_code==401


def test_exact_confirmation_and_replay(client):
    n=node(client);t=task(client,n,command=True)
    assert poll(client,n).json()['job'] is None
    assert client.post('/api/tasks/'+t['id']+'/decision',json={"approve":True,"plan_hash":"0"*64}).status_code==409
    decision={"approve":True,"plan_hash":t['plan_hash']}
    assert client.post('/api/tasks/'+t['id']+'/decision',json=decision).status_code==200
    assert client.post('/api/tasks/'+t['id']+'/decision',json=decision).status_code==409
    j=poll(client,n).json()['job'];assert j['payload']['code']=='print(42)'
    assert poll(client,n).json()['job'] is None
    assert client.get('/api/overview').json()['statistics']['counts']['confirmed']==1


def test_rejection_never_dispatches(client):
    n=node(client);t=task(client,n,command=True)
    client.post('/api/tasks/'+t['id']+'/decision',json={"approve":False,"plan_hash":t['plan_hash']})
    assert poll(client,n).json()['job'] is None
    assert client.get('/api/overview').json()['statistics']['counts']['rejected']==1


def test_plan_ttl(client):
    n=node(client);t=task(client,n,command=True)
    client.app.state.store.plans.data[t['id']]=(0,{})
    assert client.get('/api/tasks/'+t['id']).json()['state']=='plan_expired'
    assert poll(client,n).json()['job'] is None


def test_session_five(client):
    nodes=[node(client) for _ in range(6)]
    for n in nodes: task(client,n)
    jobs=[poll(client,n).json()['job'] for n in nodes]
    assert sum(j is not None for j in jobs)==5


def test_atomic_fleet_twenty(client):
    ns=[node(client) for _ in range(25)]
    for i,n in enumerate(ns): task(client,n,session='s'+str(i))
    store=client.app.state.store
    with ThreadPoolExecutor(max_workers=16) as pool:
        jobs=list(pool.map(lambda n:store.poll(n['device_id'],n['credential'],agent_protocol=1),ns))
    assert sum(j is not None for j in jobs)==20
    assert store.statistics()['active_fleet']==20


def test_result_bound_identity_and_idempotency(client):
    a,b=node(client),node(client);t=task(client,a);j=poll(client,a).json()['job']
    assert complete(client,b,t,j).status_code==403
    assert complete(client,a,t,{**j,"lease_id":"forged-lease-id-long"}).status_code==403
    assert complete(client,a,t,j).json()['state']=='verified'
    assert complete(client,a,t,j).status_code==200
    assert client.get('/api/overview').json()['statistics']['counts']['completed']==1


def test_raw_output_not_in_database(client,settings):
    marker='RAW-LOG-UNIQUE-RETENTION-839102'
    n=node(client);t=task(client,n);j=poll(client,n).json()['job']
    assert complete(client,n,t,j,output=marker).status_code==200
    assert client.get('/api/tasks/'+t['id']+'/output').json()['output']==marker
    persisted=client.get('/api/tasks').text+client.get('/api/events').text+client.get('/api/overview').text
    assert marker not in persisted
    assert marker.encode() not in __import__('pathlib').Path(settings.database_url.removeprefix('sqlite:///')).read_bytes()
    client.app.state.store.outputs.data[t['id']]=(0,marker)
    assert client.get('/api/tasks/'+t['id']+'/output').json()['output'] is None


def test_payload_not_reflected_from_invalid_agent_result(client):
    n=node(client);t=task(client,n);j=poll(client,n).json()['job']
    r=complete(client,n,t,j,summary='UNTRUSTED-RAW-SUMMARY')
    assert r.status_code==422 and 'UNTRUSTED-RAW-SUMMARY' not in r.text
    assert 'UNTRUSTED-RAW-SUMMARY' not in client.get('/api/tasks').text


def test_restart_unknown_and_lost_plan(client,settings):
    n=node(client);t=task(client,n);j=poll(client,n).json()['job']
    waiting=task(client,n,session='other',command=True)
    with TestClient(create_app(settings)) as restarted:
        restarted.headers['Authorization']='Bearer '+TOKEN
        assert restarted.get('/api/tasks/'+t['id']).json()['state']=='outcome_unknown'
        assert restarted.get('/api/tasks/'+waiting['id']).json()['state']=='plan_expired'
        assert poll(restarted,n).json()['job'] is None
        assert complete(restarted,n,t,j).json()['state']=='verified'


def test_expired_lease_no_repeat_and_manual_reconcile(client):
    n=node(client);t=task(client,n);poll(client,n)
    with client.app.state.store.tx() as c: c.execute(update(tasks).where(tasks.c.id==t['id']).values(lease_until=0))
    assert poll(client,n).json()['job'] is None
    assert client.get('/api/tasks/'+t['id']).json()['state']=='outcome_unknown'
    r=client.post('/api/tasks/'+t['id']+'/reconcile',json={"outcome":"failed","externally_checked_not_running":True})
    assert r.status_code==200
    assert client.get('/api/overview').json()['statistics']['active_fleet']==0


def test_cancel_running_does_not_claim_undo(client):
    n=node(client);t=task(client,n);poll(client,n)
    client.post('/api/tasks/'+t['id']+'/cancel')
    assert client.get('/api/tasks/'+t['id']).json()['state']=='cancel_requested'
    assert poll(client,n).json()['job'] is None


def test_command_disabled_at_enrollment(client):
    n=node(client,commands=False)
    r=client.post('/api/tasks',json={**command_contract(),"device_id":n['device_id'],"session_id":"s","kind":"command","code":"print(1)"})
    assert r.status_code==403


def test_question_and_unconfigured_integrations(client,settings):
    marker='QUESTION-WITH-RAW-CONTEXT-998113'
    r=client.post('/api/chat',json={"question":marker})
    assert r.json()['status']=='not_configured'
    assert client.get('/api/knowledge?q=test').json()['status']=='not_configured'
    assert client.get('/api/overview').json()['statistics']['counts']['question_requested']==1
    assert marker not in client.get('/api/events').text
