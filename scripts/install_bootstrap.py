"""Owned, manifest-bound first-install recovery. Standard library only; no environment deletion."""
from pathlib import Path, PurePosixPath
from contextlib import contextmanager
import argparse,hashlib,json,os,re,secrets,stat,struct,subprocess,sys,sysconfig,time

RECEIPT='.pslc-install.json'
LOCK='.pslc-install.lock'
MARKER='.pslc-owned.json'
PHASES=('preparing','venv_ready','dependencies_ready','completed')
class InstallError(Exception):pass

def require_python():
    tag=sysconfig.get_platform().lower()
    if sys.version_info<(3,12) or struct.calcsize('P')!=8 or tag not in ('win-amd64','linux-x86_64','macosx-10.9-x86_64'):
        raise InstallError('python_312_x64_required')

def reparse(p):
    try:
        info=p.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)
    except FileNotFoundError:return False

def regular_chain(path):
    path=Path(os.path.abspath(path))
    for p in reversed((path,*path.parents)):
        if reparse(p):raise InstallError('reparse_path_refused')
    return path

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def runtime_model_files(root,expected):
    # Only the immutable manifest-declared catalog may grant exact runtime names.
    # The downloader currently emits final GGUF and .gguf.partial, no sidecars.
    catalog='config/local-models.json'
    if catalog not in expected:return {}
    try:
        p=regular_chain(root/catalog)
        if p.stat().st_size>2*1024*1024:raise InstallError('invalid_local_model_catalog')
        data=json.loads(p.read_text(encoding='utf-8'));rows=data['models']
        if not isinstance(rows,list) or not 1<=len(rows)<=100:raise ValueError()
        allowed={};ids=set()
        for row in rows:
            name=row['filename'];size=row['bytes'];digest=row['sha256'];mid=row['id']
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}\.gguf',name) or not isinstance(mid,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',mid) or mid in ids or type(size)!=int or not 0<size<=8*1024**3 or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError()
            final='models/'+name
            if final in allowed:raise ValueError()
            ids.add(mid);allowed[final]=(size,digest);allowed[final+'.partial']=(size,None)
        if data['default'] not in ids:raise ValueError()
        return allowed
    except (KeyError,TypeError,ValueError,OSError):raise InstallError('invalid_local_model_catalog') from None

