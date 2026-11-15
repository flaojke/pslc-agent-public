"""On-demand behavior with tiny public fixtures, fake sockets and mock HTTP only.

No application/API listener, model/runtime process, actual HTTP transport,
protected user state, DPAPI or ACL operation is permitted by this harness.
"""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import threading
from types import SimpleNamespace

import httpx
import pytest

from backend import local_inference
from backend import model_connection
from backend.config import Settings
from backend.integrations import IntegrationError
from backend.model_connection import ModelConnection, Selection
from backend.store import Problem


MIB = 1024 * 1024
KEY = 'PUBLIC-ON-DEMAND-PROFILE-KEY'


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp('t')


@pytest.fixture(autouse=True)
def deny_external_effects(monkeypatch):
    violations, fake_binds = [], []

    def forbidden(*args, **kwargs):
        violations.append('forbidden external effect')
        raise AssertionError('real network, process and protected store access are forbidden')

    class FakeSocket:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return None
        def bind(self, address):
            assert address == ('127.0.0.1', 0)
            fake_binds.append(address)
        def getsockname(self):
            return ('127.0.0.1', 50001)

    # LocalModels chooses a port at construction. Replace its namespace, not
    # the global socket module (which other frameworks may need internally).
    monkeypatch.setattr(local_inference, 'socket', SimpleNamespace(socket=FakeSocket))
    for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ('create_connection', 'create_server'):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ('connect', 'connect_ex', 'bind', 'listen'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for namespace in (httpx, httpx._client):
        monkeypatch.setattr(namespace, 'HTTPTransport', forbidden)
        monkeypatch.setattr(namespace, 'AsyncHTTPTransport', forbidden)
    monkeypatch.setattr(model_connection, 'SecretStore', forbidden)
    yield SimpleNamespace(violations=violations, binds=fake_binds)
    assert violations == []


def fixture(tmp_path, *, content=None, ready_second=False):
    content = content or b'GGUF-PUBLIC-ON-DEMAND-SYNTHETIC-WEIGHTS' * 32
    rows = []
    for suffix in ('first', 'second'):
        row = {'id': 'fixture-' + suffix, 'title': 'Public fixture ' + suffix,
               'filename': suffix + '.gguf', 'bytes': len(content),
               'sha256': hashlib.sha256(content).hexdigest(), 'repo': 'public-fixture/models',
               'revision': '1' * 40, 'license': 'PUBLIC-TEST-FIXTURE'}
        row['url'] = 'https://huggingface.co/' + row['repo'] + '/resolve/' + row['revision'] + '/' + row['filename']
        rows.append(row)
    catalog = tmp_path / 'public-catalog.json'
    catalog.write_text(json.dumps({'default': rows[0]['id'], 'models': rows}), encoding='utf-8')
    runtime = tmp_path / 'public-runtime.json'
    runtime.write_text(json.dumps({'platforms': {}}), encoding='utf-8')
    settings = Settings('', 'sqlite:///' + (tmp_path / 'unused.sqlite3').as_posix(),
        auth_mode='accounts', knowledge_enabled=False, local_models_enabled=True,
        local_models_dir=str(tmp_path / 'models'), local_runtime_dir=str(tmp_path / 'runtime'),
        local_catalog_path=str(catalog), local_runtime_manifest_path=str(runtime),
        model_config_path='')
    if ready_second:
        folder = Path(settings.local_models_dir); folder.mkdir()
        (folder / rows[1]['filename']).write_bytes(content)
    return settings, content, rows


def joined(model, mid):
    worker = model.jobs[mid]['thread']
    worker.join(timeout=4)
    assert not worker.is_alive(), 'mock download did not stop within the test bound'
    return next(row for row in model.status()['models'] if row['id'] == mid)


def model_files(settings):
    folder = Path(settings.local_models_dir)
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in folder.glob('*') if p.is_file()}


