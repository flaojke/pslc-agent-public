"""Verify/unpack actual exported ZIPs and probe native health against disposable loopback fixtures."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,subprocess,sys,zipfile
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'.local/network-browser-qa.json').read_text(encoding='utf-8'));base=Path(cfg['directory'])
assert base.resolve().is_relative_to((ROOT/'.local').resolve())
out=base/('unpacked-'+datetime.now().strftime('%H%M%S'));out.mkdir()
unpacked={}
for label in ('Windows','Linux'):
 source=base/(label+'.zip');target=out/label;target.mkdir()
 with zipfile.ZipFile(source) as z:
  sums={}
  for line in z.read('SHA256SUMS').decode().splitlines():
   sha,name=line.split('  ',1);sums[name]=sha
  assert set(z.namelist())==set(sums)|{'SHA256SUMS'}
  for item in z.infolist():
   name=PurePosixPath(item.filename);assert not name.is_absolute() and '..' not in name.parts and '\\' not in item.filename and ':' not in item.filename
   assert (item.external_attr>>16)&0o170000!=0o120000
   h=hashlib.sha256()
   with z.open(item) as f:
    for block in iter(lambda:f.read(1048576),b''):h.update(block)
   if item.filename in sums:assert h.hexdigest()==sums[item.filename]
   z.extract(item,target)
 unpacked[label]=str(target)
windows=Path(unpacked['Windows']);script=ROOT/'scripts/native_agent_smoke.py'
r=subprocess.run([sys.executable,str(script),str(windows/'native/pslc-agent.exe')],capture_output=True,text=True,timeout=60)
assert r.returncode==0,'Windows native fixture health failed'
result={'windows':json.loads(r.stdout),'zipFilesVerified':2,'realLANScan':False,'existingIdentityUsed':False,'servicesInstalled':False,'unpacked':unpacked}
report=ROOT/'docs/qa/network-discovery-and-agent-export/native-export.json';report.write_text(json.dumps(result,indent=2),encoding='utf-8')
(ROOT/'.local/native-export-unpacked.json').write_text(json.dumps(unpacked),encoding='utf-8')
print(json.dumps({'windowsNativeHealth':True,'ZIPsVerifiedAndUnpacked':2,'hostMutation':False}))
