import asyncio,json
from pathlib import Path
from dataclasses import replace
import pytest,httpx
from fastapi.testclient import TestClient
from backend.config import Settings,network_flag
from backend.app import create_app
from backend.transport import TransportBoundary,LIMIT
from test_operator_identity import FakeIdP,TOKEN,auth_header

def network_settings(tmp_path):
 fake=FakeIdP();cert=tmp_path/'cert.pem';key=tmp_path/'key.pem';cert.write_text('ASGI fixture only, not real PKI');key.write_text('ASGI fixture only')
 plan=tmp_path/'pilot.json';plan.write_text(json.dumps({'schema_version':1,'targets':[{'id':'n'+str(i),'os_family':'Windows' if i<3 else 'Linux','environment':'test'} for i in range(5)]}))
 settings=replace(fake.settings(tmp_path),pilot_plan_path=str(plan),api_host='0.0.0.0',pilot_network_admitted=True,pilot_allowed_cidrs=('10.42.0.0/24',),pilot_it_reference='IT-TEST',pilot_security_reference='IB-TEST',tls_cert_file=str(cert),tls_key_file=str(key),oidc_redirect_uri='https://pilot.fixture.invalid/auth/callback')
 return fake,settings

@pytest.mark.parametrize('change',[{'pilot_network_admitted':False},{'auth_mode':'local'},{'pilot_allowed_cidrs':()},{'pilot_allowed_cidrs':('0.0.0.0/0',)},{'pilot_allowed_cidrs':('10.0.0.0/8',)},{'tls_key_file':''},{'pilot_it_reference':''},{'oidc_redirect_uri':'http://127.0.0.1/auth/callback'}])
def test_incomplete_or_broad_network_config_rejected(tmp_path,change):
 fake,s=network_settings(tmp_path)
 with pytest.raises(ValueError):replace(s,**change).validate()

def test_https_allowlist_oidc_peer_and_forwarded_headers(tmp_path):
 fake,s=network_settings(tmp_path);app=create_app(s,httpx.MockTransport(fake.handler));headers=auth_header(fake)
 with TestClient(app,base_url='https://pilot.fixture.invalid',client=('10.42.0.12',45000)) as c:
  assert c.get('/api/me',headers=headers).status_code==200
  assert c.get('/healthz').json()['mode']=='pilot-network'
  assert c.get('/api/me',headers={'Authorization':'Bearer '+TOKEN}).status_code==401
  # Admission did not turn on any node capabilities or production.
  assert c.get('/api/devices',headers=headers).json()==[]
 calls=len(fake.calls)
 with TestClient(app,base_url='http://pilot.fixture.invalid',client=('10.42.0.12',45000)) as c:
  assert c.get('/api/me',headers={**headers,'X-Forwarded-Proto':'https'}).status_code==403
 with TestClient(app,base_url='https://pilot.fixture.invalid',client=('203.0.113.22',45000)) as c:
  assert c.get('/api/me',headers={**headers,'X-Forwarded-For':'10.42.0.12'}).status_code==403
 assert len(fake.calls)==calls

def test_oversized_and_streamed_body_stops_before_application(tmp_path):
 async def exercise():
  calls=[]
  async def app(scope,receive,send):calls.append(True)
  boundary=TransportBoundary(app,Settings(TOKEN,'sqlite:///:memory:'));scope={'type':'http','client':('127.0.0.1',1),'scheme':'http','headers':[]}
  messages=[{'type':'http.request','body':b'RAW-NETWORK-FIXTURE'*10000,'more_body':True},{'type':'http.request','body':b'x'*150000,'more_body':False}];sent=[]
  async def receive():return messages.pop(0)
  async def send(message):sent.append(message)
  await boundary(scope,receive,send)
  assert not calls and sent[0]['status']==413 and b'RAW-NETWORK' not in sent[1]['body']
  scope['headers']=[(b'content-length',b'1')];messages[:]=[{'type':'http.request','body':b'x'*(LIMIT+1),'more_body':False}];sent.clear();await boundary(scope,receive,send);assert sent[0]['status']==413
  boundary.active=64;sent.clear();await boundary(scope,receive,send);assert sent[0]['status']==503
 asyncio.run(exercise())

def test_network_flag_is_explicit_and_default_local_remains_closed(tmp_path,monkeypatch):
 monkeypatch.setenv('PSLC_PILOT_NETWORK_ADMITTED','true')
 with pytest.raises(ValueError):network_flag()
 s=Settings(TOKEN,'sqlite:///:memory:')
 with pytest.raises(ValueError):replace(s,api_host='0.0.0.0').validate()
 with TestClient(create_app(s),client=('10.42.0.12',1)) as c:assert c.get('/healthz').status_code==403
