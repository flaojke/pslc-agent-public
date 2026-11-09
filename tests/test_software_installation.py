from recovery_fixture import simulated_recovery,historical_metadata
from backend.store import SCHEMA_VERSION
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, Table, create_engine, insert, inspect, select, update
from agent.executor import run_bounded, verify_job
from agent.installation import execute_installation
from backend.app import create_app
from backend.catalog import Catalog, CatalogError, PackageManifest, manifest_hash
from backend.config import Settings
from backend.store import Store, devices, metadata, tasks, version, digest

ARTIFACT=b'approved-fixture-installer-bytes'
MARKER='RAW-INSTALL-OUTPUT-NEVER-PERSIST-87239'
TOKEN='installation-test-token-long-enough'

@pytest.fixture
def env(tmp_path):
    installed=tmp_path/'installed-version.txt'
    probe=[sys.executable,'-I','-c',f"from pathlib import Path;p=Path({str(installed)!r});print(p.read_text() if p.exists() else 'absent')"]
    manifest=dict(id='fixture-tool',name='Fixture tool',version='1.2.3',os_family='Windows',installer='msi',
        source_url='https://packages.internal/tool.msi',sha256=hashlib.sha256(ARTIFACT).hexdigest(),max_bytes=10000,
        timeout_seconds=600,verification_argv=probe,expected_output='1.2.3',uninstall_instructions='Remove only the fixture version file in the test directory.',recovery={'uninstall_argv':['fixture-uninstall'],'absence_argv':['fixture-absence'],'absence_expected_output':'absent'})
    path=tmp_path/'catalog.json'
    def save(value=manifest):path.write_text(json.dumps({'schema_version':1,'packages':[value]}),encoding='utf-8')
    save()
    settings=Settings(TOKEN,'sqlite:///'+(tmp_path/'db.sqlite3').as_posix(),package_catalog_path=str(path))
    return {'manifest':manifest,'path':path,'installed':installed,'settings':settings,'save':save,'root':tmp_path}

def payload(env):
    p=PackageManifest.model_validate(env['manifest'])
    return {'kind':'install','manifest':p.model_dump(),'manifest_hash':manifest_hash(p),'timeout_seconds':p.timeout_seconds}

class FixtureRunner:
    """Only replaces the OS installer; executes version probes and a temporary Python fixture for real."""
    def __init__(self,env,code=0,installed_version='1.2.3',process_outcome='process_succeeded'):
        self.env=env;self.code=code;self.installed_version=installed_version;self.process_outcome=process_outcome;self.installs=[]
    def __call__(self,argv,timeout_seconds=30,cwd=None):
        if argv==self.env['manifest']['verification_argv']:
            return run_bounded(argv,timeout_seconds=timeout_seconds,cwd=cwd)
        self.installs.append(argv)
        script=f"from pathlib import Path;Path({str(self.env['installed'])!r}).write_text({self.installed_version!r});print({MARKER!r})"
        result=run_bounded([sys.executable,'-I','-c',script],timeout_seconds=5,cwd=cwd)
        result.update(exit_code=self.code,outcome=self.process_outcome)
        return result

def execute(env,runner=None,transport=None,**kwargs):
    runner=runner or FixtureRunner(env)
    transport=transport or httpx.MockTransport(lambda req:httpx.Response(200,content=ARTIFACT))
    return execute_installation(payload(env),True,str(env['root']),runner=runner,transport=transport,which=lambda name:name,**kwargs)

@pytest.fixture
def client(env):
    with TestClient(create_app(env['settings'])) as c:
        c.headers['Authorization']='Bearer '+TOKEN
        yield c

def node(c,enabled=True):
    token=c.post('/api/enrollment-tokens').json()['enrollment_token']
    return c.post('/agent/enroll',json={'agent_protocol':1,'enrollment_token':token,'name':'fixture-node','os_family':'Windows','installations_enabled':enabled}).json()

def plan(c,n,session='s'):
    return c.post('/api/installations',json={'device_id':n['device_id'],'session_id':session,'package_id':'fixture-tool','package_version':'1.2.3'})

def poll(c,n):return c.post('/agent/'+n['device_id']+'/poll',headers={'X-PSLC-Agent-Protocol':'1','Authorization':'Bearer '+n['credential']}).json()['job']

def confirm(c,t):return c.post('/api/tasks/'+t['id']+'/decision',json={'approve':True,'plan_hash':t['plan_hash']})

def finish(c,n,t,j,result):
    return c.post('/agent/'+n['device_id']+'/tasks/'+t['id']+'/result',headers={'Authorization':'Bearer '+n['credential']},json={**result,'recovery':simulated_recovery(j,'not_needed' if result.get('outcome')=='already_installed' else 'ready'),'lease_id':j['lease_id']})

