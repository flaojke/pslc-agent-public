"""Authorized local maintenance update, metadata only; no real task execution."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,time,os
from sqlalchemy import create_engine,select,func
from backend.config import ROOT,load_settings
from backend.store import accounts,devices,tasks,conversation_turns
from backend.operations import backup
from backend.secret_store import SecretStore
s=load_settings()
def fingerprint():
 engine=create_engine(s.database_url)
 with engine.connect() as c:
  active=c.scalar(select(func.count()).select_from(tasks).where(tasks.c.state.in_(['queued','running','cancel_requested'])))
  turns=c.scalar(select(func.count()).select_from(conversation_turns).where(conversation_turns.c.state.in_(['queued','generating'])))
  identity=[dict(x) for x in c.execute(select(accounts).order_by(accounts.c.id)).mappings()]+[dict(x) for x in c.execute(select(devices).order_by(devices.c.id)).mappings()]
 engine.dispose()
 protected={}
 for name in ('.local/settings.json','.local/model/connection.enc','.local/demo/identity.enc'):
  p=ROOT/name
  if p.exists():protected[name]=hashlib.sha256(p.read_bytes()).hexdigest()
 for row in identity:row.pop('last_seen',None)
 return {'active_tasks':active,'active_turns':turns,'identities':hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),'protected':protected}
if len(sys.argv)==1:
 before=fingerprint()
 if before['active_tasks'] or before['active_turns']:raise SystemExit('Active work; maintenance deferred')
 base=ROOT/'.local/backups/pre-015-settings';SecretStore(base/'metadata.json')._directory()
 if not (base/'metadata.json').exists():backup(s.database_url,base/'metadata.json')
 for name in ('.local/settings.json','.local/model/connection.enc','.local/demo/identity.enc','.local/system/preferences.json'):
  p=ROOT/name
  if p.is_file():
   target=base/Path(name).relative_to('.local');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
 (base/'fingerprints.json').write_text(json.dumps(before),encoding='utf-8')
 print(json.dumps({'backup_ready':True,'active_tasks':0,'active_turns':0}))
else:
 before=json.loads((ROOT/'.local/backups/pre-015-settings/fingerprints.json').read_text(encoding='utf-8'));after=fingerprint()
 snapshot=json.loads((ROOT/'.local/backups/pre-015-settings/metadata.json').read_text(encoding='utf-8'))['tables']
 original=sorted(snapshot['accounts'],key=lambda x:x['id'])+sorted(snapshot['devices'],key=lambda x:x['id'])
 for row in original:row.pop('last_seen',None)
 original_hash=hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest()
 assert original_hash==after['identities'] and dict(before['protected'])==after['protected'],'identity/config changed'
 print(json.dumps({'accounts_devices_preserved':True,'model_demo_settings_preserved':True,'schema':7}))
