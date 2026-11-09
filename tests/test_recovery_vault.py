"""Safe native-provider tests touch only pytest's temporary directory, not managed hosts."""
import json,os,hashlib
from pathlib import Path
import pytest
from backend.recovery_types import make_program,validate_pair,RestoreProgram,RecoveryReceipt,fingerprint
from agent.recovery_files import RecoveryError,snapshot_file,file_fingerprint,restore_file
from agent.recovery_vault import Vault

def fixture(tmp,absent=False,**kw):
 config=tmp/'configuration';config.mkdir();file=config/'application.json'
 if not absent:file.write_bytes(b'{"enabled":false}')
 resources=[{'type':'configuration_file','path':str(file),'metadata':'config-metadata-v1'}]
 program=make_program(resources,'device-fixture','task-fixture')
 return file,resources,program,Vault(tmp/'identity/recovery','device-fixture','owner-fixture',protected=[tmp/'identity'],**kw)
def test_pointer_has_no_paths_values_code_and_exact_binding(tmp_path):
 file,resources,p,v=fixture(tmp_path)
 encoded=json.dumps(p)
 assert str(file) not in encoded and 'code' not in encoded and 'enabled' not in encoded
 validate_pair(p,resources)
 with pytest.raises(ValueError):RestoreProgram.model_validate({**p,'code':'arbitrary'},strict=True)
 with pytest.raises(ValueError):validate_pair(p,[{**resources[0],'path':str(file)+'x'}])
 with pytest.raises(ValueError):make_program(resources*2,'device-fixture','task-fixture')
def test_real_file_encrypted_baseline_restore_metadata_and_idempotence(tmp_path):
 file,resources,p,v=fixture(tmp_path)
 before=snapshot_file(resources[0]);cid=v.prepare(p,resources,'a'*64);v.started(cid)
 file.write_bytes(b'{"enabled":true}')
 receipt=v.finished(cid);assert receipt['state']=='ready'
 raw=(v.directory/(cid+'.enc')).read_bytes();assert b'enabled' not in raw and b'application.json' not in raw
 restored=v.rollback(cid,p,'a'*64)
 assert restored['state']=='restored' and restored['restored_count']==1
 assert snapshot_file(resources[0])==before
 assert v.rollback(cid,p,'a'*64)['state']=='restored'
 with pytest.raises(RecoveryError,match='recovery_forward_replay_blocked'):v.prepare(p,resources,'a'*64)
 RecoveryReceipt.model_validate(restored,strict=True)
def test_failed_forward_can_restore_and_drift_never_overwritten(tmp_path):
 file,resources,p,v=fixture(tmp_path);cid=v.prepare(p,resources,'b'*64);v.started(cid)
 file.write_bytes(b'partial change before failing command');v.finished(cid)
 file.write_bytes(b'independent administrator change')
 r=v.rollback(cid,p,'b'*64);assert r['state']=='conflict' and r['restored_count']==0
 assert file.read_bytes()==b'independent administrator change'
 # A new explicit manual rollback may retry when the operator restores the captured post-state.
 cap=v._read(cid);assert 'partial change' not in cap.model_dump_json()
def test_absent_configuration_file_restored_as_absent(tmp_path):
 file,resources,p,v=fixture(tmp_path,absent=True);cid=v.prepare(p,resources,'c'*64);v.started(cid);file.write_bytes(b'{"new":true}');v.finished(cid)
 assert v.rollback(cid,p,'c'*64)['state']=='restored' and not file.exists()
def test_crash_without_poststate_is_conflict_if_resource_changed(tmp_path):
 file,resources,p,v=fixture(tmp_path);cid=v.prepare(p,resources,'d'*64);v.started(cid);file.write_bytes(b'{"crash":true}')
 restarted=Vault(v.directory,v.device_id,v.owner_id)
 assert restarted.rollback(cid,p,'d'*64)['state']=='conflict'
 assert file.read_bytes()==b'{"crash":true}'
