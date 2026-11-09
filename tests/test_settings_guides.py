import json,os,subprocess,sys,time
from dataclasses import replace
from pathlib import Path
import httpx,pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings,ROOT
from backend.store import accounts,Problem
from backend.identity import Principal
from backend.rbac import ALL,capabilities,catalogue
from backend.system_preferences import SystemPreferences,PreferenceUpdate
from backend.product_guides import source,render,markdown,FILES,variables
from sqlalchemy import select

ORIGIN='http://127.0.0.1:8787'
PASSWORD='public-settings-fixture-password'
def fixture(tmp):
 s=Settings('public-settings-token-not-a-secret','sqlite:///'+(tmp/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,system_preferences_path=str(tmp/'system/preferences.json'),model_config_path=str(tmp/'model/connection.enc'))
 calls=[]
 def deny(request):calls.append(request);raise AssertionError('unexpected_external_probe')
 a=create_app(s,httpx.MockTransport(deny))
 return s,a,TestClient(a,base_url=ORIGIN,headers={'Origin':ORIGIN}),calls
def login(c,name='admin',password='admin'):assert c.post('/auth/login',json={'username':name,'password':password}).status_code==200
def add(c,name,roles,scope):
 r=c.post('/api/users',json={'username':name,'password':PASSWORD,'roles':roles,'device_ids':scope,'knowledge_spaces':[],'disabled':False})
 assert r.status_code==201,r.text
 return r.json()
def body(p,**changes):return {k:v for k,v in {**p,**changes,'expected_revision':p['revision']}.items() if k!='revision'}

@pytest.mark.parametrize('role',ALL)
def test_each_role_scope_catalog_and_forged_global_api(tmp_path,role):
 s,a,admin,calls=fixture(tmp_path)
 with admin:
  login(admin);add(admin,'fixture', [role],['some-node'])
  with TestClient(a,base_url=ORIGIN,headers={'Origin':ORIGIN}) as c:
   login(c,'fixture',PASSWORD)
   me=c.get('/api/me').json();eff=me['capabilities']
   assert eff['operate']==(role in ('operator','platform_admin'))
   assert eff['audit_metadata']==(role in ('auditor','platform_admin'))
   assert eff['review_knowledge']==(role in ('reviewer','platform_admin'))
   assert not eff['manage_models'] and not eff['manage_users'] and not eff['configure_system']
   catalog=c.get('/api/access/catalog').json();assert len(catalog['roles'])==4 and catalog['effective']==eff
   p=c.get('/api/settings/system').json();assert c.put('/api/settings/system',json=body(p,instance_label='forged')).status_code==403
   for route in ('/api/users','/api/settings/providers','/api/discovery'):
    assert c.get(route).status_code==403
   assert c.post('/api/users',json={'username':'forged','password':PASSWORD,'roles':['platform_admin'],'device_ids':['*']}).status_code==403
   about=c.get('/api/system/about').json();assert 'administration' not in about
   for gid in FILES:assert c.get('/api/help/'+gid).status_code==200
   assert 'database_url' not in json.dumps(about) and PASSWORD not in json.dumps(about)
 assert not calls and not Path(s.system_preferences_path).exists()

def test_role_combinations_and_global_scope_are_independent():
 for scope in (frozenset(),frozenset({'node'}),frozenset({'*'})):
  p=Principal('fixture',frozenset({'operator','auditor','reviewer'}),scope,frozenset(),time.time()+100)
  c=capabilities(p);assert c['operate'] and c['audit_metadata'] and c['review_knowledge'] and not c['manage_users']
  admin=replace(p,roles=frozenset({'platform_admin'}))
  assert capabilities(admin)['manage_users']==('*' in scope)
  assert not capabilities(admin,'oidc')['manage_users']
 assert {x['id'] for x in catalogue()['roles']}==set(ALL)

def test_last_global_admin_not_replaced_by_scoped_admin_or_lost_scope(tmp_path):
 s,a,c,_=fixture(tmp_path)
 with c:
  login(c);add(c,'partial',['platform_admin'],['node'])
  admin=c.get('/api/users').json()[0]
  base={'roles':admin['roles'],'device_ids':['node'],'knowledge_spaces':[],'disabled':False,'expected_revision':admin['auth_version']}
  for mutation in ({},{'disabled':True},{'roles':['operator']}):
   r=c.put('/api/users/'+admin['id'],json={**base,**mutation});assert r.status_code==409 and r.json()['error']=='last_admin_required'
  add(c,'global',['platform_admin'],['*'])
  assert c.put('/api/users/'+admin['id'],json=base).status_code==200
  assert c.get('/api/me').status_code==401
  login(c);assert c.get('/api/users').status_code==403

def test_preferences_revision_integrity_restart_and_metadata_separation(tmp_path):
 s,a,c,calls=fixture(tmp_path)
 with c:
  login(c)
  with a.state.store.tx() as con:before=list(con.execute(select(accounts)).mappings())
  p=c.get('/api/settings/system').json();assert p['display_timezone']=='Europe/Moscow'
  r=c.put('/api/settings/system',json=body(p,instance_label='Пилот ИТ',support_contact='support@example.invalid',display_timezone='UTC'))
  assert r.status_code==200,r.text
  changed=r.json();assert changed['revision']!=p['revision']
  assert c.put('/api/settings/system',json=body(p,instance_label='stale')).status_code==409
  assert c.put('/api/settings/system',json=body(changed,display_timezone='Not/AZone')).status_code==422
  assert c.put('/api/settings/system',json={**body(changed),'fleet_limit':500}).status_code==422
  assert c.put('/api/settings/system',json=body(changed,instance_label='bad\nlabel')).status_code==422
  with a.state.store.tx() as con:after=list(con.execute(select(accounts)).mappings())
  assert before==after
  raw=Path(s.system_preferences_path).read_bytes();assert PASSWORD.encode() not in raw and b'expected_revision' not in raw
  assert SystemPreferences(s.system_preferences_path).view()==changed
  if os.name!='nt':assert not Path(s.system_preferences_path).stat().st_mode&0o077
  about=c.get('/api/system/about').json();assert about['administration']['database_engine']=='sqlite' and about['limits']=={'session':5,'fleet':20,'devices':500,'api_processes':1}
  assert about['version']==variables()['version']
 assert not calls
 restarted=create_app(s)
 with TestClient(restarted,base_url=ORIGIN,headers={'Origin':ORIGIN}) as fresh:
  login(fresh);assert fresh.get('/api/settings/system').json()==changed
  Path(s.system_preferences_path).write_text('corrupt',encoding='utf-8')
  assert fresh.get('/api/settings/system').json()['error']=='system_preferences_unavailable'
  assert fresh.put('/api/settings/system',json=body(changed)).status_code==503
  assert Path(s.system_preferences_path).read_text(encoding='utf-8')=='corrupt'

def test_guides_validated_single_source_and_safe_blocks(tmp_path):
 data=source();assert len(data.guides)==4
 subprocess.run([sys.executable,'scripts/generate_product_guides.py','--check'],cwd=ROOT,check=True,capture_output=True)
 for gid,name in FILES.items():
  g=render(gid);assert len(g['sections'])>=4
  text=markdown(g);assert '{{' not in text and variables()['version'] in text
  assert (ROOT/'docs'/name).read_text(encoding='utf-8')==text
 raw=json.loads((ROOT/'docs/product-guides.json').read_text(encoding='utf-8'))
 raw['topics']['overview']['blocks']=[{'type':'links','items':[{'label':'unsafe','url':'javascript:alert(1)'}]}]
 p=tmp_path/'guides.json';p.write_text(json.dumps(raw),encoding='utf-8')
 with pytest.raises(ValueError):source(p)
 raw['topics']['overview']['blocks']=[{'type':'paragraph','text':'<script>untrusted-example</script>'}]
 p.write_text(json.dumps(raw),encoding='utf-8')
 assert render('user',p)['sections'][0]['blocks'][0]['text'].startswith('<script>')
 raw['guides'][0]['sections'][0]['topic']='missing';p.write_text(json.dumps(raw),encoding='utf-8')
 with pytest.raises(ValueError):source(p)


def test_about_reports_running_api_version_not_mutable_source(tmp_path,monkeypatch):
 s,a,c,_=fixture(tmp_path)
 import backend.system_info as module
 monkeypatch.setattr(module,"variables",lambda:{"version":"future-source","agent_version":"0.8.0"})
 with c:
  login(c);assert c.get("/api/system/about").json()["version"]==a.version