def manifest(root,allow_runtime):
    p=regular_chain(root/'release-manifest.json')
    if not p.is_file() or p.stat().st_size>8*1024*1024:raise InstallError('invalid_release_manifest')
    try:data=json.loads(p.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise InstallError('invalid_release_manifest') from None
    if data.get('format')!='pslc-source-release-v1' or not isinstance(data.get('files'),list) or not 1<=len(data['files'])<=10000:
        raise InstallError('invalid_release_manifest')
    expected={};normalized=set()
    for row in data['files']:
        try:name=row['path'];pp=PurePosixPath(name);size=row['size'];digest=row['sha256']
        except (KeyError,TypeError):raise InstallError('invalid_release_manifest') from None
        if not isinstance(name,str) or pp.is_absolute() or '..' in pp.parts or '\\' in name or ':' in name or not pp.parts or name in (RECEIPT,LOCK,'release-manifest.json') or pp.parts[0] in ('.venv','.local') or '__pycache__' in pp.parts:
            raise InstallError('invalid_release_path')
        norm=os.path.normcase(name)
        if norm in normalized or type(size)!=int or not 0<=size<=8*1024**3 or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise InstallError('invalid_release_manifest')
        normalized.add(norm);expected[name]=row
        file=regular_chain(root/name)
        if not file.is_file() or file.stat().st_size!=size or sha(file)!=digest:raise InstallError('release_file_changed')
    runtime_models=runtime_model_files(root,expected) if allow_runtime else {}
    actual=set()
    for here,dirs,files in os.walk(root,followlinks=False):
        current=Path(here);keep=[]
        for name in dirs:
            p=current/name
            if reparse(p):raise InstallError('reparse_path_refused')
            rel=p.relative_to(root).parts
            if allow_runtime and (rel[0] in ('.venv','.local') or '__pycache__' in rel):continue
            keep.append(name)
        dirs[:]=keep
        for name in files:
            p=current/name
            if reparse(p):raise InstallError('reparse_path_refused')
            rel=p.relative_to(root).as_posix()
            if rel in (LOCK,'release-manifest.json'):continue
            if allow_runtime and (rel==RECEIPT or re.fullmatch(r'\.pslc-install\.pending-[a-f0-9]{32}\.json',rel)):continue
            if rel not in expected and rel in runtime_models:
                size,digest=runtime_models[rel]
                if not p.is_file() or p.stat().st_size>size or (digest and (p.stat().st_size!=size or sha(p)!=digest)):raise InstallError('runtime_model_file_changed')
                continue
            actual.add(rel)
    if actual!=set(expected):raise InstallError('release_extra_or_missing_files')
    return sha(root/'release-manifest.json')

def read_json(p):
    regular_chain(p)
    try:
        if not p.is_file() or p.stat().st_size>16384:raise InstallError('invalid_install_receipt')
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise InstallError('invalid_install_receipt') from None

def atomic_json(root,path,value):
    regular_chain(path)
    pending=root/('.pslc-install.pending-'+secrets.token_hex(16)+'.json')
    created=False
    try:
        with pending.open('x',encoding='utf-8') as f:
            created=True
            json.dump(value,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
        assert pending.parent==root and path.parent==root
        os.replace(pending,path)
    finally:
        if created and pending.exists():pending.unlink()

@contextmanager
def installation_lock(root):
    p=regular_chain(root/LOCK)
    with p.open('a+b') as f:
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise InstallError('installation_busy') from None
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def base_python():
    return os.path.normcase(str(Path(getattr(sys,'_base_executable',sys.executable)).resolve()))

def identity(root,digest):
    return {'format':'pslc-install-v1','installation_id':secrets.token_hex(16),'root':os.path.normcase(str(root)),'manifest_sha256':digest,'base_python':base_python(),'python_minor':list(sys.version_info[:2]),'phase':'preparing'}

def marker(receipt):
    return {k:receipt[k] for k in ('format','installation_id','root','manifest_sha256')}

def validate_receipt(root,receipt,digest):
    fields={'format','installation_id','root','manifest_sha256','base_python','python_minor','phase'}
    if not isinstance(receipt,dict) or set(receipt)!=fields or receipt.get('format')!='pslc-install-v1' or not isinstance(receipt.get('installation_id'),str) or not re.fullmatch('[a-f0-9]{32}',receipt['installation_id']) or receipt.get('phase') not in PHASES:raise InstallError('invalid_install_receipt')
    if receipt['manifest_sha256']!=digest:raise InstallError('release_manifest_changed')
    if receipt['root']!=os.path.normcase(str(root)):raise InstallError('installation_root_changed')
    if receipt['base_python']!=base_python() or receipt['python_minor']!=list(sys.version_info[:2]):raise InstallError('installation_python_changed')

def verify_bound_manifest(root,receipt):
    p=regular_chain(root/'release-manifest.json')
    if not p.is_file() or sha(p)!=receipt['manifest_sha256']:raise InstallError('release_manifest_changed')
    if manifest(root,True)!=receipt['manifest_sha256']:raise InstallError('release_manifest_changed')

def owned_environment(root,receipt):
    env=regular_chain(root/'.venv');m=regular_chain(env/MARKER)
    if not env.exists():
        if receipt['phase']!='preparing':raise InstallError('owned_environment_missing')
        env.mkdir()
    if not env.is_dir():raise InstallError('foreign_environment_refused')
    if not m.exists():
        if receipt['phase']!='preparing' or list(env.iterdir()):raise InstallError('foreign_environment_refused')
        with m.open('x',encoding='utf-8') as f:json.dump(marker(receipt),f,separators=(',',':'))
    if read_json(m)!=marker(receipt):raise InstallError('foreign_environment_refused')
    # Reject redirections before running pip or setup; do not remove any content.
    for here,dirs,files in os.walk(env,followlinks=False):
        for name in dirs+files:
            if reparse(Path(here)/name):raise InstallError('reparse_environment_refused')
    return env

def process_env():
    env=dict(os.environ)
    for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PIP_TARGET','PIP_PREFIX','PIP_USER'):env.pop(key,None)
    env.update(PYTHONDONTWRITEBYTECODE='1',PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_NO_INPUT='1')
    return env

def own_process_tree():
    """Keep the noninherited job handle alive until OS process teardown.

    Joining before creating children avoids the Popen/assign race. Closing this
    handle inside Python would terminate this process, so the OS owns cleanup.
    """
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',wintypes.DWORD),('Min',ctypes.c_size_t),('Max',ctypes.c_size_t),('Active',wintypes.DWORD),('Affinity',ctypes.c_size_t),('Priority',wintypes.DWORD),('Scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(n,ctypes.c_uint64) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
    class Extended(ctypes.Structure):
        _fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];k.CreateJobObjectW.restype=wintypes.HANDLE
    k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];k.SetInformationJobObject.restype=wintypes.BOOL
    k.GetCurrentProcess.restype=wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];k.AssignProcessToJobObject.restype=wintypes.BOOL
    k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
    job=k.CreateJobObjectW(None,None);info=Extended();info.Basic.Flags=0x2000
    if not job:raise InstallError('installer_process_ownership_failed')
    if not k.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(job,k.GetCurrentProcess()):
        k.CloseHandle(job);raise InstallError('installer_process_ownership_failed')
    return job

