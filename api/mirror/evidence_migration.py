"""Additive, offline migration of explicitly inventoried pilot evidence.

Detailed mappings are private. Import commits never assert membership or freshness,
and content identity makes retrying any interrupted durable step harmless.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from api.mirror.evidence_import import collect_legacy_sources, MAX_SOURCE_BYTES
from api.mirror.evidence_index import EvidenceIndex, IndexReadError
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_publish import EvidencePublisher, PublicationRefused, _utc_stamp
from api.mirror.evidence_schema import canonical_bytes, digest_record, validate_fact, validate_digest, EvidenceValidationError
from api.mirror.original_archive import archive_original, associate_original, recover_original, ArchiveError, MAX_ORIGINAL_BYTES


@dataclass(frozen=True)
class MigrationResult:
    summary: dict
    report_path: Path | None
    mappings: tuple[dict, ...]
    gaps: tuple[dict, ...]


def _write_private(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(canonical_bytes(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _original_hash(path, maximum=MAX_ORIGINAL_BYTES):
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            count += len(chunk)
            if count > maximum:
                raise ArchiveError("original_too_large")
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_original(root, relative):
    path = root / str(relative).replace("\\", "/")
    anchor = root.resolve()
    if not path.resolve().is_relative_to(anchor):
        raise ArchiveError("unsafe_original")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ArchiveError("unsafe_original")
        if part == root:
            break
    return path


def _observations(source):
    """Yield private provenance plus supported safe projection, never infer attempt 1."""
    document = source.document
    if source.kind == "history_manifest":
        for key, attempt in sorted(document["attempts"].items()):
            observations = attempt["observations"] or [{"body": attempt.get("body", ""),
                                                        "captured_at": attempt.get("captured_at")}]
            for index, observation in enumerate(observations):
                yield key + ":" + str(index), attempt["pseudonym"], {
                    **attempt, "body": observation["body"]}, observation
    elif source.kind == "mirror_submissions":
        for pseudonym, submission in sorted(document["submissions"].items()):
            for key, row in sorted(submission.get("attempts", {}).items()):
                yield pseudonym + ":" + key, pseudonym, row, {
                    "captured_at": document.get("last_success_at"), "body_provenance": "mirror"}
            if isinstance(submission.get("current"), dict):
                yield pseudonym + ":current", pseudonym, submission["current"], {
                    "captured_at": document.get("last_success_at"), "body_provenance": "mirror"}


def _fact(publisher, kind, key, payload):
    record = validate_fact({"schema_version": 1, "source_key": publisher.source_key,
                           "course_id": publisher.course_id, "kind": kind,
                           "entity_key": key, "payload": publisher._scrub_payload(payload)})
    publisher.verify_safe(record)
    return record


def migrate_legacy_evidence(*, workspace_root, mirror_cache_root, source_key,
                            vault, course_ids=None, source_workspace_root=None,
                            dry_run=True, on_checkpoint=None):
    """Inventory or import into ``workspace_root`` without changing legacy files.

    ``on_checkpoint(phase, mapping)`` is an optional interruption/progress hook.
    Its exceptions propagate. Phases are ``fact``, ``archive``, ``commit`` and
    ``source`` and ``report``; successful durable objects are reused on retry.
    A dry run validates/scrubs and predicts digests without publishing anything.
    The caller supplies the current identity vault and explicit source/cache roots.
    A disposable migration-private index verifies supported semantic reads.
    No selection, activation, active index replacement, Canvas, or private-store rewrite.
    """
    workspace_root = Path(workspace_root)
    source_workspace_root = Path(source_workspace_root) if source_workspace_root is not None else workspace_root
    course_ids = tuple(map(str, course_ids)) if course_ids is not None else None
    inventory = collect_legacy_sources(workspace_root=source_workspace_root,
                                      mirror_cache_root=Path(mirror_cache_root), course_ids=course_ids)
    mappings = []
    gaps = [{"kind": gap.kind, "course_id": gap.course_id,
             "assignment_id": gap.assignment_id, "path": str(gap.path), "code": gap.code}
            for gap in inventory.gaps]
    observed_courses = {source.course_id for source in inventory.sources}
    if course_ids is not None:
        for course in sorted(set(map(str, course_ids)) - observed_courses):
            gaps.append({"kind": "inventory", "course_id": course,
                         "assignment_id": None, "code": "course_missing_local"})
    elif not inventory.sources and not inventory.gaps:
        gaps.append({"kind": "inventory", "course_id": "",
                     "assignment_id": None, "code": "no_legacy_sources"})
    publishers = {}
    attempt_versions = {}
    report_path = local_source_root(source_key, workspace_root) / "migration" / "report.v1.json"

    def checkpoint(phase, mapping):
        if not dry_run:
            if "source_digest" in mapping:
                source_identity = digest_record({"kind": mapping["kind"], "path": mapping["path"],
                                                  "source_digest": mapping["source_digest"]})
                _write_private(report_path.parent / "sources" / f"{source_identity}.json", mapping)
            _write_private(report_path, {"schema_version": 1, "activation": "inactive",
                                        "complete": False, "mappings": mappings, "gaps": gaps})
            if on_checkpoint:
                on_checkpoint(phase, mapping)

    def gap(mapping, code, **details):
        entry = {"source_digest": mapping["source_digest"], "code": code, **details}
        mapping["gaps"].append(entry)
        gaps.append(entry)

    for source in inventory.sources:
        mapping = {"kind": source.kind, "path": str(source.path), "course_id": source.course_id,
                   "assignment_id": source.assignment_id, "source_digest": source.source_digest,
                   "facts": [], "commits": [], "originals": [], "observations": [], "gaps": [],
                   "status": "planned" if dry_run else "imported"}
        mappings.append(mapping)
        try:
            if _original_hash(source.path, MAX_SOURCE_BYTES) != source.source_digest:
                raise PublicationRefused("source_changed")
            if source.course_id not in publishers:
                publishers[source.course_id] = EvidencePublisher(
                    workspace_root=workspace_root, source_key=source_key,
                    course_id=source.course_id, vault=vault)
            publisher = publishers[source.course_id]
        except (OSError, ArchiveError, PublicationRefused, EvidenceValidationError):
            gap(mapping, "source_unavailable")
            mapping["status"] = "gap"
            continue

        grouped = {}

        def publish(kind, entity_key, payload, scope, scope_id, provenance=None):
            try:
                fact = _fact(publisher, kind, entity_key, payload)
                digest = digest_record(fact)
                if not dry_run:
                    assert publisher.store.publish_fact(fact) == digest
                mapping["facts"].append(digest)
                grouped.setdefault((scope, scope_id), []).append((entity_key, digest))
                if provenance is not None:
                    mapping["observations"].append({**provenance, "fact_digest": digest,
                        "entity_key": entity_key, "submitted_at": fact["payload"]["submitted_at"],
                        "body_digest": hashlib.sha256(fact["payload"].get("body", "").encode("utf-8")).hexdigest()})
                    attempt_versions.setdefault((source.course_id, entity_key), set()).add(
                        (fact["payload"]["submitted_at"], fact["payload"].get("body")))
                checkpoint("fact", mapping)
            except (PublicationRefused, EvidenceValidationError):
                gap(mapping, "publication_refused", entity_key=entity_key)

        for old_key, pseudo, row, provenance in _observations(source):
            number = row.get("attempt")
            try:
                submitted = _utc_stamp(row.get("submitted_at"))
                updated = _utc_stamp(row.get("updated_at"))
                payload = {"assignment_id": source.assignment_id, "pseudonym": pseudo,
                           "attempt": number, "submitted_at": submitted,
                           "body": str(row.get("body") or ""),
                           "score": row.get("score"), "grade": row.get("grade"),
                           "late": bool(row.get("late")), "missing": bool(row.get("missing")),
                           "workflow_state": str(row.get("workflow_state") or ""), "updated_at": updated}
                key = f"attempt:{source.assignment_id}:{pseudo}:{number}"
                if number is None:
                    key = f"attempt_unresolved:{source.assignment_id}:{pseudo}:" + digest_record(payload)[:16]
                publish("attempt_observation", key, payload, "assignment.submissions", source.assignment_id,
                        {"legacy_key": old_key, "captured_at": provenance.get("captured_at"),
                         "body_provenance": provenance.get("body_provenance"),
                         "observation_digest": provenance.get("digest"),
                         "conflict": bool(row.get("conflict") or provenance.get("conflict"))})
            except PublicationRefused:
                gap(mapping, "invalid_timestamp", legacy_key=old_key)

        if source.kind == "mirror_roster":
            for pseudo in sorted(source.document["students"]):
                publish("student", f"student:{pseudo}", {"pseudonym": pseudo, "section_ids": []},
                        "course.roster", source.course_id)
        elif source.kind == "mirror_assignments":
            for assignment_id, row in sorted(source.document["assignments"].items()):
                publish("assignment", f"assignment:{assignment_id}", {
                    "assignment_id": assignment_id, "title": str(row.get("name") or row.get("title") or ""),
                    "description": publisher._scrub_text(str(row.get("description") or ""), html=True),
                    "points_possible": row.get("points_possible")}, "course.assignments", source.course_id)
        elif source.kind not in {"history_manifest", "mirror_submissions"}:
            mapping["status"] = "inventory_only"

        if source.kind == "history_manifest":
            for old_key, attempt in sorted(source.document["attempts"].items()):
                for file in attempt.get("files", []):
                    original = {"legacy_key": old_key, "file_key": file.get("key"),
                                "status": file.get("status"), "digest": file.get("digest")}
                    mapping["originals"].append(original)
                    if file.get("status") != "captured":
                        gap(mapping, "original_unavailable", legacy_key=old_key, file_key=file.get("key"))
                        continue
                    digest = file.get("digest")
                    # Actual legacy manifest provides digest-named blobs; never search
                    # arbitrary folders or follow linked original directories.
                    try:
                        validate_digest(digest)
                        files = source.path.parent / "files"
                        candidates = sorted(files.glob(digest + ".*")) if files.exists() else []
                        if len(candidates) != 1:
                            raise ArchiveError("missing_original")
                        candidate = _bounded_original(source_workspace_root,
                                                      candidates[0].relative_to(source_workspace_root))
                        if dry_run:
                            if _original_hash(candidate) != digest:
                                raise ArchiveError("source_digest_mismatch")
                        else:
                            archive_original(workspace_root, candidate, expected_digest=digest)
                            recover_original(workspace_root, digest)
                            original["association_digest"] = associate_original(
                                workspace_root, original_digest=digest, source_key=source_key,
                                course_id=source.course_id, assignment_id=source.assignment_id,
                                pseudonym=attempt["pseudonym"], attempt=attempt["attempt"],
                                filename=file["filename"], media_type=file.get("content_type") or "application/octet-stream",
                                source_digest=source.source_digest)
                        original["status"] = "verified"
                        original["bytes"] = candidate.stat().st_size
                        checkpoint("archive", mapping)
                    except (OSError, ArchiveError, EvidenceValidationError, KeyError):
                        original["status"] = "gap"
                        gap(mapping, "original_verification_failed", legacy_key=old_key, file_key=file.get("key"))

        if source.kind == "ordinary_evidence_manifest":
            for entry in source.document["evidence"]:
                original = {"file_key": entry["evidence_id"], "status": "gap", "digest": None}
                mapping["originals"].append(original)
                try:
                    relative = entry.get("original_relative_path") or entry.get("relative_path")
                    if not relative:
                        raise ArchiveError("missing_original")
                    path = _bounded_original(source_workspace_root, relative)
                    digest = _original_hash(path)
                    expected = entry.get("original_sha256")
                    if expected and expected != digest:
                        raise ArchiveError("source_digest_mismatch")
                    original["digest"] = digest
                    identities = [person for person in vault.entries()
                                  if str(person.get("canvas_id")) == entry["user_id"]
                                  and person.get("pseudonym") and not person.get("provisional")]
                    if len(identities) != 1:
                        raise PublicationRefused("identity_unresolved")
                    pseudo = identities[0]["pseudonym"]
                    if not dry_run:
                        archive_original(workspace_root, path, expected_digest=digest)
                        recover_original(workspace_root, digest)
                        # This manifest alone cannot establish a Canvas attempt time;
                        # retain its private provenance and archive relation only.
                        original["association_digest"] = associate_original(
                            workspace_root, original_digest=digest, source_key=source_key,
                            course_id=source.course_id, assignment_id=source.assignment_id,
                            pseudonym=pseudo, attempt=entry.get("attempt"), filename=path.name,
                            media_type="application/octet-stream", source_digest=source.source_digest)
                    original["status"] = "verified"
                    original["bytes"] = path.stat().st_size
                    checkpoint("archive", mapping)
                except (OSError, ArchiveError, PublicationRefused, EvidenceValidationError):
                    gap(mapping, "original_verification_failed", file_key=entry["evidence_id"])

        for (scope, scope_id), facts in sorted(grouped.items()):
            try:
                candidates = [source.document.get("last_success_at"), source.document.get("updated_at")]
                candidates.extend(o.get("captured_at") for o in mapping["observations"])
                stamps = []
                for candidate in candidates:
                    try:
                        parsed = _utc_stamp(candidate)
                    except PublicationRefused:
                        continue
                    if parsed:
                        stamps.append(parsed)
                if not stamps:
                    raise PublicationRefused("missing_timestamp")
                # Prefer the source envelope receipt. Fallback is a retained
                # observation's capture time, never this machine's clock.
                stamp = stamps[0]
                commit = {"schema_version": 1, "source_key": source_key, "course_id": source.course_id,
                          "scope": scope, "scope_id": scope_id, "writer_key": "legacy-import",
                          "run_id": source.source_digest, "parents": [],
                          "acquisition_started_at": stamp, "acquisition_finished_at": stamp,
                          "mode": "import", "membership_complete": False,
                          "record_refs": sorted({ref for _, ref in facts}),
                          "member_keys": [] if scope == "assignment.submissions" else sorted({key for key, _ in facts}),
                          "gaps": [{"code": code} for code in sorted({g["code"] for g in mapping["gaps"]})],
                          "watermarks": {}}
                digest = digest_record(commit)
                if not dry_run:
                    assert publisher.store.publish_commit(commit) == digest
                mapping["commits"].append(digest)
                checkpoint("commit", mapping)
            except (PublicationRefused, EvidenceValidationError):
                gap(mapping, "commit_refused")
        mapping["facts"] = sorted(set(mapping["facts"]))
        if mapping["gaps"]:
            mapping["status"] = "gap"
        checkpoint("source", mapping)

    verification = []
    if not dry_run:
        verification_root = report_path.parent / "verification"
        verification_root.mkdir(parents=True, exist_ok=True)
        for course, publisher in sorted(publishers.items()):
            expected = {o["fact_digest"]: (o["entity_key"], o["submitted_at"], o["body_digest"])
                        for m in mappings if m["course_id"] == course and m["commits"]
                        for o in m["observations"]}
            checked = {"course_id": course, "expected_observations": len(expected), "status": "verified"}
            verification.append(checked)
            try:
                snapshot = publisher.store.scan()
                index = EvidenceIndex(verification_root / course / f"{snapshot.revision}.sqlite3")
                revision = index.ingest(snapshot, selected_courses=())
                checked["revision"] = revision
                actual = {}
                offset = 0
                while True:
                    page = index.query_page("attempt_history", source_key=source_key,
                                            course_id=course, limit=100, offset=offset, revision=revision)
                    for row in page["records"]:
                        if row["fact_ref"] in expected:
                            payload = json.loads(row["payload"])
                            actual[row["fact_ref"]] = (row["entity_key"], row["submitted_at"],
                                hashlib.sha256(payload.get("body", "").encode("utf-8")).hexdigest())
                    if page["next_offset"] is None:
                        break
                    offset = page["next_offset"]
                if actual != expected:
                    checked["status"] = "gap"
                    gaps.append({"course_id": course, "code": "semantic_read_mismatch"})
            except (OSError, ValueError, IndexReadError):
                checked["status"] = "gap"
                gaps.append({"course_id": course, "code": "semantic_verification_failed"})
            checkpoint("verification", checked)

    summary = {"sources": len(mappings), "observations": sum(len(m["observations"]) for m in mappings),
               "observed_course_ids": sorted({m["course_id"] for m in mappings}),
               "attempts": len(attempt_versions),
               "timestamp_body_conflicts": sum(len(versions) > 1 for versions in attempt_versions.values()),
               "facts": len({f for m in mappings for f in m["facts"]}),
               "originals_verified": len({o["digest"] for m in mappings for o in m["originals"] if o["status"] == "verified"}),
               "original_bytes_verified": sum({o["digest"]: o["bytes"] for m in mappings for o in m["originals"]
                                               if o["status"] == "verified"}.values()),
               "gaps": len(gaps), "inventory_only": sum(m["status"] == "inventory_only" for m in mappings),
               "dry_run": dry_run, "activation": "inactive", "complete": not gaps}
    summary["semantic_verification"] = verification
    if not dry_run:
        _write_private(report_path, {"schema_version": 1, "summary": summary,
                                    "mappings": mappings, "gaps": gaps})
        if on_checkpoint:
            on_checkpoint("report", summary)
    return MigrationResult(summary, None if dry_run else report_path, tuple(mappings), tuple(gaps))
