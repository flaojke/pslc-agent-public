from pathlib import Path
import pytest
from scripts.build_release import collect,explicit_inputs,validate_source_inputs,REQUIRED_BUILD_INPUTS

@pytest.fixture(scope='module')
def public_files():return collect()

def test_source_has_frontend_and_docker_build_context_without_private_state(public_files):
 assert all(n in public_files for n in REQUIRED_BUILD_INPUTS)
 assert b'/src/main.tsx' in public_files['frontend/index.html']
 assert validate_source_inputs(public_files)
 assert not any(n.startswith(('.local/','models/','runtime/','artifacts/')) for n in public_files)
 assert 'docs/qa/settings-guides/screenshots/07-live-about.png' not in public_files

@pytest.mark.parametrize('missing',['frontend/index.html','deploy/Dockerfile.agent','deploy/Dockerfile.cpu-qa'])
def test_missing_build_inputs_fail_closed(public_files,missing):
 files={k:v for k,v in public_files.items() if k!=missing}
 with pytest.raises(ValueError,match='missing_build_input'):validate_source_inputs(files)

def test_missing_nested_docker_copy_does_not_pass_on_near_prefix(public_files):
 files=dict(public_files)
 files['deploy/Dockerfile.agent']=b'COPY agent-secret /src/agent\n'
 with pytest.raises(ValueError,match='missing_docker_copy:agent-secret'):validate_source_inputs(files)

def test_explicit_build_input_symlink_rejected(monkeypatch):
 old=Path.is_symlink
 monkeypatch.setattr(Path,'is_symlink',lambda p:p.as_posix().endswith('frontend/index.html') or old(p))
 with pytest.raises(ValueError,match='invalid_build_input:frontend/index.html'):explicit_inputs(Path(__file__).resolve().parents[1])

def test_source_archive_snapshot_interface_manifest_and_no_input_mutation(public_files,tmp_path):
 import json,zipfile
 from scripts.build_release import build
 from scripts.verify_release import verify
 files={n:public_files[n] for n in REQUIRED_BUILD_INPUTS}
 files['backend/schemas.py']=public_files['backend/schemas.py']
 original=dict(files);out=tmp_path/'source.zip';result=build(out,files=files)
 assert files==original and 'release-manifest.json' not in files
 with zipfile.ZipFile(out) as z:
  z.extractall(tmp_path/'extracted')
 root=tmp_path/'extracted'/('PSLC-Agent-'+result['version'])
 assert verify(root)['status']=='verified'
 assert (root/'frontend/index.html').read_bytes()==public_files['frontend/index.html']
 manifest=json.loads((root/'release-manifest.json').read_text())
 assert manifest['database_schema']==9 and manifest['agent_version']=='0.8.0'
