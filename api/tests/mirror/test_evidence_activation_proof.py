"""Report-derived activation proof laws; all report contents are synthetic."""
from __future__ import annotations

import json

import pytest

from api.mirror.evidence_activation_proof import build_activation_coverage
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_schema import canonical_bytes, digest_record


SOURCE = "a" * 64


def _report(tmp_path, *, dry_run=False, incomplete=False):
    root = local_source_root(SOURCE, tmp_path) / "migration"
    sources = root / "sources"
    sources.mkdir(parents=True)
    roster = {"kind": "mirror_roster", "path": "synthetic/roster.json",
              "course_id": "1", "assignment_id": None,
              "source_digest": "b" * 64, "facts": ["c" * 64],
              "commits": ["d" * 64], "originals": [], "observations": [],
              "gaps": [], "status": "imported"}
    submissions = {"kind": "mirror_submissions", "path": "synthetic/submissions.json",
                   "course_id": "1", "assignment_id": "10",
                   "source_digest": "e" * 64, "facts": ["f" * 64],
                   "commits": ["0" * 64], "originals": [],
                   "observations": [{"fact_digest": "1" * 64}],
                   "gaps": [], "status": "imported"}
    mappings = [roster, submissions]
    for mapping in mappings:
        identity = digest_record({"kind": mapping["kind"], "path": mapping["path"],
                                  "source_digest": mapping["source_digest"]})
        (sources / f"{identity}.json").write_bytes(canonical_bytes(mapping))
    document = {"schema_version": 1, "activation": "inactive",
        "summary": {"sources": 2, "observations": 1, "facts": 2,
            "observed_course_ids": ["1"], "originals_verified": 0,
            "original_bytes_verified": 0, "inventory_only": 0,
            "gaps": int(incomplete), "dry_run": dry_run,
            "activation": "inactive", "complete": not incomplete,
            "semantic_verification": [{"course_id": "1", "expected_observations": 1,
                "status": "gap" if incomplete else "verified", "revision": "2" * 64}]},
        "mappings": mappings, "gaps": ([{"code": "synthetic_gap"}] if incomplete else [])}
    report = root / "report.v1.json"
    report.write_bytes(canonical_bytes(document))
    return report


def test_builds_only_sanitized_course_coverage_from_verified_report(tmp_path):
    report = _report(tmp_path)
    coverage = build_activation_coverage(report_path=report, source_key=SOURCE,
                                         workspace_root=tmp_path)
    assert coverage == {"courses": {"1": {
        "verification_state": "verified", "verified_import": True,
        "index_revision": "2" * 64,
        "required_scopes": ["assignment.submissions", "course.roster"]}}}
    assert set(coverage) == {"courses"}


@pytest.mark.parametrize("kwargs", [{"dry_run": True}, {"incomplete": True}])
def test_refuses_dry_run_or_incomplete_report(tmp_path, kwargs):
    report = _report(tmp_path, **kwargs)
    with pytest.raises(ValueError):
        build_activation_coverage(report_path=report, source_key=SOURCE,
                                  workspace_root=tmp_path)


def test_refuses_missing_or_tampered_mapping_checkpoint(tmp_path):
    report = _report(tmp_path)
    mappings = json.loads(report.read_text(encoding="utf-8"))["mappings"]
    mapping = mappings[0]
    identity = digest_record({"kind": mapping["kind"], "path": mapping["path"],
                              "source_digest": mapping["source_digest"]})
    source_file = report.parent / "sources" / f"{identity}.json"
    source_file.unlink()
    with pytest.raises(ValueError):
        build_activation_coverage(report_path=report, source_key=SOURCE,
                                  workspace_root=tmp_path)


def test_refuses_changed_report_mapping_even_when_checkpoint_exists(tmp_path):
    report = _report(tmp_path)
    document = json.loads(report.read_text(encoding="utf-8"))
    document["mappings"][0]["facts"] = ["9" * 64]
    report.write_bytes(canonical_bytes(document))
    with pytest.raises(ValueError, match="migration_mapping_mismatch"):
        build_activation_coverage(report_path=report, source_key=SOURCE,
                                  workspace_root=tmp_path)


def test_refuses_report_outside_canonical_source_location(tmp_path):
    report = _report(tmp_path)
    copied = tmp_path / "copied-report.json"
    copied.write_bytes(report.read_bytes())
    with pytest.raises(ValueError, match="migration_report_location_mismatch"):
        build_activation_coverage(report_path=copied, source_key=SOURCE,
                                  workspace_root=tmp_path)
