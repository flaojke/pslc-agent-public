"""Local upgrade checkpoint. No endpoint mutations; selected existing agent stays health-only."""
from pathlib import Path
import json,hashlib,shutil,sys,datetime
from sqlalchemy import create_engine,select,func,inspect
from backend.config import ROOT,load_settings
from backend.store import tasks,conversation_turns,investigation_jobs,accounts,devices
from backend.operations import backup
from backend.secret_store import SecretStore
s=load_settings()
def facts():
 e=create_engine(s.database_url);out={}
 with e.connect() as c:
  out['active_tasks']=c.scalar(select(func.count()).select_from(tasks).where(tasks.c.state.in_(['queued','running','cancel_requested'])))
  out['active_turns']=c.scalar(select(func.count()).select_from(conversation_turns).where(conversation_turns.c.state.in_(['queued','generating'])))
  out['active_analysis']=c.scalar(select(func.count()).select_from(investigation_jobs).where(investigation_jobs.c.analysis_state.in_(['pending','analyzing'])))
  rows=[]
  for table in (accounts,devices):
   known={x['name'] for x in inspect(c).get_columns(table.name)};columns=[col for col in table.columns if col.name in known and col.name not in ('last_seen','agent_protocol')]
   rows.extend(dict(row) for row in c.execute(select(*columns).order_by(table.c.id)).mappings())
  out['identities']=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
  # The selected pre-existing node is never granted new capabilities.
  identity_path=ROOT/'.local/smoke-agent.json';identity=json.loads(identity_path.read_text(encoding='utf-8'));did=identity['device_id']
  row=c.execute(select(devices.c.commands_enabled,devices.c.installations_enabled,devices.c.diagnostics_enabled).where(devices.c.id==did)).mappings().one()
  if any(row.values()):raise SystemExit('Selected node is not health-only')
  out['selected_device_id']=did;out['identity_file_hash']=hashlib.sha256(identity_path.read_bytes()).hexdigest()
 e.dispose();out['protected']={}
 for name in ('.local/settings.json','.local/model/connection.enc','.local/demo/identity.enc','.local/system/preferences.json'):
  p=ROOT/name
  if p.is_file():out['protected'][name]=hashlib.sha256(p.read_bytes()).hexdigest()
 return out
checkpoint=ROOT/'.local/deploy-016.json'
mode=sys.argv[1]
if mode=='preflight':
 value=facts()
 if value['active_tasks'] or value['active_turns'] or value['active_analysis']:raise SystemExit('Active work; maintenance deferred')
 base=ROOT/'.local/backups'/('pre-016-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));SecretStore(base/'metadata.json')._directory()
 value['directory']=str(base);checkpoint.write_text(json.dumps(value),encoding='utf-8')
 print(json.dumps({'idle':True,'backup_directory':str(base)}))
elif mode=='backup':
 value=json.loads(checkpoint.read_text(encoding='utf-8'));current=facts()
 if current['active_tasks'] or current['active_turns'] or current['active_analysis']:raise SystemExit('Active work')
 base=Path(value['directory']);report=backup(s.database_url,base/'metadata.json')
 for name in (*current['protected'],'.local/smoke-agent.json'):
  p=ROOT/name;target=base/Path(name).relative_to('.local');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
 current['directory']=str(base);checkpoint.write_text(json.dumps(current),encoding='utf-8')
 print(json.dumps({'backup_schema':report['schema'],'metadata_rows':sum(report['rows'].values()),'raw_context_copied':False}))
elif mode=='verify':
 before=json.loads(checkpoint.read_text(encoding='utf-8'));after=facts()
 assert before['identities']==after['identities'] and before['identity_file_hash']==after['identity_file_hash'] and before['protected']==after['protected'],'Persistent identity/config mismatch'
 print(json.dumps({'identities_grants_caps_preserved':True,'agent_identity_preserved':True,'model_demo_system_config_preserved':True,'health_only_preserved':True}))
