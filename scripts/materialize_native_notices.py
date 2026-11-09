"""Add distribution notices to existing verified bundles, re-sign; native executable bytes unchanged."""
from pathlib import Path
import base64,hashlib,json,sys,sysconfig,subprocess,importlib.metadata as md
from backend.config import ROOT
from agent.delivery import sign,verify
key=ROOT/'.local/release-qa-keys/QA-ONLY-private.pem'
public=json.loads((ROOT/'config/agent-trust.json').read_text())['public_pem'];pub=ROOT/'.local/release-qa-keys/public-current.pem';pub.write_text(public,encoding='ascii')
win_license=next(p for p in (Path(sys.base_prefix)/'LICENSE.txt',Path(sysconfig.get_path('stdlib'))/'LICENSE.txt') if p.is_file()).read_bytes()
script='import pathlib,sys,sysconfig,json,base64;p=next(p for p in (pathlib.Path(sys.base_prefix)/"LICENSE.txt",pathlib.Path(sysconfig.get_path("stdlib"))/"LICENSE.txt") if p.is_file());print(json.dumps({"license":base64.b64encode(p.read_bytes()).decode()}))'
result=subprocess.run(['docker','run','--rm','--network','none','--read-only','pslc-cpu-qa:0.18.0','python','-c',script],capture_output=True,text=True,timeout=30);assert result.returncode==0,'Linux license unavailable'
linux_license=base64.b64decode(json.loads(result.stdout)['license'])
dist=md.distribution('pyinstaller');copying=next(Path(dist.locate_file(f)) for f in dist.files if f.name=='COPYING.txt').read_bytes()
rows=[]
for platform,license in (('windows-x64',win_license),('linux-x64',linux_license)):
 root=ROOT/'artifacts/native/0.8.0'/platform/'pslc-agent';binary=root/('pslc-agent.exe' if platform.startswith('windows') else 'pslc-agent');before=hashlib.sha256(binary.read_bytes()).hexdigest()
 for name,data in {'licenses/CPython/LICENSE.txt':license,'licenses/PyInstaller/COPYING.txt':copying}.items():
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 inventory=root/'dependency-inventory.json';value=json.loads(inventory.read_text());value['runtime_notices']=['licenses/CPython/LICENSE.txt','licenses/PyInstaller/COPYING.txt'];inventory.write_text(json.dumps(value,indent=2),encoding='utf-8')
 sign(root,'0.8.0',platform,key);manifest,digest=verify(root,pub,platform)
 assert hashlib.sha256(binary.read_bytes()).hexdigest()==before
 rows.append({'platform':platform,'binary_sha256':before,'manifest_sha256':digest,'native_version':'0.8.0','binary_unchanged':True,'runtime_notices':value['runtime_notices'],'trust':'QA-only'})
(ROOT/'docs/qa/local-model/native-notices.json').write_text(json.dumps(rows,indent=2),encoding='utf-8');print(json.dumps({'native_bundles_verified':2,'executables_unchanged':True,'notices_added':4,'trust':'QA-only'}))
