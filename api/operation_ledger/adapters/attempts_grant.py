"""Operation Ledger adapter for reviewed extra attempts and reopened windows.

One kind, ``gradebook.attempts_grant``, for one assignment. The same live read
classifies the assignment as ``regular``, ``classic_quiz``, or ``new_quiz``; the
kind table below names the Canvas transport for each. Facts this module relies
on: ``docs/reference/gradebook-module-map.md``, "Verified Canvas facts:
attempts and reopening".

Steps checkpoint one Canvas write each. Whole class (``students: "all"``):
``patch_dates:0``, then ``patch_attempts:0``. A pseudonym list: ``reopen:0``
(one assignment override for every selected student), then ``grant:<index>``
per student in pseudonym order. Every write is preceded by a live re-read: a
value already at the target is skipped, a value still at the frozen ``before``
is written, and anything else never gets a blind write (a whole-class step
blocks as ``drift_detected``; a per-student row skips as
``changed_since_preview``). Extension values are *set*, never added, so the
target is frozen at preview as ``before + N``.

Receipt rows (``failed_items``) carry pseudonyms and before values only; Canvas
user ids and the override id never leave the private step/payload records.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from api import freshness_policy
from api.mirror import read_service
from api.platform_services import canvas_client

from .. import models
from . import adapter_support


KIND = "gradebook.attempts_grant"
UNLIMITED = -1

_ASSIGNMENT = "/api/v1/courses/{course_id}/assignments/{assignment_id}"
_OVERRIDES = _ASSIGNMENT + "/overrides"
_QUIZ = "/api/v1/courses/{course_id}/quizzes/{quiz_id}"
_NEW_QUIZ = "/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}"

# kind -> transport per step family: (method, path template).
KIND_TABLE = {
    "regular": {
        "attempts_all": ("PUT", _ASSIGNMENT),
        "dates_all": ("PUT", _ASSIGNMENT),
        "reopen": ("POST", _OVERRIDES),
        "grant": ("POST", _ASSIGNMENT + "/extensions"),
    },
    "classic_quiz": {
        "attempts_all": ("PUT", _QUIZ),
        "dates_all": ("PUT", _QUIZ),
        "reopen": ("POST", _OVERRIDES),
        "grant": ("POST", _QUIZ + "/extensions"),
    },
    "new_quiz": {
        "attempts_all": ("PATCH", _NEW_QUIZ),
        "dates_all": ("PUT", _ASSIGNMENT),
        "reopen": ("POST", _OVERRIDES),
        "grant": ("POST", _NEW_QUIZ + "/accommodations"),
    },
}

_REGULAR_TYPES = frozenset({"online_upload", "online_url", "online_text_entry"})
GRANTED = frozenset({"done", "already_at_target"})
CHANGED = "changed_since_preview"
DRIFT = "drift_detected"
SKIPPED = frozenset({CHANGED, DRIFT, "not_reached"})
_ITEM = {"patch_attempts": "attempts", "patch_dates": "dates",
         "reopen": "reopen", "grant": "extension"}


def api_id(value):
    text = str(value)
    return int(text) if text.isdigit() else text


def path_for(kind: str, family: str, payload_or_ids: dict) -> tuple[str, str]:
    method, template = KIND_TABLE[kind][family]
    return method, template.format(**payload_or_ids)


def _ids(payload: dict) -> dict:
    return {"course_id": payload["course_id"],
            "assignment_id": payload["assignment_id"],
            "quiz_id": payload.get("quiz_id") or ""}


# ── time and value helpers ──────────────────────────────────────────────────

def instant(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def same_instant(left, right) -> bool:
    if not left and not right:
        return True
    a, b = instant(left), instant(right)
    return a is not None and b is not None and a == b


def window_of(source: dict) -> dict:
    return {"due_at": source.get("due_at"), "lock_at": source.get("lock_at")}


def same_window(left: dict, right: dict) -> bool:
    return (same_instant(left.get("due_at"), right.get("due_at"))
            and same_instant(left.get("lock_at"), right.get("lock_at")))


def total_label(total: int):
    return "unlimited" if total == UNLIMITED else total


def _attempt_total(value) -> int:
    if value is None:
        return UNLIMITED
    if isinstance(value, bool):
        raise ValueError("attempts")
    number = float(value)
    if not number.is_integer():
        raise ValueError("attempts")
    return int(number) if number >= 1 else UNLIMITED


def _extra_value(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError("extra_attempts")
    number = float(value)
    if not number.is_integer() or number < 0:
        raise ValueError("extra_attempts")
    return int(number)


# ── live reads ──────────────────────────────────────────────────────────────

def classify_assignment(assignment: dict) -> str | None:
    """``regular``, ``classic_quiz``, ``new_quiz``, or ``None`` when unsupported."""
    types = {str(item) for item in assignment.get("submission_types") or []}
    if assignment.get("is_quiz_lti_assignment"):
        return "new_quiz"
    if "online_quiz" in types and assignment.get("quiz_id"):
        return "classic_quiz"
    if types and types <= _REGULAR_TYPES:
        return "regular"
    return None


def read_facts(course_id: str, assignment_id: str) -> tuple[dict | None, str | None]:
    """One live assignment read with base dates, classified, plus the attempts
    setting of the kind. Returns ``(facts, None)`` or ``(None, code)``."""
    ids = {"course_id": course_id, "assignment_id": assignment_id}
    assignment, error = canvas_client.canvas_get(
        _ASSIGNMENT.format(**ids), params={"override_assignment_dates": "false"})
    if error or not isinstance(assignment, dict):
        return None, "assignment_live_unavailable"
    kind = classify_assignment(assignment)
    if kind is None:
        return None, "unsupported_assignment"
    facts = {
        "kind": kind,
        "title": str(assignment.get("name") or assignment_id),
        "quiz_id": None,
        "allowed_attempts": UNLIMITED,
        "due_at": assignment.get("due_at"),
        "lock_at": assignment.get("lock_at"),
    }
    try:
        if kind == "regular":
            facts["allowed_attempts"] = _attempt_total(assignment.get("allowed_attempts"))
        elif kind == "classic_quiz":
            quiz_id = str(assignment["quiz_id"])
            quiz, error = canvas_client.canvas_get(
                _QUIZ.format(course_id=course_id, quiz_id=quiz_id))
            if error or not isinstance(quiz, dict):
                return None, "assignment_live_unavailable"
            facts.update(
                quiz_id=quiz_id, due_at=quiz.get("due_at"), lock_at=quiz.get("lock_at"),
                allowed_attempts=_attempt_total(quiz.get("allowed_attempts")))
        else:
            quiz, error = canvas_client.canvas_get(_NEW_QUIZ.format(**ids))
            if error or not isinstance(quiz, dict):
                return None, "assignment_live_unavailable"
            multiple = (quiz.get("quiz_settings") or {}).get("multiple_attempts") or {}
            limit, maximum = multiple.get("attempt_limit"), multiple.get("max_attempts")
            if not multiple.get("multiple_attempts_enabled"):
                facts["allowed_attempts"] = 1
            elif limit is False or (limit is None and maximum is None):
                facts["allowed_attempts"] = UNLIMITED
            else:
                facts["allowed_attempts"] = _attempt_total(maximum)
            facts["score_to_keep"] = str(multiple.get("score_to_keep") or "highest")
    except (ValueError, TypeError, KeyError):
        return None, "attempts_unreadable"
    return facts, None


def read_overrides(course_id: str, assignment_id: str) -> tuple[list | None, str | None]:
    rows, error, _complete = canvas_client.canvas_get_all_complete(
        _OVERRIDES.format(course_id=course_id, assignment_id=assignment_id),
        {"per_page": 100})
    if error or not isinstance(rows, list):
        return None, "overrides_unavailable"
    return [row for row in rows if isinstance(row, dict)], None


def _classic_extras(course_id: str, quiz_id: str) -> tuple[dict | None, str | None]:
    pages, error = canvas_client.canvas_get_all(
        _QUIZ.format(course_id=course_id, quiz_id=quiz_id) + "/submissions",
        {"per_page": 100})
    if error or not isinstance(pages, list):
        return None, "attempts_unavailable"
    extras = {}
    try:
        for page in pages:
            rows = page.get("quiz_submissions") if isinstance(page, dict) else None
            for row in (rows if isinstance(rows, list) else [page]):
                if isinstance(row, dict) and row.get("user_id") is not None:
                    extras[str(row["user_id"])] = _extra_value(row.get("extra_attempts"))
    except (ValueError, TypeError):
        return None, "attempts_unavailable"
    return extras, None


def read_extra_attempts(kind: str, course_id: str, assignment_id: str, quiz_id,
                        user_ids: list[str]) -> tuple[dict | None, str | None]:
    """Live ``extra_attempts`` per user (0 when absent); regular and classic only."""
    if kind == "classic_quiz":
        extras, code = _classic_extras(course_id, str(quiz_id))
        if code:
            return None, code
        return {user_id: extras.get(str(user_id), 0) for user_id in user_ids}, None
    values = {}
    for user_id in user_ids:
        submission, error = canvas_client.canvas_get(
            _ASSIGNMENT.format(course_id=course_id, assignment_id=assignment_id)
            + f"/submissions/{user_id}")
        if error or not isinstance(submission, dict):
            return None, "attempts_unavailable"
        try:
            values[str(user_id)] = _extra_value(submission.get("extra_attempts"))
        except (ValueError, TypeError):
            return None, "attempts_unavailable"
    return values, None


def _mirror_students(course_id: str, assignment_id: str) -> dict:
    """Current roster records and the students the mirror shows on this assignment.

    The one mirror seam: a blocking envelope, or the roster plus the current
    students that have a submission row for the assignment.
    """
    roster = read_service.private_roster(course_id, max_age_hours=None)
    assignments = read_service.private_assignments(course_id, max_age_hours=None)
    submissions = read_service.private_submissions(course_id, max_age_hours=None)
    if any(not isinstance(scope.get("records"), list)
           or not scope.get("last_success_at")
           or scope.get("state") not in {"current", "stale"}
           for scope in (roster, assignments, submissions)):
        return (adapter_support.mirror_freshness_attention(
            roster, assignments, submissions) or {"blocking_error": "freshness_attention"})
    attention = adapter_support.mirror_freshness_attention(
        roster, assignments, submissions)
    if attention:
        return attention
    current = {str(student["id"]) for student in roster["records"]
               if student.get("id") is not None}
    on_assignment = {
        str(row["user_id"]) for row in submissions["records"]
        if str(row.get("assignment_id")) == str(assignment_id)
        and row.get("user_id") is not None and str(row["user_id"]) in current
    }
    synced_at = min(scope["last_success_at"]
                    for scope in (roster, assignments, submissions))
    return {
        "roster": copy.deepcopy(roster["records"]),
        "on_assignment_ids": sorted(on_assignment),
        "synced_at": synced_at,
        "freshness": freshness_policy.freshness_envelope(
            "mirror", "submissions",
            "stale" if any(scope.get("state") == "stale"
                           for scope in (roster, assignments, submissions))
            else "current", synced_at),
    }


# ── one observation per step, shared by execute and reconcile ──────────────

def step_keys(payload: dict) -> list[str]:
    keys = []
    grant = payload.get("grant") or {}
    if payload.get("scope") == "all":
        whole = payload.get("whole_class") or {}
        if "dates" in whole:
            keys.append("patch_dates:0")
        if "attempts" in whole:
            keys.append("patch_attempts:0")
        return keys
    if payload.get("reopen"):
        keys.append("reopen:0")
    if grant.get("extra_attempts") is not None:
        keys.extend(f"grant:{index}" for index in range(len(payload.get("entries") or [])))
    return keys


def _split(step_key: str) -> tuple[str, int]:
    prefix, _sep, index = str(step_key).partition(":")
    return prefix, int(index or 0)


def _override_is_ours(override: dict, user_ids: set[str], window: dict) -> bool:
    return (
        {str(item) for item in override.get("student_ids") or []} == user_ids
        and same_instant(override.get("due_at"), window["due_at"])
        and same_instant(override.get("lock_at"), window["lock_at"])
    )


def observe(payload: dict, course_id: str, step_key: str) -> tuple[str, object]:
    """Read Canvas once for a step: ``("target"|"before"|"other"|"error", detail)``."""
    prefix, index = _split(step_key)
    ids = {**_ids(payload), "course_id": course_id}
    if prefix in ("patch_attempts", "patch_dates"):
        facts, code = read_facts(course_id, payload["assignment_id"])
        if code:
            return "error", code
        item = "attempts" if prefix == "patch_attempts" else "dates"
        change = payload["whole_class"][item]
        current = facts["allowed_attempts"] if item == "attempts" else window_of(facts)
        equal = ((lambda a, b: a == b) if item == "attempts" else same_window)
        if equal(current, change["after"]):
            return "target", facts
        return ("before" if equal(current, change["before"]) else "other"), facts
    if prefix == "reopen":
        overrides, code = read_overrides(course_id, payload["assignment_id"])
        if code:
            return "error", code
        user_ids = {str(entry["user_id"]) for entry in payload["entries"]}
        own = next((row for row in overrides
                    if _override_is_ours(row, user_ids, payload["reopen"])), None)
        if own is not None:
            return "target", own.get("id")
        overlap = any(user_ids & {str(item) for item in row.get("student_ids") or []}
                      for row in overrides)
        return ("other" if overlap else "before"), None
    if payload["kind"] == "new_quiz":
        return "other", None  # no readback exists for a New Quiz accommodation
    entry = payload["entries"][index]
    extras, code = read_extra_attempts(
        payload["kind"], course_id, payload["assignment_id"], ids["quiz_id"],
        [entry["user_id"]])
    if code:
        return "error", code
    current = extras[str(entry["user_id"])]
    if current == entry["after_extra"]:
        return "target", current
    return ("before" if current == entry["before_extra"] else "other"), current


def receipt_row(payload: dict, step_key: str, outcome: str) -> dict:
    """One pseudonym-only receipt row: no Canvas user id, no override id."""
    prefix, index = _split(step_key)
    row = {"step": step_key, "outcome": outcome}
    if prefix == "patch_attempts":
        change = payload["whole_class"]["attempts"]
        row.update(item="attempts", before=total_label(change["before"]),
                   after=total_label(change["after"]))
    elif prefix == "patch_dates":
        change = payload["whole_class"]["dates"]
        row.update(item="dates", before=change["before"], after=change["after"])
    elif prefix == "reopen":
        row.update(item="reopen", window=copy.deepcopy(payload["reopen"]))
    else:
        entry = payload["entries"][index]
        row.update(item="student", pseudonym=entry["pseudonym"],
                   before_extra=entry["before_extra"], after_extra=entry["after_extra"])
    return row


def _definitive(error) -> bool:
    text = str(error or "")
    return not (adapter_support.is_uncertain(text) or text.startswith("HTTP 5"))


def _send(context, steps, step, method, path, body):
    """The one write-ahead-marked Canvas write for every step of this kind."""
    marked = {**copy.deepcopy(step), **context.before_send(
        step["step_key"],
        models.sha256_dict({"method": method, "path": path, "payload": body}))}
    adapter_support.replace_step(steps, marked)
    response, error = canvas_client._canvas_send(method, path, body)
    return marked, response, error


def _whole_body(kind: str, item: str, after, facts: dict) -> dict:
    if item == "dates":
        key = "quiz" if kind == "classic_quiz" else "assignment"
        return {key: {"due_at": after["due_at"], "lock_at": after["lock_at"]}}
    if kind == "regular":
        return {"assignment": {"allowed_attempts": after}}
    if kind == "classic_quiz":
        return {"quiz": {"allowed_attempts": after}}
    settings = {"multiple_attempts_enabled": True, "attempt_limit": after != UNLIMITED,
                "score_to_keep": facts.get("score_to_keep") or "highest"}
    if after != UNLIMITED:
        settings["max_attempts"] = after
    return {"quiz_settings": {"multiple_attempts": settings}}


def _user_in(items, user_id: str) -> bool:
    return any(str(item.get("user_id") if isinstance(item, dict) else item) == user_id
               for item in items or [])


class _Run:
    """One execute pass: walks the steps, checkpoints each, and keeps the rows."""

    def __init__(self, payload: dict, target: dict, context):
        self.payload, self.context = payload, context
        self.course_id = target["course_id"]
        self.kind = payload["kind"]
        self.steps = copy.deepcopy(target.get("steps") or [])
        self.rows = {row["step"]: copy.deepcopy(row)
                     for row in target.get("failed_items") or []
                     if isinstance(row, dict) and row.get("step")}

    # -- bookkeeping ------------------------------------------------------

    @staticmethod
    def _unresolved(step) -> bool:
        return step.get("state") in ("claimed", "sent_unknown")

    def _record(self, step, outcome: str) -> None:
        self.rows[step["step_key"]] = receipt_row(
            self.payload, step["step_key"], outcome)

    def _mark(self, step, state, code=None, **extra):
        saved = self.context.checkpoint_step(
            {**copy.deepcopy(step), "state": state, "error_code": code, **extra})
        adapter_support.replace_step(self.steps, saved)
        return saved

    def _finish(self, step, outcome: str, **extra):
        self._mark(step, "applied", **extra)
        self._record(step, outcome)

    def _stop(self, state: str, step, code: str, diagnostic=None):
        self._record(step, code)
        return adapter_support.build_result(
            state, steps=self.steps, error_code=code,
            private_diagnostic=str(diagnostic) if diagnostic is not None else None,
            failed_items=list(self.rows.values()))

    def _fail_row(self, step, code: str) -> None:
        self._mark(step, "failed", code)
        self._record(step, code)

    def _read_failed(self, step, code: str):
        if self._unresolved(step):
            return self._stop("sent_unknown", step, code)
        self._fail_row(step, code)
        return None

    def _unmatched(self, step, code: str):
        """The live value is neither the frozen before nor the target."""
        if self._unresolved(step):
            return self._stop("sent_unknown", step, code)
        if step["step_key"].startswith("patch_"):
            self._mark(step, "blocked", DRIFT)
            self._record(step, DRIFT)
            return adapter_support.build_result(
                "blocked", steps=self.steps, error_code=DRIFT,
                failed_items=list(self.rows.values()))
        self._mark(step, "skipped", CHANGED)
        self._record(step, CHANGED)
        return None

    def _write_failed(self, marked, error, rejected: str, uncertain: str):
        if _definitive(error):
            self._fail_row(marked, rejected)
            return None
        self._mark(marked, "sent_unknown", uncertain, private_diagnostic=str(error))
        return self._stop("sent_unknown", marked, uncertain, error)

    def _verify(self, marked, step_key: str, unverified: str, uncertain: str):
        """Readback after a write: the step must now observe at its target."""
        verdict, _detail = observe(self.payload, self.course_id, step_key)
        if verdict == "target":
            return None
        code = uncertain if verdict == "error" else unverified
        self._mark(marked, "sent_unknown", code)
        return self._stop("sent_unknown", marked, code)

    # -- steps ------------------------------------------------------------

    def step(self, step):
        step_key = step["step_key"]
        prefix, index = _split(step_key)
        if prefix == "grant" and self.kind == "new_quiz":
            return self._accommodation(step, index)
        verdict, detail = observe(self.payload, self.course_id, step_key)
        if verdict == "error":
            return self._read_failed(step, detail)
        if verdict == "target":
            override = {"override_id": str(detail)} if prefix == "reopen" and detail else {}
            self._finish(step, "done" if self._unresolved(step) else "already_at_target",
                         **override)
            return None
        if verdict == "other":
            return self._unmatched(step, f"{_ITEM[prefix]}_uncertain")
        handler = {"patch_attempts": self._patch, "patch_dates": self._patch,
                   "reopen": self._reopen, "grant": self._grant}[prefix]
        return handler(step, index, detail)

    def _send_step(self, step, family: str, body):
        method, path = path_for(self.kind, family, {**_ids(self.payload),
                                                     "course_id": self.course_id})
        return _send(self.context, self.steps, step, method, path, body)

    def _patch(self, step, _index, facts):
        prefix, _ = _split(step["step_key"])
        item = "attempts" if prefix == "patch_attempts" else "dates"
        after = self.payload["whole_class"][item]["after"]
        marked, _response, error = self._send_step(
            step, f"{item}_all", _whole_body(self.kind, item, after, facts))
        if error:
            return self._write_failed(marked, error, f"{item}_rejected", f"{item}_uncertain")
        stop = self._verify(marked, step["step_key"], f"{item}_unverified",
                            f"{item}_uncertain")
        if stop:
            return stop
        self._finish(marked, "done")
        return None

    def _reopen(self, step, _index, _detail):
        window = self.payload["reopen"]
        marked, response, error = self._send_step(step, "reopen", {"assignment_override": {
            "student_ids": [api_id(entry["user_id"]) for entry in self.payload["entries"]],
            "due_at": window["due_at"], "lock_at": window["lock_at"]}})
        if error:
            return self._write_failed(marked, error, "reopen_rejected", "reopen_uncertain")
        override_id = response.get("id") if isinstance(response, dict) else None
        if override_id is None:
            self._mark(marked, "sent_unknown", "reopen_unverified")
            return self._stop("sent_unknown", marked, "reopen_unverified")
        marked = self._mark(marked, "claimed", override_id=str(override_id))
        stop = self._verify(marked, step["step_key"], "reopen_unverified",
                            "reopen_uncertain")
        if stop:
            return stop
        self._finish(marked, "done", override_id=str(override_id))
        return None

    def _grant(self, step, index, _detail):
        entry = self.payload["entries"][index]
        key = "quiz_extensions" if self.kind == "classic_quiz" else "assignment_extensions"
        marked, response, error = self._send_step(step, "grant", {key: [{
            "user_id": api_id(entry["user_id"]),
            "extra_attempts": entry["after_extra"]}]})
        if error:
            return self._write_failed(marked, error, "extension_rejected",
                                      "extension_uncertain")
        listed = response.get(key) if isinstance(response, dict) else None
        if isinstance(listed, list) and not _user_in(listed, str(entry["user_id"])):
            # Canvas answered 2xx but lists no row for this student: it applied nothing.
            self._fail_row(marked, "extension_not_applied")
            return None
        stop = self._verify(marked, step["step_key"], "extension_unverified",
                            "extension_uncertain")
        if stop:
            return stop
        self._finish(marked, "done")
        return None

    def _accommodation(self, step, index):
        """New Quiz has no readback: Canvas's own successful/failed lists decide."""
        if self._unresolved(step):
            return self._stop("sent_unknown", step, "accommodation_unconfirmed")
        entry = self.payload["entries"][index]
        marked, response, error = self._send_step(step, "grant", [{
            "user_id": api_id(entry["user_id"]),
            "extra_attempts": entry["after_extra"]}])
        if error:
            return self._write_failed(marked, error, "accommodation_rejected",
                                      "accommodation_uncertain")
        response = response if isinstance(response, dict) else {}
        user_id = str(entry["user_id"])
        if _user_in(response.get("successful"), user_id):
            self._finish(marked, "done")
            return None
        if _user_in(response.get("failed"), user_id):
            self._fail_row(marked, "accommodation_failed")
            return None
        self._mark(marked, "sent_unknown", "accommodation_unconfirmed")
        return self._stop("sent_unknown", marked, "accommodation_unconfirmed")