@pytest.mark.parametrize('change',[
    {'sha256':'wrong'},{'source_url':'http://unapproved.example/tool.msi'}, {'source_url':'https://user:secret@packages.internal/a'},
    {'source_url':'https://packages.internal/a?token=x'}, {'os_family':'Linux'}, {'expected_output':'OK'},
    {'silent_args':['/forcerestart']},{'silent_args':['REBOOT=Force']}, {'verification_argv':['bad\ncommand']},
])
def test_manifest_rejects_unsafe_or_incomplete(env,change):
    with pytest.raises(ValidationError):PackageManifest.model_validate({**env['manifest'],**change})

def test_empty_duplicate_and_invalid_catalog(env):
    assert Catalog().listing()=={'status':'empty','packages':[]}
    env['path'].write_text(json.dumps({'schema_version':1,'packages':[env['manifest'],env['manifest']]}))
    with pytest.raises(CatalogError):Catalog(str(env['path'])).read()
    env['path'].write_text('not JSON')
    with pytest.raises(CatalogError):Catalog(str(env['path'])).read()

def test_catalog_and_capability(client):
    assert client.get('/api/packages').json()['packages'][0]['version']=='1.2.3'
    assert client.post('/api/packages',json={}).status_code==405
    n=node(client,False);assert plan(client,n).status_code==403
    n=node(client);r=client.post('/api/installations',json={'device_id':n['device_id'],'session_id':'s','package_id':'unknown','package_version':'1'})
    assert r.status_code==409

def test_exact_confirmation_and_catalog_revalidation(client,env):
    n=node(client);t=plan(client,n).json()
    assert t['state']=='awaiting_confirmation' and poll(client,n) is None
    assert t['plan']['manifest']['sha256']==env['manifest']['sha256']
    assert client.post('/api/tasks/'+t['id']+'/decision',json={'approve':True,'plan_hash':'0'*64}).status_code==409
    assert confirm(client,t).status_code==200
    env['save']({**env['manifest'],'sha256':'0'*64})
    assert poll(client,n) is None
    assert client.get('/api/tasks/'+t['id']).json()['state']=='catalog_changed'

def test_catalog_change_before_confirmation_is_durable(client,env):
    n=node(client);t=plan(client,n).json();env['path'].write_text('{"schema_version":1,"packages":[]}')
    assert confirm(client,t).status_code==409
    assert client.get('/api/tasks/'+t['id']).json()['state']=='catalog_changed'

def test_reject_and_lease_duration(client):
    n=node(client);t=plan(client,n).json()
    client.post('/api/tasks/'+t['id']+'/decision',json={'approve':False,'plan_hash':t['plan_hash']})
    assert poll(client,n) is None
    t=plan(client,n).json();confirm(client,t);j=poll(client,n)
    row=client.get('/api/tasks/'+t['id']).json()
    assert row['lease_until']-row['created']>=659
    assert verify_job(j,n['device_id'])
    assert client.get('/api/overview').json()['statistics']['counts']['rejected']==1

def test_installer_opt_in_and_platform(env):
    assert execute_installation(payload(env),False,str(env['root']))['outcome']=='installations_disabled'
    assert execute_installation(payload(env),True,str(env['root']),os_family='Linux')['outcome']=='unsupported_platform'

@pytest.mark.parametrize('driver,os_family', [('msi','Windows'),('exe','Windows'),('deb','Linux'),('rpm','Linux')])
def test_drivers_and_verified_temp_fixture(env,driver,os_family):
    env['manifest'].update(installer=driver,os_family=os_family)
    if driver=='exe':env['manifest'].update(silent_args=['/S'],no_restart_args=['/norestart'])
    runner=FixtureRunner(env)
    r=execute(env,runner,os_family=os_family)
    assert r['outcome']=='installed' and r['installation']['version_matched']
    argv=runner.installs[0]
    assert r['installation']['artifact_sha256']==env['manifest']['sha256']
    if driver=='msi':assert '/qn' in argv and '/norestart' in argv and 'REBOOT=ReallySuppress' in argv
    if driver=='exe':assert '/S' in argv and '/norestart' in argv
    if driver=='deb':assert argv[:2]==['dpkg','--install']
    if driver=='rpm':assert argv[:2]==['rpm','--upgrade']
    assert not list(env['root'].glob('pslc-install-*'))

def test_already_installed_skips_download(env):
    env['installed'].write_text('1.2.3')
    runner=FixtureRunner(env)
    def no_download(req):raise AssertionError('No download when already installed')
    r=execute(env,runner,httpx.MockTransport(no_download))
    assert r['outcome']=='already_installed' and not runner.installs

@pytest.mark.parametrize('code,expected',[(0,'installed'),(3010,'reboot_required'),(1641,'failed'),(1603,'failed')])
def test_installer_codes_and_no_reboot_command(env,code,expected):
    runner=FixtureRunner(env,code=code)
    r=execute(env,runner)
    assert r['outcome']==expected
    assert len(runner.installs)==1
    assert not any('shutdown' in str(x).lower() for argv in runner.installs for x in argv)
    if code==3010:assert r['installation']['reboot_required']

def test_false_success_and_timeout(env):
    assert execute(env,FixtureRunner(env,installed_version='0.0'))['outcome']=='verification_failed'
    env['installed'].unlink()
    assert execute(env,FixtureRunner(env,process_outcome='timeout'))['outcome']=='outcome_unknown'

