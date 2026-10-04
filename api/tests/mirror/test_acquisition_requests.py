"""Synced focused acquisition requests are bounded and coalesced."""

from api.mirror.acquisition_requests import AcquisitionRequests
import pytest


def test_requests_coalesce_and_acknowledge_without_rewriting_requesters(tmp_path):
    queue = AcquisitionRequests(tmp_path, "a" * 64)
    first = queue.submit(writer_key="1" * 32, course_id="10", scope="course.refresh")
    second = queue.submit(writer_key="2" * 32, course_id="10", scope="course.refresh")
    other = queue.submit(writer_key="2" * 32, course_id="11", scope="roster")
    pending = queue.pending()
    assert {(item.course_id, item.scope, frozenset(item.request_ids)) for item in pending} == {
        ("10", "course.refresh", frozenset({first, second})),
        ("11", "roster", frozenset({other}))}
    request_files = tuple((queue.root / "pending").glob("*.json"))
    queue.acknowledge((first, second), owner_writer_key="3" * 32)
    assert queue.is_acknowledged(first) and queue.is_acknowledged(second)
    assert tuple((queue.root / "pending").glob("*.json")) == request_files
    assert [(item.course_id, item.scope) for item in queue.pending()] == [("11", "roster")]


def test_invalid_request_is_rejected_and_corrupt_peer_file_ignored(tmp_path):
    queue = AcquisitionRequests(tmp_path, "a" * 64)
    with pytest.raises(ValueError, match="invalid_focused_request"):
        queue.submit(writer_key="1" * 32, course_id="Avery", scope="roster")
    pending = queue.root / "pending"
    pending.mkdir(parents=True)
    (pending / ("f" * 32 + ".json")).write_text('{"secret":"token"}', encoding="utf-8")
    assert queue.pending() == ()
    assert (pending / ("f" * 32 + ".json")).exists()


def test_pending_rotates_when_earlier_scope_keeps_failing(tmp_path):
    queue = AcquisitionRequests(tmp_path, "a" * 64)
    queue.submit(writer_key="1" * 32, course_id="10", scope="roster")
    queue.submit(writer_key="1" * 32, course_id="11", scope="roster")
    first = queue.pending(limit=1)[0].course_id
    second = queue.pending(limit=1)[0].course_id
    assert {first, second} == {"10", "11"}
