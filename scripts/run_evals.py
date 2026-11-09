"""Local/fixture eval success never means production acceptance."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
def fingerprint():
 files=[]
 for folder in ('backend','agent','frontend/src','tests','scripts','config'):
  files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.ts','.tsx','.css','.cjs','.ps1','.json') and '__pycache__' not in p.parts)
 files.extend(ROOT/x for x in ('pyproject.toml','package.json','requirements.lock.txt','requirements.agent.txt','qa/scenarios.json'))
 digest=hashlib.sha256()
 for path in sorted(files):digest.update(path.relative_to(ROOT).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
 return digest.hexdigest()
def run(output,postgres_config=''):
 start=time.time();source=fingerprint();out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True);results={}
 scenarios=json.loads((ROOT/'qa/scenarios.json').read_text(encoding='utf-8'))
 for sid,selectors in scenarios.items():
  env=os.environ.copy();env['PSLC_EVAL_REPORT']=str(out/(sid+'.json'))
  if postgres_config:env['PSLC_TEST_POSTGRES_CONFIG']=str(Path(postgres_config).resolve())
  command=[sys.executable,'-m','pytest','-q','-p','scripts.eval_plugin',*selectors]
  process=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
  path=out/(sid+'.json');data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'exit_code':process.returncode,'counts':{'passed':0,'failed':1,'skipped':0}}
  state='failed' if process.returncode or data['counts']['failed'] else 'partial' if data['counts']['skipped'] else 'passed'
  results[sid]={'state':state,**data,'command':['python','-m','pytest','-q','-p','scripts.eval_plugin',*selectors],'scope':'automated local/fixture; isolated PostgreSQL when explicitly configured','external_acceptance':'pending'}
  print(sid+': '+state+' '+str(data['counts']),flush=True)
 value={'format':'pslc-evals-v1','created':time.time(),'duration_seconds':round(time.time()-start,2),'source_fingerprint':source,'source_unchanged':source==fingerprint(),'postgres_configured_for_tests':bool(postgres_config),'external_acceptance':False,'scenarios':results}
 (out/'report.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
 return 1 if not value['source_unchanged'] or any(x['state']=='failed' for x in results.values()) else 0
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',default='docs/qa/evals');p.add_argument('--postgres-config',default='');a=p.parse_args();raise SystemExit(run(a.output,a.postgres_config))
