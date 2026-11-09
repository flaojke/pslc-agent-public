"""Explicit pilot transport boundary; no forwarded-header trust or payload logging."""
from pathlib import Path
from urllib.parse import urlsplit
import asyncio,ipaddress,json,re,time
PRIVATE=[ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7')]
LIMIT=262144

def validate_transport(s):
 try:bind=ipaddress.ip_address(s.api_host)
 except ValueError:raise ValueError('numeric_bind_required') from None
 if bool(s.tls_cert_file)!=bool(s.tls_key_file):raise ValueError('complete_tls_configuration_required')
 if not s.pilot_network_admitted:
  if not bind.is_loopback:raise ValueError('pilot_network_not_admitted')
  return
 if not (bind.is_loopback or bind.is_unspecified or any(bind.version==net.version and bind in net for net in PRIVATE)):raise ValueError('private_bind_required')
 if s.auth_mode=='accounts':
  if urlsplit(s.public_origin).scheme!='https':raise ValueError('pilot_https_origin_required')
 elif s.auth_mode=='oidc':
  if urlsplit(s.oidc_issuer).scheme!='https' or urlsplit(s.oidc_redirect_uri).scheme!='https':raise ValueError('pilot_https_oidc_required')
 else:raise ValueError('pilot_account_auth_required')
 if not all(re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}',x or '') for x in (s.pilot_it_reference,s.pilot_security_reference)):raise ValueError('pilot_admission_references_required')
 if not s.tls_cert_file or not Path(s.tls_cert_file).is_file() or not Path(s.tls_key_file).is_file():raise ValueError('pilot_tls_files_required')
 if not 1<=len(s.pilot_allowed_cidrs)<=16:raise ValueError('pilot_allowlist_required')
 for value in s.pilot_allowed_cidrs:
  try:net=ipaddress.ip_network(value,strict=True)
  except ValueError:raise ValueError('invalid_pilot_cidr') from None
  if net.num_addresses>65536 or not any(net.version==allowed.version and net.subnet_of(allowed) for allowed in PRIVATE):raise ValueError('bounded_private_cidr_required')

class TransportBoundary:
 def __init__(self,app,settings):
  self.app=app;self.settings=settings;self.active=0;self.networks=[ipaddress.ip_network(x) for x in settings.pilot_allowed_cidrs] if settings.pilot_network_admitted else []
 async def reject(self,send,code,status):
  body=json.dumps({'error':code}).encode()
  await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode()),(b'cache-control',b'no-store'),(b'connection',b'close')]})
  await send({'type':'http.response.body','body':body})
 async def __call__(self,scope,receive,send):
  if scope['type']!='http':return await self.app(scope,receive,send)
  host=(scope.get('client') or ('',0))[0]
  try:peer=ipaddress.ip_address(host);local=peer.is_loopback
  except ValueError:peer=None;local=host=='testclient'
  if not local and not (self.settings.pilot_network_admitted and scope.get('scheme')=='https' and peer and any(peer.version==net.version and peer in net for net in self.networks)):
   return await self.reject(send,'transport_source_forbidden',403)
  if self.active>=64:return await self.reject(send,'request_capacity_reached',503)
  self.active+=1
  try:
   try:
    lengths=[value for key,value in scope.get('headers',[]) if key.lower()==b'content-length']
    if lengths and (len(lengths)!=1 or int(lengths[0])<0 or int(lengths[0])>LIMIT):return await self.reject(send,'request_body_limit',413)
   except ValueError:return await self.reject(send,'invalid_request_length',400)
   chunks=[];size=0;started=time.monotonic()
   while True:
    remaining=15-(time.monotonic()-started)
    if remaining<=0:return await self.reject(send,'request_body_timeout',408)
    try:message=await asyncio.wait_for(receive(),timeout=min(10,remaining))
    except asyncio.TimeoutError:return await self.reject(send,'request_body_timeout',408)
    if message['type']=='http.disconnect':return
    if message['type']!='http.request':continue
    chunk=message.get('body',b'');size+=len(chunk)
    if size>LIMIT:return await self.reject(send,'request_body_limit',413)
    chunks.append(chunk)
    if not message.get('more_body',False):break
   body=b''.join(chunks);consumed=False
   async def replay():
    nonlocal consumed
    if not consumed:
     consumed=True;return {'type':'http.request','body':body,'more_body':False}
    return await receive()
   await self.app(scope,replay,send)
  finally:self.active-=1
