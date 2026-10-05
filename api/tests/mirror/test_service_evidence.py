"""Independent-course local maintenance and transactional recovery laws."""
from __future__ import annotations

from dataclasses import replace
import sqlite3
import json

import pytest

from api.mirror import service
from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_paths import local_source_root
from api.tests.mirror.acquisition_samples import course_receipt_sample


def _maintain(env):
    return service.run_index_maintenance(root=env['root'], source_key=env['source'])


def _index(env):
    return EvidenceIndex(local_source_root(env['source'], env['root']) / 'query.sqlite3')


def test_one_course_problem_never_blocks_another(evidence_service_workspace):
    env = evidence_service_workspace
    env['publish']()
    env['publish']('second_course')
    assert _maintain(env)['state'] == 'ready'
    course_two = env['root'] / 'CanvasMirror' / 'sources' / env['source'] / 'courses' / '2'
    delayed = env['root'].parent / 'delayed-course-two'
    course_two.rename(delayed)
    receipt = course_receipt_sample()
    scopes = list(receipt.scopes)
    rows = [dict(row) for row in scopes[1].rows]
    rows[0]['name'] = 'Changed classroom writing'
    scopes[1] = replace(scopes[1], rows=tuple(rows))
    env['publish'](receipt=replace(receipt, scopes=tuple(scopes)))
    course_one = course_two.parent / '1'
    malformed = course_one / 'objects' / 'ff' / ('f' * 64 + '.json')
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text('{broken', encoding='utf-8')
    status = _maintain(env)
    assert status['state'] == 'partial'
    assert '2' in status['courses']['not_arrived']
    page = _index(env).query_page('assignment_context', course_id='1')
    assert any(json.loads(row['payload'])['title'] == 'Changed classroom writing' for row in page['records'])
    assert {row['course_id'] for row in _index(env).query_page('courses')['records']} == {'1'}
    delayed.rename(course_two)
    assert _maintain(env)['state'] == 'ready'


@pytest.mark.parametrize('damage', ['corrupt', 'mismatch'])
def test_rebuild_from_safe_files_makes_zero_canvas_calls(evidence_service_workspace, monkeypatch, damage):
    env = evidence_service_workspace
    env['publish']()
    assert _maintain(env)['state'] == 'ready'
    index = _index(env)
    if damage == 'corrupt':
        index.path.write_bytes(b'broken sqlite')
    else:
        from contextlib import closing
        with closing(sqlite3.connect(index.path)) as db, db:
            db.execute("UPDATE index_metadata SET value='99' WHERE key='schema_version'")
    def unexpected(*args, **kwargs):
        raise AssertionError('local maintenance called Canvas')
    for name in ('canvas_get', 'canvas_get_all', 'canvas_get_all_complete', 'canvas_stream_get'):
        monkeypatch.setattr(service, name, unexpected)
    assert _maintain(env)['state'] == 'ready'
    assert len(index.query_page('roster')['records']) == 2


