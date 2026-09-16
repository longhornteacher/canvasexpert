"""Private persistence and state transitions for backlog-wide Scoring Sessions.

The root record owns the frozen, ordered assignment queue. Assignment-scoped
session records remain private children consumed by the existing packet and
write owners.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

from api.powergrader import session_store


ROOT_KIND = "scoring_session"
CHILD_KIND = "assignment_run"
_TERMINAL = {"completed", "completed_with_holds", "nothing_to_grade"}
_FROZEN_FIELDS = (
    "course_id", "course_label", "assignment_id", "assignment_label",
    "due_at", "ungraded", "partially_scored",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _queue_digest(queue: list[dict]) -> str:
    frozen = [{key: item.get(key) for key in _FROZEN_FIELDS} for item in queue]
    encoded = json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _progress(root: dict) -> dict:
    queue = root.get("queue") or []
    completed = sum(1 for item in queue if item.get("status") == "completed")
    held = sum(1 for item in queue if item.get("status") == "completed_with_holds")
    no_longer = sum(1 for item in queue if item.get("status") == "nothing_to_grade")
    failed = sum(1 for item in queue if item.get("status") == "failed"
                 or (item.get("last_failure") and item.get("status") not in _TERMINAL))
    total = len(queue)
    return {
        "total": total,
        "completed": completed,
        "completed_with_holds": held,
        "no_longer_needs_grading": no_longer,
        "remaining": max(0, total - completed - held - no_longer),
        "failed": failed,
    }


def _root_status(root: dict) -> str:
    counts = _progress(root)
    if counts["remaining"] == 0:
        return "complete"
    index = int(root.get("active_index") or 0)
    queue = root.get("queue") or []
    item = queue[index] if 0 <= index < len(queue) else None
    if item:
        if item.get("status") == "needs_teacher_input" or item.get("waiting_for_teacher"):
            return "needs_teacher_input"
        if item.get("status") == "preparing":
            return "preparing"
    if counts["failed"]:
        return "failed"
    if item:
        if item.get("status") == "ready":
            return "ready"
        if item.get("status") == "failed":
            return "failed"
    return "queued"


def _touch(root: dict) -> None:
    root["updated"] = _now()
    root["progress"] = _progress(root)
    root["status"] = _root_status(root)


def create_root_session(*, queue: list[dict], scope: dict, session_id: str | None = None) -> dict | None:
    """Persist one root with a frozen queue; return it only after reload verifies it."""
    if not queue:
        return None
    root_id = str(session_id or uuid.uuid4())
    frozen_queue = []
    for entry in queue:
        item = {key: copy.deepcopy(entry.get(key)) for key in _FROZEN_FIELDS}
        item.update({"status": "pending", "child_session_id": "", "claim": "",
                     "outcome": "", "last_failure": "", "waiting_for_teacher": False,
                     "preparation_retryable": True})
        frozen_queue.append(item)
    root = {
        "session_kind": ROOT_KIND,
        "session_id": root_id,
        "created": _now(),
        "updated": _now(),
        "scope": copy.deepcopy(scope),
        "queue": frozen_queue,
        "queue_digest": _queue_digest(frozen_queue),
        "active_index": 0,
        "status": "queued",
        "progress": _progress({"queue": frozen_queue}),
    }
    session_store.save_session(root)
    return load_root_session(root_id)


def load_root_session(scoring_session_id: str) -> dict | None:
    """Load only this format's root records; old assignment sessions stay ignored."""
    root_id = str(scoring_session_id or "")
    root = session_store.load_session(root_id)
    if not isinstance(root, dict) or root.get("session_kind") != ROOT_KIND:
        return None
    if str(root.get("session_id") or "") != root_id:
        return None
    queue = root.get("queue")
    if not isinstance(queue, list) or not queue or root.get("queue_digest") != _queue_digest(queue):
        return None
    return root


def root_summaries() -> list[dict]:
    """Record-kind-filtered summaries; private assignment children never list."""
    return [summary for summary in session_store.list_session_summaries()
            if summary.get("session_kind") == ROOT_KIND]


