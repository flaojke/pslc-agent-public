"""Kill only our disposable Lite API and verify watchdog/JobObject, preserving a separate owned process."""
from pathlib import Path
import json,time,subprocess,sys,psutil,httpx
ROOT=Path(__file__).resolve().parents[1];info=json.loads((ROOT/'.local/clean-distributions.json').read_text());entry=info['variants']['Lite'];fixture=psutil.Process(entry['server_pid']);command=fixture.cmdline()
assert command[-1]=='Lite' and str(ROOT/'scripts/serve_clean_distribution.py') in command and Path(fixture.cwd()).resolve()==Path(entry['root']).resolve(),'Refuse non-fixture process'
native=[]
for p in fixture.children(recursive=True):
 try:
  if p.name().lower()=='llama-server.exe' and Path(p.exe()).resolve().is_relative_to((Path(entry['root'])/'runtime').resolve()):native.append((p.pid,p.create_time()))
 except psutil.Error:pass
assert native,'Actual Lite model process not observed'
foreign=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
try:
 start=time.monotonic();fixture.kill()
 for _ in range(100):
  alive=[]
  for pid,birth in native:
   try:
    p=psutil.Process(pid)
    if p.is_running() and abs(p.create_time()-birth)<.01:alive.append(pid)
   except psutil.NoSuchProcess:pass
  if not alive:break
  time.sleep(.2)
 assert not alive,'Owned native did not stop after API crash'
 assert foreign.poll() is None
 with httpx.Client(timeout=5) as c:assert c.get('http://127.0.0.1:8787/healthz').json()['version']=='0.17.0'
 value={'scope':'disposable clean Lite API crash, production API preserved','version':'0.18.0','actual_native_children':len(native),'api_crash_terminated_owned_runtime':True,'shutdown_seconds':round(time.monotonic()-start,3),'unrelated_process_preserved':True,'original_local_api_preserved':True,'raw_context_saved':False}
 (ROOT/'docs/qa/local-model/windows-crash-watchdog.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps(value))
finally:
 if foreign.poll() is None:foreign.terminate();foreign.wait(timeout=5)
