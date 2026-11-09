from recovery_fixture import historical_metadata
from recovery_fixture import command_contract
from backend.store import SCHEMA_VERSION
import json,time,base64,hashlib
from http.cookies import SimpleCookie
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
from dataclasses import replace
import httpx,jwt,pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request
from backend.config import Settings
from backend.identity import OperatorAuth,subject_id,LOCAL_ID
from backend.store import Problem

TOKEN='identity-test-public-local-token-long-enough'

class FakeIdP:
    issuer='https://idp.fixture.invalid/realm'
    client='fixture-client';audience='pslc-api'
    def __init__(self):
        self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()));self.jwk.update(kid='fixture-key',use='sig',alg='RS256')
        self.calls=[];self.nonce=None;self.challenge=None;self.wrong_nonce=False
    def token(self,sub='alice',groups=None,audience=None,**changes):
        claims={'iss':self.issuer,'aud':audience or self.audience,'sub':sub,'iat':int(time.time()),'exp':int(time.time())+600,'groups':['IT'] if groups is None else groups}
        claims.update(changes)
        return jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'fixture-key'})
    def handler(self,request):
        self.calls.append(request.url.path)
        if request.url.path.endswith('openid-configuration'):
            return httpx.Response(200,json={'issuer':self.issuer,'authorization_endpoint':self.issuer+'/authorize','token_endpoint':self.issuer+'/token','jwks_uri':self.issuer+'/jwks','code_challenge_methods_supported':['S256']})
        if request.url.path.endswith('/jwks'):return httpx.Response(200,json={'keys':[self.jwk]})
        if request.url.path.endswith('/token'):
            form=parse_qs(request.content.decode());assert form['client_id']==[self.client]
            actual=base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).decode().rstrip('=')
            assert actual==self.challenge
            return httpx.Response(200,json={'id_token':self.token(audience=self.client,nonce='wrong' if self.wrong_nonce else self.nonce),'access_token':'UNUSED-FIXTURE-TOKEN'})
        return httpx.Response(404)
    def settings(self,tmp_path):
        path=tmp_path/'policy.json';path.write_text(json.dumps({'schema_version':1,'groups':{'IT':{'roles':['operator'],'device_ids':['*'],'knowledge_spaces':['IT']}}}))
        return Settings(TOKEN,'sqlite:///'+(tmp_path/'identity.sqlite3').as_posix(),auth_mode='oidc',auth_policy_path=str(path),oidc_issuer=self.issuer,oidc_client_id=self.client,oidc_api_audience=self.audience)
    def auth(self,tmp_path):return OperatorAuth(self.settings(tmp_path),httpx.MockTransport(self.handler))


def request(method='GET',header=None,cookie=None,query='',origin=None):
    headers=[]
    if header:headers.append((b'authorization',header.encode()))
    if cookie:headers.append((b'cookie',cookie.encode()))
    if origin:headers.append((b'origin',origin.encode()))
    return Request({'type':'http','http_version':'1.1','method':method,'scheme':'http','server':('127.0.0.1',8787),'client':('127.0.0.1',1000),'path':'/auth/callback','query_string':query.encode(),'headers':headers})


def test_local_mode_explicit_and_no_oidc_fallback(tmp_path):
    local=OperatorAuth(Settings(TOKEN,'sqlite:///:memory:'))
    assert local.authenticate(request(header='Bearer '+TOKEN)).id==LOCAL_ID
    fake=FakeIdP();auth=fake.auth(tmp_path)
    with pytest.raises(Problem):auth.authenticate(request(header='Bearer '+TOKEN))
    with pytest.raises(Problem):auth.known(LOCAL_ID)


def test_signed_api_bearer_trusted_groups_and_live_policy(tmp_path):
    fake=FakeIdP();auth=fake.auth(tmp_path)
    principal=auth.authenticate(request(header='Bearer '+fake.token()))
    assert principal.id==subject_id(fake.issuer,'alice') and principal.operator() and not principal.audit()
    assert auth.known(principal.id).device('any')
    policy=json.loads(Path(auth.s.auth_policy_path).read_text());policy['subjects']={principal.id:{'disabled':True}}
    Path(auth.s.auth_policy_path).write_text(json.dumps(policy))
    with pytest.raises(Problem) as failure:auth.known(principal.id)
    assert failure.value.code=='operator_forbidden'
    Path(auth.s.auth_policy_path).write_text('{invalid')
    with pytest.raises(Problem) as failure:auth.known(principal.id)
    assert failure.value.code=='operator_policy_unavailable'

