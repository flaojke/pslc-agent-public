"""Logical package absence only, not a full OS snapshot or rollback of external effects."""
from backend.catalog import PackageManifest,PackageRecovery,manifest_hash
from .recovery_files import RecoveryError
class PackageProvider:
 def __init__(self,manifest=None,runner=None,workdir=None):
  from .executor import run_bounded
  self.manifest=manifest;self.runner=runner or run_bounded;self.workdir=workdir;self.contract=None
  if manifest is not None:
   package=PackageManifest.model_validate(manifest,strict=True)
   if not package.recovery:raise RecoveryError('recovery_provider_unavailable')
   self.contract={'package_id':package.id,'version':package.version,'manifest_hash':manifest_hash(package),'recovery':package.recovery.model_dump(),'verification_argv':package.verification_argv,'expected_output':package.expected_output}
 def load_baseline(self,baseline):
  contract=baseline.get('contract')
  if not isinstance(contract,dict) or set(contract)!={'package_id','version','manifest_hash','recovery','verification_argv','expected_output'}:raise RecoveryError('recovery_unavailable')
  PackageRecovery.model_validate(contract['recovery'],strict=True);self.contract=contract
 def probe(self,argv,expected,timeout):
  r=self.runner(argv,timeout_seconds=timeout,cwd=self.workdir)
  return r.get('outcome')=='process_succeeded' and r.get('exit_code')==0 and r.get('output','').strip()==expected
 def snapshot(self,resource):
  c=self.contract
  if not c or any(resource[k]!=c[k] for k in ('package_id','version','manifest_hash')):raise RecoveryError('recovery_binding_mismatch')
  r=PackageRecovery.model_validate(c['recovery'],strict=True)
  if self.probe(c['verification_argv'],c['expected_output'],15):presence='target'
  elif self.probe(r.absence_argv,r.absence_expected_output,r.verification_timeout_seconds):presence='absent'
  else:raise RecoveryError('recovery_package_state_unknown')
  return {'presence':presence,'contract':c}
 def restore(self,resource,baseline):
  self.load_baseline(baseline)
  if baseline['presence']!='absent':raise RecoveryError('recovery_package_restore_unsupported')
  current=self.snapshot(resource)
  if current['presence']=='absent':return
  r=PackageRecovery.model_validate(self.contract['recovery'],strict=True)
  result=self.runner(r.uninstall_argv,timeout_seconds=r.timeout_seconds,cwd=self.workdir)
  if result.get('outcome')!='process_succeeded' or result.get('exit_code')!=0:raise RecoveryError('recovery_package_uninstall_failed')
  if self.snapshot(resource)['presence']!='absent':raise RecoveryError('recovery_verification_failed')
