"""Isolated PostgreSQL metadata drill; never touches an existing application database."""
from pathlib import Path
import sys,json,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_operations_recovery import postgres_database,enroll,health,complete,MARKER
from backend.store import Store,devices,events
from backend.operations import backup,restore
from sqlalchemy import text,select,func
out=ROOT/'docs/qa/operations';out.mkdir(parents=True,exist_ok=True)
with postgres_database() as source,postgres_database() as target:
 store=Store(source);node=enroll(store);done=health(store,node);complete(store,node,store.poll(node['device_id'],node['credential']));pending=health(store,node,'active');store.poll(node['device_id'],node['credential'])
 with store.engine.connect() as c:version=c.scalar(text('SHOW server_version'))
 snapshot=ROOT/'.local/backups'/('postgres-drill-'+str(int(time.time()))+'.json');snapshot.parent.mkdir(exist_ok=True)
 exported=backup(source.database_url,snapshot);recovered=restore(snapshot,target.database_url,exported['digest']);restored=Store(target)
 assert restored.get_task(done['id'])['state']=='verified' and restored.get_task(pending['id'])['state']=='outcome_unknown'
 assert restored.poll(node['device_id'],node['credential']) is None
 assert MARKER.encode() not in snapshot.read_bytes()
 with restored.tx() as c:node_count=c.scalar(select(func.count()).select_from(devices));event_count=c.scalar(select(func.count()).select_from(events))
 value={'engine':'PostgreSQL','version':version,'environment':'isolated Docker on loopback','backup':exported,'restore':recovered,'nodes_restored':node_count,'events_restored':event_count,'raw_marker_absent':True,'active_becomes_unknown':True,'old_execution_not_replayed':True,'corporate_database':False,'rpo_target_hours':24,'rto_target_hours':4,'sla_proven':False}
 (out/'postgres-drill.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps({'version':version,'backup_ms':exported['duration_ms'],'restore_ms':recovered['duration_ms'],'nodes':node_count,'events':event_count}))
 store.engine.dispose();restored.engine.dispose()