@pytest.mark.parametrize('case',['issuer','audience','expired','old','future','no_subject','fake_role','none','hs256','oversized','id_token_audience'])
def test_invalid_claims_and_algorithms_closed(tmp_path,case):
    fake=FakeIdP();auth=fake.auth(tmp_path)
    changes={}
    if case=='issuer':changes['iss']='https://other.invalid'
    if case=='audience':changes['aud']='other'
    if case=='expired':changes.update(iat=int(time.time())-200,exp=int(time.time())-100)
    if case=='old':changes.update(iat=int(time.time())-1000,exp=int(time.time())+200)
    if case=='future':changes.update(iat=int(time.time())+100)
    if case=='no_subject':changes['sub']=''
    if case=='fake_role':changes.update(groups=[],roles=['platform_admin'])
    if case=='id_token_audience':changes['aud']=fake.client
    token=fake.token(**changes)
    if case=='none':token=jwt.encode({'sub':'alice'},'',algorithm='none')
    if case=='hs256':token=jwt.encode({'sub':'alice'},'attacker-secret'*4,algorithm='HS256',headers={'kid':'fixture-key'})
    if case=='oversized':token='x'*17000
    with pytest.raises(Problem):auth.authenticate(request(header='Bearer '+token))


def test_oidc_pkce_browser_binding_session_csrf_and_logout(tmp_path):
    fake=FakeIdP();auth=fake.auth(tmp_path)
    response=auth.start();params=parse_qs(urlsplit(response.headers['location']).query)
    fake.nonce=params['nonce'][0];fake.challenge=params['code_challenge'][0];state=params['state'][0]
    assert params['code_challenge_method']==['S256'] and 'HttpOnly' in response.headers['set-cookie']
    with pytest.raises(Problem):auth.callback(request(cookie='pslc_login=other',query='state='+state+'&code=fixture-code'))
    callback=auth.callback(request(cookie='pslc_login='+state,query='state='+state+'&code=fixture-code'))
    session=SimpleCookie()
    for value in callback.headers.getlist('set-cookie'):session.load(value)
    sid=session['pslc_session'].value
    principal=auth.authenticate(request(cookie='pslc_session='+sid));assert principal.operator()
    with pytest.raises(Problem) as failure:auth.authenticate(request('POST',cookie='pslc_session='+sid,origin='https://evil.invalid'))
    assert failure.value.code=='csrf_rejected'
    assert auth.authenticate(request('POST',cookie='pslc_session='+sid,origin='http://127.0.0.1:8787')).id==principal.id
    with pytest.raises(Problem):auth.callback(request(cookie='pslc_login='+state,query='state='+state+'&code=fixture-code'))
    auth.logout(request('POST',cookie='pslc_session='+sid,origin='http://127.0.0.1:8787'))
    with pytest.raises(Problem):auth.authenticate(request(cookie='pslc_session='+sid))
    with pytest.raises(Problem):auth.known(principal.id)


def test_nonce_and_jwks_origin_checks(tmp_path):
    fake=FakeIdP();auth=fake.auth(tmp_path);start=auth.start();params=parse_qs(urlsplit(start.headers['location']).query)
    fake.nonce=params['nonce'][0];fake.challenge=params['code_challenge'][0];fake.wrong_nonce=True
    state=params['state'][0]
    with pytest.raises(Problem):auth.callback(request(cookie='pslc_login='+state,query='state='+state+'&code=test'))
    auth=OperatorAuth(auth.s,httpx.MockTransport(lambda req:httpx.Response(200,json={'issuer':fake.issuer,'authorization_endpoint':fake.issuer+'/a','token_endpoint':fake.issuer+'/t','jwks_uri':'https://untrusted.invalid/keys','code_challenge_methods_supported':['S256']})))
    with pytest.raises(Problem) as failure:auth.metadata()
    assert failure.value.code=='invalid_identity_metadata'


def test_policy_does_not_trust_unsigned_role_and_unknown_grants(tmp_path):
    fake=FakeIdP();auth=fake.auth(tmp_path)
    with pytest.raises(Problem):auth.authenticate(request(header='Bearer '+fake.token(groups=['unmapped'],roles=['platform_admin'])))
    policy={'schema_version':1,'groups':{'IT':{'roles':['superuser'],'device_ids':['*']}}}
    Path(auth.s.auth_policy_path).write_text(json.dumps(policy))
    with pytest.raises(Problem):auth.authenticate(request(header='Bearer '+fake.token()))
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.schemas import Enroll
from backend.store import tasks,workflows,events,devices,version,Store,metadata
from sqlalchemy import select,update,Column,Table,MetaData,create_engine


