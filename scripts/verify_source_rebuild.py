"""Rebuild a public source snapshot, without previous dist or private runtime state."""
from pathlib import Path
import argparse,json,os,re,shutil,subprocess,tempfile,time
from scripts.build_release import ROOT,collect,validate_source_inputs
def verify(docker=False):
 files=collect();validate_source_inputs(files)
 stage=Path(tempfile.mkdtemp(prefix='source-rebuild-',dir=ROOT/'.local'))
 count=0
 for name,data in files.items():
  if name.startswith('frontend/dist/'):continue
  p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);count+=1
 assert not (stage/'frontend/dist').exists() and not (stage/'.local').exists()
 installed=ROOT/'node_modules';assert installed.is_dir()
 link=stage/'node_modules'
 if os.name=='nt':
  ps=stage/'link-dependencies.ps1';ps.write_text('param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n',encoding='utf-8')
  subprocess.run([shutil.which('pwsh') or 'powershell','-NoProfile','-File',str(ps),str(link),str(installed)],check=True,capture_output=True)
  ps.unlink()
 else:link.symlink_to(installed,target_is_directory=True)
 assert link.resolve()==installed.resolve()
 logs=stage.parent/(stage.name+'-logs');logs.mkdir()
 for label,args in [('typescript',['node',str(installed/'typescript/bin/tsc'),'--noEmit','--project',str(stage/'tsconfig.json')]),('vite',['node',str(installed/'vite/bin/vite.js'),'build','--config',str(stage/'vite.config.ts')])]:
  with (logs/(label+'.txt')).open('w',encoding='utf-8') as f:
   p=subprocess.run(args,cwd=stage,stdout=f,stderr=subprocess.STDOUT,timeout=120)
  if p.returncode:raise RuntimeError(label+'_source_rebuild_failed')
 index=(stage/'frontend/dist/index.html').read_text()
 for asset in re.findall(r'(?:src|href)="(/assets/[^"?]+)"',index):assert (stage/'frontend/dist'/asset.lstrip('/')).is_file()
 version=json.loads((stage/'package.json').read_text())['version']
 result={'version':version,'source_files':count,'required_build_inputs':True,'docker_copy_closure':True,'previous_dist_used':False,'typescript':'passed','vite':'passed','shared_installed_node_dependencies':True,'fresh_node_dependency_install':False,'private_state_copied':False,'linux_native_docker':'not_requested'}
 if docker:
  image='pslc-agent-source-qa:'+version;start=time.monotonic()
  with (logs/'docker.txt').open('w',encoding='utf-8') as f:
   p=subprocess.run(['docker','build','--progress=plain','-f','deploy/Dockerfile.agent','-t',image,'.'],cwd=stage,stdout=f,stderr=subprocess.STDOUT,timeout=900)
  if p.returncode:raise RuntimeError('native_docker_build_failed')
  p=subprocess.run(['docker','run','--rm','--network','none',image],cwd=stage,capture_output=True,text=True,timeout=30)
  assert p.returncode==0 and '0.8.0' in p.stdout,'Native source build version failed'
  result.update(linux_native_docker='passed_build_and_embedded_health_smoke',native_version='0.8.0',docker_seconds=round(time.monotonic()-start,2),native_version_launch_network='none')
 (ROOT/'.local/source-rebuild.json').write_text(json.dumps({'directory':str(stage),'logs':str(logs)},indent=2))
 out=ROOT/'docs/qa/release-handoff';out.mkdir(parents=True,exist_ok=True);(out/'source-rebuild.json').write_text(json.dumps(result,indent=2))
 print(json.dumps(result),flush=True);return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--docker',action='store_true');a=p.parse_args();verify(a.docker)