def _current_item(root: dict) -> tuple[int, dict] | tuple[None, None]:
    index = int(root.get("active_index") or 0)
    queue = root.get("queue") or []
    while 0 <= index < len(queue) and queue[index].get("status") in _TERMINAL:
        index += 1
    if index >= len(queue):
        return None, None
    return index, queue[index]


def claim_active_item(scoring_session_id: str) -> dict:
    """Claim preparation under the root lock, then let callers acquire outside it."""
    root_id = str(scoring_session_id or "")
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root:
            return {"kind": "not_found"}
        index, item = _current_item(root)
        if item is None:
            root["active_index"] = len(root.get("queue") or [])
            _touch(root)
            session_store.save_session(root)
            return {"kind": "complete", "root": root}
        root["active_index"] = index
        if item.get("status") == "ready" and item.get("child_session_id"):
            _touch(root)
            session_store.save_session(root)
            return {"kind": "ready", "root": root, "item": copy.deepcopy(item), "index": index}
        if item.get("status") == "preparing":
            return {"kind": "busy", "root": root, "item": copy.deepcopy(item)}
        if item.get("status") == "failed" and item.get("preparation_retryable") is False:
            return {"kind": "blocked", "root": root, "item": copy.deepcopy(item),
                    "index": index}
        claim = str(uuid.uuid4())
        item["status"] = "preparing"
        item["claim"] = claim
        item["last_failure"] = ""
        item["waiting_for_teacher"] = False
        _touch(root)
        session_store.save_session(root)
        return {"kind": "claimed", "root": root, "item": copy.deepcopy(item),
                "index": index, "claim": claim}


def _valid_child(root_id: str, item: dict, child: dict) -> bool:
    return bool(
        isinstance(child, dict)
        and child.get("session_kind") == CHILD_KIND
        and str(child.get("parent_scoring_session_id") or "") == root_id
        and str(child.get("course_id") or "") == str(item.get("course_id") or "")
        and str(child.get("assignment_id") or "") == str(item.get("assignment_id") or "")
        and isinstance(child.get("scoring_basis"), dict)
    )


def attach_child(scoring_session_id: str, index: int, claim: str, child_session_id: str) -> bool:
    """Attach a completed child only while the root still owns this claim."""
    root_id = str(scoring_session_id or "")
    child_id = str(child_session_id or "")
    child = session_store.load_session(child_id)
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root or not child or index != root.get("active_index"):
            return False
        queue = root.get("queue") or []
        if index < 0 or index >= len(queue):
            return False
        item = queue[index]
        if item.get("status") != "preparing" or item.get("claim") != claim:
            return False
        if not _valid_child(root_id, item, child):
            return False
        item["child_session_id"] = child_id
        item["status"] = "ready"
        item["claim"] = ""
        item["last_failure"] = ""
        item["waiting_for_teacher"] = False
        _touch(root)
        session_store.save_session(root)
        return True


def record_needs_teacher_input(scoring_session_id: str, index: int, claim: str) -> bool:
    root_id = str(scoring_session_id or "")
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root or index != root.get("active_index"):
            return False
        queue = root.get("queue") or []
        if index < 0 or index >= len(queue):
            return False
        item = queue[index]
        if item.get("status") != "preparing" or item.get("claim") != claim:
            return False
        item["status"] = "needs_teacher_input"
        item["claim"] = ""
        _touch(root)
        session_store.save_session(root)
        return True


def record_preparation_failure(
    scoring_session_id: str, index: int, claim: str, code: str, *, retryable: bool = True,
) -> bool:
    root_id = str(scoring_session_id or "")
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root or index != root.get("active_index"):
            return False
        queue = root.get("queue") or []
        if index < 0 or index >= len(queue):
            return False
        item = queue[index]
        if item.get("status") != "preparing" or item.get("claim") != claim:
            return False
        item["status"] = "failed"
        item["claim"] = ""
        item["last_failure"] = str(code or "start_failed")
        item["preparation_retryable"] = bool(retryable)
        _touch(root)
        session_store.save_session(root)
        return True


