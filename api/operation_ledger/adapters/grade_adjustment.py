"""Operation Ledger adapter for reviewed existing-grade adjustments.

Preview baselines are assembled from the typed private CanvasMirror.  The
executor calls this adapter's live assignment check before it claims a target,
then the adapter performs a live per-submission check immediately before each
``posted_grade`` PUT.  No student names or pseudonyms belong in this private
adapter baseline.
"""
from __future__ import annotations

import copy
import math

from api import freshness_policy, operational_log
from api.mirror import read_service
from api.platform_services import canvas_client

from .. import models
from . import adapter_support


KIND = "gradebook.grade_adjustment"
_SCORE_TOLERANCE = 1e-6


def _is_number(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _number(value):
    number = float(value)
    return int(number) if number.is_integer() else number


def _numbers_equal(left, right) -> bool:
    return (_is_number(left) and _is_number(right)
            and math.isclose(float(left), float(right), rel_tol=0.0,
                             abs_tol=_SCORE_TOLERANCE))


def _assignment_facts(assignment: dict) -> dict:
    return {
        "id": str(assignment.get("id") or ""),
        "grading_type": str(assignment.get("grading_type") or ""),
        "points_possible": _number(assignment.get("points_possible")),
    }


def _assignment_digest(assignment: dict) -> str:
    return models.sha256_dict(_assignment_facts(assignment))


def _freshness_attention(roster: dict, assignments: dict, submissions: dict) -> dict:
    timestamps = [
        str(scope.get("last_success_at") or "")
        for scope in (roster, assignments, submissions)
    ]
    synced_at = min(timestamps) if timestamps and all(timestamps) else ""
    state = "stale" if any(scope.get("state") == "stale"
                            for scope in (roster, assignments, submissions)) else "unavailable"
    freshness = freshness_policy.freshness_envelope(
        "mirror", "submissions", state, synced_at)
    if (freshness.get("state") in {"current", "stale"}
            and freshness.get("within_policy")):
        return {}
    attention = {
        "action": "ask_teacher_confirmation",
        "reason": (
            "This local Canvas snapshot is outside the configured freshness window. "
            "Ask the teacher before relying on it; do not refresh automatically."
        ),
    }
    return {"blocking_error": "freshness_attention", "freshness": freshness,
            "attention": attention}


def _mirror_baseline(payload: dict, target: dict) -> dict:
    course_id = target["course_id"]
    assignment_id = str(payload["assignment_id"])
    roster = read_service.private_roster(course_id, max_age_hours=None)
    assignments = read_service.private_assignments(course_id, max_age_hours=None)
    submissions = read_service.private_submissions(course_id, max_age_hours=None)
    if any(not isinstance(scope.get("records"), list)
           or not scope.get("last_success_at")
           or scope.get("state") not in {"current", "stale"}
           for scope in (roster, assignments, submissions)):
        result = _freshness_attention(roster, assignments, submissions)
        return result or {"blocking_error": "freshness_attention"}
    freshness = _freshness_attention(roster, assignments, submissions)
    if freshness:
        return freshness

    assignment = next(
        (row for row in assignments["records"]
         if str(row.get("id")) == assignment_id), None)
    if assignment is None:
        return {"blocking_error": "assignment_not_found"}
    facts = _assignment_facts(assignment)
    if facts["grading_type"] != "points" or facts["points_possible"] <= 0:
        return {"blocking_error": "unsupported_grading_type",
                "assignment": facts}

    current_ids = {
        str(student.get("id")) for student in roster["records"]
        if student.get("id") is not None
    }
    rows_by_user = {
        str(row.get("user_id")): row
        for row in submissions["records"]
        if str(row.get("assignment_id")) == assignment_id
        and row.get("user_id") is not None
    }
    entries = []
    for user_id in sorted(current_ids):
        row = rows_by_user.get(user_id)
        if row is None:
            entries.append({"user_id": user_id, "eligible": False,
                            "skip_reason": "no_score", "before": None,
                            "before_excused": False})
            continue
        excused = bool(row.get("excused"))
        score = row.get("score")
        if excused:
            entries.append({"user_id": user_id, "eligible": False,
                            "skip_reason": "excused", "before": score,
                            "before_excused": True})
        elif not _is_number(score):
            entries.append({"user_id": user_id, "eligible": False,
                            "skip_reason": "no_score", "before": score,
                            "before_excused": False})
        else:
            entries.append({"user_id": user_id, "eligible": True,
                            "skip_reason": None, "before": _number(score),
                            "before_excused": False})
    for user_id, row in sorted(rows_by_user.items()):
        if user_id in current_ids:
            continue
        entries.append({"user_id": user_id, "eligible": False,
                        "skip_reason": "not_current", "before": row.get("score"),
                        "before_excused": bool(row.get("excused"))})

    return {
        "course_id": str(course_id),
        "assignment_id": assignment_id,
        "assignment": {
            **facts,
            "name": str(assignment.get("name") or assignment_id),
        },
        "entries": entries,
        "synced_at": min(scope["last_success_at"]
                          for scope in (roster, assignments, submissions)),
        "freshness": freshness_policy.freshness_envelope(
            "mirror", "submissions",
            "stale" if any(scope.get("state") == "stale"
                            for scope in (roster, assignments, submissions))
            else "current",
            min(scope["last_success_at"]
                for scope in (roster, assignments, submissions))),
        "roster": copy.deepcopy(roster["records"]),
        "validation_digest": _assignment_digest(assignment),
    }


class GradeAdjustmentAdapter:
    kind = KIND

    def build_payload(self, prepare_request: dict) -> dict:
        if not isinstance(prepare_request, dict):
            raise ValueError("prepare request must be an object")
        return copy.deepcopy(prepare_request)

    def freeze_payload(self, payload: dict, baseline: dict) -> dict:
        frozen = copy.deepcopy(payload)
        frozen["assignment"] = copy.deepcopy(baseline.get("assignment") or {})
        frozen["points_possible"] = baseline["assignment"]["points_possible"]
        frozen["assignment_name"] = baseline["assignment"].get("name", "")
        return frozen

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "course_id": payload.get("course_id"),
            "assignment_id": payload.get("assignment_id"),
            "adjustment": payload.get("adjustment"),
            "entries": payload.get("entries") or [],
        })

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        if len(targets) != 1:
            raise ValueError("a grade adjustment requires exactly one course target")
        target = targets[0]
        course_id = str(target.get("course_id") or "")
        if not course_id or course_id != str(payload.get("course_id") or ""):
            raise ValueError("target course does not match grade adjustment request")
        return [{
            "course_id": course_id,
            "target_key": self.target_key(payload, course_id),
            "idempotency_key": self.idempotency_key(payload, course_id),
        }]

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{KIND}|{course_id}|{payload.get('assignment_id', '')}"
        )

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(f"{course_id}|{self.source_digest(payload)}")

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        # A persisted target has a mirror baseline.  At apply/retry time this
        # branch is the one live assignment read used by check_drift.
        if target.get("baseline") is not None:
            assignment, error = canvas_client.canvas_get(
                f"/api/v1/courses/{target['course_id']}/assignments/{payload['assignment_id']}"
            )
            if error or not isinstance(assignment, dict):
                return {"blocking_error": "assignment_live_unavailable",
                        "private_diagnostic": str(error or "invalid assignment response")}
            facts = _assignment_facts(assignment)
            return {"assignment": facts,
                    "validation_digest": _assignment_digest(assignment)}
        return _mirror_baseline(payload, target)

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        return copy.deepcopy(payload.get("_review") or {})

    def initial_steps(self, payload: dict, baseline: dict) -> list[dict]:
        return [
            models.new_step(f"adjust:{index}")
            for index, entry in enumerate(payload.get("entries") or [])
            if entry.get("changed")
        ]

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        if baseline.get("blocking_error"):
            return True
        stored = (target.get("baseline") or {}).get("assignment") or {}
        fresh = baseline.get("assignment") or {}
        return any(stored.get(field) != fresh.get(field)
                   for field in ("grading_type", "points_possible"))

    @staticmethod
    def _entry_for_step(payload: dict, step_key: str) -> dict | None:
        try:
            index = int(str(step_key).split(":", 1)[1])
            return (payload.get("entries") or [])[index]
        except (IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _submission_matches(submission: dict | None, score, excused: bool) -> bool:
        submission = submission or {}
        if bool(submission.get("excused")) != bool(excused):
            return False
        return _numbers_equal(submission.get("score"), score)

    @staticmethod
    def _mark_step(context, steps, step, state, error_code=None):
        step = copy.deepcopy(step)
        step["state"] = state
        step["error_code"] = error_code
        step = context.checkpoint_step(step)
        adapter_support.replace_step(steps, step)
        return step

    def execute(self, payload: dict, target: dict, baseline: dict,
                claim: dict, context) -> dict:
        steps = copy.deepcopy(target.get("steps") or [])
        preview_entries = payload.get("entries") or []
        receipt_entries = {
            str(item.get("user_id")): copy.deepcopy(item)
            for item in (target.get("failed_items") or [])
            if isinstance(item, dict) and item.get("user_id") is not None
        }
        writes = 0

        for step in steps:
            if step.get("state") == "applied":
                continue
            entry = self._entry_for_step(payload, step.get("step_key"))
            if not entry:
                continue
            user_id = str(entry["user_id"])
            path = (
                f"/api/v1/courses/{target['course_id']}/assignments/"
                f"{payload['assignment_id']}/submissions/{user_id}"
            )

            if step.get("state") == "sent_unknown":
                verdict = self._reconcile_entry(path, entry)
                if verdict == "applied":
                    self._mark_step(context, steps, step, "applied")
                    receipt_entries[user_id] = self._receipt_entry(entry, "done")
                    continue
                if verdict == "unknown":
                    receipt_entries[user_id] = self._receipt_entry(
                        entry, step.get("error_code") or "grade_write_uncertain")
                    return adapter_support.build_result(
                        "sent_unknown", steps=steps,
                        error_code=step.get("error_code") or "grade_write_uncertain",
                        failed_items=list(receipt_entries.values()),
                    )
                step["state"] = "pending"

            current, read_error = canvas_client.canvas_get(path)
            if read_error:
                step = self._mark_step(context, steps, step, "failed", "score_read_failed")
                receipt_entries[user_id] = self._receipt_entry(entry, "score_read_failed")
                return adapter_support.build_result(
                    "failed", steps=steps, error_code="score_read_failed",
                    failed_items=list(receipt_entries.values()),
                )
            if not self._submission_matches(
                    current, entry.get("before"), entry.get("before_excused", False)):
                step = self._mark_step(
                    context, steps, step, "skipped", "score_changed_since_preview")
                receipt_entries[user_id] = self._receipt_entry(
                    entry, "score_changed_since_preview")
                continue

            request = {"submission": {"posted_grade": str(entry["after"])}}
            context.before_send(
                step["step_key"],
                models.sha256_dict({"method": "PUT", "path": path, "payload": request}),
            )
            _response, error = canvas_client._canvas_send("PUT", path, request)
            if error:
                uncertain = adapter_support.is_uncertain(error)
                code = "grade_write_uncertain" if uncertain else "grade_write_rejected"
                step_state = "sent_unknown" if uncertain else "failed"
                self._mark_step(context, steps, step, step_state, code)
                receipt_entries[user_id] = self._receipt_entry(entry, code)
                return adapter_support.build_result(
                    step_state, steps=steps, error_code=code,
                    private_diagnostic=str(error),
                    failed_items=list(receipt_entries.values()),
                )

            readback, read_error = canvas_client.canvas_get(path)
            if read_error:
                code = "grade_write_uncertain"
                self._mark_step(context, steps, step, "sent_unknown", code)
                receipt_entries[user_id] = self._receipt_entry(entry, code)
                return adapter_support.build_result(
                    "sent_unknown", steps=steps, error_code=code,
                    private_diagnostic=str(read_error),
                    failed_items=list(receipt_entries.values()),
                )
            if not self._submission_matches(readback, entry.get("after"), False):
                code = "grade_write_unverified"
                self._mark_step(context, steps, step, "sent_unknown", code)
                receipt_entries[user_id] = self._receipt_entry(entry, code)
                return adapter_support.build_result(
                    "sent_unknown", steps=steps, error_code=code,
                    failed_items=list(receipt_entries.values()),
                )
            self._mark_step(context, steps, step, "applied")
            receipt_entries[user_id] = self._receipt_entry(entry, "done")
            writes += 1

        if writes:
            try:
                from api.webui import mirror_service
                mirror_service.notify_course_changed(target["course_id"])
            except Exception as exc:
                operational_log.emit("mirror.notify_course_changed", "failed",
                                     error_class=type(exc))
        return adapter_support.build_result(
            "applied", steps=steps, failed_items=list(receipt_entries.values()))

    @staticmethod
    def _receipt_entry(entry: dict, outcome: str) -> dict:
        return {
            "user_id": str(entry.get("user_id")),
            "before": entry.get("before"),
            "after": entry.get("after"),
            "outcome": outcome,
        }

    def _reconcile_entry(self, path: str, entry: dict) -> str:
        current, error = canvas_client.canvas_get(path)
        if error:
            return "unknown"
        if self._submission_matches(current, entry.get("after"), False):
            return "applied"
        if self._submission_matches(current, entry.get("before"),
                                    entry.get("before_excused", False)):
            return "pending"
        return "unknown"

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        steps = copy.deepcopy(target.get("steps") or [])
        for step in steps:
            if step.get("state") != "sent_unknown":
                continue
            entry = self._entry_for_step(payload, step.get("step_key"))
            if not entry:
                return {"state": "sent_unknown", "steps": steps}
            path = (
                f"/api/v1/courses/{target['course_id']}/assignments/"
                f"{payload['assignment_id']}/submissions/{entry['user_id']}"
            )
            verdict = self._reconcile_entry(path, entry)
            if verdict != "applied":
                return {"state": "sent_unknown", "steps": steps}
            step["state"] = "applied"
            step["error_code"] = None
        return {"state": "applied", "steps": steps}

    def retry_selector(self, operation: dict) -> list[dict]:
        return [
            target for target in operation.get("targets", [])
            if models.is_unresolved_target_state(target.get("state", "pending"))
        ]


__all__ = ["KIND", "GradeAdjustmentAdapter"]