def test_fresh_start_selection_status_and_missing_generation_never_download(tmp_path, deny_external_effects):
    settings, _, rows = fixture(tmp_path)
    calls = []
    def mock_only(request):
        calls.append(request)
        raise AssertionError('startup must not request a download')
    connection = ModelConnection(settings, httpx.MockTransport(mock_only))
    try:
        assert connection.active == 'local' and connection.local_id == rows[0]['id']
        for _ in range(2):
            connection.warmup()
            assert connection.providers()['active']['model_id'] == rows[0]['id']
            assert connection.snapshot().model_backend == 'local'
            assert all(row['state'] == 'missing' for row in connection.local.status()['models'])
        with pytest.raises(Problem) as error:
            connection.select(Selection(provider='local', model_id=rows[1]['id'], revision=connection.revision))
        assert error.value.code == 'local_model_missing'
        assert connection.local_id == rows[0]['id']
        with pytest.raises(IntegrationError, match='local_model_missing'):
            with connection.local.generation(rows[0]['id']):
                pytest.fail('missing weights must not admit generation')
        assert calls == [] and not Path(settings.local_models_dir).exists()
        assert connection.local.status()['runtime']['state'] != 'ready'
        assert deny_external_effects.binds == [('127.0.0.1', 0)]
        assert not (tmp_path / 'unused.sqlite3').exists()
    finally:
        connection.close()


@pytest.mark.parametrize('selected', [False, True])
def test_explicit_download_reports_progress_and_verified_weights_not_runtime(tmp_path, selected):
    # Just over one downloader chunk permits a visible partial checkpoint;
    # these bytes are synthetic, not a usable model (maximum ~1MiB per file).
    content = b'GGUF' + b'P' * (MIB + 60)
    settings, content, rows = fixture(tmp_path, content=content)
    checkpoint, release = threading.Event(), threading.Event()
    calls = []
    class Paused(httpx.SyncByteStream):
        def __iter__(self):
            yield content[:MIB]
            checkpoint.set()
            assert release.wait(3), 'test did not release mock stream'
            yield content[MIB:]
    def handler(request):
        calls.append((request.method, str(request.url), request.headers.get('authorization')))
        return httpx.Response(200, stream=Paused())
    model = local_inference.LocalModels(settings, httpx.MockTransport(handler))
    if selected:
        model.set_desired(model.default)
    try:
        model.warmup(model.default)
        assert calls == [] and model.status()['models'][0]['state'] == 'missing'
        assert model.request_download(model.default) == {'status': 'downloading'}
        assert checkpoint.wait(2)
        in_progress = model.status()['models'][0]
        assert in_progress['state'] == 'downloading' and in_progress['downloaded_bytes'] == MIB
        assert not model.path(model.default).exists() and not model.verified_model(model.default)
        assert model.status()['runtime']['state'] != 'ready'
    finally:
        release.set()
        if model.default in model.jobs:
            result = joined(model, model.default)
            completed_runtime = model.status()['runtime']
        model.close()
    assert result['state'] == 'ready' and result['downloaded_bytes'] == len(content)
    assert model.path(model.default).read_bytes() == content
    assert hashlib.sha256(model.path(model.default).read_bytes()).hexdigest() == rows[0]['sha256']
    assert not model.path(model.default).with_suffix('.gguf.partial').exists()
    assert completed_runtime['state'] == ('failed' if selected else 'stopped')
    if selected:
        assert completed_runtime['error'] == 'local_runtime_missing'
    assert calls == [('GET', rows[0]['url'], None)]


def test_hash_failure_requires_explicit_retry_and_preserves_existing_download(tmp_path):
    settings, content, rows = fixture(tmp_path, ready_second=True)
    original = model_files(settings)
    calls, valid = [], [False]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=content if valid[0] else b'X' * len(content))
    model = local_inference.LocalModels(settings, httpx.MockTransport(handler))
    try:
        assert model.request_download(model.default)['status'] == 'downloading'
        failed = joined(model, model.default)
        assert failed['state'] == 'failed' and failed['error'] == 'model_integrity_failed'
        assert not model.verified_model(model.default) and model_files(settings) == original
        model.warmup(model.default)
        model.status()
        assert len(calls) == 1  # failure/status/startup do not silently retry
        valid[0] = True
        model.request_download(model.default)
        assert joined(model, model.default)['state'] == 'ready'
        assert len(calls) == 2
        assert model_files(settings)[rows[1]['filename']] == original[rows[1]['filename']]
        before = model_files(settings)
        assert model.request_download(rows[1]['id']) == {'status': 'ready'}
        assert model_files(settings) == before and len(calls) == 2
        restarted = local_inference.LocalModels(settings, httpx.MockTransport(handler))
        try:
            assert all(row['state'] == 'ready' for row in restarted.status()['models'])
            assert len(calls) == 2 and model_files(settings) == before
        finally:
            restarted.close()
    finally:
        model.close()


