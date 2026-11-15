"""Synthetic tiny ZIPs only: no collect/private state/cache/runtime/network/processes."""
from pathlib import Path
import hashlib,importlib,json,socket,struct,subprocess,zipfile,zlib
import pytest
import scripts.build_distributions as dist
import scripts.build_release as source
import scripts.prepare_clean_distributions as clean

def forbidden(*a,**k):raise AssertionError('real collector/process/network/private state is forbidden')
@pytest.fixture(autouse=True)
def no_real_factories(monkeypatch):
 monkeypatch.setattr(dist,'collect',forbidden);monkeypatch.setattr(source,'collect',forbidden);monkeypatch.setattr(dist,'collect_agent_resources',forbidden)
 monkeypatch.setattr(subprocess,'run',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
 from backend.secret_store import SecretStore
 monkeypatch.setattr(SecretStore,'read',forbidden);monkeypatch.setattr(SecretStore,'write',forbidden);monkeypatch.setattr(SecretStore,'_directory',forbidden)

@pytest.fixture
def tiny(tmp_path):
 version='7.8.9';files={n:b'public synthetic input' for n in source.REQUIRED_BUILD_INPUTS}
 for n in ('deploy/Dockerfile.agent','deploy/Dockerfile.cpu-qa'):files[n]=b'FROM synthetic-not-executed'
 files.update({'package.json':json.dumps({'version':version,'dependencies':{}}).encode(),'frontend/dist/index.html':b'<script src="/assets/app.js"></script>','frontend/dist/assets/app.js':b'public web','config/local-models.json':b'{"default":"fixture-model","models":[{"id":"fixture-model","filename":"fixture.gguf"}]}','START_HERE.md':b'public install guide'})
 runtime={};catalog={'platforms':{}}
 for platform,name,data in [('linux-x64','llama/llama-server',b'SYNTHETIC_ELF_NOT_EXECUTED'),('windows-x64','llama-server.exe',b'SYNTHETIC_PE_NOT_EXECUTED')]:
  member='runtime/'+platform+'/'+name;p=tmp_path/member;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);runtime[member]=p;catalog['platforms'][platform]={'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}
 files['config/local-runtime.json']=json.dumps(catalog).encode();native={}
 for platform in ('windows-x64','linux-x64'):
  member='resources/agents/'+platform+'/pslc-agent/manifest.sig';p=tmp_path/member;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'PREVERIFIED_PUBLIC_NATIVE_FIXTURE');native[member]=p
 for name,data in files.items():p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 return {'root':tmp_path,'public_files':files,'runtime_files':runtime,'native_files':native,'native_version':'0.8.0'}

def built(tiny,name='out'):
 return dist.build(**tiny,out=tiny['root']/name)

def test_actual_tiny_zips_have_no_weights_and_exact_inventory(tiny):
 before=dict(tiny['public_files']);result=built(tiny);out=tiny['root']/'out';assert tiny['public_files']==before
 assert len(result['packages'])==1 and result['packages'][0]['variant']=='Lite' and result['packages'][0]['weights_included'] is False;assert len(result['github_assets'])==5 and not result['model_weights_included'] and not result['source_contains_weights']
 assert not any('Full' in p.name or 'part01' in p.name or p.name=='assemble_full.py' for p in out.iterdir())
 for name in ['PSLC-Agent-7.8.9-Lite.zip','PSLC-Agent-7.8.9-source.zip']:
  path=out/name;assert source.verify_model_free_archive(path,version='7.8.9')['all_crc_checked']
  with zipfile.ZipFile(path) as archive:
   names=archive.namelist();assert not any('/models/' in n or n.lower().endswith('.gguf') for n in names);manifest=json.loads(archive.read('PSLC-Agent-7.8.9/release-manifest.json'));assert manifest['weights_included'] is False
   for row in manifest['files']:assert hashlib.sha256(archive.read('PSLC-Agent-7.8.9/'+row['path'])).hexdigest()==row['sha256']
 with zipfile.ZipFile(out/'PSLC-Agent-7.8.9-Lite.zip') as archive:
  data=json.loads(archive.read('PSLC-Agent-7.8.9/distribution.json'));assert data['default_model']=='fixture-model' and not data['weights_included'] and data['model_download']=='explicit_ui_action';assert any('/runtime/' in n for n in archive.namelist()) and any('/resources/agents/' in n for n in archive.namelist())
 text=(out/'DOWNLOADS.md').read_text(encoding='utf-8');assert 'Скачайте' in text and 'РЎРє' not in text;assert all(hashlib.sha256((out/r['name']).read_bytes()).hexdigest()==r['sha256'] for r in result['github_assets'])

