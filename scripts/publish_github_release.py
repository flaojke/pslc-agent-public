"""Explicitly authorized private GitHub delivery. Credentials only in process RAM via GCM."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time,re
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OWNER='flaojke';NAME='pslc-agent';REPO=OWNER+'/'+NAME
ENV={**os.environ,'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'}
def git(*args,check=True):
 p=subprocess.run(['git',*args],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=600)
 if check and p.returncode:raise RuntimeError('git_'+args[0]+'_failed')
 return p
def credentials():
 p=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,env=ENV,timeout=20)
 if p.returncode:raise RuntimeError('github_credential_unavailable')
 value=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
 if not value.get('password'):raise RuntimeError('github_credential_unavailable')
 return value['password']
def main():
 version=json.loads((ROOT/'package.json').read_text())['version'];tag='v'+version;folder=ROOT/'artifacts/releases'/version
 manifest=json.loads((folder/'distribution-manifest.json').read_text())
 asset_rows=[*manifest['github_assets']]
 index=folder/'distribution-manifest.json'
 asset_rows.append({'name':index.name,'bytes':index.stat().st_size,'sha256':hashlib.sha256(index.read_bytes()).hexdigest()})
 assert all(row['bytes']<2*1024**3 for row in asset_rows)
 from scripts.release_page import render as render_release_page
 notes=(ROOT/('docs/RELEASE_NOTES_'+version+'.md')).read_text(encoding='utf-8')
 notes=re.sub(r'\]\(([A-Za-z0-9_.-]+\.md)\)',lambda m:'](https://github.com/'+REPO+'/blob/'+tag+'/docs/'+m[1]+')',notes)
 page=render_release_page(REPO,version,asset_rows,notes)
 token=credentials()
 with httpx.Client(headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},trust_env=False,follow_redirects=False,timeout=60) as c:
  def api(method,path,**kw):
   r=c.request(method,'https://api.github.com'+path,**kw)
   if r.status_code>=400:raise RuntimeError('github_'+method+'_'+str(r.status_code))
   return r.json() if r.content else None
  me=api('GET','/user');assert me['login']==OWNER,'Wrong authenticated owner'
  r=c.get('https://api.github.com/repos/'+REPO)
  if r.status_code==404:
   repo=api('POST','/user/repos',json={'name':NAME,'private':True,'auto_init':False,'description':'PSLC Agent: internal IT support and administration, AI PDLC + OpenSpec SDD. Local CPU Qwen, reviewed operations, manual recovery and schedules.'})
  else:r.raise_for_status();repo=r.json()
  assert repo['private'] and repo['owner']['login']==OWNER and repo['full_name']==REPO
  print(json.dumps({'phase':'private_repository_verified','url':repo['html_url']}),flush=True)
  from scripts.build_release import collect
  allowed=[]
  for name in sorted(collect()):
   p=ROOT/name
   if not p.is_file() or name.startswith('frontend/dist/'):continue
   if git('check-ignore','--quiet','--',name,check=False).returncode==0:continue
   assert not p.is_symlink() and p.stat().st_size<90*1024**2
   allowed.append(name)
  # Never silently include staged files outside the reviewed distribution allowlist.
  staged=git('diff','--cached','--name-only','-z').stdout.split('\0')
  assert not set(filter(None,staged))-set(allowed),'Unreviewed staged paths'
  for start in range(0,len(allowed),80):git('add','--',*allowed[start:start+80])
  if git('diff','--cached','--quiet',check=False).returncode:
   git('commit','-m','Release PSLC Agent '+version+': verified Full/Lite and complete source')
  sha=git('rev-parse','HEAD').stdout.strip()
  current=git('branch','--show-current').stdout.strip()
  if current!='main':git('branch','-M','main')
  remote=git('remote','get-url','origin',check=False)
  if remote.returncode:git('remote','add','origin',repo['clone_url'])
  else:assert remote.stdout.strip()==repo['clone_url'],'Unrelated remote'
  git('push','-u','origin','main')
  exists=git('rev-parse','--verify','refs/tags/'+tag,check=False)
  if exists.returncode:git('tag','-a',tag,'-m','PSLC Agent '+version+' verified local MVP')
  else:
   assert not git('diff','--name-only',tag,'HEAD','--','backend','agent','frontend','config','deploy','scripts','package.json','pyproject.toml','pnpm-lock.yaml','vite.config.ts','tsconfig.json','requirements.lock.txt','requirements.agent.txt','requirements.build-windows.lock.txt','.dockerignore').stdout.strip(),'Existing tag application differs'
   sha=git('rev-list','-n','1',tag).stdout.strip()
  git('push','origin',tag)
  print(json.dumps({'phase':'source_pushed','commit':sha,'tag':tag,'files':len(allowed)}),flush=True)
  notes=page
  matches=[x for x in api('GET','/repos/'+REPO+'/releases?per_page=100') if x['tag_name']==tag]
  assert len(matches)<=1,'Duplicate release drafts'
  if matches:release=matches[0]
  else:release=api('POST','/repos/'+REPO+'/releases',json={'tag_name':tag,'target_commitish':sha,'name':'PSLC Agent '+version+' · Full / Lite','body':notes,'draft':True,'prerelease':True})
  assert release['tag_name']==tag
  upload=release['upload_url'].split('{')[0]
  assert upload=='https://uploads.github.com/repos/'+REPO+'/releases/'+str(release['id'])+'/assets'
  existing={row['name']:row for row in api('GET','/repos/'+REPO+'/releases/'+str(release['id'])+'/assets?per_page=100')}
  verified=[]
  for row in asset_rows:
   name=row['name'];asset=existing.get(name);file=folder/name
   assert file.is_file() and file.stat().st_size==row['bytes']
   with file.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==row['sha256']
   if asset is None:
    print(json.dumps({'phase':'uploading','asset':name,'bytes':row['bytes']}),flush=True)
    with file.open('rb') as f:
     response=c.post(upload,params={'name':name},headers={'Content-Type':'application/octet-stream','Content-Length':str(row['bytes'])},content=f,timeout=httpx.Timeout(1800,connect=30))
    if response.status_code!=201:raise RuntimeError('asset_upload_'+str(response.status_code))
    asset=response.json()
   for _ in range(30):
    if asset.get('digest'):break
    time.sleep(1);asset=api('GET','/repos/'+REPO+'/releases/assets/'+str(asset['id']))
   assert asset['state']=='uploaded' and asset['size']==row['bytes']
   assert asset.get('digest')=='sha256:'+row['sha256'],'GitHub asset digest mismatch'
   assert asset['browser_download_url'].startswith('https://github.com/'+REPO+'/releases/download/')
   verified.append({'name':name,'bytes':row['bytes'],'sha256':row['sha256'],'github_digest_verified':True,'url':asset['browser_download_url']})
   print(json.dumps({'phase':'asset_verified','name':name,'bytes':row['bytes']}),flush=True)
  release=api('PATCH','/repos/'+REPO+'/releases/'+str(release['id']),json={'draft':False,'prerelease':True,'body':notes})
  final_repo=api('GET','/repos/'+REPO);assert final_repo['private'] and not release['draft']
  for _ in range(30):
   final_assets={a['name']:a for a in api('GET','/repos/'+REPO+'/releases/'+str(release['id'])+'/assets?per_page=100')}
   if all(final_assets[row['name']]['browser_download_url'].startswith('https://github.com/'+REPO+'/releases/download/'+tag+'/') for row in verified):break
   time.sleep(1)
  for row in verified:
   a=final_assets[row['name']];assert a['browser_download_url'].startswith('https://github.com/'+REPO+'/releases/download/'+tag+'/')
   assert a.get('digest')=='sha256:'+row['sha256'];row['url']=a['browser_download_url']
  report={'version':version,'repository':repo['html_url'],'private':True,'release':release['html_url'],'tag':tag,'source_commit':sha,'source_files':len(allowed),'weights_in_git':False,'assets':verified,'production_admitted':False}
  dest=ROOT/'docs/qa/releases'/version/'github-release.json';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'phase':'published','repository':repo['html_url'],'release':release['html_url'],'private':True,'assets':len(verified)}),flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:print(type(e).__name__+': '+str(e));raise SystemExit(1)
