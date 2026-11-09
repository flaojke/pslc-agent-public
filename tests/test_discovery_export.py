import asyncio,hashlib,io,json,time
from pathlib import Path
from threading import Event
import httpx,pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agent.delivery import sign
from backend.app import create_app
from backend.config import Settings
ORIGIN='http://127.0.0.1:8787'
def fixture(tmp_path):
 s=Settings('network-export-public-fixture-token','sqlite:///'+(tmp_path/'case.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False)
 app=create_app(s);app.state.discovery.networks=lambda:[{'interface':'fixture','address':'10.8.0.10','cidr':'10.8.0.0/24','addresses':256}]
 return app
def login(c,name='admin',password='admin'):assert c.post('/auth/login',json={'username':name,'password':password}).status_code==200
def wait(c,sid):
 for _ in range(200):
  r=c.get('/api/discovery/'+sid);assert r.status_code==200
  row=r.json()
  if row['state'] not in ('queued','running'):return row
  time.sleep(.01)
 pytest.fail('discovery did not terminate')
def native(app,tmp_path):
 private=Ed25519PrivateKey.generate();key=tmp_path/'test-private.pem';key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 public=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
 roots={}
 for platform,exe in [('windows-x64','pslc-agent.exe'),('linux-x64','pslc-agent')]:
  root=tmp_path/platform;root.mkdir();(root/exe).write_bytes(b'fixture-native-not-executable');(root/'license.txt').write_text('Fixture license')
  sign(root,'0.7.0',platform,key);roots[platform]=root
 app.state.agent_export.roots=roots;app.state.agent_export.public_pem=public;app.state.agent_export.scope='fixture-only'
 return roots
def profile(c,os='Windows'):
 r=c.post('/api/nodes/onboarding',json={'name':'candidate-10-8-0-1','os_family':os,'state_path':'C:/ProgramData/PSLC-Agent/identity.json' if os=='Windows' else '/var/lib/pslc-agent/identity.json','device_kind':'unknown'})
 assert r.status_code==200,r.text
 return r.json()
def test_scan_is_bounded_explicit_and_does_not_enroll(tmp_path):
 app=fixture(tmp_path);calls=[];active=0;peak=0
 async def probe(address,port):
  nonlocal active,peak
  active+=1;peak=max(peak,active);calls.append((address,port));await asyncio.sleep(.005);active-=1
  return 'open' if address.endswith('.1') and port==22 else 'refused' if address.endswith('.2') and port==445 else 'silent'
 app.state.discovery.probe=probe
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);assert c.get('/api/discovery').json()['complete_inventory_guaranteed'] is False
  for cidr in ('8.8.8.0/24','127.0.0.0/24','169.254.1.0/24','10.9.0.0/24','10.0.0.0/8','fd00::/120'):
   assert c.post('/api/discovery',json={'cidr':cidr}).status_code==422
  assert calls==[]
  row=c.post('/api/discovery',json={'cidr':'10.8.0.0/30'}).json();row=wait(c,row['id'])
  assert row['state']=='completed' and row['scanned']==row['total']==2 and peak<=64
  assert len(row['candidates'])==2 and all(not x['managed'] and x['os_family'] is None and x['device_kind']=='unknown' for x in row['candidates'])
  assert row['candidates'][0]['method'] in ('tcp_open','tcp_refused')
  assert c.get('/api/devices').json()==[]
  assert b'10.8.0.1' not in (tmp_path/'case.sqlite3').read_bytes()
def test_scan_busy_cancel_expiry_and_owner_isolation(tmp_path):
 app=fixture(tmp_path);entered=Event()
 async def probe(address,port):entered.set();await asyncio.sleep(.04);return 'silent'
 app.state.discovery.probe=probe
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);row=c.post('/api/discovery',json={'cidr':'10.8.0.0/24'}).json();assert entered.wait(1)
  assert c.post('/api/discovery',json={'cidr':'10.8.0.0/30'}).status_code==409
  assert c.post('/api/discovery/'+row['id']+'/cancel').status_code==200
  end=wait(c,row['id']);assert end['state']=='cancelled' and end['scanned']<end['total']
  c.post('/api/users',json={'username':'other-admin','password':'fixture-password','roles':['platform_admin','operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False})
  c.post('/auth/logout');login(c,'other-admin','fixture-password')
  assert c.get('/api/discovery/'+row['id']).status_code==404
  app.state.discovery.rows.drop(row['id']);assert c.get('/api/discovery/'+row['id']).status_code==404
def test_scan_deadline_and_no_operator_access(tmp_path):
 app=fixture(tmp_path)
 async def probe(address,port):await asyncio.sleep(.1);return 'silent'
 app.state.discovery.probe=probe;app.state.discovery.budget=.02
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);row=c.post('/api/discovery',json={'cidr':'10.8.0.0/30'}).json();assert wait(c,row['id'])['state']=='partial_timeout'
  c.post('/api/users',json={'username':'operator','password':'fixture-password','roles':['operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False})
  c.post('/auth/logout');login(c,'operator','fixture-password')
  assert c.get('/api/discovery').status_code==403
  assert c.post('/api/discovery',json={'cidr':'10.8.0.0/30'}).status_code==403
@pytest.mark.parametrize('os,platform,exe',[('Windows','windows-x64','pslc-agent.exe'),('Linux','linux-x64','pslc-agent')])
def test_complete_zip_checksum_cleanup_and_no_token(tmp_path,os,platform,exe):
 import zipfile
 app=fixture(tmp_path);native(app,tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);p=profile(c,os);r=c.get('/api/nodes/bundles/'+p['bundle_id']);assert r.status_code==200,r.text
  assert hashlib.sha256(r.content).hexdigest()==r.headers['x-checksum-sha256']
  with zipfile.ZipFile(io.BytesIO(r.content)) as z:
   assert 'native/'+exe in z.namelist() and 'config.json' in z.namelist() and 'README.md' in z.namelist()
   raw=b''.join(z.read(n) for n in z.namelist());assert p['enrollment_token'].encode() not in raw
   for line in z.read('SHA256SUMS').decode().splitlines():
    sha,name=line.split('  ',1);assert hashlib.sha256(z.read(name)).hexdigest()==sha
   assert json.loads(z.read('config.json'))==p['config']
  assert app.state.agent_export.transfers=={} and c.get('/api/devices').json()==[]
  app.state.agent_export.handles.drop(p['bundle_id']);assert c.get('/api/nodes/bundles/'+p['bundle_id']).status_code==404
def test_export_tamper_and_foreign_profile_fail_closed(tmp_path):
 app=fixture(tmp_path);roots=native(app,tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);p=profile(c)
  (roots['windows-x64']/'pslc-agent.exe').write_bytes(b'TAMPER')
  assert c.get('/api/nodes/bundles/'+p['bundle_id']).status_code==503
  assert app.state.agent_export.transfers=={}
  c.post('/api/users',json={'username':'other-admin','password':'fixture-password','roles':['platform_admin','operator'],'device_ids':['*'],'knowledge_spaces':[],'disabled':False})
  c.post('/auth/logout');login(c,'other-admin','fixture-password')
  assert c.get('/api/nodes/bundles/'+p['bundle_id']).status_code==404


def test_export_rejects_profile_after_explicit_enrollment(tmp_path):
 app=fixture(tmp_path);native(app,tmp_path)
 with TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
  login(c);p=profile(c)
  r=c.post('/agent/enroll',json={'agent_protocol':1,'enrollment_token':p['enrollment_token'],'name':'selected-fixture','os_family':'Windows','commands_enabled':False,'installations_enabled':False,'diagnostics_enabled':False})
  assert r.status_code==200
  assert c.get('/api/nodes/bundles/'+p['bundle_id']).status_code==409