@pytest.mark.parametrize('cache_populated',[False,True])
def test_missing_or_populated_model_cache_is_never_inspected(tiny,monkeypatch,cache_populated):
 cache=tiny['root']/'models';sentinel=cache/'cached.gguf'
 if cache_populated:cache.mkdir();sentinel.write_bytes(b'UNCHANGED_PUBLIC_CACHE_MARKER')
 old_open=Path.open;old_stat=Path.stat
 def in_cache(p):return p==cache or cache in p.parents
 def opened(p,*a,**k):
  if in_cache(p):raise AssertionError('model cache read')
  return old_open(p,*a,**k)
 def statted(p,*a,**k):
  if in_cache(p):raise AssertionError('model cache stat')
  return old_stat(p,*a,**k)
 with monkeypatch.context() as scoped:
  scoped.setattr(Path,'open',opened);scoped.setattr(Path,'stat',statted);built(tiny)
 if cache_populated:assert sentinel.read_bytes()==b'UNCHANGED_PUBLIC_CACHE_MARKER'
 else:assert not cache.exists()

@pytest.mark.parametrize('name,data',[('models/hidden.txt',b'not allowed'),('docs/blob.GGUF',b'not allowed'),('docs/model.safetensors',b'not allowed'),('docs/renamed.dat',b'GGUF'+(3).to_bytes(4,'little')+b'fake'),('docs/future.dat',b'GGUF'+(4).to_bytes(4,'little')+b'fake'),('../escape.txt',b'public'),('backend\\escape.py',b'public'),('distribution.json',b'{}')])
def test_injected_input_rejected_before_output(tiny,name,data):
 tiny['public_files'][name]=data
 with pytest.raises(ValueError):built(tiny)
 assert not (tiny['root']/'out').exists()

def test_plain_gguf_text_is_not_mistaken_for_binary_weights(tiny):
 tiny['public_files']['docs/gguf-note.txt']=b'GGUF text is public documentation';built(tiny)

def test_streamed_renamed_weight_rejected_before_output(tiny):
 p=next(iter(tiny['native_files'].values()));p.write_bytes(b'GGUF'+(4).to_bytes(4,'little')+b'fixture')
 with pytest.raises(ValueError,match='model_weight'):built(tiny)
 assert not (tiny['root']/'out').exists()

def test_cache_path_as_streamed_input_refused_without_open(tiny):
 name=next(iter(tiny['native_files']));tiny['native_files'][name]=tiny['root']/'models/cache.bin'
 with pytest.raises(ValueError,match='cache_source'):built(tiny)

def test_runtime_tamper_and_duplicate_member_refused(tiny):
 p=next(iter(tiny['runtime_files'].values()));p.write_bytes(b'changed')
 with pytest.raises(ValueError,match='runtime_not_verified'):built(tiny)

def test_case_alias_member_collision_refused(tiny):
 tiny['public_files']['backend/ALIAS.py']=b'a';tiny['public_files']['backend/alias.py']=b'b'
 with pytest.raises(ValueError,match='duplicate_distribution'):built(tiny)

