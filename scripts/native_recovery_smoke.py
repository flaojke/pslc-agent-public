"""Real standalone native forward/undo; only disposable config files and loopback fixture."""
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from threading import Thread
import json,os,platform,subprocess,sys,tempfile
from backend.recovery_types import make_program,fingerprint
binary=Path(sys.argv[1]).resolve();seen=[];current=[None]
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  if self.path.endswith('/result'):
   result=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));seen.append(result);body={'accepted':True}
  else:
   assert self.headers.get('X-PSLC-Agent-Protocol')=='1';body={'job':current[0]}
  data=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Thread(target=server.serve_forever,daemon=True).start()
with tempfile.TemporaryDirectory(prefix='pslc-native-undo-') as temporary:
 root=Path(temporary);config=root/'configuration';config.mkdir();file=config/'application.json';file.write_text('{"before":true}',encoding='utf-8')
 own=root/'identity';own.mkdir();state=own/'identity.json';state.write_text(json.dumps({'device_id':'native-recovery-qa','credential':'PUBLIC-RECOVERY-QA-CREDENTIAL'}),encoding='utf-8');identity=state.read_bytes()
 if os.name=='nt':
  runtime='powershell';quoted="'"+str(file).replace("'","''")+"'"
  code="[IO.File]::WriteAllText("+quoted+",'{\"after\":true}')"
  check="if ([IO.File]::ReadAllText("+quoted+") -eq '{\"after\":true}') {Write-Output 'FORWARD-CHECKED'}"
 else:
  runtime='python';code='from pathlib import Path;Path('+repr(str(file))+').write_text(\'{"after":true}\')';check='print("FORWARD-CHECKED")'
 resource=[{'type':'configuration_file','path':str(file),'metadata':'config-metadata-v1'}];program=make_program(resource,'native-recovery-qa','native-forward-task')
 payload={'kind':'command','device_id':'native-recovery-qa','owner_id':'native-owner-qa','task_id':'native-forward-task','runtime':runtime,'code':code,'timeout_seconds':20,'verification':{'runtime':runtime,'code':check,'expected_output':'FORWARD-CHECKED','timeout_seconds':10},'recovery':{'resources':resource},'anti_program':program}
 original_hash=fingerprint(payload)
 def run(payload,task,remove_runtimes=False):
  current[0]={'task_id':task,'lease_id':'PUBLIC-NATIVE-LEASE-123456789','plan_hash':fingerprint(payload),'payload':payload}
  env=dict(os.environ)
  if remove_runtimes:env['PATH']=str(Path(env.get('SystemRoot','C:/Windows'))/'System32') if os.name=='nt' else ''
  r=subprocess.run([str(binary),'--server','http://127.0.0.1:'+str(server.server_port),'--state',str(state),'--allow-commands','--once'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
  assert r.returncode==0,'Native process failed'
  assert state.read_bytes()==identity,'Identity changed'
  return seen[-1]
 try:
  result=run(payload,'native-forward-task')
  assert result['outcome']=='process_succeeded' and result['recovery']['receipt']['state']=='ready',{'outcome':result['outcome'],'fields':list(result)}
  assert file.read_text(encoding='utf-8')=='{"after":true}'
  undo={'kind':'rollback','device_id':'native-recovery-qa','owner_id':'native-owner-qa','original_kind':'command','original_task_id':'native-forward-task','original_plan_hash':original_hash,'anti_program':program,'capsule_id':result['recovery']['receipt']['capsule_id'],'timeout_seconds':180}
  restored=run(undo,'native-undo-task',True)
  assert restored['outcome']=='restored' and restored['recovery']['receipt']['restored_count']==1,{'outcome':restored['outcome']}
  assert file.read_text(encoding='utf-8')=='{"before":true}'
  assert run(undo,'native-undo-again',True)['outcome']=='restored'
  print(json.dumps({'scope':'standalone native temporary configuration only','os':platform.system(),'native_version':'0.8.0','agent_protocol':1,'forward_verified':True,'restored_verified':True,'idempotent_manual_restore':True,'undo_without_script_runtimes':True,'identity_preserved':True,'managed_host_mutation':False,'service_installed':False}))
 finally:server.shutdown();server.server_close()
