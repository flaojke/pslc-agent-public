"""Local-only configuration and cross-platform one-identity lock."""
from contextlib import contextmanager
from pathlib import Path
import json,os,ssl
from pydantic import BaseModel,ConfigDict,Field
from backend.integrations import validate_endpoint

class AgentConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    server:str
    state:str
    ca_file:str=''
    commands_enabled:bool=False
    installations_enabled:bool=False
    diagnostics_enabled:bool=False
    def checked(self):
        validate_endpoint(self.server)
        if not Path(self.state).is_absolute():raise ValueError('absolute_state_required')
        if self.ca_file and not Path(self.ca_file).is_absolute():raise ValueError('absolute_ca_required')
        return self

def load_config(path):
    p=Path(path)
    if p.is_symlink() or p.stat().st_size>16384:raise ValueError('invalid_agent_config')
    return AgentConfig.model_validate_json(p.read_bytes(),strict=True).checked()

def tls_context(ca_file=''):
    return ssl.create_default_context(cafile=ca_file or None)

@contextmanager
def instance_lock(state):
    path=Path(str(Path(state).resolve())+'.lock');path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise RuntimeError('invalid_identity_lock')
    handle=path.open('a+b');handle.seek(0)
    if path.stat().st_size==0:handle.write(b'0');handle.flush()
    handle.seek(0)
    try:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise RuntimeError('identity_already_running') from None
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    finally:handle.close()
