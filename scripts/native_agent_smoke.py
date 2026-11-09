"""Actual native binary, isolated local health job, no commands or enrollment."""
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from threading import Thread
import hashlib,json,platform,subprocess,sys,tempfile
binary=Path(sys.argv[1]).resolve();payload={'kind':'health','device_id':'native-health-qa','timeout_seconds':30};seen=[]
job={'task_id':'native-health-task','lease_id':'native-public-lease-123456789','payload':payload,'plan_hash':hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()}
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  if self.path.endswith('/result'):
   data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));seen.append(data);value={'status':'accepted'}
  else:value={'job':job}
  body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);Thread(target=server.serve_forever,daemon=True).start()
with tempfile.TemporaryDirectory(prefix='pslc-native-') as temp:
 state=Path(temp)/'identity.json';state.write_text(json.dumps({'device_id':'native-health-qa','credential':'PUBLIC-QA-NATIVE-CREDENTIAL'}),encoding='utf-8')
 original=state.read_bytes()
 try:
  result=subprocess.run([str(binary),'--server','http://127.0.0.1:'+str(server.server_port),'--state',str(state),'--once'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
  assert result.returncode==0,'Native binary failed: '+result.stderr.decode(errors='replace')[:300]
  assert len(seen)==1 and seen[0]['outcome']=='ok' and all(isinstance(seen[0].get(k),(int,float)) for k in ('cpu_percent','memory_percent','disk_free_gb')),str(list(seen[0].keys()) if seen else [])
  assert state.read_bytes()==original
  summary={'os':platform.system(),'kernel':platform.release(),'nativeStandalone':True,'realHealth':True,'identityPreserved':True,'commands':False,'installation':False,'serviceInstalled':False,'resultFields':sorted(seen[0].keys())}
  print(json.dumps(summary))
 finally:server.shutdown();server.server_close()
