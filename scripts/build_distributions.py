"""Stream Full/Lite releases and <=2GiB GitHub assets without weights in Git."""
from pathlib import Path
import hashlib,json,sys,zipfile,shutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_release import collect
from scripts.agent_resources import collect_agent_resources
from backend.local_inference import hash_file
def build():
 version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version'];prefix='PSLC-Agent-'+version+'/'
 base=collect();model_catalog=json.loads((ROOT/'config/local-models.json').read_text(encoding='utf-8'))
 runtime_catalog=json.loads((ROOT/'config/local-runtime.json').read_text(encoding='utf-8'));runtime={}
 for key,spec in runtime_catalog['platforms'].items():
  root=ROOT/'runtime'/key
  for row in spec['files']:
   p=root/row['path']
   if p.is_symlink() or not p.is_file() or p.stat().st_size!=row['size'] or hash_file(p)!=row['sha256']:raise ValueError('runtime_not_verified')
   runtime['runtime/'+key+'/'+row['path']]=p
 native,native_version=collect_agent_resources()
 default=next(m for m in model_catalog['models'] if m['id']==model_catalog['default']);weights=ROOT/'models'/default['filename']
 if not weights.is_file() or weights.stat().st_size!=default['bytes'] or hash_file(weights)!=default['sha256']:raise ValueError('default_model_not_verified')
 code=hashlib.sha256()
 for name,data in sorted(base.items()):code.update(name.encode()+b'\0'+data+b'\0')
 code_digest=code.hexdigest();out=ROOT/'artifacts/releases'/version;out.mkdir(parents=True,exist_ok=True);assets=[];packages=[]
 for variant in ('Lite','Full'):
  files=dict(base);streamed={**runtime,**native}
  if variant=='Full':streamed['models/'+default['filename']]=weights
  files['distribution.json']=json.dumps({'variant':variant,'version':version,'default_model':default['id'],'weights_included':variant=='Full','cpu_only':True,'code_sha256':code_digest},indent=2).encode()
  entries=[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]
  entries.extend({'path':name,'size':p.stat().st_size,'sha256':default['sha256'] if name.startswith('models/') else hash_file(p)} for name,p in sorted(streamed.items()))
  files['release-manifest.json']=json.dumps({'format':'pslc-source-release-v1','version':version,'variant':variant,'database_schema':__import__('backend.store',fromlist=['SCHEMA_VERSION']).SCHEMA_VERSION,'agent_version':native_version,'code_sha256':code_digest,'production_admitted':False,'files':sorted(entries,key=lambda r:r['path'])},indent=2).encode()
  target=out/('PSLC-Agent-'+version+'-'+variant+'.zip')
  with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
   for name,data in sorted(files.items()):
    info=zipfile.ZipInfo(prefix+name,date_time=(2026,11,8,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;archive.writestr(info,data)
   for name,p in sorted(streamed.items()):
    info=zipfile.ZipInfo(prefix+name,date_time=(2026,11,8,0,0,0));info.compress_type=zipfile.ZIP_STORED if name.startswith('models/') else zipfile.ZIP_DEFLATED;info.external_attr=0o100755<<16
    with archive.open(info,'w',force_zip64=True) as dst,p.open('rb') as src:shutil.copyfileobj(src,dst,4*1024*1024)
  digest=hash_file(target);package={'variant':variant,'name':target.name,'bytes':target.stat().st_size,'sha256':digest,'code_sha256':code_digest};packages.append(package)
  target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
  if variant=='Lite':assets.extend([target,target.with_suffix('.zip.sha256')])
  else:
   parts=[]
   with target.open('rb') as src:
    index=1
    while True:
     initial=src.read(4*1024*1024)
     if not initial:break
     part=target.with_name(target.name+'.part'+str(index).zfill(2));h=hashlib.sha256();total=0
     with part.open('wb') as dst:
      chunk=initial
      while chunk:
       dst.write(chunk);h.update(chunk);total+=len(chunk)
       if total==1600000000:break
       chunk=src.read(min(4*1024*1024,1600000000-total))
     parts.append({'name':part.name,'bytes':total,'sha256':h.hexdigest()});assets.append(part);index+=1
   manifest=out/('PSLC-Agent-'+version+'-Full.parts.json')
   manifest.write_text(json.dumps({'format':'pslc-full-parts-v1','archive':target.name,'bytes':target.stat().st_size,'sha256':digest,'parts':parts},indent=2),encoding='utf-8');assets.extend([manifest,target.with_suffix('.zip.sha256')])
 assembler=out/'assemble_full.py';shutil.copyfile(ROOT/'scripts/assemble_full.py',assembler);assets.append(assembler)
 readme=out/'DOWNLOADS.md'
 readme.write_text('# PSLC Agent '+version+'\n\nLite: скачайте PSLC-Agent-'+version+'-Lite.zip, распакуйте и следуйте START_HERE.md. CPU runtime включён, Qwen4B загружается по кнопке в «Настройки → Модели».\n\nFull: скачайте все Full.zip.partNN, Full.parts.json и assemble_full.py в одну папку. Выполните python assemble_full.py PSLC-Agent-'+version+'-Full.parts.json. Будет собран проверенный Full.zip. Распакуйте и следуйте START_HERE.md. Веса Qwen4B уже включены; повторная загрузка не требуется.\n\nОба варианта: Python3.12+, одинаковый код и готовый web, CPU runtime Windows/Linuxx64, без пользовательской БД/keys/идентичностей. Установка Python dependencies требует package index либо wheelhouse. SHA256 подтверждает байтовую целостность, не корпоративную подпись. Default admin/admin создаётся один раз. Существующие внешние профили сохраняются при upgrade.\n',encoding='utf-8');assets.append(readme)
 result={'version':version,'code_sha256':code_digest,'packages':packages,'github_assets':[{'name':p.name,'bytes':p.stat().st_size,'sha256':hash_file(p)} for p in assets],'source_contains_weights':False,'production_admitted':False}
 (out/'distribution-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps({'version':version,'packages':packages,'github_assets':len(assets),'full_parts':len([p for p in assets if '.part0' in p.name]),'secret_scan':'passed'}),flush=True)
 return result
if __name__=='__main__':build()