@pytest.mark.parametrize('response,expected',[
    (httpx.Response(200,content=b'corrupt'),'integrity_failed'),
    (httpx.Response(302,headers={'location':'https://elsewhere/'}),'download_failed'),
    (httpx.Response(200,content=b'x'*11000),'download_failed'),
    (httpx.Response(500,content=b'server error'),'download_failed')])
def test_download_failures_do_not_execute(env,response,expected):
    runner=FixtureRunner(env);calls=[]
    def transport(req):calls.append(req);return response
    r=execute(env,runner,httpx.MockTransport(transport))
    assert r['outcome']==expected and not runner.installs and len(calls)==1
    assert not list(env['root'].glob('pslc-install-*'))

def test_confirmation_install_verify_end_to_end_and_retention(client,env):
    n=node(client);t=plan(client,n).json();confirm(client,t);j=poll(client,n)
    r=execute(env,os_family='Windows');assert r['outcome']=='installed'
    result=finish(client,n,t,j,r);assert result.status_code==200
    assert result.json()['state']=='verified' and result.json()['summary']['verified_version']=='1.2.3'
    assert finish(client,n,t,j,r).status_code==200
    assert client.get('/api/overview').json()['statistics']['counts']['completed']==1
    assert MARKER not in client.get('/api/tasks').text+client.get('/api/events').text
    assert MARKER.encode() not in (env['root']/'db.sqlite3').read_bytes()
    assert client.get('/api/tasks/'+t['id']+'/output').json()['output'].strip()==MARKER

def test_missing_proof_not_success(client):
    n=node(client);t=plan(client,n).json();confirm(client,t);j=poll(client,n)
    r=finish(client,n,t,j,{'outcome':'installed','exit_code':0})
    assert r.json()['state']=='verification_failed'

def test_wrong_proof_and_unknown_duplicate(client,env):
    n=node(client);t=plan(client,n).json();confirm(client,t);j=poll(client,n)
    r=execute(env,FixtureRunner(env,process_outcome='timeout'))
    wrong={**r,'installation':{**r['installation'],'manifest_hash':'0'*64}}
    assert finish(client,n,t,j,wrong).status_code==422
    assert finish(client,n,t,j,r).json()['state']=='outcome_unknown'
    assert finish(client,n,t,j,r).status_code==200
    assert client.get('/api/overview').json()['statistics']['counts']['completed']==1
    assert poll(client,n) is None
    assert client.get('/api/overview').json()['statistics']['active_fleet']==1

def test_installation_restart_preserves_metadata_without_plan(client,env):
    n=node(client);t=plan(client,n).json();confirm(client,t);j=poll(client,n)
    with TestClient(create_app(env['settings'])) as new:
        new.headers['Authorization']='Bearer '+TOKEN
        row=new.get('/api/tasks/'+t['id']).json()
        assert row['state']=='outcome_unknown' and row['plan'] is None
        assert row['package_meta']['version']=='1.2.3' and poll(new,n) is None
        r=execute(env);assert finish(new,n,t,j,r).json()['state']=='verified'

def test_migrate_v1_preserves_nodes_tasks_and_disables_installation(env):
    engine=create_engine(env['settings'].database_url);old=historical_metadata(1)
    old.create_all(engine)
    with engine.begin() as c:
        c.execute(old.tables['schema_version'].insert().values(id=1));c.execute(old.tables['dispatch_guard'].insert().values(id=1))
        c.execute(old.tables['devices'].insert().values(id='old-node',name='old',os_family='Windows',credential_hash=digest('preserved'),revoked=False,commands_enabled=True,last_seen=1))
        c.execute(old.tables['tasks'].insert().values(id='old-task',device_id='old-node',session_id='s',kind='health',runtime='python',state='verified',plan_hash='0'*64,created=1,expires=1,summary={'outcome':'verified'}))
    engine.dispose();store=Store(env['settings'])
    d=store.list_devices()[0]
    assert d['id']=='old-node' and not d['installations_enabled'] and d['commands_enabled']
    assert store.get_task('old-task')['state']=='verified'
    with store.tx() as c:
        assert c.execute(select(version.c.id)).scalar()==SCHEMA_VERSION
        assert c.execute(select(devices.c.credential_hash)).scalar()==digest('preserved')
    store.engine.dispose()

def test_unknown_schema_fails_without_modification(env):
    store=Store(env['settings'])
    with store.tx() as c:c.execute(update(version).values(id=99))
    store.engine.dispose()
    with pytest.raises(ValueError,match='unsupported_schema_version'):Store(env['settings'])


@pytest.mark.parametrize('count,same_session,expected',[(6,True,5),(21,False,20)])
def test_installation_shares_session_and_fleet_limits(client,count,same_session,expected):
    nodes=[node(client) for _ in range(count)]
    for i,n in enumerate(nodes):
        t=plan(client,n,'shared' if same_session else 's'+str(i)).json();assert confirm(client,t).status_code==200
    jobs=[poll(client,n) for n in nodes]
    assert sum(x is not None for x in jobs)==expected