def record_nothing_to_grade(scoring_session_id: str, index: int, claim: str) -> bool:
    root_id = str(scoring_session_id or "")
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root or index != root.get("active_index"):
            return False
        queue = root.get("queue") or []
        if index < 0 or index >= len(queue):
            return False
        item = queue[index]
        if item.get("status") != "preparing" or item.get("claim") != claim:
            return False
        item["status"] = "nothing_to_grade"
        item["outcome"] = "nothing_to_grade"
        item["claim"] = ""
        item["last_failure"] = ""
        _touch(root)
        session_store.save_session(root)
        return True


def resolve_active_child(scoring_session_id: str) -> dict:
    """Return the claimed active child only; never accept a child id from MCP."""
    root_id = str(scoring_session_id or "")
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root:
            return {"ok": False, "code": "session_not_found"}
        index, item = _current_item(root)
        if item is None:
            return {"ok": False, "code": "session_complete", "root": root}
        if index != root.get("active_index") or item.get("status") != "ready":
            return {"ok": False, "code": str(item.get("status") or "not_ready"),
                    "root": root, "item": copy.deepcopy(item)}
        child_id = str(item.get("child_session_id") or "")
        child = session_store.load_session(child_id) if child_id else None
        if not child or not _valid_child(root_id, item, child):
            return {"ok": False, "code": "child_unavailable", "root": root,
                    "item": copy.deepcopy(item)}
        return {"ok": True, "root": root, "item": copy.deepcopy(item),
                "child": child, "index": index}


def record_submit_result(scoring_session_id: str, child_session_id: str, result: dict) -> dict:
    """Record terminal assignment outcomes after the child write owner releases its lock."""
    root_id = str(scoring_session_id or "")
    child_id = str(child_session_id or "")
    with session_store.session_lock(root_id):
        root = load_root_session(root_id)
        if not root:
            return {"ok": False, "code": "session_not_found"}
        index, item = _current_item(root)
        if item is None or index != root.get("active_index"):
            return {"ok": False, "code": "session_complete"}
        if item.get("status") != "ready" or item.get("child_session_id") != child_id:
            return {"ok": False, "code": "active_item_changed"}
        if str(result.get("status") or "") == "needs_teacher_input":
            item["waiting_for_teacher"] = True
            _touch(root)
            session_store.save_session(root)
            return {"ok": True, "progress": _progress(root), "status": root["status"]}

        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        failed_rows = int(counts.get("failed") or 0)
        if not result.get("ok") or failed_rows:
            item["last_failure"] = str(result.get("code") or "write_failed")
            item["waiting_for_teacher"] = False
            _touch(root)
            session_store.save_session(root)
            return {"ok": True, "progress": _progress(root), "status": root["status"]}

        held_rows = int(counts.get("held") or 0)
        item["outcome_counts"] = {
            key: int(counts.get(key) or 0)
            for key in ("finalized", "already_applied", "held", "failed")
        }
        if str(result.get("status") or "") == "held" or held_rows:
            item["status"] = "completed_with_holds"
            item["outcome"] = "completed_with_holds"
        else:
            item["status"] = "completed"
            item["outcome"] = "completed"
        item["last_failure"] = ""
        item["waiting_for_teacher"] = False
        _touch(root)
        session_store.save_session(root)
        return {"ok": True, "progress": _progress(root), "status": root["status"]}


def public_progress(root: dict) -> dict:
    """Identity-free queue progress suitable for MCP responses."""
    return _progress(root)


def active_labels(root: dict) -> dict:
    """Display-only active labels; no Canvas coordinates or child IDs."""
    index, item = _current_item(root)
    if item is None:
        return {"active_course": "", "active_assignment": ""}
    return {
        "active_course": str(item.get("course_label") or ""),
        "active_assignment": str(item.get("assignment_label") or ""),
    }
