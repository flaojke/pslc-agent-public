"""Actual version0.8 ZIP build, manifest and content verification; isolated registration only."""
from pathlib import Path,PurePosixPath
import hashlib,json,tempfile,zipfile
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings,ROOT
from agent.delivery import verify
base=Path(tempfile.mkdtemp(prefix='recovery-export-',dir=ROOT/'.local'));rows=[]
s=Settings('public-recovery-export-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',knowledge_enabled=False,system_preferences_path=str(base/'prefs.json'))
app=create_app(s)
with TestClient(app,base_url='http://127.0.0.1:8787',headers={'Origin':'http://127.0.0.1:8787'}) as c:
 assert c.post('/auth/login',json={'username':'admin','password':'admin'}).status_code==200
 for os in ('Windows','Linux'):
  r=c.post('/api/nodes/onboarding',json={'name':'qa-export-only','os_family':os,'state_path':'C:/ProgramData/PSLC-Agent/identity.json' if os=='Windows' else '/var/lib/pslc-agent/identity.json','device_kind':'unknown'})
  assert r.status_code==200;r=r.json();secret=r['enrollment_token'].encode()
  r=c.get('/api/nodes/bundles/'+r['bundle_id']);assert r.status_code==200
  assert hashlib.sha256(r.content).hexdigest()==r.headers['X-Checksum-SHA256']
  p=base/(os+'.zip');p.write_bytes(r.content)
  with zipfile.ZipFile(p) as z:
   sums=dict(line.split('  ',1)[::-1] for line in z.read('SHA256SUMS').decode().splitlines())
   assert set(z.namelist())==set(sums)|{'SHA256SUMS'}
   for n,sha in sums.items():
    name=PurePosixPath(n);assert not name.is_absolute() and '..' not in name.parts and ':' not in n and '\\' not in n
    data=z.read(n);assert hashlib.sha256(data).hexdigest()==sha and secret not in data
   manifest=json.loads(z.read('native/manifest.json'));assert manifest['version']=='0.8.0'
   assert not any('identity' in n.lower() or 'private.pem' in n for n in z.namelist())
   rows.append({'os':os,'native':'0.8.0','files_verified':len(sums),'zip_sha256':r.headers['X-Checksum-SHA256'],'enrollment_secret_in_zip':False,'existing_identity_used':False})
out=ROOT/'docs/qa/reversible-task-plans/exports.json';out.write_text(json.dumps({'scope':'isolated actual native ZIPs, no host installation','exports':rows},indent=2),encoding='utf-8');print(json.dumps({'verified_actual_native_exports':len(rows),'native':'0.8.0','host_mutation':False}))
