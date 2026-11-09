from recovery_fixture import paired_fixture
from pathlib import Path
from threading import Event,Thread
from datetime import datetime,timezone,timedelta
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import base64,hashlib,json,ssl,sys
import httpx,pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.asymmetric import ed25519,rsa
from agent.delivery import sign,verify,stage,activate,active,host_platform,canonical
from agent.runtime import instance_lock,load_config,tls_context
from agent.__main__ import run
from agent.executor import execute

def bundle(tmp_path,version='0.7.0'):
 root=tmp_path/'bundle';root.mkdir(exist_ok=True);(root/'pslc-agent').write_bytes(b'public-binary-fixture')
 key=ed25519.Ed25519PrivateKey.generate();private=tmp_path/'private.pem';public=tmp_path/'public.pem'
 private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
 digest=sign(root,version,host_platform(),private);return root,private,public,digest

@pytest.mark.parametrize('mode',['file','extra','missing','signature','manifest','nested-manifest','wrong-key'])
def test_signed_distribution_rejects_tampering(tmp_path,mode):
 root,key,public,digest=bundle(tmp_path);assert verify(root,public)[1]==digest
 if mode=='file':(root/'pslc-agent').write_bytes(b'changed')
 elif mode=='extra':(root/'extra').write_bytes(b'changed')
 elif mode=='missing':(root/'pslc-agent').unlink()
 elif mode=='signature':(root/'manifest.sig').write_bytes(base64.b64encode(b'0'*64))
 elif mode=='manifest':(root/'manifest.json').write_text('{}')
 elif mode=='nested-manifest':(root/'nested').mkdir();(root/'nested/manifest.json').write_text('hidden')
 else:public.write_bytes(ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
 with pytest.raises(Exception):verify(root,public)

@pytest.mark.parametrize('path',['../outside','/absolute','C:/outside','nested/../../escape','a\\b'])
def test_signed_path_escape_rejected_even_with_valid_key(tmp_path,path):
 root,key,public,digest=bundle(tmp_path);value=json.loads((root/'manifest.json').read_bytes());value['files'][0]['path']=path
 private=serialization.load_pem_private_key(key.read_bytes(),None);payload=canonical(value);(root/'manifest.json').write_bytes(payload);(root/'manifest.sig').write_bytes(base64.b64encode(private.sign(payload)))
 with pytest.raises(Exception):verify(root,public)

def test_stage_activation_lock_expected_digest_downgrade_preserves_identity(tmp_path):
 root,key,public,digest=bundle(tmp_path);release=tmp_path/'release';state=tmp_path/'identity.json';state.write_text('identity-untouched')
 folder,staged=stage(root,release,public);assert staged==digest
 with pytest.raises(ValueError):activate(release,'0.7.0',public,'0'*64,state)
 with instance_lock(state):
  with pytest.raises(RuntimeError):activate(release,'0.7.0',public,digest,state)
 activate(release,'0.7.0',public,digest,state);assert active(release)['digest']==digest and state.read_text()=='identity-untouched'
 sign(root,'0.6.0',host_platform(),key);older,od=stage(root,release,public)
 with pytest.raises(ValueError):activate(release,'0.6.0',public,od,state)
 assert active(release)['version']=='0.7.0'

def test_strict_config_defaults_and_frozen_python_runtime(tmp_path,monkeypatch):
 path=tmp_path/'agent.json';path.write_text(json.dumps({'server':'https://example.invalid','state':str(tmp_path/'identity')}));conf=load_config(path)
 assert not conf.commands_enabled and not conf.installations_enabled and not conf.diagnostics_enabled
 path.write_text(json.dumps({'server':'http://remote.invalid','state':'relative'}))
 with pytest.raises(Exception):load_config(path)
 monkeypatch.setattr(sys,'frozen',True,raising=False);monkeypatch.setattr('shutil.which',lambda name:None)
 payload,context=paired_fixture({'kind':'command','runtime':'python','code':'print(1)','timeout_seconds':5},tmp_path)
 assert execute(payload,True,str(tmp_path),task_context=context)['outcome']=='prerequisite_missing'

def tls_server(tmp_path):
 now=datetime.now(timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048);name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
 cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256())
 certpath=tmp_path/'ca.pem';keypath=tmp_path/'key.pem';certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM));keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 calls=[]
 class Handler(BaseHTTPRequestHandler):
  def do_POST(self):
   calls.append(self.path);data=b'{"job":null}';self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
  def log_message(self,*args):pass
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certpath,keypath);server.socket=context.wrap_socket(server.socket,server_side=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start();return server,certpath,calls

def test_actual_tls_success_bad_trust_hostname_and_stop(tmp_path):
 server,ca,calls=tls_server(tmp_path);port=server.server_port;state=tmp_path/'identity.json';state.write_text(json.dumps({'device_id':'fixture-node','credential':'public-fixture-credential'}))
 try:
  run('https://localhost:'+str(port),state,once=True,ca_file=str(ca));assert len(calls)==1
  with pytest.raises(RuntimeError):run('https://localhost:'+str(port),state,once=True)
  with pytest.raises(RuntimeError):run('https://127.0.0.1:'+str(port),state,once=True,ca_file=str(ca))
  assert len(calls)==1
  stop=Event();stop.set();run('https://localhost:'+str(port),state,ca_file=str(ca),stop_event=stop);assert len(calls)==1
 finally:server.shutdown();server.server_close()
