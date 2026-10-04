"""Private feedback-only comment revisions; immutable packets and durable send intents.

The public lane never accepts scores. The sole Canvas payload is ``comment`` at
the existing comment endpoint. All identities and original evidence stay here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import mimetypes
from pathlib import Path
import re
import uuid

from api import feedback_scrub, feedback_safety
from api.mcp_server import pseudonym
from api.mirror import read_service
from api.nq_report import html_to_text
from api.operation_ledger.adapters import forge_files, assignment_whole
from api.platform_services import canvas_client
from api.powergrader import assignment_refresh, scoring_local, session_store
from api.webui import source_materials
from api.shared_work import SharedWorkStore, WorkItemNotFound
from api.work_registry.providers import home_attention


TOKEN_BUDGET = 25_000
ATTACHMENT_LABEL = "Reference document for your revision."
_PRIVATE_TEXT = re.compile(r"https?://|file://|[A-Za-z]:[\\/]|\\\\|\b(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]", re.I)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _fail(code, **counts):
    return {**counts, "ok": False, "code": code}


def _work_id(course_id, assignment_id):
    return "feedback-" + session_store.scope_digest(course_id, assignment_id)


def _coordinate(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None


def _save(store, state):
    store.save_snapshot(state["work_id"], state, kind="feedback_revision",
                        course_id=state["course_id"], assignment_id=state["assignment_id"])


def _packet_identity(state):
    return {key: state[key] for key in (
        "work_id", "course_id", "assignment_id", "run_id", "packet", "targets")}


def _valid_packet(state, work_id):
    try:
        return (state["kind"] == "feedback_revision" and state["work_id"] == work_id
                and _coordinate(state["course_id"]) and _coordinate(state["assignment_id"])
                and work_id == _work_id(state["course_id"], state["assignment_id"])
                and isinstance(state["targets"], list)
                and isinstance(state["packet"], dict)
                and state["packet_digest"] == _digest(_packet_identity(state)))
    except (KeyError, TypeError, ValueError):
        return False


def _open(store, work_id, course_gate):
    store.require_owner(work_id)
    state = store.load_snapshot(work_id)
    if not _valid_packet(state, work_id):
        return None, _fail("feedback_packet_invalid")
    if course_gate(state["course_id"]):
        return None, _fail("course_unavailable")
    return state, None


def _attention(state):
    return state.get("status") == "attention" or any(
        row.get("status") in {"intent", "unknown"} for row in (
            *state.get("receipts", []), *state.get("attachment_receipts", [])))


def _scrub(text, replacements):
    return feedback_scrub.scrub_text(html_to_text(text), replacements)


def _safe_text(payload, vault):
    verdict = feedback_safety.scan_payload(payload, vault)
    # A scrub miss is a private implementation failure; never return its text.
    return verdict["green"] and not verdict["soft"] and not _PRIVATE_TEXT.search(
        json.dumps(payload, ensure_ascii=False))


def _summary(state):
    return {"ok": True, "work_id": state["work_id"], "status": state["status"],
            "packet_digest": state["packet_digest"], **state["packet"]["counts"]}


def prepare(course_id, assignment_id, *, use_existing_mirror=False, vault, course_gate):
    if not _coordinate(course_id) or not _coordinate(assignment_id) or course_gate(course_id):
        return _fail("invalid_scope")
    work_id = _work_id(course_id, assignment_id)
    with session_store.scope_lock(course_id, assignment_id):
        store = SharedWorkStore()
        try:
            previous, error = _open(store, work_id, course_gate)
        except WorkItemNotFound:
            previous, error = None, None
        if error:
            return error
        if previous:
            if _attention(previous):
                return _fail("canvas_write_attention", work_id=work_id)
            if previous["status"] in {"ready", "staged"}:
                return _summary(previous)

        rows, assignment, meta = assignment_refresh.prepare_assignment_from_mirror(
            course_id, assignment_id)
        if rows is None or not isinstance(assignment, dict):
            return _fail(str((meta or {}).get("code") or "mirror_projection_unavailable"))
        if assignment.get("is_quiz") or assignment.get("quiz_kind") or assignment.get("is_quiz_lti_assignment"):
            return _fail("feedback_revision_requires_ordinary_assignment")
        comments = read_service.private_submission_comments(course_id, max_age_hours=None)
        base = (meta or {}).get("freshness") or {}
        comment_fresh = scoring_local._freshness(course_id, "", [comments])
        if (comments.get("state") != "current" or comment_fresh.get("projection_state") != "current"
                or base.get("state") == "unavailable" or not base.get("last_success_at")):
            return _fail("mirror_projection_unavailable",
                         user_action="Refresh the course mirror, including comments, then retry.")
        if (base.get("requires_teacher_confirmation") or comment_fresh["requires_teacher_confirmation"]) and not use_existing_mirror:
            return _fail("mirror_refresh_needed",
                         last_success_at=min(base["last_success_at"], comment_fresh["last_success_at"]),
                         user_action="Refresh this course's mirror with refresh_mirror, then retry. If the teacher has said nothing changed, retry with use_existing_mirror=true instead.")

        replacements = feedback_scrub.build_replacement_map(vault.entries(), set())
        context = {"title": _scrub(str(assignment.get("name") or ""), replacements),
                   "prompt": _scrub(str(assignment.get("description") or ""), replacements),
                   "points_possible": assignment.get("points_possible")}
        counts = {"eligible_comments": 0, "eligible_students": 0, "held_students": 0,
                  "excluded_students": 0, "excluded_comments": 0}
        packet_rows, targets, seen_users, seen_comments = [], [], set(), set()
        run_id = uuid.uuid4().hex
        for row in rows:
            if not isinstance(row, dict):
                return _fail("feedback_submission_malformed")
            user_id = str(row.get("user_id") or "")
            if not _coordinate(user_id) or user_id in seen_users:
                return _fail("feedback_submission_malformed")
            seen_users.add(user_id)
            if row.get("workflow_state") != "graded":
                counts["excluded_students"] += 1
                continue
            score = row.get("score")
            if type(score) not in (int, float) or not math.isfinite(score):
                return _fail("feedback_score_invalid")
            if row.get("_mirror_unreadable") or not str(row.get("body") or "").strip():
                counts["held_students"] += 1
                continue
            raw_comments = row.get("submission_comments")
            if not isinstance(raw_comments, list) or any(not isinstance(c, dict) for c in raw_comments):
                return _fail("feedback_comments_malformed")
            entry = next((e for e in vault.entries() if str(e.get("canvas_id") or "") == user_id), None)
            if entry and entry.get("provisional"):
                return _fail("pseudonym_provisional")
            label = str((entry or {}).get("pseudonym") or "")
            identity = vault.reverse(label) if label else None
            if not identity or str(identity.get("canvas_id") or "") != user_id:
                return _fail("identity_unavailable")
            included = 0
            for comment in raw_comments:
                author_id = home_attention._author_id(comment)
                if not author_id or not home_attention._is_proven_staff(comment, author_id, user_id):
                    counts["excluded_comments"] += 1
                    continue
                comment_id = str(comment.get("id") or "")
                if not _coordinate(comment_id):
                    return _fail("feedback_comment_identity_missing", refresh_comments_required=True,
                                 user_action="Ask the teacher to refresh the course mirror with comments, then retry.")
                if comment_id in seen_comments or not isinstance(comment.get("comment"), str):
                    return _fail("feedback_comments_malformed")
                created_at = str(comment.get("created_at") or "")
                timestamp = scoring_local._parse_timestamp(created_at) if created_at else None
                seen_comments.add(comment_id)
                key = _digest([run_id, user_id, comment_id])
                packet_rows.append({"pseudonym": label, "comment_key": key, "score": score,
                                    "created_at": timestamp.isoformat().replace("+00:00", "Z") if timestamp else "",
                                    "response": _scrub(str(row["body"]), replacements),
                                    "feedback": _scrub(comment["comment"], replacements)})
                targets.append({"pseudonym": label, "comment_key": key, "user_id": user_id,
                                "comment_id": comment_id, "score": score,
                                "original_comment": copy.deepcopy(comment),
                                "original_submission": copy.deepcopy(row)})
                included += 1
            if included:
                counts["eligible_students"] += 1
            else:
                counts["held_students"] += 1
        counts["eligible_comments"] = len(packet_rows)
        packet = {"assignment": context, "counts": counts, "revisions": packet_rows}
        if not _safe_text(packet, vault):
            return _fail("feedback_packet_privacy_blocked")
        # Complete text is either carried or refused, never sliced/truncated.
        for row in packet_rows or [{}]:
            if source_materials.estimate_text_tokens(json.dumps({"assignment": context, "revisions": [row]})) > TOKEN_BUDGET - 512:
                return _fail("feedback_packet_too_large")
        if not packet_rows:
            return _fail("feedback_revision_nothing_eligible", **counts)
        history = copy.deepcopy(previous.get("history", [])) if previous else []
        if previous:
            history.append({key: copy.deepcopy(value) for key, value in previous.items() if key != "history"})
        state = {"kind": "feedback_revision", "work_id": work_id,
                 "course_id": course_id, "assignment_id": assignment_id,
                 "run_id": run_id, "status": "ready", "packet": packet,
                 "targets": targets, "receipts": [], "history": history,
                 "freshness": {"assignment": base, "comments": comment_fresh,
                               "use_existing_mirror": bool(use_existing_mirror)}}
        state["packet_digest"] = _digest(_packet_identity(state))
        _save(store, state)
        return _summary(state)


def packet(work_id, offset=0, limit=10, *, vault, course_gate):
    store = SharedWorkStore()
    state, error = _open(store, work_id, course_gate)
    if error:
        return error
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        return _fail("feedback_packet_page_invalid")
    all_rows = state["packet"]["revisions"]
    result = {**_summary(state), "assignment": state["packet"]["assignment"],
              "total": len(all_rows), "revisions": []}
    for row in all_rows[offset:offset + min(limit, 100)]:
        candidate = {**result, "revisions": [*result["revisions"], row]}
        if source_materials.estimate_text_tokens(json.dumps(candidate)) > TOKEN_BUDGET:
            if not result["revisions"]:
                return _fail("feedback_packet_too_large")
            break
        result = candidate
    result["returned"] = len(result["revisions"])
    if offset + result["returned"] < len(all_rows):
        result["next_offset"] = offset + result["returned"]
    return pseudonym.gate(result, vault) if _safe_text(result, vault) else _fail("feedback_packet_privacy_blocked")


def _feedback_valid(text, vault):
    if (not isinstance(text, str) or not text.strip() or html_to_text(text) != text
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text)):
        return False
    labels = [str(entry.get("pseudonym") or "") for entry in vault.entries()]
    return (_safe_text({"feedback": text}, vault)
            and not feedback_scrub.find_token_matches(text, labels)
            and feedback_scrub.ID_PLACEHOLDER not in text)


def _plan(state, revisions, vault):
    if not isinstance(revisions, list) or not revisions:
        return None
    targets = {(t["pseudonym"], t["comment_key"]): t for t in state["targets"]}
    seen, plan = set(), []
    for row in revisions:
        if not isinstance(row, dict) or set(row) != {"pseudonym", "comment_key", "feedback"}:
            return None
        if not isinstance(row["pseudonym"], str) or not isinstance(row["comment_key"], str):
            return None
        pair = (row["pseudonym"], row["comment_key"])
        if pair in seen or pair not in targets or not _feedback_valid(row["feedback"], vault):
            return None
        target = targets[pair]
        if not _coordinate(target["user_id"]) or not _coordinate(target["comment_id"]):
            return None
        identity = vault.reverse(pair[0])
        if (not identity or identity.get("provisional")
                or str(identity.get("canvas_id") or "") != target["user_id"]
                or any(e.get("provisional") for e in vault.entries()
                       if str(e.get("canvas_id") or "") == target["user_id"])):
            return None
        original = target["original_comment"]
        author_id = home_attention._author_id(original)
        if not author_id or not home_attention._is_proven_staff(original, author_id, target["user_id"]):
            return None
        seen.add(pair)
        plan.append({**row, "user_id": target["user_id"], "comment_id": target["comment_id"],
                     "path": f'/api/v1/courses/{state["course_id"]}/assignments/{state["assignment_id"]}/submissions/{target["user_id"]}/comments/{target["comment_id"]}'})
    return plan


def _stage_identity(state, plan, attachment=None):
    value = {"packet_digest": state["packet_digest"], "plan": plan}
    if attachment is not None:
        value["attachment"] = attachment
    return value


def _resolve_attachment(filename, vault):
    if (not isinstance(filename, str) or not filename or Path(filename).name != filename
            or any(c in filename for c in "/\\:")):
        return None
    if Path(filename).suffix.lstrip(".").casefold() not in forge_files.ALLOWED_ATTACHMENT_EXTENSIONS:
        return None
    if not _safe_text({"file": filename}, vault):
        return None
    try:
        record = forge_files.resolve_attachments([{"file": filename, "label": ATTACHMENT_LABEL}])[0]
        size = Path(record["path"]).stat().st_size
        if size <= 0 or size > forge_files.MAX_STAGED_ATTACHMENT_BYTES:
            return None
        record.update(filename=filename, size_bytes=size,
                      content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
        return record
    except (OSError, ValueError):
        return None


def _attachment_unchanged(record):
    try:
        return (forge_files.verify_private_record_path(record)
                and record["label"] == ATTACHMENT_LABEL
                and record["filename"] == record["file"]
                and Path(record["path"]).stat().st_size == record["size_bytes"]
                and forge_files.sha256_file(Path(record["path"])) == record["sha256"])
    except (KeyError, OSError, ValueError):
        return False


def stage(work_id, expected_packet_digest, revisions, *, attachment_file=None, vault, course_gate):
    store = SharedWorkStore()
    state, error = _open(store, work_id, course_gate)
    if error:
        return error
    with session_store.scope_lock(state["course_id"], state["assignment_id"]):
        state, error = _open(store, work_id, course_gate)
        if error:
            return error
        if _attention(state):
            return _fail("canvas_write_attention")
        if state["status"] not in {"ready", "staged"}:
            return _fail("feedback_revision_completed")
        if state.get("receipts") or state.get("attachment_receipts"):
            return _fail("feedback_revision_apply_started")
        if expected_packet_digest != state["packet_digest"]:
            return _fail("stale_packet")
        plan = _plan(state, revisions, vault)
        if plan is None:
            return _fail("feedback_revisions_invalid")
        attachment = _resolve_attachment(attachment_file, vault) if attachment_file is not None else None
        if attachment_file is not None and attachment is None:
            return _fail("feedback_attachment_invalid")
        digest = _digest(_stage_identity(state, plan, attachment))
        state.update(status="staged", stage={"plan": plan, "digest": digest}, receipts=[])
        if attachment is not None:
            state["stage"]["attachment"] = attachment
        state.pop("attachment_receipts", None)
        _save(store, state)
        result = {"ok": True, "work_id": work_id, "stage_digest": digest,
                  "selected": len(plan), "untouched": len(state["targets"]) - len(plan)}
        if attachment is not None:
            result.update(attachment={"file": attachment["file"], "size_bytes": attachment["size_bytes"]},
                          attachment_students=len({row["user_id"] for row in plan}))
        return result


def preview(work_id, offset=0, limit=25, *, vault, course_gate):
    """Project exact staged edits against their scrubbed immutable originals.

    Rebuild and validate the frozen plan as apply does, without any Canvas I/O
    or local mutation. Private endpoint coordinates and attachment paths never
    enter this projection.
    """
    store = SharedWorkStore()
    state, error = _open(store, work_id, course_gate)
    if error:
        return error
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        return _fail("feedback_preview_page_invalid")
    frozen = state.get("stage")
    if not isinstance(frozen, dict) or not isinstance(frozen.get("plan"), list):
        return _fail("nothing_staged")
    revisions = [{key: row.get(key) for key in ("pseudonym", "comment_key", "feedback")}
                 for row in frozen["plan"] if isinstance(row, dict)]
    plan = _plan(state, revisions, vault)
    if (plan is None or plan != frozen["plan"]
            or frozen.get("digest") != _digest(_stage_identity(state, plan, frozen.get("attachment")))):
        return _fail("preview_stale")
    attachment = frozen.get("attachment")
    if attachment and state["status"] != "completed" and not _attachment_unchanged(attachment):
        return _fail("feedback_attachment_changed")
    originals = {(row["pseudonym"], row["comment_key"]): row["feedback"]
                 for row in state["packet"]["revisions"]}
    if any((row["pseudonym"], row["comment_key"]) not in originals for row in plan):
        return _fail("preview_stale")
    rows = [{"pseudonym": row["pseudonym"], "comment_key": row["comment_key"],
             "current_comment": originals[(row["pseudonym"], row["comment_key"])],
             "new_comment": row["feedback"]} for row in plan]
    rows.sort(key=lambda row: (row["pseudonym"], row["comment_key"]))
    limit = min(limit, 100)
    result = {"ok": True, "work_id": work_id, "stage_digest": frozen["digest"],
              "counts": {"selected": len(plan), "untouched": len(state["targets"]) - len(plan)},
              "attachment": ({"file": attachment["file"], "size_bytes": attachment["size_bytes"]}
                             if attachment else None),
              "offset": offset, "limit": limit, "total": len(rows), "rows": [],
              "returned": 0, "next_offset": None}
    for row in rows[offset:offset + limit]:
        returned = len(result["rows"]) + 1
        candidate = {**result, "rows": [*result["rows"], row], "returned": returned,
                     "next_offset": offset + returned if offset + returned < len(rows) else None}
        if source_materials.estimate_text_tokens(json.dumps(candidate)) > TOKEN_BUDGET:
            if not result["rows"]:
                return _fail("feedback_preview_too_large")
            break
        result = candidate
    return pseudonym.gate(result, vault) if _safe_text(result, vault) else _fail("feedback_packet_privacy_blocked")


def _outcomes(state):
    receipts = state["receipts"]
    attached = state.get("attachment_receipts", [])
    failed = sum(r["status"] == "failed" for r in (*receipts, *attached))
    attention = sum(r["status"] in {"intent", "unknown"} for r in (*receipts, *attached))
    edited = {r["comment_key"] for r in receipts if r["status"] == "accepted"}
    accepted = len(edited)
    if state["stage"].get("attachment"):
        attached_users = {r["user_id"] for r in attached if r["step"] == "attachment_comment" and r["status"] == "accepted"}
        accepted = sum(row["comment_key"] in edited and row["user_id"] in attached_users
                       for row in state["stage"]["plan"])
    result = {"ok": not failed and not attention, "work_id": state["work_id"],
            "status": state["status"], "accepted": accepted,
            "failed": failed, "attention": attention,
            "untouched": len(state["targets"]) - len(state["stage"]["plan"])}
    if state["stage"].get("attachment"):
        result.update(edited_comments=len(edited), attached_students=len(attached_users),
                      partial=len(edited) - accepted)
    if failed and not attention:
        result["code"] = ("feedback_attachment_changed" if any(r.get("failure_code") == "file_drift" for r in attached)
                          else "canvas_rejected")
    return result


def _attachment_receipts_valid(state):
    seen = {}
    user_ids = {row["user_id"] for row in state["stage"]["plan"]}
    for receipt in state.get("attachment_receipts", []):
        pair = (receipt.get("user_id"), receipt.get("step"))
        if (pair in seen or pair[0] not in user_ids or pair[1] not in {"upload", "attachment_comment"}
                or receipt.get("stage_digest") != state["stage"]["digest"]
                or receipt.get("status") not in {"intent", "unknown", "failed", "accepted"}):
            return False
        if receipt["status"] == "accepted" and not _coordinate(receipt.get("file_id")):
            return False
        seen[pair] = receipt
    for (uid, step), receipt in seen.items():
        if step == "attachment_comment":
            upload = seen.get((uid, "upload"))
            if (not upload or upload["status"] != "accepted"
                    or receipt.get("file_id") != upload["file_id"]):
                return False
    return True


def _send_status(error):
    if not error:
        return "accepted"
    return ("failed" if error == "file_drift" or re.match(r"^(?:Canvas file upload failed: )?HTTP [345][0-9]{2}:", str(error))
            else "unknown")


def _apply_attachments(store, state):
    record = state["stage"]["attachment"]
    accepted_edits = {r["comment_key"] for r in state["receipts"] if r["status"] == "accepted"}
    users = list(dict.fromkeys(row["user_id"] for row in state["stage"]["plan"]
                              if row["comment_key"] in accepted_edits))
    receipts = state.setdefault("attachment_receipts", [])
    lookup = {(r["user_id"], r["step"]): r for r in receipts}
    for user_id in users:
        base = f'/api/v1/courses/{state["course_id"]}/assignments/{state["assignment_id"]}/submissions/{user_id}'
        for step in ("upload", "attachment_comment"):
            prior = lookup.get((user_id, step))
            if prior:
                if prior["status"] == "failed":
                    break
                continue
            upload = lookup.get((user_id, "upload"))
            receipt = {"user_id": user_id, "step": step, "status": "intent",
                       "stage_digest": state["stage"]["digest"]}
            if step == "attachment_comment":
                if not upload or upload["status"] != "accepted":
                    break
                receipt["file_id"] = upload["file_id"]
            elif not _attachment_unchanged(record):
                return _fail("feedback_attachment_changed", **_outcomes(state))
            store.require_owner(state["work_id"])
            receipts.append(receipt)
            _save(store, state)
            try:
                if step == "upload":
                    init, error = canvas_client._canvas_send("POST", base + "/comments/files", {
                        "name": record["filename"], "size": record["size_bytes"],
                        "content_type": record["content_type"]})
                    file_info = None
                    if not error:
                        file_info, error = assignment_whole.upload_initialized_file(
                            init, Path(record["path"]), filename=record["filename"], content_type=record["content_type"],
                            expected_sha256=record["sha256"], expected_size=record["size_bytes"])
                    file_id = forge_files._file_id(file_info)
                    if not error and not file_id:
                        error = "upload_identity_unknown"
                    if file_id and not error:
                        receipt["file_id"] = file_id
                else:
                    _response, error = canvas_client._canvas_send("PUT", base, {"comment": {
                        "text_comment": record["label"], "file_ids": [receipt["file_id"]]}})
            except Exception:
                error = "transport_unknown"
            receipt["status"] = _send_status(error)
            if error == "file_drift":
                receipt["failure_code"] = error
            if receipt["status"] == "unknown":
                state["status"] = "attention"
            _save(store, state)
            lookup[(user_id, step)] = receipt
            if receipt["status"] == "unknown":
                return _fail("write_transport_unknown", **_outcomes(state))
            if receipt["status"] == "failed":
                break
    return None


def apply(work_id, expected_stage_digest, *, vault, course_gate):
    store = SharedWorkStore()
    state, error = _open(store, work_id, course_gate)
    if error:
        return error
    with session_store.scope_lock(state["course_id"], state["assignment_id"]):
        state, error = _open(store, work_id, course_gate)
        if error:
            return error
        frozen = state.get("stage")
        if not isinstance(frozen, dict) or not isinstance(frozen.get("plan"), list):
            return _fail("stage_unavailable")
        if expected_stage_digest != frozen.get("digest"):
            return _fail("stage_changed")
        revisions = [{key: row.get(key) for key in ("pseudonym", "comment_key", "feedback")} for row in frozen["plan"]]
        plan = _plan(state, revisions, vault)
        if (plan is None or plan != frozen["plan"]
                or frozen["digest"] != _digest(_stage_identity(state, plan, frozen.get("attachment")))):
            return _fail("stage_invalid")
        if not _attachment_receipts_valid(state):
            return _fail("feedback_receipts_invalid")
        if _attention(state):
            return _fail("canvas_write_attention", **_outcomes(state))
        if state["status"] == "completed":
            return _outcomes(state)
        if state["status"] != "staged":
            return _fail("stage_unavailable")
        if frozen.get("attachment") and not _attachment_unchanged(frozen["attachment"]):
            return _fail("feedback_attachment_changed")
        receipt_map = {r["comment_key"]: r for r in state["receipts"]}
        if (len(receipt_map) != len(state["receipts"])
                or any(k not in {p["comment_key"] for p in plan} for k in receipt_map)
                or any(r["status"] not in {"accepted", "failed"} for r in receipt_map.values())):
            return _fail("feedback_receipts_invalid")
        for row in plan:
            if row["comment_key"] in receipt_map:
                continue
            store.require_owner(work_id)
            receipt = {"comment_key": row["comment_key"], "stage_digest": frozen["digest"], "status": "intent"}
            state["receipts"].append(receipt)
            _save(store, state)  # Crash after this point means attention, never resend.
            try:
                _response, send_error = canvas_client._canvas_send("PUT", row["path"], {"comment": row["feedback"]})
            except Exception:
                send_error = "transport_unknown"
            if send_error:
                receipt["status"] = "failed" if re.match(r"^HTTP [345][0-9]{2}:", str(send_error)) else "unknown"
            else:
                receipt["status"] = "accepted"
            if receipt["status"] == "unknown":
                state["status"] = "attention"
            _save(store, state)  # Durable outcome before the next send.
            if state["status"] == "attention":
                return _fail("write_transport_unknown", **_outcomes(state))
        if frozen.get("attachment"):
            attachment_error = _apply_attachments(store, state)
            if attachment_error:
                return attachment_error
        state["status"] = "completed"
        _save(store, state)
        return _outcomes(state)