def test_root_override_requires_all_fixture_inputs_before_collect(tiny):
 with pytest.raises(ValueError,match='all_inputs_required'):dist.build(root=tiny['root'])
 args=dict(tiny);args['native_files']=None
 with pytest.raises(ValueError,match='all_inputs_required'):dist.build(**args)
 with pytest.raises(ValueError,match='root_override_requires_files'):source.build(root=tiny['root'])

def test_existing_artifact_directory_and_source_file_are_retained(tiny):
 out=tiny['root']/'out';out.mkdir();old=out/'PSLC-Agent-0.18.3-Full.zip';old.write_bytes(b'HISTORICAL_UNCHANGED')
 with pytest.raises(ValueError,match='output_exists'):built(tiny)
 assert old.read_bytes()==b'HISTORICAL_UNCHANGED'
 target=tiny['root']/'historic-source.zip';target.write_bytes(b'HISTORICAL_SOURCE_UNCHANGED')
 with pytest.raises(ValueError,match='source_output_exists'):source.build(target,files=tiny['public_files'],root=tiny['root'])
 assert target.read_bytes()==b'HISTORICAL_SOURCE_UNCHANGED'

def archive_fixture(path,files,format='pslc-source-release-v1'):
 rows=[{'path':n,'size':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in files.items()];manifest={'format':format,'version':'7.8.9','files':rows,'weights_included':False}
 with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
  for n,b in {**files,'release-manifest.json':json.dumps(manifest).encode()}.items():archive.writestr('PSLC-Agent-7.8.9/'+n,b)
 return path

@pytest.mark.parametrize('name,data',[('docs/hidden.dat',b'GGUF'+(4).to_bytes(4,'little')+b'fixture'),('models/m.txt',b'weights'),('file.safetensors',b'weights')])
def test_postzip_rejects_weights_even_with_self_consistent_manifest(tmp_path,name,data):
 path=archive_fixture(tmp_path/'bad.zip',{name:data})
 with pytest.raises(ValueError,match='model_weight'):source.verify_model_free_archive(path)

@pytest.mark.parametrize('format',[None,'wrong'])
def test_postzip_requires_installer_manifest_format(tmp_path,format):
 path=archive_fixture(tmp_path/'bad.zip',{'docs/public.txt':b'public'},format=format)
 with pytest.raises(ValueError):source.verify_model_free_archive(path)

def test_postzip_crc_damage_is_rejected(tiny):
 built(tiny);path=tiny['root']/'out/PSLC-Agent-7.8.9-Lite.zip'
 with zipfile.ZipFile(path) as archive:info=archive.getinfo('PSLC-Agent-7.8.9/frontend/dist/assets/app.js')
 data=bytearray(path.read_bytes());length,extra=struct.unpack_from('<HH',data,info.header_offset+26);offset=info.header_offset+30+length+extra;data[offset]^=255;path.write_bytes(data)
 with pytest.raises((ValueError,zipfile.BadZipFile,zlib.error)):source.verify_model_free_archive(path)

def test_builder_does_not_commit_postwrite_weight_injection(tiny,monkeypatch):
 actual=dist.verify_model_free_archive
 def verify(path,**kwargs):
  if path.name.endswith('-Lite.zip'):
   with zipfile.ZipFile(path,'a') as archive:archive.writestr('PSLC-Agent-7.8.9/models/injected.gguf',b'GGUF'+(3).to_bytes(4,'little'))
  return actual(path,**kwargs)
 monkeypatch.setattr(dist,'verify_model_free_archive',verify)
 with pytest.raises(ValueError):built(tiny)
 assert not (tiny['root']/'out').exists()

def test_source_direct_injection_is_rejected(tiny):
 files={**tiny['public_files'],'models/direct.gguf':b'weights'}
 with pytest.raises(ValueError):source.build(tiny['root']/'source.zip',files=files,root=tiny['root'])
 assert not (tiny['root']/'source.zip').exists()

def test_clean_helper_is_import_safe_and_inventory_driven(tiny,monkeypatch):
 with monkeypatch.context() as scoped:
  scoped.setattr(clean.tempfile,'mkdtemp',forbidden);importlib.reload(clean)
 result=built(tiny);checked=clean.prepare(root=tiny['root'],release_dir=tiny['root']/'out');assert checked['installer_executed'] is False and set(checked['variants'])=={'Lite'} and checked['variants']['Lite']['initial_model_present'] is False
 bad=dict(result);bad['packages']=[*result['packages'],{'variant':'Full'}]
 with pytest.raises(ValueError,match='model_free_inventory'):clean.model_free_inventory(bad)


def test_direct_source_fixture_version_must_match(tiny):
 files={**tiny['public_files'],'package.json':b'{"version":"1.2.3"}'}
 with pytest.raises(ValueError,match='version_mismatch'):source.build(tiny['root']/'source.zip',files=files,root=tiny['root'])
 assert not (tiny['root']/'source.zip').exists()

@pytest.mark.parametrize('name',['docs/weights.safetensors.','docs/model.gguf ','docs/CON.txt','docs/alias?.txt'])
def test_noncanonical_windows_member_aliases_refused(tiny,name):
 tiny['public_files'][name]=b'public'
 with pytest.raises(ValueError):built(tiny)
 assert not (tiny['root']/'out').exists()

def test_manifest_boolean_size_is_not_an_integer(tmp_path):
 path=archive_fixture(tmp_path/'bad.zip',{'docs/a.txt':b'a'})
 with zipfile.ZipFile(path) as z:files={n:z.read(n) for n in z.namelist()}
 manifest=json.loads(files['PSLC-Agent-7.8.9/release-manifest.json']);manifest['files'][0]['size']=True;files['PSLC-Agent-7.8.9/release-manifest.json']=json.dumps(manifest).encode()
 with zipfile.ZipFile(path,'w') as z:
  for n,b in files.items():z.writestr(n,b)
 with pytest.raises(ValueError,match='invalid_distribution_manifest'):source.verify_model_free_archive(path)


@pytest.mark.parametrize('large_size,many',[(2*1024**3+1,False),(1,True)])
def test_fresh_extract_rechecks_declared_size_and_count_without_large_data(tiny,monkeypatch,large_size,many):
 from types import SimpleNamespace
 built(tiny);info=SimpleNamespace(filename='PSLC-Agent-7.8.9/docs/public.txt',file_size=large_size)
 class Archive:
  def __init__(self,*a,**k):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def infolist(self):return [info]*10001 if many else [info]
  def extract(self,*a,**k):forbidden()
 monkeypatch.setattr(clean,'verify_model_free_archive',lambda *a,**k:{'variant':'Lite'})
 monkeypatch.setattr(clean.zipfile,'ZipFile',Archive)
 with pytest.raises(ValueError,match='extract_limit'):clean.prepare(root=tiny['root'],release_dir=tiny['root']/'out')


@pytest.mark.parametrize('suffix',['.zip.sha256','.release.json'])
def test_source_existing_sidecars_are_retained_before_collection(tiny,suffix):
 target=tiny['root']/'source.zip';sidecar=target.with_suffix(suffix);sidecar.write_bytes(b'HISTORICAL_SIDECAR_UNCHANGED')
 with pytest.raises(ValueError,match='source_output_exists'):source.build(target,files=tiny['public_files'],root=tiny['root'])
 assert sidecar.read_bytes()==b'HISTORICAL_SIDECAR_UNCHANGED' and not target.exists()

def test_source_symlink_sidecar_is_refused_without_following(tiny,monkeypatch):
 target=tiny['root']/'source.zip';sidecar=target.with_suffix('.zip.sha256');old=Path.is_symlink;monkeypatch.setattr(Path,'is_symlink',lambda p:p==sidecar or old(p))
 with pytest.raises(ValueError,match='source_output_exists'):source.build(target,files=tiny['public_files'],root=tiny['root'])
 assert not target.exists()
