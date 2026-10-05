"""Publish already acquired course receipts; never fetch or activate a backend.

Rows are private Canvas dictionaries, not safe output. Supported exact scopes:
``course.context`` (course objects), ``course.assignments`` (assignment objects),
``course.roster`` (users with optional enrollments), ``assignment.submissions``
(submission rows with optional submission_history), ``assignment.comments``
(flattened comments with recipient user_id, optional attempt/author_role), and
``assignment.overrides`` (overrides with student_ids/section_id/group_id).
Structure scopes are ``course.groups`` (groups with acquired user_ids/users),
``course.modules`` (modules with acquired items), ``course.pages`` (page details
with body), and ``course.assignment_groups`` (assignment-group objects). The
collection's complete flag must include every nested membership/detail fetch.
Scope ids are the course id or exact assignment id respectively. Comments must
have their own pagination receipt: embedded submission_comments do not prove
comment membership. A filtered acquisition uses mode='delta', never 'snapshot'.
Attachment bytes are not acquired here: each observed attachment publishes an
opaque ``assignment.attachments`` association fact (key, media type, size,
status) that the private job queue later republishes with a verified digest.
Unsupported lifecycle fields remain explicit gaps. No raw receipt is persisted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib

from api import feedback_scrub
from api.mirror.evidence_publish import EvidencePublisher, PublicationRefused, _utc_now, _utc_stamp
from api.mirror.evidence_schema import (
    EvidenceValidationError, SCOPE_KINDS, canonical_bytes, validate_component,
    validate_commit,
)
from api import roster_service


@dataclass(frozen=True)
class ScopeReceipt:
    scope: str
    scope_id: str
    rows: tuple[dict, ...]
    complete: bool = False
    error_code: str | None = None
    mode: str = "snapshot"
    watermarks: dict | None = None


@dataclass(frozen=True)
class CourseAcquisitionReceipt:
    course_id: str
    acquisition_started_at: str
    acquisition_finished_at: str
    scopes: tuple[ScopeReceipt, ...]


@dataclass(frozen=True)
class AcquisitionPublication:
    fact_refs: tuple[str, ...]
    commit_refs: tuple[str, ...]
    gaps: tuple[str, ...]
    successful_scopes: tuple[tuple[str, str], ...]


_STUDENT_BEARING_SCOPES = frozenset({
    "course.roster", "course.groups", "assignment.submissions",
    "assignment.comments", "assignment.overrides", "assignment.attachments",
    "assignment.extractions", "assignment.notes",
})


def _id(value):
    value = str(value if value is not None else "")
    if not value.isdecimal():
        raise PublicationRefused("invalid_navigation_id")
    return value


def _stamp_fields(row, fields):
    return {key: _utc_stamp(row[key]) for key in fields if key in row}


def _submission(publisher, row, assignment_id, pseudo):
    # Sparse observations stay sparse: do not turn absent late/missing/body into
    # false/empty values, or copy current values into an earlier history row.
    payload = {"assignment_id": assignment_id, "pseudonym": pseudo,
               "attempt": row.get("attempt")}
    for key in ("body", "score", "grade", "late", "missing", "workflow_state"):
        if key in row:
            payload[key] = row[key]
    payload.update(_stamp_fields(row, ("submitted_at", "updated_at")))
    if "cached_due_date" in row or "effective_due_at" in row:
        payload["effective_due_at"] = _utc_stamp(row.get("effective_due_at", row.get("cached_due_date")))
    return payload


def _attachment_descriptor(item):
    """Extract only the fields needed for an opaque association; never a URL."""
    if not isinstance(item, dict):
        return None
    file_id = str(item.get("id") or item.get("file_id") or "")
    filename = str(item.get("filename") or item.get("display_name") or "")
    try:
        size = max(0, int(item.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    media_type = str(item.get("content-type") or item.get("content_type") or "")[:160]
    if not file_id and not filename:
        return None
    return {"file_id": file_id, "filename": filename, "size": size,
            "media_type": media_type}


def _attachment_key(descriptor):
    """Opaque, stable identity: a hash of the Canvas file id or stable metadata."""
    if descriptor["file_id"]:
        return hashlib.sha256(f"file:{descriptor['file_id']}".encode("utf-8")).hexdigest()
    return hashlib.sha256(canonical_bytes({
        "filename": descriptor["filename"], "size": descriptor["size"],
        "media_type": descriptor["media_type"]})).hexdigest()


def iter_attachment_descriptors(observation):
    """Yield ``(attempt, key, media_type, size, filename, file_id)`` per attachment.

    Shared by safe association publication and the private job queue so both
    derive the same opaque key. The filename and raw file id are private and
    never published; the file id lets the resolver reacquire a fresh URL.
    """
    if not isinstance(observation, dict):
        return
    for item in observation.get("attachments") or []:
        descriptor = _attachment_descriptor(item)
        if descriptor is None:
            continue
        yield (observation.get("attempt"), _attachment_key(descriptor),
               descriptor["media_type"] or "application/octet-stream",
               descriptor["size"], descriptor["filename"], descriptor["file_id"])


def _attachment_facts(publisher, assignment_id, pseudo, observation, gaps):
    """Yield one safe association fact per attachment; never a filename or URL.

    The original bytes are captured later by the private job queue, which
    republishes the same entity key with ``status='captured'`` and a digest.
    """
    for attempt, key, media_type, size, _filename, _file_id in iter_attachment_descriptors(observation):
        payload = {
            "assignment_id": assignment_id, "pseudonym": pseudo,
            "attempt": attempt,
            "attachment_key": key, "original_digest": None,
            "media_type": media_type, "size": size, "status": "pending", "revision": 1,
        }
        yield "attachment", f"attachment:{assignment_id}:{pseudo}:{attempt}:{key}", payload, frozenset()


def _rows(publisher, scope, row, gaps):
    """Yield allowlisted (kind, key, payload, HTML fields) before privacy gate."""
    name, sid = scope.scope, scope.scope_id
    if name == "course.context":
        if row.get("id") is not None and _id(row["id"]) != publisher.course_id:
            raise PublicationRefused("scope_mismatch")
        payload = {"title": row.get("name", row.get("title", ""))}
        if "workflow_state" in row:
            payload["workflow_state"] = row["workflow_state"]
        payload.update(_stamp_fields(row, ("start_at", "end_at", "conclude_at")))
        if "restrict_enrollments_to_course_dates" in row:
            payload["restrict_enrollments_to_course_dates"] = row["restrict_enrollments_to_course_dates"]
        if "concluded" in row:
            payload["course_concluded"] = row["concluded"] is True
        if "enrollment_states" in row:
            payload["enrollment_states"] = row["enrollment_states"]
        term = row.get("term")
        if isinstance(term, dict) and term.get("end_at") is not None:
            payload.update(_stamp_fields({"term_end_at": term["end_at"]}, ("term_end_at",)))
        yield "course", f"course:{sid}", payload, frozenset()
    elif name == "course.sections":
        section_id = _id(row.get("id", row.get("section_id")))
        yield "section", f"section:{section_id}", {
            "section_id": section_id,
            "name": str(row.get("name") or f"Section {section_id}"),
        }, frozenset()
    elif name == "course.roster":
        pseudo = publisher._pseudo(row.get("id", row.get("user_id")), str(row.get("name") or ""))
        sections = {_id(i) for i in row.get("section_ids", [])}
        for enrollment in row.get("enrollments") or []:
            if not isinstance(enrollment, dict):
                raise PublicationRefused("invalid_response")
            if enrollment.get("course_section_id") is not None:
                sections.add(_id(enrollment["course_section_id"]))
        yield "student", f"student:{pseudo}", {"pseudonym": pseudo,
                "section_ids": sorted(sections)}, frozenset()
    elif name == "course.assignments":
        aid = _id(row.get("id", row.get("assignment_id")))
        rubric = []
        for criterion in row.get("rubric") or []:
            ratings = [{"rating_id": str(r.get("id", r.get("rating_id", ""))),
                        "description": r.get("description", ""), "points": r.get("points")}
                       for r in criterion.get("ratings") or []]
            rubric.append({"criterion_id": str(criterion.get("id", criterion.get("criterion_id", ""))),
                           "description": criterion.get("description", ""),
                           "points": criterion.get("points"), "ratings": ratings})
        payload = {"assignment_id": aid, "title": row.get("name", row.get("title", "")),
                   "description": row.get("description", ""), "rubric": rubric}
        if "points_possible" in row:
            payload["points_possible"] = row["points_possible"]
        payload.update(_stamp_fields(row, ("due_at", "unlock_at", "lock_at", "updated_at")))
        for key in ("published", "submission_types"):
            if key in row:
                payload[key] = row[key]
        if row.get("assignment_group_id") is not None:
            payload["assignment_group_id"] = _id(row["assignment_group_id"])
        if "all_dates" in row:
            payload["all_dates"] = []
            for dates in row["all_dates"]:
                entry = {"base": dates.get("base", False)}
                entry.update(_stamp_fields(dates, ("due_at", "unlock_at", "lock_at")))
                if dates.get("id", dates.get("override_id")) is not None:
                    entry["override_id"] = _id(dates.get("id", dates.get("override_id")))
                if "title" in dates:
                    entry["title"] = dates["title"]
                payload["all_dates"].append(entry)
        yield "assignment", f"assignment:{aid}", payload, frozenset({"description"})
    elif name == "course.groups":
        category_id = row.get("group_category_id")
        category_name = row.get("group_category_name")
        if category_id in (None, "") or not str(category_name or "").strip():
            gaps.append("group_category_unavailable")
        category_key = hashlib.sha256(
            f"{publisher.source_key}:{publisher.course_id}:{category_id}".encode("utf-8")
        ).hexdigest() if category_id not in (None, "") else None
        if row.get("_category_only") is True:
            if category_key is not None and str(category_name or "").strip():
                yield "group_category", f"group_category:{category_key}", {
                    "category_key": category_key,
                    "category_name": str(category_name).strip(),
                }, frozenset()
            return
        gid = _id(row.get("id", row.get("group_id")))
        users = row.get("user_ids", row.get("student_ids", row.get("users")))
        if users is None:
            gaps.append("group_membership_unavailable")
            users = []
        pseudos = []
        for user in users:
            try:
                pseudos.append(publisher._pseudo(user.get("id", user.get("user_id"))
                                                  if isinstance(user, dict) else user))
            except PublicationRefused as exc:
                gaps.append(exc.code)
        payload = {"group_id": gid,
            "title": row.get("name", row.get("title", "")),
            "student_pseudonyms": sorted(set(pseudos))}
        if category_key is not None and str(category_name or "").strip():
            payload.update({"category_key": category_key,
                            "category_name": str(category_name).strip()})
        yield "group", f"group:{gid}", payload, frozenset()
    elif name == "course.modules":
        mid = _id(row.get("id", row.get("module_id")))
        if "items" not in row:
            gaps.append("module_items_unavailable")
        items = []
        for item in row.get("items") or []:
            normalized = {"item_id": _id(item.get("id", item.get("item_id"))),
                          "title": item.get("title", ""), "type": item.get("type"),
                          "position": item.get("position", 0)}
            for key in ("content_id", "page_id"):
                if item.get(key) is not None:
                    normalized[key] = _id(item[key])
            items.append(normalized)
        payload = {"module_id": mid, "title": row.get("name", row.get("title", "")),
                   "position": row.get("position", 0), "items": items}
        if "published" in row:
            payload["published"] = row["published"]
        yield "module", f"module:{mid}", payload, frozenset()
    elif name == "course.pages":
        pid = _id(row.get("page_id", row.get("id")))
        if "body" not in row and "body_text" not in row:
            gaps.append("page_body_unavailable")
        payload = {"page_id": pid, "title": row.get("title", ""),
                   "body": row.get("body", row.get("body_text", ""))}
        payload.update(_stamp_fields(row, ("updated_at",)))
        for key in ("published", "front_page"):
            if key in row:
                payload[key] = row[key]
        yield "page", f"page:{pid}", payload, frozenset({"body"}) if "body" in row else frozenset()
    elif name == "course.assignment_groups":
        gid = _id(row.get("id", row.get("assignment_group_id")))
        yield "assignment_group", f"assignment_group:{gid}", {
            "assignment_group_id": gid, "title": row.get("name", row.get("title", "")),
            "position": row.get("position", 0), "group_weight": row.get("group_weight", 0)}, frozenset()
    elif name == "assignment.submissions":
        if row.get("assignment_id") is not None and _id(row["assignment_id"]) != sid:
            raise PublicationRefused("scope_mismatch")
        pseudo = publisher._pseudo(row.get("user_id"))
        html = frozenset({"body"}) if row.get("submission_type") == "online_text_entry" else frozenset()
        try:
            payload = _submission(publisher, row, sid, pseudo)
            yield "submission", f"submission:{sid}:{pseudo}", payload, html
        except PublicationRefused as exc:
            gaps.append(exc.code)
        for observation in (row, *(row.get("submission_history") or [])):
            if not isinstance(observation, dict):
                gaps.append("invalid_attempt")
                continue
            try:
                payload = _submission(publisher, observation, sid, pseudo)
                payload.setdefault("submitted_at", None)
                attempt = payload["attempt"]
                if attempt is None:
                    # Use scrubbed semantic content, never a raw student-derived
                    # hash, for unresolved observation identity.
                    safe = publisher._scrub_payload(payload, html_fields=html)
                    suffix = hashlib.sha256(canonical_bytes(safe)).hexdigest()[:16]
                    key = f"attempt_unresolved:{sid}:{pseudo}:{suffix}"
                else:
                    key = f"attempt:{sid}:{pseudo}:{attempt}"
                observed_html = frozenset({"body"}) if observation.get(
                    "submission_type", row.get("submission_type")) == "online_text_entry" else frozenset()
                yield "attempt_observation", key, payload, observed_html
            except (PublicationRefused, EvidenceValidationError):
                gaps.append("invalid_attempt")
            # Attachment associations publish under their own scope so a missing
            # original never blocks submission membership. The private job queue
            # later republishes the same key with a captured digest.
            for kind, key, payload, html_fields in _attachment_facts(
                    publisher, sid, pseudo, observation, gaps):
                yield kind, key, payload, html_fields
        if row.get("submission_comments"):
            gaps.append("comments_scope_required")
    elif name == "assignment.comments":
        if row.get("assignment_id") is not None and _id(row["assignment_id"]) != sid:
            raise PublicationRefused("scope_mismatch")
        pseudo = publisher._pseudo(row.get("user_id"))
        cid = _id(row.get("id", row.get("comment_id")))
        payload = {"assignment_id": sid, "pseudonym": pseudo, "comment_id": cid,
                   "text": row.get("comment", row.get("text", ""))}
        payload.update(_stamp_fields(row, ("created_at",)))
        if "attempt" in row:
            payload["attempt"] = row["attempt"]
        role = row.get("author_role")
        if role in {"teacher", "ta", "system"}:
            payload["author_role"] = role
        elif row.get("author_id") is not None:
            payload["author_pseudonym"] = publisher._pseudo(row["author_id"])
            payload["author_role"] = "student"
        else:
            gaps.append("comment_author_unresolved")
        yield "comment", f"comment:{sid}:{cid}", payload, frozenset()
    elif name == "assignment.overrides":
        if row.get("assignment_id") is not None and _id(row["assignment_id"]) != sid:
            raise PublicationRefused("scope_mismatch")
        oid = _id(row.get("id", row.get("override_id")))
        payload = {"assignment_id": sid, "override_id": oid}
        if "student_ids" in row:
            payload["student_pseudonyms"] = sorted({publisher._pseudo(i) for i in row["student_ids"]})
        for key in ("section_id", "group_id"):
            if row.get(key) is not None:
                payload[key] = _id(row[key])
        payload.update(_stamp_fields(row, ("due_at", "unlock_at", "lock_at")))
        yield "override", f"override:{sid}:{oid}", payload, frozenset()


def publish_captured_attachment(*, publisher: EvidencePublisher, job,
                                digest: str, writer_key: str, run_id: str) -> str:
    """Republish one attachment association with its verified original digest.

    The private job queue calls this after archiving the exact bytes. The safe
    fact carries only the opaque key, digest, media type, size, and status; the
    filename and any URL stay in the private association record.
    """
    payload = {
        "assignment_id": job.assignment_id, "pseudonym": job.pseudonym,
        "attempt": job.attempt, "attachment_key": job.attachment_key,
        "original_digest": digest, "media_type": job.media_type,
        "size": job.size, "status": "captured", "revision": 1,
    }
    key = f"attachment:{job.assignment_id}:{job.pseudonym}:{job.attempt}:{job.attachment_key}"
    _, digest = publisher._fact("attachment", key, payload)
    snapshot = publisher.store.scan()
    scope_key = (publisher.source_key, publisher.course_id, "assignment.attachments",
                 job.assignment_id)
    state = snapshot.scopes.get(scope_key)
    # Replace only this entity's prior (pending) ref; keep sibling attachments.
    refs = sorted({ref for ref in (state.current_refs if state else ())
                   if snapshot.facts.get(ref, {}).get("entity_key") != key} | {digest})
    members = sorted(set(state.member_keys if state else ()) | {key})
    record = {
        "schema_version": 1, "source_key": publisher.source_key,
        "course_id": publisher.course_id, "scope": "assignment.attachments",
        "scope_id": job.assignment_id, "writer_key": writer_key, "run_id": run_id,
        "parents": list(state.heads if state else ()),
        "acquisition_started_at": _utc_now(),
        "acquisition_finished_at": _utc_now(),
        "mode": "snapshot", "membership_complete": bool(state and state.membership_complete),
        "record_refs": refs, "member_keys": members, "gaps": [], "watermarks": {},
    }
    return publisher.store.publish_commit(record)


def publish_attachment_status(*, publisher: EvidencePublisher, job, status: str,
                              writer_key: str, run_id: str) -> str:
    """Publish a terminal capture outcome for a known attachment association."""
    if status not in {"too_large", "unavailable", "foreign_origin"}:
        raise PublicationRefused("invalid_status")
    from api.mirror.evidence_publish import _utc_now
    payload = {
        "assignment_id": job.assignment_id, "pseudonym": job.pseudonym,
        "attempt": job.attempt, "attachment_key": job.attachment_key,
        "original_digest": None, "media_type": job.media_type,
        "size": job.size, "status": status, "revision": 1,
    }
    key = f"attachment:{job.assignment_id}:{job.pseudonym}:{job.attempt}:{job.attachment_key}"
    _, digest = publisher._fact("attachment", key, payload)
    snapshot = publisher.store.scan()
    scope_key = (publisher.source_key, publisher.course_id, "assignment.attachments",
                 job.assignment_id)
    state = snapshot.scopes.get(scope_key)
    refs = sorted({ref for ref in (state.current_refs if state else ())
                   if snapshot.facts.get(ref, {}).get("entity_key") != key} | {digest})
    members = sorted(set(state.member_keys if state else ()) | {key})
    record = {
        "schema_version": 1, "source_key": publisher.source_key,
        "course_id": publisher.course_id, "scope": "assignment.attachments",
        "scope_id": job.assignment_id, "writer_key": writer_key, "run_id": run_id,
        "parents": list(state.heads if state else ()),
        "acquisition_started_at": _utc_now(), "acquisition_finished_at": _utc_now(),
        "mode": "snapshot", "membership_complete": bool(state and state.membership_complete),
        "record_refs": refs, "member_keys": members, "gaps": [], "watermarks": {},
    }
    return publisher.store.publish_commit(record)


def publish_course_receipt(*, publisher: EvidencePublisher,
                           receipt: CourseAcquisitionReceipt,
                           writer_key: str, run_id: str) -> AcquisitionPublication:
    """Facts first, scope commits last; failures cannot erase sibling evidence.

    ``complete`` is trusted only as the caller's pagination-bearing exact-scope
    proof. ``successful_scopes`` lists proven successful enumerations, including
    deltas; it is the only acknowledgement a caller may use for watermarks.
    Invalid top-level receipt metadata refuses before any publication.
    """
    if str(receipt.course_id) != publisher.course_id:
        raise PublicationRefused("scope_mismatch")
    started, finished = _utc_stamp(receipt.acquisition_started_at), _utc_stamp(receipt.acquisition_finished_at)
    if not started or not finished or datetime.fromisoformat(finished.replace("Z", "+00:00")) < datetime.fromisoformat(started.replace("Z", "+00:00")):
        raise PublicationRefused("invalid_acquisition_interval")
    validate_component(writer_key)
    validate_component(run_id)
    # Register every available roster identity before scrubbing any prose.
    roster_rows = []
    for scope in receipt.scopes:
        if scope.scope == "course.roster" and isinstance(scope.rows, (tuple, list)):
            roster_rows.extend(row for row in scope.rows if isinstance(row, dict))
    registration_failed = False
    try:
        roster_service.upsert_roster(publisher.vault, roster_rows)
    except Exception:
        registration_failed = True
    if not registration_failed and hasattr(publisher.vault, "save"):
        publisher.vault.save()
    unresolved_roster_rows = []
    if not registration_failed:
        for row in roster_rows:
            try:
                publisher.vault.require_stable(row.get("id", row.get("user_id")))
            except Exception:
                unresolved_roster_rows.append(row)
        publisher._replacement_map = feedback_scrub.build_replacement_map(publisher.vault.entries(), set())
    heads = {key: list(state.heads) for key, state in publisher.store.scan().scopes.items()}
    all_facts, commits, all_gaps, success = set(), [], [], []
    # Attachment associations live in their own scope so a missing original never
    # blocks submission membership; they are published after the scope loop.
    attachment_refs: dict[str, list[str]] = {}
    attachment_members: dict[str, list[str]] = {}
    attachment_complete: dict[str, bool] = {}
    for scope in receipt.scopes:
        gaps, refs, members, current, student_payloads = [], [], [], {}, {}
        if registration_failed and scope.scope in _STUDENT_BEARING_SCOPES:
            all_gaps.append("identity_registration_failed")
            continue
        if unresolved_roster_rows and scope.scope == "course.roster":
            gaps.append("identity_unresolved")
        if scope.scope not in SCOPE_KINDS:
            all_gaps.append("unsupported_scope")
            continue
        try:
            sid = _id(scope.scope_id)
            if scope.scope.startswith("course.") and sid != publisher.course_id:
                raise PublicationRefused("scope_mismatch")
            if scope.mode not in {"snapshot", "delta"}:
                raise PublicationRefused("invalid_mode")
            if type(scope.complete) is not bool:
                raise PublicationRefused("invalid_completeness")
        except PublicationRefused as exc:
            all_gaps.append(exc.code)
            continue
        if scope.error_code:
            # Never echo arbitrary transport exception text/URLs.
            gaps.append("acquisition_failed")
        if not scope.complete:
            gaps.append("pagination_incomplete")
        rows = scope.rows
        if not isinstance(rows, (tuple, list)):
            rows = ()
            gaps.append("invalid_response")
        for row in rows:
            if not isinstance(row, dict):
                gaps.append("invalid_response")
                continue
            try:
                for kind, key, payload, html in _rows(publisher, scope, row, gaps):
                    if kind == "student" and key in student_payloads:
                        payload["section_ids"] = sorted(set(payload["section_ids"]) |
                                                       set(student_payloads[key]["section_ids"]))
                    try:
                        _, digest = publisher._fact(kind, key, payload, html_fields=html)
                    except (EvidenceValidationError, PublicationRefused) as exc:
                        gaps.append(exc.code)
                        continue
                    all_facts.add(digest)
                    if kind == "attachment":
                        attachment_refs.setdefault(sid, []).append(digest)
                        attachment_members.setdefault(sid, []).append(key)
                        attachment_complete[sid] = attachment_complete.get(sid, True) and scope.complete
                        continue
                    if kind == "student":
                        if key in current:
                            refs[:] = [ref for ref in refs if ref != current[key]]
                            current.pop(key)
                        student_payloads[key] = payload
                    if kind != "attempt_observation" and key in current and current[key] != digest:
                        gaps.append("conflicting_scope_entity")
                        continue
                    current[key] = digest
                    refs.append(digest)
                    if kind != "attempt_observation":
                        members.append(key)
            except (EvidenceValidationError, PublicationRefused) as exc:
                gaps.append(exc.code)
            except (TypeError, AttributeError, KeyError):
                gaps.append("publication_refused")
        # Pending ancillary acquisition says nothing about enumeration of the
        # submission rows themselves. Its separate scope owns coverage.
        ancillary = {"attachments_pending", "comments_scope_required"}
        scope_gaps = [code for code in gaps if code not in ancillary]
        proven = scope.complete and not scope_gaps
        watermarks = scope.watermarks or {}
        record = {
            "schema_version": 1, "source_key": publisher.source_key,
            "course_id": publisher.course_id, "scope": scope.scope, "scope_id": sid,
            "writer_key": writer_key, "run_id": run_id,
            "parents": heads.get((publisher.source_key, publisher.course_id, scope.scope, sid), []),
            "acquisition_started_at": started, "acquisition_finished_at": finished,
            "mode": scope.mode, "membership_complete": proven and scope.mode == "snapshot",
            "record_refs": sorted(set(refs)), "member_keys": sorted(set(members)),
            "gaps": [{"code": code} for code in sorted(set(scope_gaps))],
            "watermarks": {},
        }
        try:
            if proven:
                record["watermarks"] = {key: _utc_stamp(value) for key, value in watermarks.items()}
            validate_commit(record)
            digest = publisher.store.publish_commit(record)
        except (EvidenceValidationError, PublicationRefused, AttributeError, TypeError):
            all_gaps.extend(gaps + ["commit_refused"])
            continue
        commits.append(digest)
        heads[(publisher.source_key, publisher.course_id, scope.scope, sid)] = [digest]
        if proven:
            success.append((scope.scope, sid))
        all_gaps.extend(gaps)
    # Publish one attachment scope per assignment that observed attachments.
    for aid in sorted(attachment_refs):
        record = {
            "schema_version": 1, "source_key": publisher.source_key,
            "course_id": publisher.course_id, "scope": "assignment.attachments",
            "scope_id": aid, "writer_key": writer_key, "run_id": run_id,
            "parents": heads.get((publisher.source_key, publisher.course_id,
                                  "assignment.attachments", aid), []),
            "acquisition_started_at": started, "acquisition_finished_at": finished,
            "mode": "snapshot",
            "membership_complete": bool(attachment_complete.get(aid)),
            "record_refs": sorted(set(attachment_refs[aid])),
            "member_keys": sorted(set(attachment_members[aid])),
            "gaps": [], "watermarks": {},
        }
        try:
            validate_commit(record)
            digest = publisher.store.publish_commit(record)
        except (EvidenceValidationError, PublicationRefused, AttributeError, TypeError):
            all_gaps.append("commit_refused")
            continue
        commits.append(digest)
        heads[(publisher.source_key, publisher.course_id, "assignment.attachments", aid)] = [digest]
        if attachment_complete.get(aid):
            success.append(("assignment.attachments", aid))
    return AcquisitionPublication(tuple(sorted(all_facts)), tuple(commits),
                                  tuple(sorted(set(all_gaps))), tuple(success))