def test_index_failure_after_publication_keeps_safe_files(evidence_service_workspace, monkeypatch):
    env = evidence_service_workspace
    env['publish']()
    safe = env['root'] / 'CanvasMirror'
    before = {p: p.read_bytes() for p in safe.rglob('*.json')}
    original = EvidenceIndex.ingest_many
    with monkeypatch.context() as patch:
        patch.setattr(EvidenceIndex, 'ingest_many', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic')))
        assert _maintain(env)['state'] == 'failed'
    assert {p: p.read_bytes() for p in safe.rglob('*.json')} == before
    assert EvidenceIndex.ingest_many is original
    assert _maintain(env)['state'] == 'ready'


def test_descriptor_failure_keeps_index_ready(evidence_service_workspace, monkeypatch):
    from api.mirror import evidence_queries
    env = evidence_service_workspace
    env['publish']()
    monkeypatch.setattr(evidence_queries, 'write_reader_descriptor',
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    status = _maintain(env)
    assert status['state'] == 'ready'
    assert status['descriptor'] == {'state': 'failed', 'code': 'descriptor_write_failed'}
    assert _index(env).query_page('roster')['records']


def test_absent_whole_course_removes_stale_rows(evidence_service_workspace):
    env = evidence_service_workspace
    env['publish']()
    _maintain(env)
    courses = env['root'] / 'CanvasMirror' / 'sources' / env['source'] / 'courses'
    courses.rename(env['root'].parent / 'delayed-courses')
    status = _maintain(env)
    assert status['state'] == 'partial'
    assert '1' in status['courses']['not_arrived']
    assert _index(env).query_page('roster')['records'] == []


def test_maintenance_scans_without_holding_vault_lock(evidence_service_workspace, monkeypatch):
    from api.mirror.evidence_store import EvidenceStore
    from api.mirror.evidence_schema import canonical_bytes, digest_record
    env = evidence_service_workspace
    env['publish']()
    unsafe = {'schema_version': 1, 'kind': 'submission', 'source_key': env['source'],
              'course_id': '1', 'entity_key': 'submission:10:Pikachu',
              'payload': {'assignment_id': '10', 'pseudonym': 'Pikachu', 'attempt': 1,
                          'body': 'Synthetic Learner One'}}
    ref = digest_record(unsafe)
    path = env['root'] / 'CanvasMirror' / 'sources' / env['source'] / 'courses' / '1' / 'objects' / ref[:2] / (ref + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(unsafe))
    original = EvidenceStore.scan
    calls = []
    def scan(store):
        assert not env['locked'][0]
        calls.append(True)
        return original(store)
    monkeypatch.setattr(EvidenceStore, 'scan', scan)
    assert _maintain(env)['state'] == 'ready'
    assert calls
    with _index(env).read_connection() as db:
        assert db.execute('SELECT 1 FROM safe_facts WHERE fact_ref=?', (ref,)).fetchone() is None


def test_unsupported_course_records_keep_last_good_and_warn(evidence_service_workspace):
    from api.mirror.evidence_schema import canonical_bytes, digest_record
    env = evidence_service_workspace
    env['publish']()
    first = _maintain(env)
    fact = {'schema_version': 2, 'kind': 'submission', 'source_key': env['source'],
            'course_id': '1', 'entity_key': 'future', 'payload': {}, 'future': True}
    ref = digest_record(fact)
    path = env['root'] / 'CanvasMirror' / 'sources' / env['source'] / 'courses' / '1' / 'objects' / ref[:2] / (ref + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(fact))
    status = _maintain(env)
    assert status['state'] == 'partial' and status['code'] == 'evidence_update_required'
    assert status['courses']['update_required'] == ['1']
    assert len(_index(env).query_page('roster')['records']) == 2
    persisted = service._read_maintenance_status(env['root'], env['source'])
    assert persisted['update_required_courses'] == ['1']
    assert persisted['last_success_at']
    assert first['revision'] != status['revision']


def test_status_defaults_are_read_only_and_reject_private_fields(evidence_service_workspace):
    from api.mirror.evidence_paths import maintenance_status_path
    env = evidence_service_workspace
    path = maintenance_status_path(env['source'], env['root'])
    assert service._read_maintenance_status(env['root'], env['source'])['state'] == 'not_run'
    assert not path.parent.exists()
    env['publish']()
    _maintain(env)
    document = json.loads(path.read_text(encoding='utf-8'))
    document['descriptor']['private_detail'] = 'should not leave status owner'
    path.write_text(json.dumps(document), encoding='utf-8')
    before = path.read_bytes()
    assert service._read_maintenance_status(env['root'], env['source'])['state'] == 'not_run'
    assert path.read_bytes() == before


def test_maintenance_binding_change_discards_scan_result(evidence_service_workspace, monkeypatch):
    from api.mirror.evidence_store import EvidenceStore
    env = evidence_service_workspace
    env['publish']()
    original = EvidenceStore.scan
    def scan(store):
        result = original(store)
        monkeypatch.setattr(service.config, 'get_canvas_base', lambda: 'https://changed.example.test')
        return result
    monkeypatch.setattr(EvidenceStore, 'scan', scan)
    assert _maintain(env)['state'] == 'pending'
    assert not _index(env).path.exists()


def test_requests_coalesce_and_are_not_lost_during_rebuild(evidence_service_workspace, monkeypatch):
    import threading
    stop = threading.Event()
    calls = []
    def rebuild():
        calls.append(True)
        if len(calls) == 1:
            service.request_index_maintenance('during_rebuild')
        else:
            stop.set()
    monkeypatch.setattr(service, 'run_index_maintenance', rebuild)
    service.request_index_maintenance('one')
    service.request_index_maintenance('two')
    service.index_maintenance_worker(stop, wait=lambda _: pytest.fail('coalesced request was lost'))
    assert len(calls) == 2
    assert not service._maintenance_requested


def test_synced_file_arrival_is_indexed_on_the_next_cycle(evidence_service_workspace, monkeypatch):
    import threading
    env = evidence_service_workspace
    stop = threading.Event()
    calls = []
    original = service.run_index_maintenance
    env['publish']()
    def rebuild():
        calls.append(original(root=env['root'], source_key=env['source']))
        if len(calls) == 2:
            stop.set()
    def wait(seconds):
        assert seconds == service.INDEX_MAINTENANCE_SECONDS
        env['publish']('second_course')
    monkeypatch.setattr(service, 'run_index_maintenance', rebuild)
    service.request_index_maintenance('startup')
    service.index_maintenance_worker(stop, wait=wait)
    assert all(status['state'] == 'ready' for status in calls)
    assert {row['course_id'] for row in _index(env).query_page('courses')['records']} == {'1', '2'}


def test_owner_heartbeat_progresses_during_slow_rebuild(evidence_service_workspace, monkeypatch):
    import threading
    import time
    env = evidence_service_workspace
    env['publish']()
    entered, release = threading.Event(), threading.Event()
    original = EvidenceIndex.ingest_many
    def slow(index, *args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(index, *args, **kwargs)
    monkeypatch.setattr(EvidenceIndex, 'ingest_many', slow)
    monkeypatch.setattr(service.config, 'mirror_enabled', lambda: False)
    worker = threading.Thread(target=lambda: _maintain(env))
    worker.start()
    try:
        assert entered.wait(1)
        before = time.monotonic()
        assert service.acquisition_owner_status(tick=True) is None
        assert time.monotonic() - before < 1
    finally:
        release.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
