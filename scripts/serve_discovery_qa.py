"""Synthetic network only; actual signed native exports. No LAN probing or node installation."""
from pathlib import Path
import asyncio,json,socket,tempfile
import uvicorn
from backend.app import create_app
from backend.config import ROOT,Settings
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
url='http://127.0.0.1:'+str(port);base=Path(tempfile.mkdtemp(prefix='network-browser-',dir=ROOT/'.local'))
app=create_app(Settings('network-browser-public-fixture-token','sqlite:///'+(base/'qa.sqlite3').as_posix(),auth_mode='accounts',public_origin=url,knowledge_enabled=False))
meta={'probe_calls':0,'real_lan_scan':False,'native_exports':'actual signed packages; QA-only trust'}
app.state.discovery.networks=lambda:[{'interface':'QA synthetic network','address':'10.8.0.10','cidr':'10.8.0.0/24','addresses':256}]
async def probe(address,port):
 meta['probe_calls']+=1;await asyncio.sleep(.08)
 return 'open' if address.endswith('.1') and port==22 else 'refused' if address.endswith('.2') and port==445 else 'silent'
app.state.discovery.probe=probe
@app.get('/qa/status')
def status():return meta
route=app.router.routes.pop();app.router.routes.insert(0,route)
(ROOT/'.local/network-browser-qa.json').write_text(json.dumps({'url':url,'directory':str(base),'scope':'synthetic network / actual native exports'}),encoding='utf-8')
uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning',proxy_headers=False)
