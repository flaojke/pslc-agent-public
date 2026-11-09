"""Native build only. Never installs a service or copies operator settings."""
from pathlib import Path
import importlib.metadata as md,json,os,subprocess,sys,ssl,sysconfig
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.delivery import host_platform
from agent import __version__ as AGENT_VERSION

def build():
 platform=host_platform();output=ROOT/'artifacts/native'/AGENT_VERSION/platform;work=ROOT/'.local/build-work'/AGENT_VERSION/platform;spec=ROOT/'.local/build-spec'/AGENT_VERSION/platform
 for path in (output,work,spec):
  assert path.resolve().is_relative_to(ROOT.resolve())
  path.mkdir(parents=True,exist_ok=True)
 command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--name','pslc-agent','--paths',str(ROOT),'--distpath',str(output),'--workpath',str(work),'--specpath',str(spec),'--log-level','WARN']
 if os.name=='nt':command+=['--hidden-import','win32timezone','--hidden-import','servicemanager','--hidden-import','win32service','--hidden-import','win32serviceutil']
 trust_asset=Path(ssl.__file__).parent/'codex-rejected-cas.pem'
 if trust_asset.is_file():command+=['--add-data',str(trust_asset)+os.pathsep+'.']
 command+=[str(ROOT/'agent/entrypoint.py')]
 subprocess.run(command,cwd=ROOT,check=True)
 bundle=output/'pslc-agent';inventory=[]
 # Materialize native build symlinks before signing; release verifier rejects all links.
 for link in list(bundle.rglob('*')):
  if link.is_symlink():
   target=link.resolve()
   if not target.is_relative_to(bundle.resolve()) or not target.is_file():raise ValueError('unexpected_build_link')
   data=target.read_bytes();mode=target.stat().st_mode;link.unlink();link.write_bytes(data);link.chmod(mode)

 packages=['httpx','httpcore','h11','anyio','certifi','idna','psutil','pydantic','pydantic_core','annotated-types','typing-inspection','typing_extensions','cryptography','cffi','pycparser']+(['pywin32'] if os.name=='nt' else [])
 for name in packages:
  dist=md.distribution(name);inventory.append({'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See upstream license files'})
  for file in dist.files or []:
   if ('licenses/' in str(file).replace('\\','/') or file.name.lower() in ('license','license.txt','copying')):
    src=Path(dist.locate_file(file))
    if src.is_file():
     target=bundle/'licenses'/name/file.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(src.read_bytes())
 # CPython DLL/shared runtime and PyInstaller bootloader notices travel with both native bundles.
 python_license=next((p for p in (Path(sys.base_prefix)/'LICENSE.txt',Path(sysconfig.get_path('stdlib'))/'LICENSE.txt') if p.is_file()),None)
 if python_license is None:raise ValueError('python_license_missing')
 copying=md.distribution('pyinstaller');copying_file=next((Path(copying.locate_file(f)) for f in copying.files or [] if f.name=='COPYING.txt'),None)
 if copying_file is None:raise ValueError('pyinstaller_license_missing')
 for name,src in [('CPython/LICENSE.txt',python_license),('PyInstaller/COPYING.txt',copying_file)]:
  p=bundle/'licenses'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(src.read_bytes())
 (bundle/'dependency-inventory.json').write_text(json.dumps({'scope':'agent runtime dependency inventory; not a security certification','platform':platform,'python':sys.version.split()[0],'pyinstaller':md.version('pyinstaller'),'packages':inventory},indent=2),encoding='utf-8')
 print(json.dumps({'bundle':str(bundle),'platform':platform}))
if __name__=='__main__':build()
