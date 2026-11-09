"""Explicit private-network discovery. No credentials, banners, enrollment or commands."""
import asyncio,copy,ipaddress,secrets,socket,threading,time
from concurrent.futures import ThreadPoolExecutor
import psutil
from pydantic import Field
from .schemas import Strict
from .store import Problem,Volatile
PORTS=(22,135,139,445,3389,5985,5986,8787)
PRIVATE=tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
TTL=900

class ScanRequest(Strict):
    cidr:str=Field(min_length=9,max_length=43)

def private(network):
    return network.version==4 and any(network.subnet_of(n) for n in PRIVATE)

def local_networks():
    result=[];stats=psutil.net_if_stats()
    for name,addresses in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup:continue
        for a in addresses:
            if a.family!=socket.AF_INET or not a.netmask:continue
            try:n=ipaddress.ip_network(a.address+'/'+a.netmask,strict=False)
            except ValueError:continue
            if private(n):result.append({'interface':name[:128],'address':a.address,'cidr':str(n),'addresses':n.num_addresses})
    return result

async def tcp_probe(address,port):
    writer=None
    try:
        _,writer=await asyncio.wait_for(asyncio.open_connection(address,port),.5)
        return 'open'
    except ConnectionRefusedError:return 'refused'
    except (OSError,asyncio.TimeoutError):return 'silent'
    finally:
        if writer:
            writer.close()
            try:await asyncio.wait_for(writer.wait_closed(),.5)
            except (OSError,asyncio.TimeoutError):pass

class Discovery:
    def __init__(self,nodes,networks=local_networks,probe=tcp_probe,budget=90):
        self.nodes=nodes;self.networks=networks;self.probe=probe;self.budget=budget
        self.rows=Volatile(16,4*1024*1024);self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pslc-discovery')
        self.active=None;self.cancel_event=None;self.closed=False
    def info(self):
        self.nodes.allowed()
        with self.lock:
            self.rows.purge();recent=[{k:r[k] for k in ('id','created')} for _,r in self.rows.data.values() if r['owner_id']==self.nodes.ops.p.id]
        recent.sort(key=lambda r:r['created'],reverse=True)
        return {'recent':recent,'networks':self.networks(),'ports':list(PORTS),'maximum_addresses':1024,'timeout_seconds':self.budget,
            'method':'TCP open/refused response; no OS fingerprinting','complete_inventory_guaranteed':False}
    def check_owner(self,owner):
        principal=self.nodes.ops.auth.known(owner)
        if 'platform_admin' not in principal.roles or '*' not in principal.device_ids:raise Problem('operator_forbidden',403)
    def admit(self,cidr):
        try:
            network=ipaddress.ip_network(cidr,strict=True)
            known=[ipaddress.ip_network(row['cidr'],strict=False) for row in self.networks()]
        except ValueError:raise Problem('invalid_scan_network',422) from None
        if not private(network) or network.num_addresses>1024 or not any(network.subnet_of(n) for n in known if n.version==4):raise Problem('scan_network_not_allowed',422)
        return network
    def start(self,data):
        self.nodes.allowed();network=self.admit(data.cidr);owner=self.nodes.ops.p.id
        with self.lock:
            self.rows.purge()
            if self.active:raise Problem('scan_busy',409)
            sid=secrets.token_hex(16);now=time.time();stop=threading.Event()
            row={'id':sid,'owner_id':owner,'cidr':str(network),'state':'queued','created':now,'expires':now+TTL,
                'total':sum(1 for _ in network.hosts()),'scanned':0,'candidates':[],'ports':list(PORTS),
                'complete_inventory_guaranteed':False,'managed_devices_created':0}
            self.rows.put(sid,row,TTL);self.active=sid;self.cancel_event=stop
            try:self.pool.submit(self.run,sid,owner,network,stop)
            except RuntimeError:self.rows.drop(sid);self.active=None;self.cancel_event=None;raise Problem('scan_unavailable',503) from None
        with self.nodes.store.tx() as c:self.nodes.store.event(c,'network_scan_requested',actor_id=owner)
        return self.get(sid)
    def get(self,sid):
        self.nodes.allowed();owner=self.nodes.ops.p.id
        with self.lock:
            row=self.rows.get(sid)
            if not row or row['owner_id']!=owner:raise Problem('not_found',404)
            return copy.deepcopy(row)
    def cancel(self,sid):
        self.get(sid)
        with self.lock:
            if self.active==sid and self.cancel_event:self.cancel_event.set()
        return self.get(sid)
    def run(self,sid,owner,network,stop):
        def update_row(**values):
            with self.lock:
                row=self.rows.get(sid)
                if row:row.update(values);self.rows.put(sid,row,max(1,row['expires']-time.time()))
        async def scan():
            semaphore=asyncio.Semaphore(64);addresses=iter(network.hosts())
            async def one(address,port):
                async with semaphore:
                    if stop.is_set() or self.closed:return 'silent'
                    return await self.probe(str(address),port)
            async def worker():
                while not stop.is_set() and not self.closed:
                    try:address=next(addresses)
                    except StopIteration:return
                    self.check_owner(owner)
                    replies=await asyncio.gather(*(one(address,p) for p in PORTS))
                    with self.lock:
                        row=self.rows.get(sid)
                        if not row:return
                        row['scanned']+=1
                        if any(x in ('open','refused') for x in replies):
                            row['candidates'].append({'address':str(address),'open_ports':[p for p,r in zip(PORTS,replies) if r=='open'],
                                'method':'tcp_open' if 'open' in replies else 'tcp_refused','os_family':None,'device_kind':'unknown','managed':False})
                        self.rows.put(sid,row,max(1,row['expires']-time.time()))
            update_row(state='running')
            try:
                async with asyncio.timeout(self.budget):await asyncio.gather(*(worker() for _ in range(16)))
                update_row(state='cancelled' if stop.is_set() or self.closed else 'completed')
            except TimeoutError:update_row(state='partial_timeout')
        try:
            self.check_owner(owner);asyncio.run(scan())
        except Problem:update_row(state='access_revoked')
        except Exception:update_row(state='failed')
        finally:
            with self.lock:
                if self.active==sid:self.active=None;self.cancel_event=None
    def purge(self):
        with self.lock:self.rows.purge()
    def close(self):
        self.closed=True
        with self.lock:
            if self.cancel_event:self.cancel_event.set()
        self.pool.shutdown(wait=False,cancel_futures=True)
