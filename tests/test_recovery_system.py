"""Provider codec/control fixtures; never changes a real service or registry."""
import os,sys,types
import pytest
from agent import recovery_system as system
from agent.recovery_files import RecoveryError
def test_service_runtime_fixture_and_stable_states(monkeypatch):
 calls=[];state=['running']
 if os.name=='nt':
  def win(name,action=None):
   calls.append((name,action))
   if action:state[0]=action
   return {'state':state[0]}
  monkeypatch.setattr(system,'_win_service',win)
 else:
  monkeypatch.setattr(os,'geteuid',lambda:0)
  def run(argv,**kw):
   calls.append(argv)
   if argv[1] in ('start','stop'):state[0]='running' if argv[1]=='start' else 'stopped';return types.SimpleNamespace(returncode=0,stdout=b'')
   return types.SimpleNamespace(returncode=0,stdout=b'LoadState=loaded\nActiveState='+('active' if state[0]=='running' else 'inactive').encode()+b'\n')
  monkeypatch.setattr(system.subprocess,'run',run)
 r={'type':'service_runtime','name':'fixture.service'}
 before=system.service(r);assert before=={'state':'running'}
 assert system.service(r,'stopped')=={'state':'stopped'}
 assert system.service(r,before['state'])==before and calls
@pytest.mark.skipif(os.name!='nt',reason='Windows registry codec with a fake winreg module')
def test_registry_single_value_and_absent_key_restoration(monkeypatch):
 values={'Software':{},'Software\\Fixture':{'value':(b'baseline',3)}};root=object()
 class Handle:
  def __init__(self,key):self.key=key
  def __enter__(self):return self
  def __exit__(self,*args):pass
 fake=types.SimpleNamespace(HKEY_LOCAL_MACHINE=root,HKEY_CURRENT_USER=root,KEY_READ=1,KEY_WRITE=2,KEY_CREATE_SUB_KEY=4,REG_NONE=0,REG_SZ=1,REG_EXPAND_SZ=2,REG_BINARY=3,REG_DWORD=4,REG_MULTI_SZ=7,REG_QWORD=11)
 def opened(hive,key,*args):
  if key not in values:raise FileNotFoundError()
  return Handle(key)
 fake.OpenKey=opened;fake.CloseKey=lambda _:None
 fake.QueryInfoKey=lambda h:(sum(k.startswith(h.key+'\\') for k in values),len(values[h.key]),0)
 def query(h,name):
  if name not in values[h.key]:raise FileNotFoundError()
  return values[h.key][name]
 fake.QueryValueEx=query
 def create(hive,key,*args):values.setdefault(key,{});return Handle(key)
 fake.CreateKeyEx=create
 fake.SetValueEx=lambda h,name,reserved,kind,data:values[h.key].__setitem__(name,(data,kind))
 def delete(h,name):values[h.key].pop(name,None)
 fake.DeleteValue=delete;fake.DeleteKey=lambda hive,key:values.pop(key)
 monkeypatch.setitem(sys.modules,'winreg',fake)
 monkeypatch.setattr(system,'_registry_value_size',lambda h,name:len(query(h,name)[0]) if name in values[h.key] else None)
 resource={'type':'registry_value','hive':'HKCU','key':'Software\\Fixture','name':'value'}
 old=system.snapshot_registry(resource);values['Software\\Fixture']['value']=(b'changed',3);system.restore_registry(resource,old);assert system.snapshot_registry(resource)==old
 absent={**resource,'key':'Software\\NewFixture'};old=system.snapshot_registry(absent);values[absent['key']]={'value':('new',1)}
 system.restore_registry(absent,old);assert system.snapshot_registry(absent)==old and absent['key'] not in values
 resource={**resource,'key':'Software\\NewFixture'};old=system.snapshot_registry(resource);values[resource['key']]={'value':('new',1),'foreign':('keep',1)}
 with pytest.raises(RecoveryError,match='recovery_registry_conflict'):system.restore_registry(resource,old)
 assert values[resource['key']]['foreign']==('keep',1)
