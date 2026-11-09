"""Verified native node resources for both server distributions; public trust only."""
from pathlib import Path
import json,tempfile,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.delivery import verify
from agent import __version__ as AGENT_VERSION
ROOT=Path(__file__).resolve().parents[1]
def collect_agent_resources():
 trust=json.loads((ROOT/'config/agent-trust.json').read_text(encoding='utf-8'));files={};versions=set()
 with tempfile.TemporaryDirectory(prefix='pslc-public-key-') as tmp:
  key=Path(tmp)/'public.pem';key.write_text(trust['public_pem'],encoding='ascii')
  for platform in ('windows-x64','linux-x64'):
   source=ROOT/'artifacts/native'/AGENT_VERSION/platform/'pslc-agent'
   if not source.is_dir():source=ROOT/'resources/agents'/platform/'pslc-agent'
   manifest,_=verify(source,key,platform)
   if manifest.version!=AGENT_VERSION:raise ValueError("native_source_version_mismatch")
   versions.add(manifest.version)
   names=[r.path for r in manifest.files]+['manifest.json','manifest.sig']
   for name in names:files['resources/agents/'+platform+'/pslc-agent/'+name]=source/name
 if len(versions)!=1:raise ValueError('native_versions_differ')
 return files,next(iter(versions))
if __name__=='__main__':
 files,version=collect_agent_resources();print(json.dumps({'platforms':2,'native_version':version,'files':len(files),'bytes':sum(p.stat().st_size for p in files.values()),'scope':'verified native resources; QA-only trust'}))
