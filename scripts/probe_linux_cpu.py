"""Networkless Linux CPU proof: real pinned weights/runtime + owned watchdog, no private host state."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,secrets,shutil,socket,subprocess,time,urllib.request,urllib.error,psutil,platform
runtime=Path('/tmp/pslc-llama');shutil.copytree('/runtime/llama-b12110',runtime);exe=runtime/'llama-server';exe.chmod(0o755)
weights=Path('/models/Qwen3.5-4B-Q4_K_M.gguf')
with weights.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
key=secrets.token_urlsafe(32);url='http://127.0.0.1:'+str(port)+'/v1';owner=psutil.Process()
args=[str(exe),'-m',str(weights),'--host','127.0.0.1','--port',str(port),'--alias','qwen3.5-4b-q4','-ngl','0','-c','8192','-np','1','-t','6','-tb','6','--cache-ram','0','--no-context-shift','--chat-template-kwargs','{"enable_thinking":false}','--log-disable','--no-webui','--timeout','120']
env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','HOME')};env.update(LLAMA_API_KEY=key,LD_LIBRARY_PATH=str(runtime),PYTHONIOENCODING='utf-8')
p=subprocess.Popen(['python','-m','backend.runtime_host','--parent',str(owner.pid),'--parent-created',str(owner.create_time()),*args],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
unrelated=subprocess.Popen(['python','-c','import time;time.sleep(180)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);children=[]
def call(path,body=None,auth=True):
 r=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers={**({'Authorization':'Bearer '+key} if auth else {}),'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=125) as f:return json.loads(f.read())
try:
 start=time.monotonic()
 for _ in range(450):
  assert p.poll() is None,'Owned Linux runtime failed'
  try:
   if call('/models')['data']:break
  except (OSError,urllib.error.URLError):time.sleep(.2)
 else:raise AssertionError('Linux runtime readiness timeout')
 load=time.monotonic()-start
 try:call('/models',auth=False);raise AssertionError('Unauthenticated request unexpectedly accepted')
 except urllib.error.HTTPError as e:assert e.code==401
 began=time.monotonic();answer=call('/chat/completions',{'model':'qwen3.5-4b-q4','messages':[{'role':'user','content':'Return only JSON with status equal to ok.'}],'temperature':0,'max_tokens':64,'response_format':{'type':'json_object'},'chat_template_kwargs':{'enable_thinking':False}})
 value=json.loads(answer['choices'][0]['message']['content']);assert value.get('status')=='ok'
 elapsed=time.monotonic()-began;children=[(x.pid,x.create_time()) for x in psutil.Process(p.pid).children(recursive=True)]
 p.terminate();p.wait(timeout=20)
 for pid,birth in children:
  try:assert not psutil.Process(pid).is_running() or psutil.Process(pid).create_time()!=birth
  except psutil.NoSuchProcess:pass
 assert unrelated.poll() is None
 print(json.dumps({'scope':'Linux Docker, network none, read-only host mounts','app_code':'0.18.0','runtime':'b12110','model':'qwen3.5-4b-q4','weights_sha256_verified':True,'real_model_json':True,'cpu_only':True,'context_tokens':8192,'threads':6,'load_seconds':round(load,3),'json_seconds':round(elapsed,3),'unauthorized_rejected':True,'owned_child_stopped':True,'unrelated_process_preserved':True,'libc':platform.libc_ver(),'raw_context_saved':False}),flush=True)
finally:
 if p.poll() is None:p.terminate();p.wait(timeout=20)
 if unrelated.poll() is None:unrelated.terminate();unrelated.wait(timeout=5)
