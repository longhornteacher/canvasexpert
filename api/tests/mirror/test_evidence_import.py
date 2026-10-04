import hashlib
import json

import pytest

from api.mirror.evidence_import import collect_legacy_sources


@pytest.fixture
def legacy_root(tmp_path):
    workspace = tmp_path / "workspace"
    mirror = tmp_path / "cache" / "Canvas Mirror"
    workspace.mkdir()
    mirror.mkdir(parents=True)
    return workspace, mirror


def test_inventory_preserves_exact_bytes_and_valid_sibling(legacy_root):
    workspace, mirror = legacy_root
    course = mirror / "42"
    course.mkdir()
    document = {"schema_version": 1, "course_id": "42", "state": "current",
                "last_success_at": "", "last_attempt_at": "", "error_code": "",
                "students": {}, "sections": {}}
    payload = json.dumps(document, indent=3).encode()
    (course / "roster.v1.json").write_bytes(payload)
    (course / "assignments.v1.json").write_bytes(b"{broken")
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert len(inventory.sources) == 1
    source = inventory.sources[0]
    assert source.kind == "mirror_roster"
    assert source.source_digest == hashlib.sha256(payload).hexdigest()
    assert source.document == document
    assert [(g.kind, g.code) for g in inventory.gaps] == [("mirror_assignments", "invalid_source")]
    assert (course / "roster.v1.json").read_bytes() == payload
    assert (course / "assignments.v1.json").read_bytes() == b"{broken"
    assert inventory == collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)


def test_retained_history_discovery_and_digest_validation(legacy_root):
    workspace, mirror = legacy_root
    directory = workspace / "_System" / "Archive" / "Submission History" / "9" / "8"
    directory.mkdir(parents=True)
    body = "Synthetic draft."
    digest = hashlib.sha256(json.dumps({"body": body, "files": []}, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    document = {"schema_version": 1, "course_id": "9", "assignment_id": "8",
                "revision": 1, "updated_at": "", "attempts": {"Synthetic|2|time": {
                    "pseudonym": "Synthetic", "attempt": 2, "submitted_at": "time",
                    "observations": [{"digest": digest, "body": body, "captured_at": "time", "file_keys": []}]}}}
    path = directory / "history.v1.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert [(s.kind, s.course_id, s.assignment_id) for s in inventory.sources] == [("history_manifest", "9", "8")]
    assert not inventory.gaps
    document["attempts"]["Synthetic|2|time"]["observations"][0]["body"] = "Altered"
    path.write_text(json.dumps(document), encoding="utf-8")
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert not inventory.sources
    assert inventory.gaps[0].code == "invalid_source"


def test_invalid_ids_unknown_submission_and_no_arbitrary_scan(legacy_root):
    workspace, mirror = legacy_root
    arbitrary = workspace / "teacher-folder"
    arbitrary.mkdir()
    (arbitrary / "history.v1.json").write_text("bad")
    submissions = mirror / "1" / "submissions"
    submissions.mkdir(parents=True)
    (submissions / "3.v9.json").write_text("{}")
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror,
                                       course_ids=["../escape", "1"])
    assert not inventory.sources
    assert {g.code for g in inventory.gaps} == {"invalid_identity", "unsupported_record"}
    assert all(g.path != arbitrary for g in inventory.gaps)


def test_directory_symlink_is_never_followed(legacy_root, tmp_path):
    workspace, mirror = legacy_root
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (mirror / "1").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror,
                                       course_ids=["1"])
    assert not inventory.sources
    assert inventory.gaps
    assert {g.code for g in inventory.gaps} == {"unsafe_path"}


def test_bounded_size_is_explicit_gap(legacy_root, monkeypatch):
    workspace, mirror = legacy_root
    directory = mirror / "1"
    directory.mkdir()
    (directory / "roster.v1.json").write_bytes(b" " * 20)
    monkeypatch.setattr("api.mirror.evidence_import.MAX_SOURCE_BYTES", 10)
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert inventory.gaps[0].code == "source_too_large"


@pytest.fixture
def ordinary_manifest_folder(legacy_root):
    workspace, _ = legacy_root
    folder = workspace / "Student Work" / "Submissions" / "Synthetic Course — 73" / "Assignments" / "Draft — 86"
    folder.mkdir(parents=True)
    document = {"version": 1, "course_id": "73", "assignment_id": "86",
                "refreshed_at": "2026-01-01T00:00:00+00:00", "status": "current",
                "assignment_indicators": {}, "assignment_name": "Draft", "evidence": [],
                "binary_budget_bytes": 100, "binary_bytes_reserved": 0}
    return folder, document


def test_ordinary_manifest_bounded_named_folder_discovery(legacy_root, ordinary_manifest_folder):
    workspace, mirror = legacy_root
    folder, document = ordinary_manifest_folder
    path = folder / "_assignment_evidence_manifest.json"
    payload = json.dumps(document).encode()
    path.write_bytes(payload)
    # Arbitrary nested folders are never interpreted as additional sources.
    unrelated = folder / "arbitrary"
    unrelated.mkdir()
    (unrelated / path.name).write_text("broken")
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert not inventory.gaps
    assert len(inventory.sources) == 1
    source = inventory.sources[0]
    assert (source.kind, source.course_id, source.assignment_id) == ("ordinary_evidence_manifest", "73", "86")
    assert source.path == path
    assert source.document == document
    assert source.source_digest == hashlib.sha256(payload).hexdigest()
    assert path.read_bytes() == payload
    assert not collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror, course_ids=["99"]).sources


def test_ordinary_manifest_conflict_and_bad_identity_are_explicit(legacy_root, ordinary_manifest_folder):
    workspace, mirror = legacy_root
    folder, document = ordinary_manifest_folder
    (folder / "_assignment_evidence_manifest.json").write_text(json.dumps(document))
    conflict = folder / "_assignment_evidence_manifest-PC.json"
    document["assignment_id"] = "wrong"
    conflict.write_text(json.dumps(document))
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert len(inventory.sources) == 1
    assert [gap.code for gap in inventory.gaps].count("manifest_conflict") == 2
    assert [gap.code for gap in inventory.gaps].count("invalid_source") == 1


@pytest.mark.parametrize("relative", ["../outside.docx", "D:\\outside.docx", "\\\\server\\file.docx"])
def test_ordinary_manifest_refuses_escape_locators(legacy_root, ordinary_manifest_folder, relative):
    workspace, mirror = legacy_root
    folder, document = ordinary_manifest_folder
    document["evidence"] = [{"kind": "ordinary", "course_id": "73", "assignment_id": "86",
                              "user_id": "synthetic", "evidence_id": "file-1", "relative_path": relative}]
    (folder / "_assignment_evidence_manifest.json").write_text(json.dumps(document))
    inventory = collect_legacy_sources(workspace_root=workspace, mirror_cache_root=mirror)
    assert not inventory.sources
    assert inventory.gaps[0].code == "invalid_source"