def api_fixture(tmp_path):
    fake=FakeIdP();settings=fake.settings(tmp_path)
    policy=json.loads(Path(settings.auth_policy_path).read_text());policy['groups'].update(IB={'roles':['auditor'],'device_ids':['*']},Admins={'roles':['platform_admin'],'device_ids':['*']})
    Path(settings.auth_policy_path).write_text(json.dumps(policy))
    app=create_app(settings,httpx.MockTransport(fake.handler))
    ticket=app.state.store.ticket()['enrollment_token']
    node=app.state.store.enroll(Enroll(agent_protocol=1,enrollment_token=ticket,name='scope-node',os_family='Windows',commands_enabled=True,diagnostics_enabled=True))
    return fake,settings,app,node


def auth_header(fake,sub='alice',groups=None):return {'Authorization':'Bearer '+fake.token(sub,groups)}


def test_api_ownership_metadata_scope_and_confirmations(tmp_path):
    fake,settings,app,node=api_fixture(tmp_path);marker='PRIVATE-COMMAND-FIXTURE-71238'
    alice=auth_header(fake);bob=auth_header(fake,'bob');auditor=auth_header(fake,'auditor',['IB'])
    with TestClient(app) as c:
        assert c.get('/api/me',headers={'Authorization':'Bearer '+TOKEN}).status_code==401
        assert c.get('/api/me',headers=alice).json()['roles']==['operator']
        t=c.post('/api/tasks',headers=alice,json={**command_contract(),'device_id':node['device_id'],'session_id':'s','kind':'command','code':"print('"+marker+"')"}).json()
        assert t['owner_id']==subject_id(fake.issuer,'alice')
        assert c.get('/api/tasks',headers=bob).json()==[]
        assert c.get('/api/tasks/'+t['id'],headers=bob).status_code==404
        assert c.get('/api/tasks/'+t['id']+'/output',headers=bob).status_code==404
        detail=c.get('/api/tasks/'+t['id'],headers=auditor);assert detail.status_code==200 and marker not in detail.text and detail.json()['plan'] is None
        assert c.get('/api/tasks/'+t['id']+'/output',headers=auditor).status_code==404
        assert c.post('/api/tasks/'+t['id']+'/decision',headers=bob,json={'approve':True,'plan_hash':t['plan_hash']}).status_code==404
        assert c.post('/api/tasks',headers=auditor,json={'device_id':node['device_id'],'session_id':'s'}).status_code==403
        assert c.post('/api/enrollment-tokens',headers=alice).status_code==403
        assert c.post('/api/enrollment-tokens',headers=auth_header(fake,'admin',['Admins'])).status_code==200
        wbody={'request_id':'owner-request-001','session_id':'workflow-s','question':'Проверь состояние scope-node'}
        w=c.post('/api/agent/requests',headers=alice,json=wbody).json()
        assert c.post('/api/agent/requests',headers=bob,json=wbody).status_code==404
        assert c.get('/api/workflows/'+w['id'],headers=bob).status_code==404
        assert c.get('/api/workflows/'+w['id'],headers=auditor).json()['preview'] is None
        assert c.post('/api/workflows/'+w['id']+'/decision',headers=bob,json={'approve':True,'plan_hash':w['plan_hash']}).status_code==404
        assert c.get('/api/knowledge?q=test',headers=alice).json()['status']=='not_configured'
        policy=json.loads(Path(settings.auth_policy_path).read_text());policy['subjects']={subject_id(fake.issuer,'alice'):{'roles':['operator'],'device_ids':[],'disabled':True}}
        # Existing exact confirmation was issued before revocation.
        Path(settings.auth_policy_path).write_text(json.dumps(policy))
        assert c.post('/agent/'+node['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1','Authorization':'Bearer '+node['credential']}).json()['job'] is None
        assert app.state.store.get_task(t['id'])['state']=='access_revoked'
        assert c.get('/api/tasks',headers=alice).status_code==403
        assert marker.encode() not in (tmp_path/'identity.sqlite3').read_bytes()
        for token in [alice['Authorization'][7:],bob['Authorization'][7:]]:assert token.encode() not in (tmp_path/'identity.sqlite3').read_bytes()