def test_cancelled_download_is_not_ready_restart_and_retry_are_explicit(tmp_path):
    settings, content, rows = fixture(tmp_path, ready_second=True)
    original = model_files(settings)
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Paused(httpx.SyncByteStream):
        def __iter__(self):
            entered.set()
            assert release.wait(3), 'test did not release mock stream'
            yield content
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, stream=Paused()) if len(calls) == 1 else httpx.Response(200, content=content)
    model = local_inference.LocalModels(settings, httpx.MockTransport(handler))
    try:
        model.request_download(model.default)
        assert entered.wait(2)
        assert model.cancel_download(model.default) == {'status': 'cancel_requested'}
    finally:
        release.set()
    try:
        assert joined(model, model.default)['state'] == 'cancelled'
        assert not model.path(model.default).exists() and not model.verified_model(model.default)
        assert model_files(settings)[rows[1]['filename']] == original[rows[1]['filename']]
        assert len(calls) == 1
        restarted = local_inference.LocalModels(settings, httpx.MockTransport(handler))
        try:
            assert restarted.status()['models'][0]['state'] == 'interrupted'
            restarted.warmup(restarted.default)
            assert len(calls) == 1 and not restarted.verified_model(restarted.default)
            restarted.request_download(restarted.default)
            assert joined(restarted, restarted.default)['state'] == 'ready'
            assert restarted.path(restarted.default).read_bytes() == content and len(calls) == 2
            assert model_files(settings)[rows[1]['filename']] == original[rows[1]['filename']]
        finally:
            restarted.close()
    finally:
        model.close()


@pytest.mark.parametrize('active', ['local', 'disabled', 'saved'])
def test_saved_provider_selection_keys_and_files_do_not_trigger_download(tmp_path, monkeypatch, active):
    settings, _, rows = fixture(tmp_path)
    config_path = tmp_path / 'fake-protected.enc'
    config_path.write_bytes(b'PUBLIC-OPAQUE-FAKE-SECRET-TOKEN')
    saved = {'version': 2, 'active': active, 'local_id': rows[0]['id'],
             'profiles': {'saved': {'url': 'https://fixture.invalid/api', 'key': KEY,
                                   'model': 'public-external-model', 'title': 'Public profile'}},
             'revision': 'a' * 32}
    before = (config_path.read_bytes(), config_path.stat().st_mtime_ns)
    writes, calls = [], []
    class FakeSecretStore:
        def __init__(self, path, **kwargs):
            assert Path(path) == config_path
            self.path = Path(path)
        def read(self):
            return copy.deepcopy(saved)
        def write(self, value):
            writes.append(value)
            pytest.fail('startup/missing selection must not rewrite saved configuration')
    monkeypatch.setattr(model_connection, 'SecretStore', FakeSecretStore)
    settings = Settings(**{**settings.__dict__, 'model_config_path': str(config_path)})
    def handler(request):
        calls.append(request)
        raise AssertionError('startup or missing selection must not contact a provider')
    connection = ModelConnection(settings, httpx.MockTransport(handler))
    try:
        for _ in range(2):
            connection.warmup()
            view = connection.providers()
            snapshot = connection.snapshot()
            assert view['active']['provider'] == ('openwebui' if active == 'saved' else active)
            assert view['local']['models'][0]['state'] == 'missing'
            assert connection.profiles == saved['profiles'] and connection.revision == saved['revision']
            assert connection.active == active
            assert snapshot.model_token == (KEY if active == 'saved' else connection.local.token if active == 'local' else '')
        with pytest.raises(Problem) as error:
            connection.select(Selection(provider='local', model_id=rows[0]['id'], revision=connection.revision))
        assert error.value.code == 'local_model_missing'
        assert connection.active == active and connection.profiles == saved['profiles']
        assert not calls and not writes
        assert (config_path.read_bytes(), config_path.stat().st_mtime_ns) == before
        assert not Path(settings.local_models_dir).exists()
    finally:
        connection.close()
