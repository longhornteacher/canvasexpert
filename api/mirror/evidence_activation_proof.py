"""Build activation coverage from a completed additive migration report.

The report and its detailed source mappings are private local checkpoints. This
module accepts a report location, never a caller-created report dictionary, and
returns only the small course coverage projection used by activation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from api.mirror.evidence_activation import REQUIRED_ACTIVATION_SCOPES
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_schema import canonical_bytes, digest_record


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SUPPORTED_SCOPES = {
    "mirror_roster": "course.roster",
    "mirror_assignments": "course.assignments",
    "mirror_submissions": "assignment.submissions",
    "history_manifest": "assignment.submissions",
}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid_migration_report") from exc
    if not isinstance(value, dict):
        raise ValueError("invalid_migration_report")
    return value


def _nonnegative_int(value) -> bool:
    return type(value) is int and value >= 0


def build_activation_coverage(*, report_path, source_key: str,
                              workspace_root) -> dict:
    """Validate the persisted migration proof and return sanitized course coverage.

    Detailed source mappings must still match the copies written alongside the
    report. All coverage is derived from verified imported source kinds and the
    report's semantic index verification; dry runs, gaps, incomplete imports,
    missing mappings, and inconsistent summaries are refused.
    """
    path = Path(report_path)
    canonical_report = (local_source_root(source_key, workspace_root) /
                        "migration" / "report.v1.json")
    try:
        if path.resolve(strict=True) != canonical_report.resolve(strict=False):
            raise ValueError("migration_report_location_mismatch")
    except OSError as exc:
        raise ValueError("invalid_migration_report") from exc

    report = _read_json(path)
    if report.get("schema_version") != 1 or report.get("activation") != "inactive":
        raise ValueError("invalid_migration_report")
    # A final report has summary; the checkpoint format deliberately lacks it.
    summary = report.get("summary")
    mappings, gaps = report.get("mappings"), report.get("gaps")
    if (not isinstance(summary, dict) or not isinstance(mappings, list)
            or not mappings or not isinstance(gaps, list) or gaps):
        raise ValueError("incomplete_migration_report")
    if (summary.get("dry_run") is not False or summary.get("complete") is not True
            or summary.get("activation") != "inactive" or summary.get("gaps") != 0):
        raise ValueError("incomplete_migration_report")

    for field in ("sources", "observations", "facts", "originals_verified",
                  "original_bytes_verified", "inventory_only"):
        if not _nonnegative_int(summary.get(field)):
            raise ValueError("invalid_migration_report")
    if summary["sources"] != len(mappings):
        raise ValueError("migration_report_mismatch")

    verified_by_course = {}
    semantic = summary.get("semantic_verification")
    if not isinstance(semantic, list) or not semantic:
        raise ValueError("unverified_migration_report")
    for entry in semantic:
        if (not isinstance(entry, dict) or not isinstance(entry.get("course_id"), str)
                or not entry["course_id"].isdecimal()
                or entry.get("status") != "verified"
                or not _nonnegative_int(entry.get("expected_observations"))
                or not _DIGEST.fullmatch(str(entry.get("revision") or ""))
                or entry["course_id"] in verified_by_course):
            raise ValueError("unverified_migration_report")
        verified_by_course[entry["course_id"]] = entry

    total_facts = set()
    verified_originals = {}
    totals = {"observations": 0, "originals_verified": 0,
              "original_bytes_verified": 0, "inventory_only": 0}
    scopes_by_course = {}
    expected_source_files = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise ValueError("invalid_migration_report")
        course = mapping.get("course_id")
        kind, source_digest = mapping.get("kind"), mapping.get("source_digest")
        source_path = mapping.get("path")
        if (not isinstance(course, str) or not course.isdecimal()
                or not isinstance(kind, str) or not _DIGEST.fullmatch(str(source_digest or ""))
                or not isinstance(source_path, str)):
            raise ValueError("invalid_migration_report")
        identity = digest_record({"kind": kind, "path": source_path,
                                  "source_digest": source_digest})
        if identity in expected_source_files:
            raise ValueError("migration_report_mismatch")
        expected_source_files.add(identity)
        saved_path = path.parent / "sources" / f"{identity}.json"
        saved = _read_json(saved_path)
        if canonical_bytes(saved) != canonical_bytes(mapping):
            raise ValueError("migration_mapping_mismatch")
        if mapping.get("gaps") or mapping.get("status") not in {"imported", "inventory_only"}:
            raise ValueError("incomplete_migration_report")
        facts = mapping.get("facts")
        commits = mapping.get("commits")
        observations = mapping.get("observations")
        originals = mapping.get("originals")
        if not all(isinstance(value, list) for value in
                   (facts, commits, observations, originals)):
            raise ValueError("invalid_migration_report")
        if any(not _DIGEST.fullmatch(str(value or "")) for value in facts + commits):
            raise ValueError("invalid_migration_report")
        if any(not isinstance(row, dict) or not _DIGEST.fullmatch(
                str(row.get("fact_digest") or "")) for row in observations):
            raise ValueError("invalid_migration_report")
        total_facts.update(facts)
        totals["observations"] += len(observations)
        for row in originals:
            if not isinstance(row, dict):
                raise ValueError("invalid_migration_report")
            if row.get("status") == "verified":
                digest, byte_count = row.get("digest"), row.get("bytes")
                if not _DIGEST.fullmatch(str(digest or "")) or not _nonnegative_int(byte_count):
                    raise ValueError("invalid_migration_report")
                if digest in verified_originals and verified_originals[digest] != byte_count:
                    raise ValueError("migration_report_mismatch")
                verified_originals[digest] = byte_count
        if mapping["status"] == "inventory_only":
            totals["inventory_only"] += 1
        scope = _SUPPORTED_SCOPES.get(kind)
        if scope and mapping["status"] == "imported" and facts and commits:
            scopes_by_course.setdefault(course, set()).add(scope)

    totals["facts"] = len(total_facts)
    totals["originals_verified"] = len(verified_originals)
    totals["original_bytes_verified"] = sum(verified_originals.values())
    if totals != {key: summary[key] for key in totals}:
        raise ValueError("migration_report_mismatch")
    observed = sorted({mapping["course_id"] for mapping in mappings})
    if observed != summary.get("observed_course_ids"):
        raise ValueError("migration_report_mismatch")
    if set(verified_by_course) != set(scopes_by_course):
        raise ValueError("unverified_migration_report")
    for course, entry in verified_by_course.items():
        expected_refs = {row["fact_digest"] for m in mappings
                         if m.get("course_id") == course
                         for row in m.get("observations", [])}
        if entry["expected_observations"] != len(expected_refs):
            raise ValueError("migration_report_mismatch")

    courses = {}
    for course in sorted(verified_by_course):
        scopes = sorted(scopes_by_course[course])
        # A migration report proves only its explicitly imported scopes. It
        # cannot manufacture context/section coverage absent from the report.
        if not scopes or not set(scopes).issubset(REQUIRED_ACTIVATION_SCOPES):
            raise ValueError("incomplete_migration_coverage")
        courses[course] = {"verification_state": "verified",
                           "verified_import": True,
                           "index_revision": verified_by_course[course]["revision"],
                           "required_scopes": scopes}
    return {"courses": courses}