def test_scoped_inventory_and_events_do_not_expose_other_device(tmp_path):
    fake,settings,app,node=api_fixture(tmp_path)
    second=app.state.store.enroll(Enroll(agent_protocol=1,enrollment_token=app.state.store.ticket()['enrollment_token'],name='private-node',os_family='Windows'))
    policy=json.loads(Path(settings.auth_policy_path).read_text());policy['subjects']={subject_id(fake.issuer,'limited'):{'roles':['operator'],'device_ids':[node['device_id']]}}
    # Subject grant and group grants are additive, so this actor has no IT group.
    Path(settings.auth_policy_path).write_text(json.dumps(policy))
    limited=auth_header(fake,'limited',[])
    with TestClient(app) as c:
        rows=c.get('/api/devices',headers=limited).json();assert [x['id'] for x in rows]==[node['device_id']]
        assert c.post('/api/tasks',headers=limited,json={'device_id':second['device_id'],'session_id':'s'}).status_code==404
        response=c.post('/api/agent/requests',headers=limited,json={'request_id':'limited-request-001','session_id':'s','question':'Проверь состояние private-node'})
        assert response.json()['state']=='clarification' and second['device_id'] not in response.text
        assert second['device_id'] not in c.get('/api/events',headers=limited).text
        own=c.post('/api/tasks',headers=limited,json={'device_id':node['device_id'],'session_id':'s'}).json()
        assert c.get('/api/tasks',headers=limited).json()[0]['id']==own['id']


def test_confirmed_owner_permission_loss_and_session_limit_is_per_owner(tmp_path):
    fake,settings,app,node=api_fixture(tmp_path)
    with TestClient(app) as c:
        nodes=[]
        for index in range(11):
            n=app.state.store.enroll(Enroll(agent_protocol=1,enrollment_token=app.state.store.ticket()['enrollment_token'],name='node-'+str(index),os_family='Windows'))
            owner='alice' if index<5 or index==10 else 'bob'
            result=c.post('/api/tasks',headers=auth_header(fake,owner),json={'device_id':n['device_id'],'session_id':'same-session'})
            assert result.status_code==201;nodes.append(n)
        jobs=[c.post('/agent/'+n['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1','Authorization':'Bearer '+n['credential']}).json()['job'] for n in nodes]
        assert sum(job is not None for job in jobs)==10 and jobs[-1] is None
        assert c.get('/api/overview',headers=auth_header(fake)).json()['statistics']['active_fleet']==5
        assert c.get('/api/overview',headers=auth_header(fake,'bob')).json()['statistics']['active_fleet']==5
        assert app.state.store.statistics()['active_fleet']==10
        body={'request_id':'revoked-confirmation-001','session_id':'new-session','question':'Проверь состояние scope-node'}
        w=c.post('/api/agent/requests',headers=auth_header(fake),json=body).json()
        assert c.post('/api/workflows/'+w['id']+'/decision',headers=auth_header(fake),json={'approve':True,'plan_hash':w['plan_hash']}).status_code==200
        policy=json.loads(Path(settings.auth_policy_path).read_text());policy['subjects']={subject_id(fake.issuer,'alice'):{'disabled':True}};Path(settings.auth_policy_path).write_text(json.dumps(policy))
        assert c.post('/agent/'+node['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1','Authorization':'Bearer '+node['credential']}).json()['job'] is None
        assert app.state.store.workflow.get(w['id'])['state']=='stopped'


def test_schema_v3_upgrade_preserves_local_owner_and_credentials(tmp_path):
    settings=Settings(TOKEN,'sqlite:///'+(tmp_path/'old.sqlite3').as_posix());engine=create_engine(settings.database_url);old=historical_metadata(3)
    old.create_all(engine)
    with engine.begin() as c:
        c.execute(old.tables['schema_version'].insert().values(id=3));c.execute(old.tables['dispatch_guard'].insert().values(id=1))
        c.execute(old.tables['devices'].insert().values(id='old',name='old',os_family='Windows',credential_hash='a'*64,revoked=False,commands_enabled=False,installations_enabled=False,diagnostics_enabled=False,last_seen=1))
        c.execute(old.tables['tasks'].insert().values(id='old-task',device_id='old',session_id='s',kind='health',runtime='python',state='verified',plan_hash='0'*64,created=1,expires=1))
    engine.dispose();store=Store(settings)
    assert store.get_task('old-task')['owner_id']==LOCAL_ID
    with store.tx() as c:assert c.scalar(select(version.c.id))==SCHEMA_VERSION and c.scalar(select(devices.c.credential_hash))=='a'*64
    store.engine.dispose()
