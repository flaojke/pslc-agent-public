"""Disposable /tmp configuration rollback. Intended for a networkless read-only container."""
import json,sys,tempfile,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.recovery_vault import Vault
from agent.recovery_files import snapshot_file
from backend.recovery_types import make_program
with tempfile.TemporaryDirectory(prefix='pslc-recovery-') as tmp:
 root=Path(tmp);folder=root/'configuration';folder.mkdir();p=folder/'application.json';p.write_bytes(b'{"before":true}');os.chmod(p,0o640);os.setxattr(p,'user.pslc_fixture',b'baseline-metadata')
 resources=[{'type':'configuration_file','path':str(p),'metadata':'config-metadata-v1'}]
 program=make_program(resources,'linux-fixture','temporary-task');before=snapshot_file(resources[0])
 vault=Vault(root/'identity/recovery','linux-fixture','owner-fixture',protected=[root/'identity'])
 cid=vault.prepare(program,resources,'1'*64);vault.started(cid);p.write_bytes(b'{"after":true}');os.chmod(p,0o600);os.setxattr(p,'user.pslc_fixture',b'changed');receipt=vault.finished(cid)
 restored=vault.rollback(cid,program,'1'*64)
 assert restored['state']=='restored' and snapshot_file(resources[0])==before
 p.write_bytes(b'{"independent_change":true}')
 assert vault.rollback(cid,program,'1'*64)['state']=='conflict'
 print(json.dumps({'scope':'Linux container temporary configuration only','restored':True,'mode_owner_xattrs_mtime_verified':True,'drift_denied':True,'real_managed_host':False,'forward_commands':False}))