def test_vault_expiry_foreign_owner_corruption_and_quota(tmp_path):
 now=[1000.0];file,resources,p,v=fixture(tmp_path,clock=lambda:now[0],max_capsules=1)
 cid=v.prepare(p,resources,'e'*64);v.started(cid);v.finished(cid)
 other=make_program(resources,'device-fixture','second-task')
 with pytest.raises(RecoveryError,match='recovery_quota'):v.prepare(other,resources,'f'*64)
 foreign=Vault(v.directory,v.device_id,'foreign-owner',clock=lambda:now[0])
 with pytest.raises(RecoveryError,match='recovery_unavailable'):foreign.rollback(cid,p,'e'*64)
 now[0]+=7*86400+1
 with pytest.raises(RecoveryError,match='recovery_expired'):v.rollback(cid,p,'e'*64)
 second=v.prepare(other,resources,'f'*64);assert second!=cid and not v._path(cid).exists()
 raw=v._path(second).read_bytes();v._path(second).write_bytes(raw[:-1]+bytes([raw[-1]^1]))
 with pytest.raises(RecoveryError,match='recovery_unavailable'):v.started(second)
@pytest.mark.parametrize('name',['application.log','raw.evtx','script.ps1'])
def test_logs_and_forward_source_are_not_config_resources(tmp_path,name):
 file,resources,p,v=fixture(tmp_path);bad=file.with_name(name);bad.write_bytes(b'RAW-OUTPUT-NOT-CONFIG')
 r=[{**resources[0],'path':str(bad)}]
 with pytest.raises(RecoveryError):v.prepare(make_program(r,'device-fixture','unsafe-task'),r,'f'*64)
def test_protected_directory_hardlink_and_partial_undo(tmp_path,monkeypatch):
 file,resources,p,v=fixture(tmp_path)
 identity=tmp_path/'identity/private.json';identity.parent.mkdir(exist_ok=True);identity.write_bytes(b'credential')
 with pytest.raises(RecoveryError):snapshot_file({'path':str(identity)},v.protected)
 hard=file.with_name('hard.json')
 try:os.link(file,hard)
 except OSError:pytest.skip('hardlinks unavailable')
 with pytest.raises(RecoveryError):snapshot_file(resources[0])
 hard.unlink()
 second=file.with_name('second.json');second.write_bytes(b'old')
 resources.append({'type':'configuration_file','path':str(second),'metadata':'config-metadata-v1'});p=make_program(resources,'device-fixture','partial-task')
 cid=v.prepare(p,resources,'1'*64);v.started(cid);file.write_bytes(b'new1');second.write_bytes(b'new2');v.finished(cid)
 original=v.restore_resource
 def fail(resource,baseline):
  if resource['path']==str(second):raise RecoveryError()
  return original(resource,baseline)
 monkeypatch.setattr(v,'restore_resource',fail)
 receipt=v.rollback(cid,p,'1'*64);assert receipt['state']=='partial' and receipt['restored_count']==1
 monkeypatch.setattr(v,'restore_resource',original)
 assert v.rollback(cid,p,'1'*64)['state']=='restored' and second.read_bytes()==b'old'

def test_drift_during_multi_resource_undo_is_not_overwritten(tmp_path,monkeypatch):
 file,resources,p,v=fixture(tmp_path)
 second=file.with_name('second.json');second.write_bytes(b'old')
 resources.append({'type':'configuration_file','path':str(second),'metadata':'config-metadata-v1'});p=make_program(resources,'device-fixture','mid-undo-task')
 cid=v.prepare(p,resources,'2'*64);v.started(cid);file.write_bytes(b'new');second.write_bytes(b'new');v.finished(cid)
 original=v.restore_resource
 def concurrent_edit(resource,baseline):
  original(resource,baseline)
  if resource['path']==str(file):second.write_bytes(b'independent edit during first restore')
 monkeypatch.setattr(v,'restore_resource',concurrent_edit)
 result=v.rollback(cid,p,'2'*64)
 assert result['state']=='partial' and result['restored_count']==1
 assert second.read_bytes()==b'independent edit during first restore'

def test_deleted_existing_configuration_restores_original_metadata(tmp_path):
 file,resources,p,v=fixture(tmp_path);before=snapshot_file(resources[0])
 cid=v.prepare(p,resources,'3'*64);v.started(cid);file.unlink();v.finished(cid)
 assert v.rollback(cid,p,'3'*64)['state']=='restored' and snapshot_file(resources[0])==before