class AttemptsGrantAdapter:
    kind = KIND

    def build_payload(self, prepare_request: dict) -> dict:
        if not isinstance(prepare_request, dict):
            raise ValueError("prepare request must be an object")
        return copy.deepcopy(prepare_request)

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "course_id": payload.get("course_id"),
            "assignment_id": payload.get("assignment_id"),
            "kind": payload.get("kind"),
            "grant": payload.get("grant"),
            "whole_class": payload.get("whole_class"),
            "reopen": payload.get("reopen"),
            "entries": payload.get("entries") or [],
        })

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        if len(targets) != 1:
            raise ValueError("an attempts grant requires exactly one course target")
        course_id = str(targets[0].get("course_id") or "")
        if not course_id or course_id != str(payload.get("course_id") or ""):
            raise ValueError("target course does not match attempts grant request")
        return [{
            "course_id": course_id,
            "target_key": self.target_key(payload, course_id),
            "idempotency_key": self.idempotency_key(payload, course_id),
        }]

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{KIND}|{course_id}|{payload.get('assignment_id', '')}")

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(f"{course_id}|{self.source_digest(payload)}")

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id, assignment_id = target["course_id"], str(payload["assignment_id"])
        facts, code = read_facts(course_id, assignment_id)
        if code:
            return {"blocking_error": code}
        if target.get("baseline") is not None:
            # Apply/retry: the one live read used by check_drift.
            return {"assignment": facts}
        baseline = {"course_id": course_id, "assignment_id": assignment_id,
                    "assignment": facts}
        if (payload.get("grant") or {}).get("students") == "all":
            return baseline
        mirror = _mirror_students(course_id, assignment_id)
        if mirror.get("blocking_error"):
            return mirror
        overrides, code = read_overrides(course_id, assignment_id)
        if code:
            return {"blocking_error": code}
        return {**baseline, **mirror, "overrides": overrides}

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        return copy.deepcopy(payload.get("_review") or {})

    def initial_steps(self, payload: dict, baseline: dict) -> list[dict]:
        return [models.new_step(key) for key in step_keys(payload)]

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        frozen = (baseline or {}).get("assignment")
        if not frozen:
            return True
        facts, code = read_facts(target["course_id"], str(payload["assignment_id"]))
        if code or facts["kind"] != frozen.get("kind"):
            return True
        # Whole-class values are checked per step against the frozen before, so
        # a resume after partial progress is not mistaken for drift.
        if (payload.get("scope") == "students"
                and (payload.get("grant") or {}).get("extra_attempts") is not None):
            return facts["allowed_attempts"] != frozen.get("allowed_attempts")
        return False

    def execute(self, payload: dict, target: dict, baseline: dict,
                claim: dict, context) -> dict:
        run = _Run(payload, target, context)
        for step in list(run.steps):
            if step.get("state") == "applied":
                continue
            stop = run.step(step)
            if stop is not None:
                return stop
        clean = all(step.get("state") == "applied" for step in run.steps)
        return adapter_support.build_result(
            "applied" if clean else "partial",
            steps=run.steps, failed_items=list(run.rows.values()))

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        steps = copy.deepcopy(target.get("steps") or [])
        unknown = pending = False
        for step in steps:
            if step.get("state") == "applied":
                continue
            if step.get("state") not in ("claimed", "sent_unknown"):
                pending = True
                continue
            if _split(step["step_key"])[0] == "grant" and payload.get("kind") == "new_quiz":
                unknown = True
                continue
            verdict, detail = observe(payload, target["course_id"], step["step_key"])
            if verdict == "target":
                step["state"], step["error_code"] = "applied", None
                if _split(step["step_key"])[0] == "reopen" and detail:
                    step["override_id"] = str(detail)
            elif verdict == "before":
                pending = True
            else:
                unknown = True
        state = "sent_unknown" if unknown else "pending" if pending else "applied"
        return {"state": state, "steps": steps}

    def retry_selector(self, operation: dict) -> list[dict]:
        return [
            target for target in operation.get("targets", [])
            if models.is_unresolved_target_state(target.get("state", "pending"))
        ]


__all__ = ["KIND", "KIND_TABLE", "AttemptsGrantAdapter"]