def command(args,root,error):
    result=subprocess.run(args,cwd=root,env=process_env(),timeout=900)
    if result.returncode:raise InstallError(error)

def validate_venv(env,root):
    python=regular_chain(env/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))
    cfg=regular_chain(env/'pyvenv.cfg')
    if not python.is_file() or not cfg.is_file():raise InstallError('owned_environment_incomplete')
    code="import sys,struct,json;print(json.dumps({'prefix':sys.prefix,'base':getattr(sys,'_base_executable',sys.executable),'minor':list(sys.version_info[:2]),'bits':struct.calcsize('P')*8}))"
    p=subprocess.run([str(python),'-I','-c',code],cwd=root,env=process_env(),capture_output=True,text=True,timeout=30)
    try:value=json.loads(p.stdout)
    except ValueError:raise InstallError('owned_environment_invalid') from None
    if p.returncode or os.path.normcase(str(Path(value.get('prefix','')).resolve()))!=os.path.normcase(str(env)) or value.get('minor')!=list(sys.version_info[:2]) or value.get('bits')!=64 or os.path.normcase(str(Path(value.get('base','')).resolve()))!=base_python():raise InstallError('owned_environment_invalid')
    return python

def install(root,resume=False,wheelhouse=None):
    require_python()
    root=regular_chain(root).resolve()
    if not root.is_dir():raise InstallError('release_directory_missing')
    receipt_path=regular_chain(root/RECEIPT)
    env=regular_chain(root/'.venv')
    regular_chain(root/'.local')
    if not resume and (env.exists() or receipt_path.exists()):raise InstallError('existing_installation_use_resume_or_update')
    if resume and not receipt_path.is_file():raise InstallError('resume_receipt_missing')
    with installation_lock(root):
        if resume:
            receipt=read_json(receipt_path)
            current_manifest=regular_chain(root/'release-manifest.json')
            if not current_manifest.is_file():raise InstallError('invalid_release_manifest')
            validate_receipt(root,receipt,sha(current_manifest))
            verify_bound_manifest(root,receipt)
        else:
            if env.exists() or receipt_path.exists():raise InstallError('existing_installation_use_resume_or_update')
            digest=manifest(root,False)
            receipt=identity(root,digest);atomic_json(root,receipt_path,receipt)
        env=owned_environment(root,receipt)
        if receipt['phase']=='completed':
            validate_venv(env,root)
            return {'status':'already_completed','configuration_preserved':True}
        if receipt['phase']=='preparing':
            command([sys.executable,'-m','venv',str(env)],root,'venv_creation_failed')
            validate_venv(env,root);receipt['phase']='venv_ready';atomic_json(root,receipt_path,receipt)
        python=validate_venv(env,root)
        if receipt['phase']=='venv_ready':
            verify_bound_manifest(root,receipt)
            args=[str(python),'-I','-m','pip','install','--prefix',str(env),'-r','requirements.lock.txt']
            if wheelhouse:
                wheelhouse=regular_chain(wheelhouse).resolve()
                if not wheelhouse.is_dir():raise InstallError('wheelhouse_missing')
                args+=['--no-index','--find-links',str(wheelhouse),'--only-binary=:all:']
            command(args,root,'dependency_installation_failed')
            receipt['phase']='dependencies_ready';atomic_json(root,receipt_path,receipt)
        verify_bound_manifest(root,receipt)
        command([str(python),'scripts/setup_dev.py'],root,'local_setup_failed')
        verify_bound_manifest(root,receipt);receipt['phase']='completed';atomic_json(root,receipt_path,receipt)
        return {'status':'installed','configuration_preserved':True}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--resume',action='store_true');p.add_argument('--wheelhouse');args=p.parse_args()
    try:
        owned_job=own_process_tree()
        print(json.dumps(install(args.root,args.resume,args.wheelhouse)))
    except (InstallError,OSError,subprocess.SubprocessError) as e:
        code=str(e) if isinstance(e,InstallError) else 'installation_io_or_process_failed'
        print(json.dumps({'error':code,'action':'Keep existing files; retry owned interrupted installation with -Resume or use update instructions.'}));raise SystemExit(2)
