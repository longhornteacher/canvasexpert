"""Unit tests for the shared atomic-file/quarantine primitives.

course_catalog.py and operation_ledger/storage.py each used to hand-roll their
own corrupt-file quarantine rename (with one outlier format). They now both
delegate to quarantine_corrupt_file here.
"""
import re

from api import storage_support


def test_utc_compact_stamp_matches_expected_shape():
    stamp = storage_support.utc_compact_stamp()
    assert re.fullmatch(r"\d{8}T\d{6}\d{6}Z", stamp)


def test_quarantine_corrupt_file_moves_and_renames(tmp_path):
    bad = tmp_path / "registry.v1.json"
    bad.write_text("not json", encoding="utf-8")
    target_dir = tmp_path / "quarantine"

    storage_support.quarantine_corrupt_file(bad, target_dir)

    assert not bad.exists()
    matches = list(target_dir.glob("registry.v1.json.*.corrupt"))
    assert len(matches) == 1


def test_quarantine_corrupt_file_is_a_noop_when_source_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    target_dir = tmp_path / "quarantine"

    storage_support.quarantine_corrupt_file(missing, target_dir)

    assert not target_dir.exists()
