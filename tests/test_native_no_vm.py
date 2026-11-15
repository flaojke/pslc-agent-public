"""Withdrawn compatibility CLI must refuse without process or state access."""
from pathlib import Path
import builtins,json,os,socket,subprocess,sys
import pytest

@pytest.mark.parametrize('arguments',[[],['prepare'],['preview','--fresh-state'],['start','--import-state','--root','PUBLIC-STALE-PATH']])
def test_vm_stub_refuses_before_process_or_file_io(monkeypatch,capsys,arguments):
 script=Path(__file__).resolve().parents[1]/'scripts/linux_vm.py'
 code=compile(script.read_text(encoding='utf-8'),str(script),'exec')
 effects=[]
 def forbidden(*args,**kwargs):
  effects.append(True)
  raise AssertionError('compatibility refusal attempted external effects')
 with monkeypatch.context() as scoped:
  scoped.setattr(sys,'argv',['linux_vm.py',*arguments])
  for name in ('open',):
   scoped.setattr(builtins,name,forbidden)
  for name in ('open','stat','getenv'):
   scoped.setattr(os,name,forbidden)
  for name in ('Popen','run','call','check_call','check_output'):
   scoped.setattr(subprocess,name,forbidden)
  for name in ('socket','create_connection','create_server'):
   scoped.setattr(socket,name,forbidden)
  namespace={'__name__':'isolated_vm_compatibility'}
  exec(code,namespace)
  assert namespace['main']()==2
 assert not effects
 result=json.loads(capsys.readouterr().out)
 assert result['error']=='vm_deployment_withdrawn'
 assert 'native startup' in result['action'] and 'PUBLIC-STALE-PATH' not in result['action']
