"""Self-contained public source fixtures; never read working state or local QA."""
from pathlib import Path
import json
import pytest
from scripts import build_release as source

@pytest.fixture(scope='module')
def public_files(tmp_path_factory):
 fixture_root=tmp_path_factory.mktemp('public-source')
 files={name:b'# public synthetic build input\n' for name in source.REQUIRED_BUILD_INPUTS}
 files.update({
  'package.json':json.dumps({'version':'7.8.9','dependencies':{}}).encode(),
  'requirements.lock.txt':b'',
  'frontend/index.html':b'<script type="module" src="/src/main.tsx"></script>',
  'frontend/src/main.tsx':b'export const publicFixture = true;',
  'frontend/dist/index.html':b'<main>public compiled fixture</main>',
  'deploy/Dockerfile.agent':b'COPY agent /app/agent\n',
  'deploy/Dockerfile.cpu-qa':b'COPY backend /app/backend\n',
  'backend/schemas.py':b'"""Public synthetic schema."""\n',
  'docs/INSTALL_RELEASE.md':b'Public fixture installation.',
  'LICENSE':b'PUBLIC SYNTHETIC LICENSE',
  'NOTICE':b'PUBLIC SYNTHETIC NOTICE',
  'SECURITY.md':b'Public fixture security guidance.',
 })
 for name,data in files.items():
  path=fixture_root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 private_names=('.local/example.json','models/example.gguf','docs/qa/example.json')
 for name in private_names:
  path=fixture_root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'SYNTHETIC_EXCLUDED_FIXTURE')
 def forbidden(*args,**kwargs):raise AssertionError('private collector operation attempted')
 import backend.config,backend.model_connection,backend.secret_store
 with pytest.MonkeyPatch.context() as scoped:
  scoped.setattr(source,'ROOT',fixture_root)
  scoped.setattr(backend.config,'load_settings',forbidden)
  scoped.setattr(backend.model_connection,'ModelConnection',forbidden)
  scoped.setattr(backend.secret_store.SecretStore,'read',forbidden)
  scoped.setattr(backend.secret_store.SecretStore,'write',forbidden)
  scoped.setattr(backend.secret_store.SecretStore,'_directory',forbidden)
  public=source.collect(scan_private=False)
 assert not any(b'SYNTHETIC_EXCLUDED_FIXTURE' in data for data in public.values())
 return public

def test_source_has_complete_public_build_inputs_without_state(public_files):
 assert all(name in public_files for name in source.REQUIRED_BUILD_INPUTS)
 assert b'/src/main.tsx' in public_files['frontend/index.html']
 assert source.validate_source_inputs(public_files)
 assert not any(name.startswith(('.local/','models/','runtime/','artifacts/','docs/qa/')) for name in public_files)
 assert all(name in public_files for name in ('LICENSE','NOTICE','SECURITY.md'))

@pytest.mark.parametrize('missing',['frontend/index.html','deploy/Dockerfile.agent','deploy/Dockerfile.cpu-qa','scripts/install_bootstrap.py','backend/runtime_host.py','backend/runtime_process.py'])
def test_missing_build_inputs_fail_closed(public_files,missing):
 files={name:data for name,data in public_files.items() if name!=missing}
 with pytest.raises(ValueError,match='missing_build_input'):
  source.validate_source_inputs(files)

def test_missing_nested_docker_copy_does_not_pass_on_near_prefix(public_files):
 files=dict(public_files);files['deploy/Dockerfile.agent']=b'COPY agent-secret /app/agent\n'
 with pytest.raises(ValueError,match='missing_docker_copy:agent-secret'):
  source.validate_source_inputs(files)

def test_explicit_build_input_symlink_rejected(public_files,tmp_path,monkeypatch):
 for name,data in public_files.items():
  path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 original=Path.is_symlink;alias=tmp_path/'frontend/index.html'
 monkeypatch.setattr(Path,'is_symlink',lambda path:path==alias or original(path))
 with pytest.raises(ValueError,match='invalid_build_input:frontend/index.html'):
  source.explicit_inputs(tmp_path)

def test_injected_source_snapshot_manifest_and_no_input_mutation(public_files,tmp_path):
 import zipfile
 from scripts.verify_release import verify
 fixture_root=tmp_path/'input';fixture_root.mkdir()
 (fixture_root/'package.json').write_bytes(public_files['package.json'])
 files=dict(public_files);original=dict(files);out=tmp_path/'source.zip'
 result=source.build(out,files=files,root=fixture_root)
 assert files==original and 'release-manifest.json' not in files
 with zipfile.ZipFile(out) as archive:
  assert archive.testzip() is None
  archive.extractall(tmp_path/'extracted')
 extracted=tmp_path/'extracted'/('PSLC-Agent-'+result['version'])
 assert verify(extracted)['status']=='verified'
 assert (extracted/'frontend/index.html').read_bytes()==public_files['frontend/index.html']
 manifest=json.loads((extracted/'release-manifest.json').read_text(encoding='utf-8'))
 assert manifest['database_schema']==9 and manifest['agent_version']=='0.8.0'
 assert manifest['weights_included'] is False
