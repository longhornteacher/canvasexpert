"""Operation Ledger adapter for the course-wide reviewed missing-work sweep.

Separate from ``gradebook.grade_adjustment`` (grade-adjustment-contract.md):
that lane changes an existing numeric score and reverts to a recorded
``before`` value, neither of which fits a blank row. This kind fills an
eligible missing row with the policy's missing value and an explicit Canvas
``missing`` status, and undoes back to blank.

Assignment facts (``grading_type``, ``submission_types``, group settings,
closed grading period) are not in the mirror, so this adapter reads them
live, once per *candidate* assignment, at baseline capture -- both at preview
and at apply/retry (grading-policy-contract.md section 6). Candidates are
pre-filtered from the mirror (published, has a due date, at least one
eligible-looking row) so a course with many assignments does not require a
live read of every one.

Per-row staleness is re-checked live immediately before each write inside
``execute`` (a submission, a score, an excuse, a custom grade status, or an
extended late status all skip that one row as ``changed_since_preview``), so
``check_drift`` only needs to report the baseline capture's own blocking
errors -- a coarse course-level diff would be a weaker guarantee than the
per-row check already gives.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone

from api import grading_policy, operational_log
from api.freshness_policy import LOCAL_TIMEZONE
from api.mirror import read_service
from api.platform_services import canvas_client, config

from .. import models
from . import adapter_support


KIND = "gradebook.missing_fill"

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


def _today_local_date():
    return datetime.now(timezone.utc).astimezone(LOCAL_TIMEZONE).date()


def _assignment_eligibility(assignment: dict) -> str | None:
    """Return ``None`` when the live assignment is eligible for the sweep,
    else the one exclusion reason (decision 3 / grading-policy-contract.md
    section 6)."""
    if not assignment.get("published", True):
        return "unpublished"
    grading_type = str(assignment.get("grading_type") or "")
    points_possible = assignment.get("points_possible")
    if grading_type != "points" or not (
            _is_number(points_possible) and float(points_possible) > 0):
        return "not_points"
    submission_types = assignment.get("submission_types") or []
    is_quiz_lti = bool(assignment.get("is_quiz_lti_assignment"))
    if not submission_types or "none" in submission_types:
        return "no_submission"
    if "on_paper" in submission_types:
        return "on_paper"
    if "external_tool" in submission_types and not is_quiz_lti:
        return "external_tool"
    if assignment.get("group_category_id") and not assignment.get(
            "grade_group_students_individually"):
        return "group_assignment"
    if assignment.get("in_closed_grading_period"):
        return "closed_grading_period"
    return None


def _row_looks_eligible(row: dict, current_ids: set[str]) -> bool:
    """Cheap mirror-only pre-filter: is this row worth a live assignment GET?

    Deliberately loose -- the exact policy window math runs in
    ``_row_eligibility`` once the assignment itself is confirmed eligible, so
    a row that looks promising here but fails the window still costs zero
    Canvas calls of its own.
    """
    if str(row.get("user_id")) not in current_ids:
        return False
    if row.get("excused"):
        return False
    if str(row.get("workflow_state") or "") != "unsubmitted":
        return False
    if _is_number(row.get("score")):
        return False
    if not row.get("missing"):
        return False
    return bool(row.get("cached_due_date"))


def _row_eligibility(row: dict, current_ids: set[str], no_school_dates,
                     threshold: int, today) -> str | None:
    """Return ``None`` when the row is eligible, else the one skip reason
    (decision 4 / grading-policy-contract.md section 6)."""
    if str(row.get("user_id")) not in current_ids:
        return "not_current"
    if row.get("excused"):
        return "excused"
    if str(row.get("workflow_state") or "") != "unsubmitted":
        return "has_submission"
    if _is_number(row.get("score")):
        return "has_score"
    if not row.get("missing"):
        return "not_missing"
    due = row.get("cached_due_date")
    if not due:
        return "no_due_date"
    due_date = grading_policy._local_date(due)
    days = grading_policy.school_days_between(due_date, today, no_school_dates)
    if days < threshold:
        return "before_window"
    return None


def _discover(course_id: str) -> tuple[list[dict], dict, dict | None]:
    """Course-wide discovery: mirror pre-filter, one live GET per candidate
    assignment, then per-row eligibility. Returns
    ``(entries, skipped_counts, blocking)``; ``blocking`` is ``None`` on
    success."""
    try:
        policy = grading_policy.load_policy()
    except grading_policy.GradingPolicyFileError as exc:
        return [], {}, {"blocking_error": "grading_policy_file_invalid",
                        "message": str(exc)}
    if not policy:
        return [], {}, {"blocking_error": "no_grading_policy"}

    roster = read_service.private_roster(course_id, max_age_hours=None)
    assignments = read_service.private_assignments(course_id, max_age_hours=None)
    submissions = read_service.private_submissions(course_id, max_age_hours=None)
    if any(not isinstance(scope.get("records"), list)
           or not scope.get("last_success_at")
           or scope.get("state") not in {"current", "stale"}
           for scope in (roster, assignments, submissions)):
        attention = adapter_support.mirror_freshness_attention(
            roster, assignments, submissions)
        return [], {}, attention or {"blocking_error": "freshness_attention"}
    attention = adapter_support.mirror_freshness_attention(
        roster, assignments, submissions)
    if attention:
        return [], {}, attention

    current_ids = {
        str(student.get("id")) for student in roster["records"]
        if student.get("id") is not None
    }
    no_school_dates = grading_policy.load_no_school_dates()
    extra_time = {
        str(row.get("id")): int(row.get("days") or 0)
        for row in (config.get_extra_time(course_id) or [])
        if row.get("id") is not None
    }
    threshold_base = int(policy.get("sweep_after_school_days") or 0)
    missing_percent = float(policy.get("missing_percent") or 0)
    today = _today_local_date()

    rows_by_assignment: dict[str, list[dict]] = {}
    for row in submissions.get("records") or []:
        rows_by_assignment.setdefault(str(row.get("assignment_id")), []).append(row)

    entries: list[dict] = []
    skipped: dict[str, int] = {}

    for assignment in assignments.get("records") or []:
        if not assignment.get("published", True) or not assignment.get("due_at"):
            continue
        assignment_id = str(assignment.get("id"))
        rows = rows_by_assignment.get(assignment_id, [])
        if not any(_row_looks_eligible(row, current_ids) for row in rows):
            continue

        live, error = adapter_support.get_assignment(course_id, assignment_id)
        if error:
            return [], {}, {"blocking_error": "assignment_live_unavailable",
                            "private_diagnostic": error}
        reason = _assignment_eligibility(live)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue

        points_possible = float(live["points_possible"])
        missing_value = grading_policy.round_half_up(
            missing_percent / 100 * points_possible)
        title = str(live.get("name") or assignment.get("name") or assignment_id)

        for row in rows:
            grace_days = extra_time.get(str(row.get("user_id")), 0)
            row_reason = _row_eligibility(
                row, current_ids, no_school_dates, threshold_base + grace_days, today)
            if row_reason:
                skipped[row_reason] = skipped.get(row_reason, 0) + 1
                continue
            entries.append({
                "assignment_id": assignment_id,
                "assignment_title": title,
                "points_possible": _number(points_possible),
                "missing_value": missing_value,
                "cached_due_date": row.get("cached_due_date"),
                "user_id": str(row.get("user_id")),
                "action": "fill",
            })

    entries.sort(key=lambda entry: (entry["assignment_title"], entry["user_id"]))
    return entries, skipped, None


def _build_revert_entries(course_id: str,
                          receipt_entries: list[dict]) -> tuple[list[dict], dict]:
    entries: list[dict] = []
    skipped: dict[str, int] = {}
    for item in receipt_entries:
        if item.get("outcome") != "done":
            continue
        assignment_id = str(item.get("assignment_id"))
        user_id = str(item.get("user_id"))
        path = (
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            f"/submissions/{user_id}"
        )
        current, error = canvas_client.canvas_get(path)
        if error or not _still_swept(current, item.get("missing_value")):
            skipped["changed_since_sweep"] = skipped.get("changed_since_sweep", 0) + 1
            continue
        entries.append({
            "assignment_id": assignment_id,
            "assignment_title": item.get("assignment_title", assignment_id),
            "points_possible": item.get("points_possible"),
            "missing_value": item.get("missing_value"),
            "cached_due_date": item.get("cached_due_date"),
            "user_id": user_id,
            "action": "undo",
        })
    entries.sort(key=lambda entry: (entry["assignment_title"], entry["user_id"]))
    return entries, skipped


def _still_swept(submission: dict | None, missing_value) -> bool:
    submission = submission or {}
    return (_numbers_equal(submission.get("entered_score"), missing_value)
            and str(submission.get("late_policy_status") or "") == "missing")


def _row_changed_for_fill(submission: dict | None) -> bool:
    submission = submission or {}
    if str(submission.get("workflow_state") or "") != "unsubmitted":
        return True
    if _is_number(submission.get("score")):
        return True
    if submission.get("excused"):
        return True
    if submission.get("custom_grade_status_id") is not None:
        return True
    if str(submission.get("late_policy_status") or "") == "extended":
        return True
    return False


class MissingFillAdapter:
    kind = KIND

    def build_payload(self, prepare_request: dict) -> dict:
        if not isinstance(prepare_request, dict):
            raise ValueError("prepare request must be an object")
        return copy.deepcopy(prepare_request)

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "course_id": payload.get("course_id"),
            "mode": payload.get("mode"),
            "entries": payload.get("entries") or [],
        })

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        if len(targets) != 1:
            raise ValueError("a missing sweep requires exactly one course target")
        target = targets[0]
        course_id = str(target.get("course_id") or "")
        if not course_id or course_id != str(payload.get("course_id") or ""):
            raise ValueError("target course does not match missing sweep request")
        return [{
            "course_id": course_id,
            "target_key": self.target_key(payload, course_id),
            "idempotency_key": self.idempotency_key(payload, course_id),
        }]

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{KIND}|{course_id}|{payload.get('mode', 'sweep')}|"
            f"{self.source_digest(payload)}"
        )

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(f"{course_id}|{self.source_digest(payload)}")

    # ── Discovery entry points used by the service layer ────────────────

    def discover(self, course_id: str) -> tuple[list[dict], dict, dict | None]:
        return _discover(course_id)

    def build_revert_entries(self, course_id: str,
                             receipt_entries: list[dict]) -> tuple[list[dict], dict]:
        return _build_revert_entries(course_id, receipt_entries)

    # ── Operation Adapter protocol ───────────────────────────────────────

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        _entries, _skipped, blocking = _discover(payload["course_id"])
        if blocking:
            return blocking
        return {"discovered_at": models.now_iso()}

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        review = copy.deepcopy(payload.get("_review") or {})
        if payload.get("mode") == "undo":
            review["reverting_operation_id"] = payload.get("revert_operation_id")
        return review

    def initial_steps(self, payload: dict, baseline: dict) -> list[dict]:
        return [
            models.new_step(f"fill:{entry['assignment_id']}:{entry['user_id']}")
            for entry in payload.get("entries") or []
        ]

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        return bool(baseline.get("blocking_error"))

    @staticmethod
    def _entry_for_step(payload: dict, step_key: str) -> dict | None:
        try:
            _prefix, assignment_id, user_id = str(step_key).split(":", 2)
        except ValueError:
            return None
        return next(
            (entry for entry in payload.get("entries") or []
             if str(entry.get("assignment_id")) == assignment_id
             and str(entry.get("user_id")) == user_id), None)

    @staticmethod
    def _mark_step(context, steps, step, state, error_code=None):
        step = copy.deepcopy(step)
        step["state"] = state
        step["error_code"] = error_code
        step = context.checkpoint_step(step)
        adapter_support.replace_step(steps, step)
        return step

    def _reconcile_entry(self, path: str, entry: dict, is_undo: bool) -> str:
        current, error = canvas_client.canvas_get(path)
        if error:
            return "unknown"
        if is_undo:
            if (not _is_number(current.get("entered_score"))
                    and str(current.get("late_policy_status") or "") == "missing"):
                return "applied"
            if _still_swept(current, entry.get("missing_value")):
                return "pending"
            return "unknown"
        if _still_swept(current, entry.get("missing_value")):
            return "applied"
        if not _row_changed_for_fill(current):
            return "pending"
        return "unknown"

    @staticmethod
    def _receipt_entry(entry: dict, outcome: str) -> dict:
        return {
            "assignment_id": entry.get("assignment_id"),
            "user_id": entry.get("user_id"),
            "assignment_title": entry.get("assignment_title"),
            "points_possible": entry.get("points_possible"),
            "missing_value": entry.get("missing_value"),
            "cached_due_date": entry.get("cached_due_date"),
            "action": entry.get("action"),
            "outcome": outcome,
        }

    def execute(self, payload: dict, target: dict, baseline: dict,
               claim: dict, context) -> dict:
        steps = copy.deepcopy(target.get("steps") or [])
        receipt_entries = {
            f"{item.get('assignment_id')}:{item.get('user_id')}": copy.deepcopy(item)
            for item in (target.get("failed_items") or [])
            if isinstance(item, dict) and item.get("user_id") is not None
        }
        is_undo = payload.get("mode") == "undo"
        any_failed = False
        writes = 0

        for step in steps:
            if step.get("state") == "applied":
                continue
            entry = self._entry_for_step(payload, step.get("step_key"))
            if not entry:
                continue
            key = f"{entry['assignment_id']}:{entry['user_id']}"
            path = (
                f"/api/v1/courses/{target['course_id']}/assignments/"
                f"{entry['assignment_id']}/submissions/{entry['user_id']}"
            )

            if step.get("state") == "sent_unknown":
                verdict = self._reconcile_entry(path, entry, is_undo)
                if verdict == "applied":
                    self._mark_step(context, steps, step, "applied")
                    receipt_entries[key] = self._receipt_entry(entry, "done")
                    continue
                if verdict == "unknown":
                    code = step.get("error_code") or "missing_fill_uncertain"
                    receipt_entries[key] = self._receipt_entry(entry, code)
                    return adapter_support.build_result(
                        "sent_unknown", steps=steps, error_code=code,
                        failed_items=list(receipt_entries.values()),
                    )
                step["state"] = "pending"

            # Live read-before-write: no write has been sent yet for this row,
            # so a read failure here can safely fail just this one row and
            # move on -- unlike a failure after the PUT, it carries no risk of
            # an unresolved outbound write.
            current, read_error = canvas_client.canvas_get(path)
            if read_error:
                self._mark_step(context, steps, step, "failed", "missing_read_failed")
                receipt_entries[key] = self._receipt_entry(entry, "missing_read_failed")
                any_failed = True
                continue

            if is_undo:
                stale = not _still_swept(current, entry.get("missing_value"))
                skip_code = "changed_since_sweep"
            else:
                stale = _row_changed_for_fill(current)
                skip_code = "changed_since_preview"
            if stale:
                self._mark_step(context, steps, step, "skipped", skip_code)
                receipt_entries[key] = self._receipt_entry(entry, skip_code)
                continue

            posted_grade = "" if is_undo else str(entry["missing_value"])
            request = {"submission": {"posted_grade": posted_grade,
                                      "late_policy_status": "missing"}}
            context.before_send(
                step["step_key"],
                models.sha256_dict({"method": "PUT", "path": path, "payload": request}),
            )
            _response, error = canvas_client._canvas_send("PUT", path, request)
            if error:
                if adapter_support.is_uncertain(error):
                    code = "missing_fill_uncertain"
                    self._mark_step(context, steps, step, "sent_unknown", code)
                    receipt_entries[key] = self._receipt_entry(entry, code)
                    return adapter_support.build_result(
                        "sent_unknown", steps=steps, error_code=code,
                        private_diagnostic=str(error),
                        failed_items=list(receipt_entries.values()),
                    )
                # A definite Canvas rejection (e.g. one row landed in a
                # closed grading period after preview): count it and keep
                # going -- one bad row must never stop a course-wide sweep.
                code = "missing_fill_rejected"
                self._mark_step(context, steps, step, "failed", code)
                receipt_entries[key] = self._receipt_entry(entry, code)
                any_failed = True
                continue

            readback, read_error = canvas_client.canvas_get(path)
            if read_error:
                code = "missing_fill_uncertain"
                self._mark_step(context, steps, step, "sent_unknown", code)
                receipt_entries[key] = self._receipt_entry(entry, code)
                return adapter_support.build_result(
                    "sent_unknown", steps=steps, error_code=code,
                    private_diagnostic=str(read_error),
                    failed_items=list(receipt_entries.values()),
                )
            if is_undo:
                verified = (not _is_number(readback.get("entered_score"))
                           and str(readback.get("late_policy_status") or "") == "missing")
            else:
                verified = _still_swept(readback, entry.get("missing_value"))
            if not verified:
                code = "missing_fill_unverified"
                self._mark_step(context, steps, step, "sent_unknown", code)
                receipt_entries[key] = self._receipt_entry(entry, code)
                return adapter_support.build_result(
                    "sent_unknown", steps=steps, error_code=code,
                    failed_items=list(receipt_entries.values()),
                )
            self._mark_step(context, steps, step, "applied")
            receipt_entries[key] = self._receipt_entry(entry, "done")
            writes += 1

        if writes:
            try:
                from api.webui import mirror_service
                mirror_service.notify_course_changed(target["course_id"])
            except Exception as exc:
                operational_log.emit("mirror.notify_course_changed", "failed",
                                     error_class=type(exc))

        state = "partial" if any_failed else "applied"
        return adapter_support.build_result(
            state, steps=steps, failed_items=list(receipt_entries.values()))

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        """Process-restart recovery for a target left ``claimed``/``sent_unknown``
        mid-loop. A ``failed`` step is already a resolved, known outcome (a
        definite Canvas rejection), so only steps still ambiguous are proven
        here; see the module docstring for why continue-on-rejection can leave
        some steps resolved while the target itself never reached a terminal
        state."""
        steps = copy.deepcopy(target.get("steps") or [])
        is_undo = payload.get("mode") == "undo"
        unresolved = False
        for step in steps:
            if step.get("state") in ("applied", "skipped", "failed"):
                continue
            entry = self._entry_for_step(payload, step.get("step_key"))
            if not entry:
                unresolved = True
                continue
            path = (
                f"/api/v1/courses/{target['course_id']}/assignments/"
                f"{entry['assignment_id']}/submissions/{entry['user_id']}"
            )
            verdict = self._reconcile_entry(path, entry, is_undo)
            if verdict == "applied":
                step["state"] = "applied"
                step["error_code"] = None
            elif verdict == "pending":
                step["state"] = "pending"
            else:
                unresolved = True
        if unresolved:
            return {"state": "sent_unknown", "steps": steps}
        state = "partial" if any(step.get("state") == "failed" for step in steps) else "applied"
        return {"state": state, "steps": steps}

    def retry_selector(self, operation: dict) -> list[dict]:
        return [
            target for target in operation.get("targets", [])
            if models.is_unresolved_target_state(target.get("state", "pending"))
        ]


__all__ = ["KIND", "MissingFillAdapter"]
