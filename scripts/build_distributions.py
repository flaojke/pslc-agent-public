"""One model-free install ZIP and source ZIP. Weights are explicit UI downloads."""
from pathlib import Path
import hashlib,json,os,re,shutil,sys,tempfile,zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_release import collect,build as build_source,public_member,weight_header,validate_public_payload,verify_model_free_archive
from scripts.agent_resources import collect_agent_resources

def hash_file(path):
 with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def regular(root,path):
 root=Path(root).resolve();p=Path(path);p=p if p.is_absolute() else root/p
 try:relative=p.relative_to(root)
 except ValueError:raise ValueError('unsafe_distribution_source') from None
 if any(x.lower() in ('models','weights','.local') for x in relative.parts):raise ValueError('model_cache_source_forbidden')
 if not p.resolve().is_relative_to(root):raise ValueError('unsafe_distribution_source')
 for item in (p,*p.parents):
  if item==root.parent:break
  if item.is_symlink() or getattr(item,'is_junction',lambda:False)():raise ValueError('unsafe_distribution_source')
 if not p.is_file():raise ValueError('missing_distribution_source')
 return p

def inputs(root,public_files,runtime_files,native_files,native_version):
 if root!=ROOT.resolve() and (public_files is None or runtime_files is None or native_files is None or native_version is None):raise ValueError('all_inputs_required_for_root_override')
 if public_files is None and root!=ROOT.resolve():raise ValueError('public_files_required_for_root_override')
 if root!=ROOT.resolve() and (public_files is None or runtime_files is None or native_files is None or native_version is None):raise ValueError('all_inputs_required_for_root_override')
 if public_files is None and root!=ROOT.resolve():raise ValueError('public_files_required_for_root_override')
 base=dict(collect() if public_files is None else public_files);validate_public_payload(base)
 version=json.loads(base['package.json'])['version']
 if not isinstance(version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('invalid_distribution_version')
 if json.loads((root/'package.json').read_bytes())['version']!=version:raise ValueError('distribution_version_mismatch')
 models=json.loads(base['config/local-models.json']);default=models['default']
 if default not in {m['id'] for m in models['models']}:raise ValueError('invalid_default_model')
 catalog=json.loads(base['config/local-runtime.json']);declared={}
 for platform,spec in catalog['platforms'].items():
  for row in spec['files']:
   name=public_member('runtime/'+platform+'/'+row['path'])
   if name in declared:raise ValueError('duplicate_distribution_member')
   declared[name]=row
 runtime={n:root/n for n in declared} if runtime_files is None else dict(runtime_files)
 if set(runtime)!=set(declared):raise ValueError('runtime_manifest_mismatch')
 if native_files is None:native,native_version=collect_agent_resources()
 else:native=dict(native_files)
 if not isinstance(native_version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',native_version):raise ValueError('invalid_native_version')
 if not all(any(n.startswith('resources/agents/'+p+'/pslc-agent/') for n in native) for p in ('linux-x64','windows-x64')):raise ValueError('native_resources_missing')
 streams={};seen={n.lower() for n in base}
 for name,value in [*runtime.items(),*native.items()]:
  public_member(name)
  if name.lower() in seen:raise ValueError('duplicate_distribution_member')
  if name in native and not re.fullmatch(r'resources/agents/(linux|windows)-x64/pslc-agent/.+',name):raise ValueError('invalid_native_resource')
  seen.add(name.lower());p=regular(root,value)
  with p.open('rb') as source:
   if weight_header(source.read(8)):raise ValueError('model_weight_in_distribution')
  size=p.stat().st_size;sha=hash_file(p)
  if name in declared and (size!=declared[name]['size'] or sha!=declared[name]['sha256']):raise ValueError('runtime_not_verified')
  streams[name]={'path':p,'size':size,'sha256':sha}
 return base,streams,version,default,native_version

def build(*,root=ROOT,public_files=None,runtime_files=None,native_files=None,native_version=None,out=None):
 root=Path(root).resolve();base,streams,version,default,native_version=inputs(root,public_files,runtime_files,native_files,native_version)
 code=hashlib.sha256()
 for name,data in sorted(base.items()):code.update(name.encode()+b'\0'+data+b'\0')
 code_digest=code.hexdigest();target_dir=Path(out or root/'artifacts/releases'/version)
 if target_dir.exists():raise ValueError('distribution_output_exists')
 target_dir.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.model-free-',dir=target_dir.parent));prefix='PSLC-Agent-'+version+'/'
 files=dict(base);files['distribution.json']=json.dumps({'variant':'Lite','version':version,'default_model':default,'weights_included':False,'model_download':'explicit_ui_action','cpu_only':True,'code_sha256':code_digest},indent=2).encode()
 entries=[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()]
 entries.extend({'path':name,'size':row['size'],'sha256':row['sha256']} for name,row in streams.items())
 files['release-manifest.json']=json.dumps({'format':'pslc-source-release-v1','version':version,'variant':'Lite','weights_included':False,'database_schema':__import__('backend.store',fromlist=['SCHEMA_VERSION']).SCHEMA_VERSION,'agent_version':native_version,'code_sha256':code_digest,'production_admitted':False,'files':sorted(entries,key=lambda r:r['path'])},indent=2).encode()
 install=stage/('PSLC-Agent-'+version+'-Lite.zip')
 with zipfile.ZipFile(install,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
  for name,data in sorted(files.items()):
   info=zipfile.ZipInfo(prefix+name,date_time=(2026,11,8,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;archive.writestr(info,data)
  for name,row in sorted(streams.items()):
   info=zipfile.ZipInfo(prefix+name,date_time=(2026,11,8,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100755<<16
   with archive.open(info,'w',force_zip64=True) as dst,row['path'].open('rb') as source:shutil.copyfileobj(source,dst,1024*1024)
 verify_model_free_archive(install,version=version);install_sha=hash_file(install);install.with_suffix('.zip.sha256').write_text(install_sha+'  '+install.name+'\n',encoding='ascii')
 package={'variant':'Lite','name':install.name,'bytes':install.stat().st_size,'sha256':install_sha,'code_sha256':code_digest,'weights_included':False}
 source=stage/('PSLC-Agent-'+version+'-source.zip');source_result=build_source(source,files=base,root=root);verify_model_free_archive(source,version=version)
 readme=stage/'DOWNLOADS.md';readme.write_text('# PSLC Agent '+version+'\n\nСкачайте один Lite.zip и распакуйте его. Веса моделей в программу и Source.zip не включены. Начните с START_HERE.md; требуется Python3.12+ x64, зависимости устанавливаются из package index либо подготовленного wheelhouse.\n\nЛокальная модель загружается только явной кнопкой «Скачать» в «Настройки → Модели». Доступны прогресс, отмена, повтор и переключение моделей/OpenWebUI. Готовые веса не доказывают успешный запуск CPU runtime; Windows policy block не исправляется повторной загрузкой весов. Автоматической загрузки и смены провайдера нет.\n\nSource.zip предназначен разработчикам: исходники, готовый web и входы пересборки, без весов и рабочего состояния. CPU runtime и native bundles включены в Lite. Full/parts/assembler относятся только к историческим выпускам и не нужны для этой установки.\n',encoding='utf-8')
 assets=[install,install.with_suffix('.zip.sha256'),source,source.with_suffix('.zip.sha256'),readme]
 result={'version':version,'source_archive':{'name':source.name,'bytes':source_result['size_bytes'],'sha256':source_result['sha256']},'code_sha256':code_digest,'packages':[package],'github_assets':[{'name':p.name,'bytes':p.stat().st_size,'sha256':hash_file(p)} for p in assets],'source_contains_weights':False,'model_weights_included':False,'model_download':'explicit_ui_action','production_admitted':False}
 (stage/'distribution-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 # Commit only after both actual ZIP streams/CRC/manifests passed. Existing
 # releases, Full archives and downloaded caches are never overwritten.
 if target_dir.exists():raise ValueError('distribution_output_exists')
 os.rename(stage,target_dir);return result
if __name__=='__main__':print(json.dumps(build()),flush=True)
