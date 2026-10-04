from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api.tests.mirror import legacy_samples as samples
from api.mirror.attempt_text import digest as attempt_text_digest
from api.mirror import store


def _legacy_observation_digest(body, files):
    payload = json.dumps({"body": body, "files": files}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("variant", [
    "sparse", "rich", "timestamp_conflict", "malformed_manifest", "malformed_file",
    "computer_a", "computer_b", "new_quiz_inventory",
])
def test_every_sample_mirror_submission_document_passes_legacy_validator(tmp_path, variant):
    root = samples.legacy_root(tmp_path, variant)
    mirror_path = (root / "cache" / "Canvas Mirror" / samples.COURSE /
                   "submissions" / f"{samples.ASSIGNMENT}.v1.json")
    document = json.loads(mirror_path.read_text(encoding="utf-8"))

    assert store.validate_submissions(document, samples.COURSE, samples.ASSIGNMENT) == document


@pytest.mark.parametrize("variant", ["sparse", "rich", "timestamp_conflict"])
def test_legacy_root_writes_mirror_and_retained_history(tmp_path, variant):
    root = samples.legacy_root(tmp_path, variant=variant)
    expected = samples.expected_observations(variant)

    mirror_path = root / "cache" / "Canvas Mirror" / samples.COURSE / "submissions" / f"{samples.ASSIGNMENT}.v1.json"
    manifest_path = (root / "_System" / "Archive" / "Submission History" /
                     samples.COURSE / samples.ASSIGNMENT / "history.v1.json")
    mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert mirror["course_id"] == samples.COURSE
    assert samples.PSEUDONYM in mirror["submissions"]
    assert expected["ordinary_attempts"]
    assert manifest["attempts"]
    if variant == "timestamp_conflict":
        assert expected["timestamp_conflict"] is True
        assert len([record for record in manifest["attempts"].values()
                    if record["attempt"] == 1]) == 2
        assert any(record["conflict"] for record in manifest["attempts"].values())


def test_rich_sample_retains_original_bytes_and_observation_metadata(tmp_path):
    root = samples.legacy_root(tmp_path, "rich")
    metadata = samples.sample_metadata("rich")
    blob = (root / "_System" / "Archive" / "Submission History" /
            samples.COURSE / samples.ASSIGNMENT / "files" /
            f"{metadata['retained_blob_sha256']}.txt")

    assert blob.read_bytes() == samples.PAYLOAD
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == metadata["retained_blob_sha256"]
    assert samples.expected_observations("rich")["retained_original_sha256"] == metadata["retained_blob_sha256"]


@pytest.mark.parametrize("variant", ["sparse", "rich", "timestamp_conflict", "computer_a", "computer_b"])
def test_observation_digests_match_legacy_canonicalization(tmp_path, variant):
    root = samples.legacy_root(tmp_path, variant)
    manifest = (root / "_System" / "Archive" / "Submission History" /
                samples.COURSE / samples.ASSIGNMENT / "history.v1.json")
    records = json.loads(manifest.read_text(encoding="utf-8"))["attempts"]

    for record in records.values():
        for observation in record["observations"]:
            associated = [
                {key: file_entry[key] for key in ("key", "filename", "size", "content_type")}
                for file_key in observation["file_keys"]
                for file_entry in record.get("files", []) if file_entry.get("key") == file_key
            ]
            body = observation["body"] or ""
            assert observation["digest"] == _legacy_observation_digest(body, associated)
            assert observation["text_digest"] == attempt_text_digest(body)


@pytest.mark.parametrize("variant", ["malformed_manifest", "malformed_file"])
def test_corrupt_samples_keep_independent_mirror_source(tmp_path, variant):
    root = samples.legacy_root(tmp_path, variant)
    mirror = root / "cache" / "Canvas Mirror" / samples.COURSE / "submissions" / f"{samples.ASSIGNMENT}.v1.json"
    manifest = (root / "_System" / "Archive" / "Submission History" /
                samples.COURSE / samples.ASSIGNMENT / "history.v1.json")

    assert mirror.is_file()
    assert samples.expected_observations(variant)["malformed_source"] is True
    if variant == "malformed_manifest":
        assert manifest.read_text(encoding="utf-8") == "{broken"
    else:
        blob = manifest.parent / "files" / f"{samples.PAYLOAD_SHA256}.txt"
        assert blob.read_bytes() == b"corrupt bytes"


def test_two_computer_samples_have_distinct_local_evidence(tmp_path):
    desktop = samples.legacy_root(tmp_path, "computer_a")
    laptop = samples.legacy_root(tmp_path, "computer_b")
    desktop_expected = samples.expected_observations("computer_a")["ordinary_attempts"]
    laptop_expected = samples.expected_observations("computer_b")["ordinary_attempts"]

    assert desktop != laptop
    assert desktop_expected[-1]["attempt"] == 3
    assert laptop_expected[-1]["attempt"] == 4
    assert desktop_expected[-1]["submitted_at"] != laptop_expected[-1]["submitted_at"]


def test_new_quiz_sample_is_private_inventory_only(tmp_path):
    root = samples.legacy_root(tmp_path, "new_quiz_inventory")
    expected = samples.expected_observations("new_quiz_inventory")
    quiz_dir = root / "cache" / "Canvas Mirror" / samples.COURSE / "new_quizzes" / samples.QUIZ_ASSIGNMENT
    quiz = json.loads((quiz_dir / "quiz.v2.json").read_text(encoding="utf-8"))
    student = json.loads((quiz_dir / "students" / "synthetic-student.v2.json").read_text(encoding="utf-8"))

    assert expected["ordinary_attempts"] == []
    assert expected["inventory_only_quiz_assignment_ids"] == [samples.QUIZ_ASSIGNMENT]
    assert quiz["assignment_id"] == student["assignment_id"] == samples.QUIZ_ASSIGNMENT
    assert student["attempts"]


def test_builder_rejects_unknown_variant(tmp_path):
    with pytest.raises(ValueError, match="unknown legacy sample variant"):
        samples.legacy_root(tmp_path, "surprise")
