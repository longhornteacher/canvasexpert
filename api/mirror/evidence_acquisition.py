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
Unsupported lifecycle fields and attachment bytes remain explicit gaps. No raw
receipt is persisted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib

from api import feedback_scrub
from api.mirror.evidence_publish import EvidencePublisher, PublicationRefused, _utc_stamp
from api.mirror.evidence_queries import publish_reader_contract
from api.mirror.evidence_schema import (
    EvidenceValidationError, SCOPE_KINDS, canonical_bytes, validate_component,
    validate_commit,
)
from api.platform_services import workspace


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
        yield "group", f"group:{gid}", {"group_id": gid,
            "title": row.get("name", row.get("title", "")),
            "student_pseudonyms": sorted(set(pseudos))}, frozenset()
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
        if row.get("attachments") or any(isinstance(h, dict) and h.get("attachments")
                                          for h in row.get("submission_history") or []):
            gaps.append("attachments_pending")
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
    for scope in receipt.scopes:
        if scope.scope == "course.roster" and isinstance(scope.rows, (tuple, list)):
            for row in scope.rows:
                if isinstance(row, dict):
                    try:
                        publisher._pseudo(row.get("id", row.get("user_id")), str(row.get("name") or ""))
                    except PublicationRefused:
                        pass
    if hasattr(publisher.vault, "save"):
        publisher.vault.save()
    publisher._replacement_map = feedback_scrub.build_replacement_map(publisher.vault.entries(), set())
    publish_reader_contract(workspace.canvas_mirror_evidence_root(publisher.workspace_root))
    heads = {key: list(state.heads) for key, state in publisher.store.scan().scopes.items()}
    all_facts, commits, all_gaps, success = set(), [], [], []
    for scope in receipt.scopes:
        gaps, refs, members, current, student_payloads = [], [], [], {}, {}
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
    return AcquisitionPublication(tuple(sorted(all_facts)), tuple(commits),
                                  tuple(sorted(set(all_gaps))), tuple(success))
