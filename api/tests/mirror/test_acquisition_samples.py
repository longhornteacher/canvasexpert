"""Shape checks for reusable synthetic CanvasMirror acquisition samples."""
from __future__ import annotations

import json

import pytest

from api.tests.mirror.acquisition_samples import (
    course_receipt_sample,
    presence_root,
)


PRESENCE_VARIANTS = (
    "advancing_incumbent",
    "stale_unchanged",
    "clean_release",
    "simultaneous_claim",
    "late_old_incarnation",
    "future_wallclock_free",
)


@pytest.mark.parametrize("variant", PRESENCE_VARIANTS)
def test_presence_samples_use_frozen_opaque_schema(tmp_path, variant):
    root = presence_root(tmp_path / variant, variant)
    assert root == tmp_path / variant / "_System" / "CanvasMirror Control" / "presence"
    files = sorted(root.glob("*.json"))
    assert files
    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert set(document) == {"schema_version", "writer_key", "sources"}
        assert document["schema_version"] == 1
        assert path.name == f"{document['writer_key']}.json"
        assert len(document["writer_key"]) == 32
        for source, claim in document["sources"].items():
            assert len(source) == 64
            assert set(claim) == {
                "incarnation", "claim_id", "lineage", "heartbeat_counter",
                "released", "advertised_commit_refs",
            }
            assert len(claim["incarnation"]) == len(claim["claim_id"]) == 32
            assert all(len(item) == 32 for item in claim["lineage"])
            assert all(len(item) == 64 for item in claim["advertised_commit_refs"])
            assert type(claim["heartbeat_counter"]) is int and claim["heartbeat_counter"] >= 0
            assert type(claim["released"]) is bool
            assert not any("wall" in key for key in claim)


def test_presence_scenarios_are_deterministic_and_distinct(tmp_path):
    encoded = {}
    for variant in PRESENCE_VARIANTS:
        one = presence_root(tmp_path / "one" / variant, variant)
        two = presence_root(tmp_path / "two" / variant, variant)
        payloads = lambda root: tuple((p.name, p.read_bytes()) for p in sorted(root.glob("*.json")))
        assert payloads(one) == payloads(two)
        encoded[variant] = payloads(one)
    assert len(set(encoded.values())) == len(PRESENCE_VARIANTS)


def test_receipt_samples_cover_complete_partial_empty_and_retained_shapes():
    full = course_receipt_sample("full")
    full_by_scope = {(scope.scope, scope.scope_id): scope for scope in full.scopes}
    assert full_by_scope[("assignment.submissions", "10")].complete is True
    assert full_by_scope[("assignment.submissions", "10")].rows
    assert full_by_scope[("assignment.comments", "10")].rows
    assert full_by_scope[("assignment.overrides", "10")].rows

    partial = course_receipt_sample("partial_submissions")
    partial_scope = next(scope for scope in partial.scopes if scope.scope == "assignment.submissions")
    assert partial_scope.rows and not partial_scope.complete and partial_scope.error_code

    empty = course_receipt_sample("empty_submissions")
    empty_scope = next(scope for scope in empty.scopes if scope.scope == "assignment.submissions")
    assert empty_scope.rows == () and empty_scope.complete

    retained = course_receipt_sample("deselected_course")
    marker = retained.scopes[0]
    assert marker.watermarks == {"selection": "deselected"}


def test_receipt_samples_cover_sparse_history_and_isolated_assignment_failure():
    sparse = course_receipt_sample("sparse_attempts")
    submission = next(scope for scope in sparse.scopes if scope.scope == "assignment.submissions").rows[0]
    history = submission["submission_history"]
    assert [row.get("attempt") for row in history] == [1, 2]
    assert "body" not in history[1] and "submitted_at" not in history[1]

    failed = course_receipt_sample("one_failed_assignment")
    failures = [scope for scope in failed.scopes if scope.error_code]
    assert len(failures) == 1
    assert failures[0].scope_id == "11" and failures[0].error_code == "timeout"
    assert any(scope.scope == "assignment.submissions" and scope.scope_id == "10"
               and scope.complete for scope in failed.scopes)


def test_receipt_variants_are_distinct_and_unknown_names_refuse(tmp_path):
    variants = ("full", "partial_submissions", "empty_submissions", "one_failed_assignment",
                "deselected_course", "sparse_attempts")
    receipts = [course_receipt_sample(name) for name in variants]
    assert len({repr(receipt) for receipt in receipts}) == len(variants)
    with pytest.raises(ValueError, match="unknown_presence_variant"):
        presence_root(tmp_path, "unknown")
    with pytest.raises(ValueError, match="unknown_course_receipt_variant"):
        course_receipt_sample("unknown")
