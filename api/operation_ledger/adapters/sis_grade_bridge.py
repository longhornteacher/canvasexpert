"""Crash-safe SIS grade-bridge operation adapter.

Registered-family validation, private roster/submission reads, exact grade
writes, postcondition checks, retry decisions, and reconciliation for
``gradebook.sis_bridge`` live here. Family creation and registration belong to
the differentiated content operation. Assistant-facing projections are owned by
``api.sis_grade_bridge`` and never expose this adapter's private baselines.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from api import operational_log
from api.platform_services import canvas_client, config

from .. import models
from . import adapter_support, differentiated_bridge


KIND = "gradebook.sis_bridge"
_SCORE_TOLERANCE = 1e-6


class _BridgeReadError(RuntimeError):
    pass


class _BridgeInvariantError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class SisGradeBridgeAdapter:
    kind = KIND

    def build_payload(self, prepare_request: dict) -> dict:
        if not isinstance(prepare_request, dict):
            raise ValueError("prepare request must be an object")
        course_id = _required_text(prepare_request.get("course_id"), "course_id")
        family_title = _required_text(
            prepare_request.get("family_title"), "family_title"
        )
        registration = prepare_request.get("registration")
        if not registration:
            raise ValueError(
                "Differentiated family is not registered; create it through "
                "AssignmentForge or QuizForge first."
            )
        payload = {
            "course_id": course_id,
            "family_title": family_title,
            "registered": True,
            "source_assignment_ids": [],
            "source_titles": [],
            "bridge_assignment_id": None,
            "registered_bridge_digest": None,
        }
        if registration:
            if not isinstance(registration, dict):
                raise ValueError("registration must be an object")
            if str(registration.get("family_title") or "").strip() != family_title:
                raise ValueError("registered family title does not match request")
            payload.update({
                "source_assignment_ids": [
                    _required_text(value, "source_assignment_id")
                    for value in (registration.get("source_assignment_ids") or [])
                ],
                "source_titles": [
                    _required_text(value, "source_title")
                    for value in (registration.get("source_titles") or [])
                ],
                "bridge_assignment_id": _required_text(
                    registration.get("bridge_assignment_id"),
                    "bridge_assignment_id",
                ),
                "registered_bridge_digest": _required_text(
                    registration.get("bridge_state_digest"),
                    "bridge_state_digest",
                ),
            })
            if len(payload["source_assignment_ids"]) < 2:
                raise ValueError("registered bridge has fewer than two sources")
            if len(payload["source_assignment_ids"]) != len(payload["source_titles"]):
                raise ValueError("registered source IDs and titles do not align")
        return payload

    def freeze_payload(self, payload: dict, baseline: dict) -> dict:
        """Freeze registered-family live facts into the private payload."""
        if baseline.get("blocking_error"):
            raise ValueError(str(baseline["blocking_error"]))
        frozen = copy.deepcopy(payload)
        frozen.update({
            "source_assignment_ids": list(baseline["source_assignment_ids"]),
            "source_titles": list(baseline["source_titles"]),
            "points_possible": baseline["points_possible"],
            "assignment_group_id": baseline["assignment_group_id"],
            "due_at": baseline["due_at"],
            "bridge_due_at": baseline["bridge_state"]["due_at"],
            "bridge_description": baseline["bridge_state"]["description"],
        })
        return frozen

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "course_id": payload.get("course_id"),
            "family_title": payload.get("family_title"),
            "registered": bool(payload.get("registered")),
            "source_assignment_ids": payload.get("source_assignment_ids") or [],
            "source_titles": payload.get("source_titles") or [],
            "bridge_assignment_id": payload.get("bridge_assignment_id"),
            "registered_bridge_digest": payload.get("registered_bridge_digest"),
            "points_possible": payload.get("points_possible"),
            "assignment_group_id": payload.get("assignment_group_id"),
            "due_at": payload.get("due_at"),
            "bridge_due_at": payload.get("bridge_due_at"),
            "bridge_description": payload.get("bridge_description"),
        })

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        active_ids = {str(course.get("id") or "").strip()
                      for course in config.active_courses()}
        verified = []
        for target in targets:
            course_id = str(target.get("course_id") or "").strip()
            if not course_id or course_id != payload.get("course_id"):
                raise ValueError("target course does not match bridge request")
            if course_id not in active_ids:
                raise ValueError("course is not in Current courses")
            verified.append({
                "course_id": course_id,
                "target_key": self.target_key(payload, course_id),
                "idempotency_key": self.idempotency_key(payload, course_id),
            })
        if len(verified) != 1:
            raise ValueError("a bridge operation requires exactly one course target")
        return verified

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{KIND}|{course_id}|{payload.get('family_title', '')}"
        )

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{course_id}|{self.source_digest(payload)}"
        )

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        try:
            return self._capture_baseline(payload, target)
        except _BridgeInvariantError as exc:
            return {
                "blocking_error": exc.code,
                "private_diagnostic": exc.detail or exc.code,
            }
        except _BridgeReadError as exc:
            return {
                "blocking_error": "canvas_read_failed",
                "private_diagnostic": str(exc),
            }

    def _capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        source_rows = []
        for source_id in payload.get("source_assignment_ids") or []:
            source, error = _get_assignment(course_id, str(source_id))
            if error or source is None:
                raise _BridgeInvariantError("registered_source_missing")
            source_rows.append(source)
        bridge_id = str(payload.get("bridge_assignment_id") or "")
        bridge_row, bridge_error = _get_assignment(course_id, bridge_id)
        if bridge_error or bridge_row is None:
            raise _BridgeInvariantError("registered_bridge_missing_or_renamed")
        _validate_source_identity(payload, source_rows)

        bridge_state = None
        if str(bridge_row.get("id")) != bridge_id:
            raise _BridgeInvariantError("registered_bridge_missing_or_renamed")
        bridge_state = _read_bridge_state(course_id, bridge_id, bridge_row)
        if _bridge_digest(bridge_state) != payload.get("registered_bridge_digest"):
            raise _BridgeInvariantError("registered_bridge_drift")

        active_users = _get_all(
            f"/api/v1/courses/{course_id}/users",
            {
                "enrollment_type[]": "student",
                "enrollment_state[]": "active",
                "per_page": 100,
            },
        )
        active_ids = {str(row.get("id")) for row in active_users
                      if row.get("id") is not None}

        source_states = []
        source_memberships: list[list[str]] = []
        source_target_evidence: list[dict] = []
        source_targeting_kinds: set[str] = set()
        effective_due_dates: list[str | None] = []
        for source in source_rows:
            _validate_source_shape(source)
            source_id = str(source["id"])
            overrides = _get_all(
                f"/api/v1/courses/{course_id}/assignments/{source_id}/overrides",
                {"per_page": 100},
            )
            members, due_dates, target_evidence = _source_override_members(
                course_id, source, overrides
            )
            source_memberships.append(sorted(members))
            source_target_evidence.append(target_evidence)
            source_targeting_kinds.add(target_evidence["kind"])
            effective_due_dates.extend(due_dates)
            source_states.append(_source_state(source, overrides))

        if len(source_targeting_kinds) != 1:
            raise _BridgeInvariantError("mixed_family_targeting")

        _validate_common_source_shape(source_rows, effective_due_dates)
        first = source_rows[0]
        _due, _module, bridge_due_at = differentiated_bridge.require_family_delivery(
            effective_due_dates[0], "registered family"
        )
        expected_family = {
            "base_title": payload["family_title"],
            "bridge_description": differentiated_bridge.bridge_description(),
            "bridge_due_at": bridge_due_at,
            "points_possible": _normalized_number(first.get("points_possible")),
            "assignment_group_id": str(first.get("assignment_group_id")),
        }
        if bridge_row is None or not differentiated_bridge.bridge_matches(
            bridge_row, expected_family, active=True
        ):
            raise _BridgeInvariantError("registered_bridge_shape_drift")
        if (bridge_state or {}).get("overrides"):
            raise _BridgeInvariantError("registered_bridge_has_overrides")
        memberships_by_student: dict[str, list[int]] = {}
        all_members = set()
        for source_index, members in enumerate(source_memberships):
            for user_id in members:
                all_members.add(user_id)
                if user_id in active_ids:
                    memberships_by_student.setdefault(user_id, []).append(source_index)
        overlaps = {uid: indexes for uid, indexes in memberships_by_student.items()
                    if len(indexes) > 1}
        if overlaps:
            raise _BridgeInvariantError(
                "overlapping_active_memberships",
                f"active_overlap_count={len(overlaps)}",
            )

        grade_entries = []
        pending_review = 0
        unsubmitted = 0
        skipped_other = 0
        submission_counts = 0
        for source_index, source in enumerate(source_rows):
            source_id = str(source["id"])
            submissions = _get_all(
                f"/api/v1/courses/{course_id}/assignments/{source_id}/submissions",
                {"per_page": 100},
            )
            by_user: dict[str, dict] = {}
            for submission in submissions:
                user_id = str(submission.get("user_id") or "")
                if not user_id:
                    continue
                if user_id in by_user:
                    raise _BridgeInvariantError("duplicate_submission_row")
                by_user[user_id] = submission

            for user_id in source_memberships[source_index]:
                if user_id not in active_ids:
                    continue
                submission_counts += 1
                submission = by_user.get(user_id)
                if not submission:
                    unsubmitted += 1
                    continue
                workflow_state = str(submission.get("workflow_state") or "")
                if workflow_state == "pending_review":
                    pending_review += 1
                    continue
                if workflow_state == "unsubmitted":
                    unsubmitted += 1
                    continue
                if bool(submission.get("excused")):
                    grade_entries.append({
                        "source_index": source_index,
                        "source_assignment_id": source_id,
                        "user_id": user_id,
                        "excused": True,
                        "score": None,
                        "late_policy_status": _late_status(submission),
                    })
                    continue
                score = submission.get("score")
                if workflow_state == "graded" and _is_number(score):
                    grade_entries.append({
                        "source_index": source_index,
                        "source_assignment_id": source_id,
                        "user_id": user_id,
                        "excused": False,
                        "score": float(score),
                        "late_policy_status": _late_status(submission),
                    })
                else:
                    skipped_other += 1

        grade_entries.sort(key=lambda row: (row["source_index"], row["user_id"]))
        assigned_active = len(memberships_by_student)
        uncovered_active = len(active_ids - set(memberships_by_student))
        inactive_assignees = len(all_members - active_ids)
        counts = {
            "active_students": len(active_ids),
            "assigned_active": assigned_active,
            "uncovered_active": uncovered_active,
            "overlapping_active": 0,
            "inactive_assignees": inactive_assignees,
            "eligible_final": len(grade_entries),
            "eligible_numeric": sum(not row["excused"] for row in grade_entries),
            "eligible_excused": sum(bool(row["excused"]) for row in grade_entries),
            "pending_review": pending_review,
            "unsubmitted": unsubmitted,
            "skipped_other": skipped_other,
        }
        if submission_counts != assigned_active:
            raise _BridgeInvariantError("membership_count_inconsistent")

        warnings = [
            {"code": code, "count": counts[count_key]}
            for code, count_key in (
                ("active_students_uncovered", "uncovered_active"),
                ("pending_review_skipped", "pending_review"),
                ("unsubmitted_skipped", "unsubmitted"),
                ("inactive_assignees_ignored", "inactive_assignees"),
                ("other_nonfinal_skipped", "skipped_other"),
            )
            if counts[count_key]
        ]

        baseline = {
            "source_assignment_ids": [str(row["id"]) for row in source_rows],
            "source_titles": [str(row.get("name") or "") for row in source_rows],
            "source_states": source_states,
            "source_memberships": source_memberships,
            "source_target_evidence": source_target_evidence,
            "source_targeting_kind": next(iter(source_targeting_kinds)),
            "active_student_ids": sorted(active_ids),
            "grade_entries": grade_entries,
            "points_possible": _normalized_number(first.get("points_possible")),
            "assignment_group_id": str(first.get("assignment_group_id")),
            "due_at": effective_due_dates[0],
            "bridge_state": bridge_state,
            "counts": counts,
            "warnings": warnings,
        }
        baseline["validation_digest"] = models.sha256_dict({
            "source_assignment_ids": baseline["source_assignment_ids"],
            "source_titles": baseline["source_titles"],
            "source_states": [
                {
                    key: value for key, value in state.items()
                    if key not in {"omit_from_final_grade", "post_to_sis"}
                }
                for state in source_states
            ],
            "source_memberships": source_memberships,
            "source_target_evidence": source_target_evidence,
            "source_targeting_kind": baseline["source_targeting_kind"],
            "active_student_ids": baseline["active_student_ids"],
            "grade_entries": grade_entries,
            "points_possible": baseline["points_possible"],
            "assignment_group_id": baseline["assignment_group_id"],
            "due_at": baseline["due_at"],
            "counts": counts,
        })
        baseline["baseline_digest"] = models.sha256_dict(baseline)
        return baseline

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        fresh = self.capture_baseline(payload, target)
        if fresh.get("blocking_error"):
            return True
        return fresh.get("validation_digest") != baseline.get("validation_digest")

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        if baseline.get("blocking_error"):
            raise ValueError(str(baseline["blocking_error"]))
        return {
            "course_id": target["course_id"],
            "family_title": payload["family_title"],
            "source_count": len(baseline.get("source_assignment_ids") or []),
            "bridge_exists": bool(payload.get("registered")),
            "points_possible": baseline.get("points_possible"),
            "common_assignment_group": True,
            "common_due_date": True,
            "no_active_overlap": True,
            "source_targeting_kind": baseline.get("source_targeting_kind"),
            "counts": copy.deepcopy(baseline.get("counts") or {}),
            "warnings": copy.deepcopy(baseline.get("warnings") or []),
        }

    def initial_steps(self, payload: dict, baseline: dict) -> list[dict]:
        keys = (
            f"copy_grade:{index}"
            for index, _entry in enumerate(baseline.get("grade_entries") or [])
        )
        return [models.new_step(key) for key in keys]

    def execute(
        self, payload: dict, target: dict, baseline: dict,
        claim: dict, context,
    ) -> dict:
        if baseline.get("blocking_error"):
            return adapter_support.build_result(
                "blocked",
                steps=copy.deepcopy(target.get("steps") or []),
                error_code=str(baseline["blocking_error"]),
                private_diagnostic=baseline.get("private_diagnostic"),
            )

        course_id = target["course_id"]
        steps = copy.deepcopy(target.get("steps") or [])
        grade_entries = baseline.get("grade_entries") or []
        bridge_id = str(payload.get("bridge_assignment_id") or "")
        if not bridge_id:
            return adapter_support.build_result(
                "blocked", steps=steps, error_code="registered_bridge_id_missing"
            )

        # A retry may enter with one or more ambiguous steps. Reconcile those
        # exact effects before deciding whether any call is safe to repeat.
        for step in steps:
            if step.get("state") != "sent_unknown":
                continue
            verdict = self._reconcile_step(
                payload, target, baseline, step, steps,
            )
            if verdict != "applied":
                return adapter_support.build_result(
                    "sent_unknown", steps=steps,
                    returned_object_id=bridge_id,
                    error_code=step.get("error_code") or "ambiguous_canvas_outcome",
                )
            step["state"] = "applied"
            step["error_code"] = None
            step = context.checkpoint_step(
                step,
                returned_object_id=step.get("returned_object_id"),
                returned_object_url=step.get("returned_object_url"),
            )
            adapter_support.replace_step(steps, step)

        grade_writes = 0
        for index, entry in enumerate(grade_entries):
            step_key = f"copy_grade:{index}"
            step = adapter_support.find_step(steps, step_key)
            if step.get("state") == "applied":
                continue
            request = {"submission": _grade_request(entry)}
            path = (
                f"/api/v1/courses/{course_id}/assignments/{bridge_id}/"
                f"submissions/{entry['user_id']}"
            )
            context.before_send(step_key, _request_digest("PUT", path, request))
            _response, error = canvas_client._canvas_send("PUT", path, request)
            if error:
                return self._stop_after_error(
                    context, steps, step, error,
                    uncertain_code="grade_write_uncertain",
                    rejection_code="grade_write_rejected",
                    returned_object_id=bridge_id,
                )
            submission, read_error = canvas_client.canvas_get(path)
            if read_error or not _grade_matches(submission or {}, entry):
                return self._stop_after_error(
                    context, steps, step, read_error or "grade postcondition mismatch",
                    uncertain_code="grade_write_unverified",
                    force_uncertain=True,
                    returned_object_id=bridge_id,
                )
            step["state"] = "applied"
            step["error_code"] = None
            step = context.checkpoint_step(step)
            adapter_support.replace_step(steps, step)
            grade_writes += 1

        if grade_writes:
            try:
                from api.webui import mirror_service
                mirror_service.notify_course_changed(course_id)
            except Exception as exc:
                operational_log.emit(
                    "mirror.notify_course_changed", "failed",
                    error_class=type(exc),
                )

        final_assignment, _error = _get_assignment(course_id, bridge_id)
        return adapter_support.build_result(
            "applied", steps=steps,
            returned_object_id=bridge_id,
            returned_object_url=(final_assignment or {}).get("html_url"),
        )

    def _stop_after_error(
        self, context, steps: list[dict], step: dict, error: str,
        *, uncertain_code: str, rejection_code: str | None = None,
        force_uncertain: bool = False, returned_object_id: str | None = None,
    ) -> dict:
        uncertain = force_uncertain or adapter_support.is_uncertain(error)
        step["state"] = "sent_unknown" if uncertain else "blocked"
        step["error_code"] = uncertain_code if uncertain else rejection_code
        step["private_diagnostic"] = str(error)
        step = context.checkpoint_step(
            step,
            returned_object_id=step.get("returned_object_id"),
            returned_object_url=step.get("returned_object_url"),
        )
        adapter_support.replace_step(steps, step)
        return adapter_support.build_result(
            step["state"], steps=steps,
            returned_object_id=returned_object_id or step.get("returned_object_id"),
            returned_object_url=step.get("returned_object_url"),
            error_code=step.get("error_code"),
            private_diagnostic=str(error),
        )

    def _reconcile_step(
        self, payload: dict, target: dict, baseline: dict,
        step: dict, steps: list[dict],
    ) -> str:
        course_id = target["course_id"]
        key = str(step.get("step_key") or "")
        bridge_id = _bridge_id(payload, target, steps)
        if key.startswith("copy_grade:"):
            if not bridge_id:
                return "sent_unknown"
            try:
                index = int(key.split(":", 1)[1])
                entry = baseline["grade_entries"][index]
            except (ValueError, IndexError, KeyError):
                return "sent_unknown"
            path = (
                f"/api/v1/courses/{course_id}/assignments/{bridge_id}/"
                f"submissions/{entry['user_id']}"
            )
            submission, error = canvas_client.canvas_get(path)
            return "applied" if not error and _grade_matches(submission or {}, entry) else "sent_unknown"
        return "sent_unknown"

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        steps = copy.deepcopy(target.get("steps") or [])
        if not adapter_support.has_outbound_marker(steps):
            return {"state": "pending"}
        for step in steps:
            if step.get("state") == "applied":
                continue
            if step.get("state") != "sent_unknown":
                return {"state": "sent_unknown", "steps": steps}
            if self._reconcile_step(payload, target, baseline, step, steps) != "applied":
                return {"state": "sent_unknown", "steps": steps}
            step["state"] = "applied"
            step["error_code"] = None
        return {
            "state": "applied",
            "steps": steps,
            "returned_object_id": _bridge_id(payload, target, steps),
        }

    def retry_selector(self, operation: dict) -> list[dict]:
        return [
            target for target in operation.get("targets", [])
            if models.is_unresolved_target_state(target.get("state", "pending"))
        ]


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _get_all(path: str, params: dict | None = None) -> list[dict]:
    rows, error, complete = canvas_client.canvas_get_all_complete(path, params or {})
    if error or not complete or not isinstance(rows, list):
        raise _BridgeReadError(str(error or "incomplete Canvas collection"))
    return rows


def _get_assignment(course_id: str, assignment_id: str) -> tuple[dict | None, str | None]:
    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    )
    if error or not isinstance(assignment, dict):
        return None, str(error or "invalid assignment response")
    return assignment, None


def _validate_source_identity(payload: dict, source_rows: list[dict]) -> None:
    frozen_ids = [str(value) for value in payload.get("source_assignment_ids") or []]
    frozen_titles = [str(value) for value in payload.get("source_titles") or []]
    if frozen_ids and [str(row.get("id")) for row in source_rows] != frozen_ids:
        raise _BridgeInvariantError("source_id_drift")
    if frozen_titles and [str(row.get("name") or "") for row in source_rows] != frozen_titles:
        raise _BridgeInvariantError("source_title_drift")
    if any(
        str(row.get("course_id") or "") != str(payload.get("course_id") or "")
        for row in source_rows
    ):
        raise _BridgeInvariantError("assignment_outside_selected_course")


def _validate_source_shape(source: dict) -> None:
    if source.get("published") is not True:
        raise _BridgeInvariantError("source_not_published")
    if source.get("grading_type") != "points":
        raise _BridgeInvariantError("source_not_point_graded")
    if source.get("only_visible_to_overrides") is not True:
        raise _BridgeInvariantError("source_not_override_only")
    if not _is_number(source.get("points_possible")):
        raise _BridgeInvariantError("source_points_invalid")
    if source.get("omit_from_final_grade") is not True:
        raise _BridgeInvariantError("source_counts_toward_final_grade")
    if source.get("post_to_sis") is not False:
        raise _BridgeInvariantError("source_sis_sync_enabled")


def _source_override_members(
    course_id: str, source: dict, overrides: list[dict]
) -> tuple[set[str], list[str | None], dict]:
    if not overrides:
        raise _BridgeInvariantError("source_missing_student_overrides")
    members: set[str] = set()
    due_dates: list[str | None] = []
    target_kind: str | None = None
    private_overrides: list[dict] = []
    for override in sorted(overrides, key=lambda row: str(row.get("id") or "")):
        student_ids = override.get("student_ids")
        has_students = isinstance(student_ids, list) and bool(student_ids)
        group_id = str(override.get("group_id") or "").strip()
        has_group = bool(group_id)
        if override.get("course_section_id") is not None:
            raise _BridgeInvariantError("section_override_not_allowed")
        if has_students and has_group:
            raise _BridgeInvariantError("mixed_override_targeting")
        if has_students:
            current_kind = "explicit_students"
            override_members = set()
            for value in student_ids:
                member_id = str(value or "").strip()
                if not member_id:
                    raise _BridgeInvariantError(
                        "source_override_student_id_invalid"
                    )
                if member_id in override_members:
                    raise _BridgeInvariantError("duplicate_source_membership")
                override_members.add(member_id)
            evidence = {
                "override_id": str(override.get("id") or ""),
                "member_ids": sorted(override_members),
            }
        elif has_group:
            current_kind = "differentiation_tag"
            override_members, evidence = _differentiation_tag_members(
                course_id, source, override, group_id
            )
        else:
            raise _BridgeInvariantError("source_override_target_invalid")
        if target_kind is not None and current_kind != target_kind:
            raise _BridgeInvariantError("mixed_source_targeting")
        target_kind = current_kind
        effective_due = override.get("due_at") or source.get("due_at")
        due_dates.append(str(effective_due) if effective_due is not None else None)
        for member_id in override_members:
            if member_id in members:
                raise _BridgeInvariantError("duplicate_source_membership")
            members.add(member_id)
        private_overrides.append(evidence)
    return members, due_dates, {
        "kind": target_kind,
        "overrides": private_overrides,
    }


def _differentiation_tag_members(
    course_id: str, source: dict, override: dict, group_id: str
) -> tuple[set[str], dict]:
    if source.get("group_category_id") not in (None, ""):
        raise _BridgeInvariantError("group_assignment_not_allowed")

    group, error = canvas_client.canvas_get(f"/api/v1/groups/{group_id}")
    if error or not isinstance(group, dict):
        raise _BridgeReadError(str(error or "invalid group response"))
    if str(group.get("course_id") or "") != course_id:
        raise _BridgeInvariantError("tag_group_course_mismatch")
    if group.get("non_collaborative") is not True:
        raise _BridgeInvariantError("collaborative_group_not_allowed")

    category_id = str(group.get("group_category_id") or "").strip()
    if not category_id:
        raise _BridgeInvariantError("tag_group_category_missing")
    category, error = canvas_client.canvas_get(
        f"/api/v1/group_categories/{category_id}"
    )
    if error or not isinstance(category, dict):
        raise _BridgeReadError(str(error or "invalid group category response"))
    if str(category.get("course_id") or "") != course_id:
        raise _BridgeInvariantError("tag_category_course_mismatch")

    rows, error, complete = canvas_client.canvas_get_all_complete(
        f"/api/v1/groups/{group_id}/users", {"per_page": 100}
    )
    if error or not complete or not isinstance(rows, list):
        raise _BridgeInvariantError("tag_membership_incomplete")
    member_ids = {
        str(row.get("id") or "").strip()
        for row in rows
        if str(row.get("id") or "").strip()
    }
    if len(member_ids) != len(rows):
        raise _BridgeInvariantError("tag_membership_invalid")
    return member_ids, {
        "override_id": str(override.get("id") or ""),
        "group_id": group_id,
        "group_category_id": category_id,
        "group_course_id": str(group.get("course_id")),
        "category_course_id": str(category.get("course_id")),
        "non_collaborative": True,
        "member_ids": sorted(member_ids),
    }


def _validate_common_source_shape(
    source_rows: list[dict], effective_due_dates: list[str | None]
) -> None:
    points = [_normalized_number(row.get("points_possible")) for row in source_rows]
    if any(not _numbers_equal(points[0], value) for value in points[1:]):
        raise _BridgeInvariantError("mixed_points_possible")
    groups = [str(row.get("assignment_group_id")) for row in source_rows]
    if len(set(groups)) != 1:
        raise _BridgeInvariantError("mixed_assignment_groups")
    if (not effective_due_dates or effective_due_dates[0] is None
            or len(set(effective_due_dates)) != 1):
        raise _BridgeInvariantError("mixed_effective_due_dates")


def _source_state(source: dict, overrides: list[dict]) -> dict:
    return {
        "id": str(source.get("id")),
        "course_id": str(source.get("course_id")),
        "name": str(source.get("name") or ""),
        "published": source.get("published") is True,
        "grading_type": source.get("grading_type"),
        "points_possible": _normalized_number(source.get("points_possible")),
        "assignment_group_id": str(source.get("assignment_group_id")),
        "due_at": source.get("due_at"),
        "only_visible_to_overrides": source.get("only_visible_to_overrides") is True,
        "omit_from_final_grade": source.get("omit_from_final_grade") is True,
        "post_to_sis": source.get("post_to_sis") is True,
        "overrides": [
            {
                "id": str(row.get("id") or ""),
                "student_ids": sorted(str(value) for value in (row.get("student_ids") or [])),
                "due_at": row.get("due_at"),
            }
            for row in sorted(overrides, key=lambda item: str(item.get("id") or ""))
        ],
    }


def _read_bridge_state(course_id: str, bridge_id: str, assignment: dict | None = None) -> dict:
    if assignment is None:
        assignment, error = _get_assignment(course_id, bridge_id)
        if error:
            raise _BridgeReadError(error)
    overrides = _get_all(
        f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides",
        {"per_page": 100},
    )
    return differentiated_bridge.assignment_shape(assignment or {}, overrides)


def _bridge_digest(state: dict) -> str:
    return differentiated_bridge.structural_digest(state)


def _grade_request(entry: dict) -> dict:
    request = {"excuse": True} if entry.get("excused") else {
        "posted_grade": str(entry.get("score"))
    }
    if entry.get("late_policy_status"):
        request["late_policy_status"] = entry["late_policy_status"]
    return request


def _grade_matches(submission: dict, entry: dict) -> bool:
    if entry.get("excused"):
        if submission.get("excused") is not True:
            return False
    elif not _numbers_equal(submission.get("score"), entry.get("score")):
        return False
    expected_status = entry.get("late_policy_status")
    if expected_status and submission.get("late_policy_status") != expected_status:
        return False
    return True


def _late_status(submission: dict) -> str | None:
    value = str(submission.get("late_policy_status") or "").strip()
    return value or None


def _bridge_id(payload: dict, target: dict, steps: list[dict]) -> str | None:
    if payload.get("bridge_assignment_id"):
        return str(payload["bridge_assignment_id"])
    create_step = next(
        (step for step in steps if step.get("step_key") == "create_bridge"),
        None,
    )
    value = (create_step or {}).get("returned_object_id") or target.get("returned_object_id")
    return str(value) if value is not None else None


def _request_digest(method: str, path: str, payload: dict) -> str:
    return models.sha256_dict({"method": method, "path": path, "payload": payload})


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _normalized_number(value: Any) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _numbers_equal(left: Any, right: Any) -> bool:
    if not _is_number(left) or not _is_number(right):
        return False
    return math.isclose(
        float(left), float(right), rel_tol=0.0, abs_tol=_SCORE_TOLERANCE
    )


__all__ = ["KIND", "SisGradeBridgeAdapter"]
