import hashlib,json,sys
from pathlib import Path
import pytest
from agent.executor import execute
from agent.recovery_vault import Vault
from backend.recovery_types import make_program,fingerprint,RecoveryReceipt
def fixture(tmp,fail=False):
 config=tmp/'configuration';config.mkdir();file=config/'app.json';file.write_text('{"before":true}',encoding='utf-8')
 state=tmp/'identity/state.json';work=state.parent/'agent-work';work.mkdir(parents=True)
 resources=[{'type':'configuration_file','path':str(file),'metadata':'config-metadata-v1'}]
 task='paired-task';program=make_program(resources,'device-fixture',task)
 code='from pathlib import Path;Path('+repr(str(file))+').write_text(\'{"after":true}\',encoding="utf-8");print("RAW-FORWARD-OUTPUT")'+(';raise RuntimeError("expected-fixture-failure")' if fail else '')
 payload={'kind':'command','owner_id':'owner-fixture','device_id':'device-fixture','task_id':task,'runtime':'python','code':code,'timeout_seconds':10,'verification':{'runtime':'python','code':'print("checked")','timeout_seconds':5,'expected_output':'checked'},'recovery':{'resources':resources},'anti_program':program}
 context={'state_path':str(state),'task_id':task,'device_id':'device-fixture','plan_hash':fingerprint(payload)}
 return file,work,payload,context
def undo(payload,result,context):
 p={'kind':'rollback','owner_id':payload['owner_id'],'device_id':payload['device_id'],'original_kind':'command','anti_program':payload['anti_program'],'original_plan_hash':context['plan_hash'],'capsule_id':result['recovery']['receipt']['capsule_id']}
 c={**context,'task_id':'undo-task','plan_hash':fingerprint(p)}
 return p,c
@pytest.mark.parametrize('fail',[False,True])
def test_real_temporary_file_forward_success_or_failure_and_manual_restore(tmp_path,fail):
 file,work,p,c=fixture(tmp_path,fail)
 before=file.read_bytes();result=execute(p,True,str(work),task_context=c)
 assert result['outcome']==('failed' if fail else 'process_succeeded')
 assert result['recovery']['receipt']['state']=='ready' and file.read_bytes()!=before
 assert 'RAW-FORWARD-OUTPUT' in result['output']
 for item in (work.parent/'recovery').glob('*.enc'):
  assert b'RAW-FORWARD-OUTPUT' not in item.read_bytes()
 restore,rc=undo(p,result,c);r=execute(restore,True,str(work),task_context=rc)
 assert r['outcome']=='restored' and file.read_bytes()==before
 assert execute(restore,True,str(work),task_context=rc)['outcome']=='restored'
def test_missing_pair_or_unsafe_resource_blocks_code(tmp_path):
 file,work,p,c=fixture(tmp_path)
 assert execute(p,True,str(work))['outcome']=='recovery_required'
 p['recovery']['resources'][0]['path']=str(file.with_suffix('.log'));p['anti_program']=make_program(p['recovery']['resources'],'device-fixture','paired-task');c['plan_hash']=fingerprint(p)
 assert execute(p,True,str(work),task_context=c)['outcome']=='recovery_unavailable'
 assert file.read_bytes()==b'{"before":true}'
def test_failure_to_persist_after_forward_is_unknown_never_success(tmp_path,monkeypatch):
 file,work,p,c=fixture(tmp_path);original=Vault._write
 def failing(self,cap):
  if cap.state=='ready':raise OSError('fixture disk failure')
  return original(self,cap)
 monkeypatch.setattr(Vault,'_write',failing)
 result=execute(p,True,str(work),task_context=c);assert result['outcome']=='outcome_unknown' and file.read_bytes()==b'{"after":true}'
def test_catalog_install_uninstall_uses_install_optin_and_proves_absence(tmp_path,monkeypatch):
 import agent.executor as executor,agent.installation as installation
 from backend.catalog import PackageManifest,manifest_hash
 installed=[False];calls=[]
 def runner(argv,**kw):
  calls.append(argv[0]);out='';exit=0
  if argv[0]=='check-target':out='fixture 4.0' if installed[0] else 'absent'
  elif argv[0]=='check-absence':out='absent' if not installed[0] else 'present'
  elif argv[0]=='remove-fixture':installed[0]=False
  elif argv[0]=='msiexec':installed[0]=True
  else:raise AssertionError('unexpected argv')
  return {'outcome':'process_succeeded','exit_code':exit,'output':out,'duration_ms':1}
 monkeypatch.setattr(executor,'run_bounded',runner);monkeypatch.setattr(executor.shutil,'which',lambda _: 'msiexec')
 def download(package,path,*args):path.write_bytes(b'fixture');return hashlib.sha256(b'fixture').hexdigest()
 monkeypatch.setattr(installation,'download',download);monkeypatch.setattr(installation.platform,'system',lambda:'Windows')
 manifest=PackageManifest(id='fixture',name='fixture',version='4.0',os_family='Windows',installer='msi',source_url='https://fixture.invalid/package.msi',sha256=hashlib.sha256(b'fixture').hexdigest(),verification_argv=['check-target'],expected_output='fixture 4.0',uninstall_instructions='Approved fixture only',recovery={'uninstall_argv':['remove-fixture'],'absence_argv':['check-absence'],'absence_expected_output':'absent'})
 state=tmp_path/'identity/state.json';work=state.parent/'agent-work';work.mkdir(parents=True)
 resources=[{'type':'package_presence','package_id':'fixture','version':'4.0','manifest_hash':manifest_hash(manifest)}]
 p={'kind':'install','owner_id':'owner-fixture','device_id':'device-fixture','task_id':'install-task','timeout_seconds':manifest.timeout_seconds,'manifest':manifest.model_dump(),'manifest_hash':manifest_hash(manifest),'recovery':{'resources':resources},'anti_program':make_program(resources,'device-fixture','install-task')}
 c={'state_path':str(state),'task_id':'install-task','device_id':'device-fixture','plan_hash':fingerprint(p)}
 result=execute(p,False,str(work),installations_enabled=True,task_context=c)
 assert result['outcome']=='installed' and installed[0]
 restore,rc=undo(p,result,c);restore['original_kind']='install';rc['plan_hash']=fingerprint(restore)
 assert execute(restore,True,str(work),installations_enabled=False,task_context=rc)['outcome']=='installations_disabled'
 r=execute(restore,False,str(work),installations_enabled=True,task_context=rc)
 assert r['outcome']=='restored' and not installed[0] and 'remove-fixture' in calls
 # Existing target is a pure no-op; no installer or new rollback entitlement.
 installed[0]=True;calls.clear();p['task_id']='already-task';p['anti_program']=make_program(resources,'device-fixture','already-task');c.update(task_id='already-task',plan_hash=fingerprint(p))
 r=execute(p,False,str(work),installations_enabled=True,task_context=c)
 assert r['outcome']=='already_installed' and r['recovery']['receipt']['state']=='not_needed'
 assert 'msiexec' not in calls and 'remove-fixture' not in calls

def test_lost_capsule_is_unavailable_without_changing_configuration(tmp_path):
 file,work,p,c=fixture(tmp_path);result=execute(p,True,str(work),task_context=c)
 receipt=result['recovery']['receipt'];(work.parent/'recovery'/(receipt['capsule_id']+'.enc')).unlink()
 restore,rc=undo(p,result,c);response=execute(restore,True,str(work),task_context=rc)
 assert response['outcome']=='recovery_unavailable'
 assert file.read_bytes()==b'{"after":true}'
