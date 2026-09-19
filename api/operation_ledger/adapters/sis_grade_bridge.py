"""Crash-safe SIS grade-bridge operation adapter.

Verified-family-link validation, private roster/submission reads, exact grade
writes, postcondition checks, retry decisions, and reconciliation for
``gradebook.sis_bridge`` live here. Family creation and linking belong to
the differentiated content operation. Assistant-facing projections are owned by
``api.sis_grade_bridge`` and never expose this adapter's private baselines.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from typing import Any

from api import operational_log
from api.platform_services import canvas_client, config

from .. import models, operations
from . import adapter_support, differentiated_bridge
from .module_placement import attach_assignment_type_module_item


KIND = "gradebook.sis_bridge"
_SCORE_TOLERANCE = 1e-6


class _BridgeReadError(RuntimeError):
    pass


class _BridgeInvariantError(ValueError):
    def __init__(self, code: str, detail: str = "", *, fields: list[str] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.fields = sorted(set(fields or []))


class SisGradeBridgeAdapter:
    kind = KIND

    def build_payload(self, prepare_request: dict) -> dict:
        if not isinstance(prepare_request, dict):
            raise ValueError("prepare request must be an object")
        course_id = _required_text(prepare_request.get("course_id"), "course_id")
        family_title = _required_text(
            prepare_request.get("family_title"), "family_title"
        )
        # ``registration`` is the private persisted family-link shape retained
        # for pilot storage compatibility; the product boundary calls it a link.
        registration = prepare_request.get("registration")
        discovered = prepare_request.get("discovered_family")
        if not registration and isinstance(discovered, dict):
            source_ids = [_required_text(v, "source_assignment_id") for v in discovered.get("source_assignment_ids") or []]
            if len(source_ids) < 2:
                raise ValueError("discovered family has fewer than two source assignments")
            return {
                "mode": "reconcile", "course_id": course_id,
                "family_title": family_title,
                "family_key": _required_text(discovered.get("family_key"), "family_key"),
                "source_assignment_ids": source_ids,
                "source_titles": [str(v or "").strip() for v in discovered.get("source_titles") or []],
                "bridge_assignment_id": str(discovered.get("bridge_assignment_id") or "").strip() or None,
                "module_id": str(discovered.get("module_id") or "").strip() or None,
                "module_name": str(discovered.get("module_name") or "").strip() or None,
                "write_origin": "assistant",
            }
        if not registration:
            raise ValueError(
                "Differentiated family needs a verified family link; run reconciliation first."
            )
        payload = {
            "course_id": course_id,
            "family_title": family_title,
            # Private payload terminology mirrors the persisted pilot record.
            # Private payload compatibility flag; the product surface calls
            # this state a verified family link.
            "registered": True,
            "source_assignment_ids": [],
            "source_titles": [],
            "bridge_assignment_id": None,
            # Persisted pilot payload key retained for compatibility; product
            # language calls this the family-link digest.
            "registered_bridge_digest": None,
            "module_id": None,
            "module_name": None,
            "write_origin": (
                "routine" if prepare_request.get("write_origin") == "routine"
                else "assistant"
            ),
        }
        if registration:
            if not isinstance(registration, dict):
                raise ValueError("registration must be an object")
            if str(registration.get("family_title") or "").strip() != family_title:
                raise ValueError("family link title does not match request")
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
                "module_id": str(registration.get("module_id") or "").strip() or None,
                "module_name": str(registration.get("module_name") or "").strip() or None,
            })
            if len(payload["source_assignment_ids"]) < 2:
                raise ValueError("verified family link has fewer than two source assignments")
            if len(payload["source_assignment_ids"]) != len(payload["source_titles"]):
                raise ValueError("linked source IDs and titles do not align")
        return payload

    def freeze_payload(self, payload: dict, baseline: dict) -> dict:
        """Freeze registered-family live facts into the private payload."""
        if baseline.get("blocking_error"):
            raise ValueError(str(baseline["blocking_error"]))
        frozen = copy.deepcopy(payload)
        if payload.get("mode") == "reconcile":
            frozen.update({
                "source_assignment_ids": list(baseline["source_assignment_ids"]),
                "source_titles": list(baseline["source_titles"]),
                "bridge_assignment_id": baseline.get("bridge_assignment_id"),
                "expected_bridge": copy.deepcopy(baseline["expected_bridge"]),
                "repair_fields": list(baseline.get("drift_fields") or []),
                "action": baseline.get("action"),
                "module_id": baseline.get("module_id"),
                "module_name": baseline.get("module_name"),
                "module_state": copy.deepcopy(baseline.get("module_state") or {}),
            })
            return frozen
        frozen.update({
            "source_assignment_ids": list(baseline["source_assignment_ids"]),
            "source_titles": list(baseline["source_titles"]),
            "points_possible": baseline["points_possible"],
            "assignment_group_id": baseline["assignment_group_id"],
            "due_at": baseline["due_at"],
            "bridge_due_at": baseline["bridge_state"]["due_at"],
            "bridge_description": baseline["bridge_state"]["description"],
            "write_origin": payload.get("write_origin", "assistant"),
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
            "module_id": payload.get("module_id"),
            "module_name": payload.get("module_name"),
            "write_origin": payload.get("write_origin", "assistant"),
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
        if payload.get("mode") == "reconcile":
            try:
                return self._capture_reconciliation_baseline(payload, target)
            except _BridgeInvariantError as exc:
                result = {"blocking_error": exc.code, "private_diagnostic": exc.detail or exc.code}
                if exc.fields:
                    result["drift_fields"] = exc.fields
                return result
            except _BridgeReadError as exc:
                return {"blocking_error": "canvas_read_failed", "private_diagnostic": str(exc)}
        try:
            return self._capture_baseline(payload, target)
        except _BridgeInvariantError as exc:
            result = {
                "blocking_error": exc.code,
                "private_diagnostic": exc.detail or exc.code,
            }
            if exc.fields:
                result["drift_fields"] = exc.fields
            return result
        except _BridgeReadError as exc:
            return {
                "blocking_error": "canvas_read_failed",
                "private_diagnostic": str(exc),
            }

    def _capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        source_rows = []
        for source_id in payload.get("source_assignment_ids") or []:
            source, error = adapter_support.get_assignment(course_id, str(source_id))
            if error or source is None:
                raise _BridgeInvariantError("family_link_source_missing")
            source_rows.append(source)
        bridge_id = str(payload.get("bridge_assignment_id") or "")
        bridge_row, bridge_error = adapter_support.get_assignment(course_id, bridge_id)
        if bridge_error or bridge_row is None:
            raise _BridgeInvariantError("family_link_bridge_missing_or_renamed")
        _validate_source_identity(payload, source_rows)

        bridge_state = None
        family_link_bridge_drift = False
        if str(bridge_row.get("id")) != bridge_id:
            raise _BridgeInvariantError("family_link_bridge_missing_or_renamed")
        bridge_state = _read_bridge_state(course_id, bridge_id, bridge_row)
        module_state = _capture_module_state(course_id)
        if payload.get("module_id"):
            module_state = _validate_module_membership(
                module_state, payload["module_id"],
                [str(row["id"]) for row in source_rows], bridge_id,
            )
        family_link_bridge_drift = (
            _bridge_digest(bridge_state) != payload.get("registered_bridge_digest")
        )

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
            effective_due_dates[0], "linked family"
        )
        expected_family = {
            "base_title": payload["family_title"],
            "bridge_description": differentiated_bridge.bridge_description(),
            "bridge_due_at": bridge_due_at,
            "points_possible": _normalized_number(first.get("points_possible")),
            "assignment_group_id": str(first.get("assignment_group_id")),
        }
        drift_fields = differentiated_bridge.bridge_mismatch_fields(
            bridge_row, expected_family, active=True,
            overrides=(bridge_state or {}).get("overrides") or [],
        )
        if drift_fields:
            raise _BridgeInvariantError(
                "family_link_bridge_shape_drift", fields=drift_fields,
            )
        if family_link_bridge_drift:
            raise _BridgeInvariantError("family_link_bridge_drift")
        memberships_by_student: dict[str, list[int]] = {}
        all_members = set()
        for source_index, members in enumerate(source_memberships):
            for user_id in members:
                all_members.add(user_id)
                if user_id in active_ids:
                    memberships_by_student.setdefault(user_id, []).append(source_index)
        overlaps = {
            uid: indexes for uid, indexes in memberships_by_student.items()
            if len(indexes) > 1
        }

        source_submissions: dict[str, list[dict]] = {
            user_id: [] for user_id in active_ids
        }
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

            for user_id in active_ids:
                submission = by_user.get(user_id)
                source_submissions[user_id].append(
                    _source_submission_state(source_index, source_id, user_id, submission)
                )

        bridge_rows = _get_all(
            f"/api/v1/courses/{course_id}/assignments/{bridge_id}/submissions",
            {"per_page": 100},
        )
        bridge_by_user: dict[str, dict] = {}
        for submission in bridge_rows:
            user_id = str(submission.get("user_id") or "")
            if not user_id or user_id not in active_ids:
                continue
            if user_id in bridge_by_user:
                raise _BridgeInvariantError("duplicate_bridge_submission_row")
            bridge_by_user[user_id] = _bridge_submission_state(user_id, submission)
        for user_id in active_ids:
            bridge_by_user.setdefault(user_id, _bridge_submission_state(user_id, None))

        prior_writes = _prior_routine_writes(course_id, payload["family_title"], bridge_id)
        past_due = _past_due(bridge_due_at)
        grade_entries: list[dict] = []
        counts = {
            "active_students": len(active_ids),
            "assigned_active": len(memberships_by_student),
            "uncovered_active": len(active_ids - set(memberships_by_student)),
            "overlapping_active": len(overlaps),
            "inactive_assignees": len(all_members - active_ids),
            "copied_scores": 0,
            "copied_excused": 0,
            "missing_zeroes": 0,
            "cleared_prior_values": 0,
            "already_matching": 0,
            "held": 0,
            "conflicting_final_values": 0,
            "planned_changes": 0,
        }

        for user_id in sorted(active_ids):
            states = source_submissions[user_id]
            current = bridge_by_user[user_id]
            final_rows = [row for row in states if row["final"] and row["posted"]]
            hidden_final = any(row["final"] and not row["posted"] for row in states)
            if hidden_final:
                counts["held"] += 1
                continue

            target, conflict = _resolve_final_target(user_id, final_rows)
            if conflict:
                counts["conflicting_final_values"] += 1
                continue
            if target is not None:
                if _grade_matches(current, target):
                    counts["already_matching"] += 1
                else:
                    grade_entries.append(target)
                    counts["planned_changes"] += 1
                    counts["copied_excused" if target["excused"] else "copied_scores"] += 1
                continue

            submitted_ungraded = any(row["submitted_ungraded"] for row in states)
            if submitted_ungraded:
                if _bridge_is_blank(current):
                    counts["held"] += 1
                elif _current_matches_prior_write(current, prior_writes.get(user_id)):
                    grade_entries.append(_clear_entry(user_id))
                    counts["planned_changes"] += 1
                    counts["cleared_prior_values"] += 1
                else:
                    counts["held"] += 1
                continue

            if past_due:
                target = _missing_entry(user_id)
                if _grade_matches(current, target):
                    counts["already_matching"] += 1
                else:
                    grade_entries.append(target)
                    counts["planned_changes"] += 1
                    counts["missing_zeroes"] += 1
                continue

            if _bridge_is_blank(current):
                counts["already_matching"] += 1
            elif _current_matches_prior_write(current, prior_writes.get(user_id)):
                grade_entries.append(_clear_entry(user_id))
                counts["planned_changes"] += 1
                counts["cleared_prior_values"] += 1
            else:
                counts["held"] += 1

        grade_entries.sort(key=lambda row: row["user_id"])
        warnings = [
            {"code": code, "count": counts[count_key]}
            for code, count_key in (
                ("active_students_uncovered", "uncovered_active"),
                ("overlapping_membership_ignored", "overlapping_active"),
                ("inactive_assignees_ignored", "inactive_assignees"),
                ("held_source_or_bridge_rows", "held"),
                ("conflicting_final_values", "conflicting_final_values"),
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
            "source_submissions": source_submissions,
            "bridge_submissions": bridge_by_user,
            "grade_entries": grade_entries,
            "points_possible": _normalized_number(first.get("points_possible")),
            "assignment_group_id": str(first.get("assignment_group_id")),
            "due_at": effective_due_dates[0],
            "bridge_due_at": bridge_due_at,
            "bridge_state": bridge_state,
            "counts": counts,
            "warnings": warnings,
            "module_id": payload.get("module_id"),
            "module_name": payload.get("module_name"),
            "module_state": module_state,
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
            "source_submissions": source_submissions,
            "bridge_submissions": bridge_by_user,
            "grade_entries": grade_entries,
            "points_possible": baseline["points_possible"],
            "assignment_group_id": baseline["assignment_group_id"],
            "due_at": baseline["due_at"],
            "bridge_due_at": bridge_due_at,
            "counts": counts,
            "module_state": baseline.get("module_state"),
        })
        baseline["baseline_digest"] = models.sha256_dict(baseline)
        return baseline

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        if payload.get("mode") == "reconcile":
            fresh = self.capture_baseline(payload, target)
            if fresh.get("blocking_error") != baseline.get("blocking_error"):
                return True
            if fresh.get("blocking_error"):
                return True
            return fresh.get("validation_digest") != baseline.get("validation_digest")
        fresh = self.capture_baseline(payload, target)
        if fresh.get("blocking_error"):
            return True
        return fresh.get("validation_digest") != baseline.get("validation_digest")

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        if payload.get("mode") == "reconcile":
            return {
                "course_id": target["course_id"],
                "family_title": payload["family_title"],
                "status": "missing" if not baseline.get("bridge_exists") else "drifted",
                "bridge_assignment_id": baseline.get("bridge_assignment_id"),
                "repair_fields": list(baseline.get("drift_fields") or []),
                "action": baseline.get("action"),
                "source_count": len(baseline.get("source_assignment_ids") or []),
            }
        if baseline.get("blocking_error"):
            raise ValueError(str(baseline["blocking_error"]))
        return {
            "course_id": target["course_id"],
            "family_title": payload["family_title"],
            "source_count": len(baseline.get("source_assignment_ids") or []),
            "bridge_exists": bool(payload.get("registered")),
            "family_link_verified": True,
            "points_possible": baseline.get("points_possible"),
            "common_assignment_group": True,
            "common_due_date": True,
            "no_active_overlap": not bool(
                (baseline.get("counts") or {}).get("overlapping_active")
            ),
            "source_targeting_kind": baseline.get("source_targeting_kind"),
            "counts": copy.deepcopy(baseline.get("counts") or {}),
            "warnings": copy.deepcopy(baseline.get("warnings") or []),
        }

    def initial_steps(self, payload: dict, baseline: dict) -> list[dict]:
        if payload.get("mode") == "reconcile":
            action = baseline.get("action") or ("create" if not baseline.get("bridge_exists") else "register")
            return [models.new_step({"create": "create_bridge", "repair": "repair_bridge", "register": "register_bridge"}.get(action, "reconcile_bridge"))]
        keys = (
            f"copy_grade:{index}"
            for index, _entry in enumerate(baseline.get("grade_entries") or [])
        )
        return [models.new_step(key) for key in keys]

    def execute(
        self, payload: dict, target: dict, baseline: dict,
        claim: dict, context,
    ) -> dict:
        if payload.get("mode") == "reconcile":
            return self._execute_reconciliation(payload, target, baseline, context)
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
                "blocked", steps=steps, error_code="family_link_bridge_id_missing"
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

        final_assignment, _error = adapter_support.get_assignment(course_id, bridge_id)
        return adapter_support.build_result(
            "applied", steps=steps,
            returned_object_id=bridge_id,
            returned_object_url=(final_assignment or {}).get("html_url"),
        )

    def _capture_reconciliation_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        source_rows = []
        for source_id in payload.get("source_assignment_ids") or []:
            source, error = adapter_support.get_assignment(course_id, source_id)
            if error or not source:
                raise _BridgeInvariantError("source_exact_id_unverified")
            source_rows.append(source)
        if len({str(row.get("id")) for row in source_rows}) != len(source_rows):
            raise _BridgeInvariantError("duplicate_source_assignment")
        if len(source_rows) < 2:
            raise _BridgeInvariantError("two_source_threshold_not_met")
        _validate_source_identity(payload, source_rows)
        module_state = _capture_module_state(course_id)
        source_ids = [str(row.get("id")) for row in source_rows]
        bridge_hint = str(payload.get("bridge_assignment_id") or "")
        module_id, module_name = _resolve_reconciliation_module(
            module_state, source_ids, bridge_hint,
            requested_id=payload.get("module_id"),
        )
        due_dates = []
        members_by_source = []
        source_members = []
        source_target_evidence = []
        source_states = []
        for source in source_rows:
            _validate_source_shape(source)
            overrides = _get_all(
                f"/api/v1/courses/{course_id}/assignments/{source['id']}/overrides",
                {"per_page": 100},
            )
            members, override_due, evidence = _source_override_members(course_id, source, overrides)
            if not members:
                raise _BridgeInvariantError("source_missing_student_overrides")
            members_by_source.append(members)
            source_members.append(sorted(members))
            source_target_evidence.append(evidence)
            source_states.append(_source_state(source, overrides))
            due_dates.extend(override_due)
        if not due_dates or due_dates[0] is None or len(set(due_dates)) != 1:
            raise _BridgeInvariantError("mixed_effective_due_dates")
        active_rows = _get_all(
            f"/api/v1/courses/{course_id}/users",
            {"enrollment_type[]": "student", "enrollment_state[]": "active", "per_page": 100},
        )
        active_ids = {str(row.get("id")) for row in active_rows if str(row.get("id") or "").strip()}
        union = set().union(*members_by_source)
        overlap = set().union(*(members_by_source[i] & members_by_source[j] for i in range(len(members_by_source)) for j in range(i + 1, len(members_by_source)))) & active_ids
        if (union & active_ids) != active_ids:
            raise _BridgeInvariantError("source_member_coverage_incomplete")
        if overlap:
            raise _BridgeInvariantError("source_member_coverage_overlaps")
        _validate_common_source_shape(source_rows, due_dates)
        first = source_rows[0]
        bridge_due = differentiated_bridge.require_family_delivery(due_dates[0], "reconciliation")[2]
        expected = {
            "name": differentiated_bridge.bridge_title(payload["family_title"]),
            "description": differentiated_bridge.bridge_description(),
            "points_possible": _normalized_number(first.get("points_possible")),
            "assignment_group_id": str(first.get("assignment_group_id") or ""),
            "due_at": bridge_due, "grading_type": "points", "submission_types": ["none"],
            "published": True, "only_visible_to_overrides": False,
            "omit_from_final_grade": False, "post_to_sis": True,
        }
        bridge_id = str(payload.get("bridge_assignment_id") or "") or None
        bridge = None
        if bridge_id:
            bridge, error = adapter_support.get_assignment(course_id, bridge_id)
            if error or not bridge:
                raise _BridgeInvariantError("bridge_exact_id_unverified")
            overrides = _get_all(
                f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides",
                {"per_page": 100},
            )
            if overrides:
                raise _BridgeInvariantError("bridge_overrides_present")
            actual = differentiated_bridge.assignment_shape(bridge, [])
            safety_fields = [
                key for key, value in expected.items()
                if key not in {"name", "description", "due_at"}
                and not differentiated_bridge._fields_match({key: actual.get(key)}, {key: value})
            ]
            if safety_fields:
                raise _BridgeInvariantError("bridge_safety_drift", fields=safety_fields)
            if str(bridge.get("name") or "").casefold() not in {
                payload["family_title"].casefold(), expected["name"].casefold()
            }:
                raise _BridgeInvariantError("bridge_title_ambiguous")
            # Exact ``Base`` is an accepted existing bridge name. Preserve it
            # in the frozen payload and registration; only newly created
            # bridges use the server name ``Base - Bridge``.
            expected["name"] = str(bridge.get("name") or expected["name"])
        drift_fields = [field for field in ("name", "description", "due_at") if bridge and bridge.get(field) != expected[field]]
        bridge_state = differentiated_bridge.assignment_shape(bridge or {}, [])
        validation_digest = models.sha256_dict({
            "source_assignment_ids": [str(row.get("id")) for row in source_rows],
            "source_titles": [str(row.get("name") or "") for row in source_rows],
            "source_states": source_states,
            "source_members": source_members,
            "source_target_evidence": source_target_evidence,
            "active_student_ids": sorted(active_ids),
            "effective_due_dates": due_dates,
            "expected_bridge": expected,
            "bridge_state": bridge_state,
            "bridge_assignment_id": bridge_id,
            "bridge_overrides": [],
            "action": "create" if bridge is None else ("repair" if drift_fields else "register"),
            "module_state": module_state,
            "module_id": module_id,
            "module_name": module_name,
        })
        return {
            "source_assignment_ids": [str(row.get("id")) for row in source_rows],
            "source_titles": [str(row.get("name") or "") for row in source_rows],
            "bridge_assignment_id": bridge_id,
            "bridge_exists": bridge is not None,
            "bridge": bridge or {}, "expected_bridge": expected,
            "bridge_state": bridge_state,
            "source_members": source_members,
            "source_target_evidence": source_target_evidence,
            "active_student_ids": sorted(active_ids),
            "effective_due_dates": due_dates,
            "drift_fields": drift_fields,
            "action": "create" if bridge is None else ("repair" if drift_fields else "register"),
            "validation_digest": validation_digest,
            "revision": (bridge or {}).get("updated_at") or differentiated_bridge.structural_digest(
                differentiated_bridge.assignment_shape(bridge or {}, [])
            ),
            "module_id": module_id,
            "module_name": module_name,
            "module_state": module_state,
        }

    def _execute_reconciliation(self, payload: dict, target: dict, baseline: dict, context) -> dict:
        if baseline.get("blocking_error"):
            return adapter_support.build_result("blocked", steps=copy.deepcopy(target.get("steps") or []), error_code=baseline["blocking_error"])
        course_id = target["course_id"]
        steps = copy.deepcopy(target.get("steps") or [])
        action = baseline.get("action") or payload.get("action") or "create"
        step = steps[0] if steps else models.new_step({"create": "create_bridge", "repair": "repair_bridge", "register": "register_bridge"}.get(action, "reconcile_bridge"))
        expected = baseline["expected_bridge"]
        bridge_id = baseline.get("bridge_assignment_id") or step.get("returned_object_id")
        if not bridge_id:
            path = f"/api/v1/courses/{course_id}/assignments"
            request = {"assignment": copy.deepcopy(expected)}
            marked = context.before_send(
                step["step_key"],
                models.sha256_dict({"method": "POST", "path": path, "payload": request}),
            )
            adapter_support.replace_step(steps, marked)
            step = marked
            response, error = canvas_client._canvas_send("POST", path, request)
            if error or not isinstance(response, dict) or not response.get("id"):
                step["state"] = "sent_unknown" if adapter_support.is_uncertain(error) else "blocked"
                step["error_code"] = "bridge_create_uncertain" if step["state"] == "sent_unknown" else "bridge_create_rejected"
                step = context.checkpoint_step(step)
                adapter_support.replace_step(steps, step)
                return adapter_support.build_result(step["state"], steps=steps, error_code=step["error_code"])
            bridge_id = str(response["id"])
            step["state"] = "claimed"
            step["returned_object_id"] = bridge_id
            step = context.checkpoint_step(
                step,
                returned_object_id=bridge_id,
                returned_object_url=(response or {}).get("html_url"),
            )
            adapter_support.replace_step(steps, step)
        elif step.get("state") == "sent_unknown":
            # The POST returned an exact ID but the immediate verification
            # read was unavailable. Verify that ID before any possible retry;
            # never issue a second create POST.
            existing, read_error = adapter_support.get_assignment(course_id, str(bridge_id))
            if read_error or not existing:
                step["state"] = "sent_unknown"
                step["error_code"] = "bridge_reconciliation_unverified"
                step = context.checkpoint_step(step, returned_object_id=str(bridge_id))
                adapter_support.replace_step(steps, step)
                return adapter_support.build_result("sent_unknown", steps=steps, returned_object_id=str(bridge_id), error_code=step["error_code"])
        elif baseline.get("drift_fields"):
            request = {"assignment": {field: expected[field] for field in baseline["drift_fields"]}}
            path = f"/api/v1/courses/{course_id}/assignments/{bridge_id}"
            marked = context.before_send(
                step["step_key"],
                models.sha256_dict({"method": "PUT", "path": path, "payload": request}),
            )
            adapter_support.replace_step(steps, marked)
            step = marked
            _response, error = canvas_client._canvas_send("PUT", path, request)
            if error:
                step["state"] = "sent_unknown" if adapter_support.is_uncertain(error) else "blocked"
                step["error_code"] = "bridge_repair_uncertain" if step["state"] == "sent_unknown" else "bridge_repair_rejected"
                step = context.checkpoint_step(step)
                adapter_support.replace_step(steps, step)
                return adapter_support.build_result(step["state"], steps=steps, error_code=step["error_code"])
        bridge, error = adapter_support.get_assignment(course_id, bridge_id)
        bridge_overrides = _get_all(
            f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides", {"per_page": 100}
        ) if not error else []
        actual_bridge = differentiated_bridge.assignment_shape(bridge or {}, bridge_overrides)
        mismatches = [
            field for field, value in expected.items()
            if field == "name" and str(actual_bridge.get(field) or "").casefold() not in {
                payload["family_title"].casefold(), str(value).casefold()
            }
            or field != "name" and not differentiated_bridge._fields_match({field: actual_bridge.get(field)}, {field: value})
        ]
        if error or not bridge or bridge_overrides or mismatches:
            # The Canvas mutation already happened, so persist the ambiguous
            # outcome locally before returning.  In particular, a create may
            # have an exact returned ID even when the follow-up read is
            # temporarily unavailable; recovery must be able to inspect that
            # ID rather than treating the step as still claimed.
            step["state"] = "sent_unknown"
            step["error_code"] = "bridge_reconciliation_unverified"
            step["returned_object_id"] = bridge_id
            step = context.checkpoint_step(
                step,
                returned_object_id=bridge_id,
                returned_object_url=(bridge or {}).get("html_url") if bridge else None,
            )
            adapter_support.replace_step(steps, step)
            return adapter_support.build_result(
                "sent_unknown", steps=steps, returned_object_id=bridge_id,
                error_code="bridge_reconciliation_unverified",
            )
        layout_error = self._reconcile_module_layout(
            payload, baseline, steps, context, bridge_id,
        )
        if layout_error:
            return adapter_support.build_result(
                # The bridge mutation already happened, so every layout
                # failure is recoverable attention rather than a fresh block.
                "sent_unknown",
                steps=steps, returned_object_id=bridge_id, error_code=layout_error,
            )
        registration = {
            "family_title": payload["family_title"], "family_key": payload["family_key"],
            "source_assignment_ids": baseline["source_assignment_ids"],
            "source_titles": baseline["source_titles"], "bridge_assignment_id": bridge_id,
            "bridge_state_digest": differentiated_bridge.structural_digest(
                differentiated_bridge.assignment_shape(bridge, [])
            ),
            "module_id": baseline.get("module_id"),
            "module_name": baseline.get("module_name"),
        }
        config.save_sis_grade_bridge(course_id, registration)
        saved = config.get_sis_grade_bridge(course_id, payload["family_title"])
        if saved != registration:
            return adapter_support.build_result("blocked", steps=steps, returned_object_id=bridge_id, error_code="family_registration_unverified")
        step["state"] = "applied"
        step["returned_object_id"] = bridge_id
        step = context.checkpoint_step(
            step,
            returned_object_id=bridge_id,
            returned_object_url=(bridge or {}).get("html_url"),
        )
        adapter_support.replace_step(steps, step)
        return adapter_support.build_result("applied", steps=steps, returned_object_id=bridge_id, returned_object_url=bridge.get("html_url"))

    def _reconcile_module_layout(self, payload: dict, baseline: dict, steps: list[dict], context, bridge_id: str) -> str | None:
        module_id = str(baseline.get("module_id") or payload.get("module_id") or "")
        if not module_id:
            return "module_selection_required"
        module_state = _capture_module_state(payload["course_id"])
        module = next((row for row in module_state.get("modules", []) if str(row.get("id")) == module_id), None)
        if not module:
            return "module_exact_id_unverified"
        source_ids = [str(value) for value in baseline.get("source_assignment_ids") or []]
        for index, source_id in enumerate(source_ids):
            key = f"attach_source_module:{index}"
            step = adapter_support.ensure_step(steps, key)
            current_state = _capture_module_state(payload["course_id"])
            current_module = next((row for row in current_state.get("modules", []) if str(row.get("id")) == module_id), None)
            items = (current_module or {}).get("items") or []
            existing = next((item for item in items if str(item.get("content_id")) == source_id), None)
            if existing:
                step["state"] = "applied"
                step["module_id"] = module_id
                step = context.checkpoint_step(step, returned_object_id=str(existing["id"]))
                adapter_support.replace_step(steps, step)
                continue
            result = attach_assignment_type_module_item(
                course_id=payload["course_id"], content_id=source_id,
                title=baseline["source_titles"][index], module_id=module_id,
                module_name="", steps=steps, context=context,
                attach_step_key=key, returned_object_id=bridge_id,
                deterministic_failure_state="blocked",
            )
            if result.get("state") != "applied":
                return (
                    "source_module_layout_unverified"
                    if result.get("state") == "sent_unknown"
                    else result.get("error_code") or "source_module_layout_unverified"
                )
        # Remove every exact bridge item, including legacy copies in modules
        # other than the selected repair target.  Each deletion has its own
        # stable step so recovery can prove the exact item is gone.
        fresh_state = _capture_module_state(payload["course_id"])
        bridge_items = [
            (str(module_row.get("id")), str(item.get("id")))
            for module_row in fresh_state.get("modules", [])
            for item in module_row.get("items") or []
            if bridge_id and str(item.get("content_id")) == str(bridge_id)
        ]
        for item_module_id, item_id in bridge_items:
            key = f"remove_bridge_module:{item_module_id}:{item_id}"
            step = adapter_support.ensure_step(steps, key)
            if step.get("state") in {"applied", "skipped"}:
                continue
            path = f"/api/v1/courses/{payload['course_id']}/modules/{item_module_id}/items/{item_id}"
            marked = context.before_send(key, models.sha256_dict({"method": "DELETE", "path": path}))
            marked["module_id"] = item_module_id
            marked["returned_object_id"] = item_id
            adapter_support.replace_step(steps, marked)
            _response, error = canvas_client._canvas_send("DELETE", path, None)
            if error:
                marked["state"] = "sent_unknown" if adapter_support.is_uncertain(error) else "blocked"
                marked["error_code"] = "bridge_item_remove_uncertain" if marked["state"] == "sent_unknown" else "bridge_item_remove_rejected"
                marked = context.checkpoint_step(marked, returned_object_id=item_id)
                adapter_support.replace_step(steps, marked)
                return marked["error_code"]
            marked["state"] = "applied"
            marked = context.checkpoint_step(marked, returned_object_id=item_id)
            adapter_support.replace_step(steps, marked)
        try:
            _validate_module_membership(
                _capture_module_state(payload["course_id"]),
                module_id, source_ids, bridge_id,
            )
        except _BridgeInvariantError as exc:
            return exc.code
        return None

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
        if key.startswith("attach_source_module:"):
            try:
                index = int(key.rsplit(":", 1)[1])
                source_id = str(baseline["source_assignment_ids"][index])
                module_id = str(step.get("module_id") or baseline.get("module_id") or "")
                if not module_id:
                    return "sent_unknown"
                items = _get_all(
                    f"/api/v1/courses/{course_id}/modules/{module_id}/items",
                    {"per_page": 100},
                )
            except (ValueError, IndexError, KeyError, _BridgeReadError):
                return "sent_unknown"
            matches = [item for item in items if str(item.get("content_id")) == source_id]
            if len(matches) != 1:
                return "sent_unknown"
            step["returned_object_id"] = str(matches[0].get("id"))
            step["module_id"] = module_id
            return "applied"
        if key.startswith("remove_bridge_module:"):
            parts = key.split(":")
            if len(parts) != 3:
                return "sent_unknown"
            module_id, item_id = parts[1], parts[2]
            try:
                items = _get_all(
                    f"/api/v1/courses/{course_id}/modules/{module_id}/items",
                    {"per_page": 100},
                )
            except _BridgeReadError:
                return "sent_unknown"
            # A successful DELETE may have returned an uncertain transport
            # error.  Absence from a complete collection is the safe
            # postcondition; never issue a second DELETE.  An exact item still
            # present remains unresolved.
            return "sent_unknown" if any(str(item.get("id")) == item_id for item in items) else "applied"
        if key in {"create_bridge", "repair_bridge"}:
            if not bridge_id:
                return "sent_unknown"
            bridge, error = adapter_support.get_assignment(course_id, bridge_id)
            if error or not bridge:
                return "sent_unknown"
            try:
                overrides = _get_all(
                    f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides",
                    {"per_page": 100},
                )
            except _BridgeReadError:
                return "sent_unknown"
            if overrides:
                return "sent_unknown"
            expected = payload.get("expected_bridge") or {}
            actual = differentiated_bridge.assignment_shape(bridge, [])
            for field, value in expected.items():
                if field == "name":
                    if str(actual.get(field) or "").casefold() not in {
                        str(payload.get("family_title") or "").casefold(), str(value).casefold()
                    }:
                        return "sent_unknown"
                elif not differentiated_bridge._fields_match({field: actual.get(field)}, {field: value}):
                    return "sent_unknown"
            return "applied"
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
            if step.get("state") == "pending":
                # Registration is a local finalization step; recovery may
                # legitimately reach it with earlier Canvas steps unresolved.
                continue
            if step.get("state") != "sent_unknown":
                return {"state": "sent_unknown", "steps": steps}
            if self._reconcile_step(payload, target, baseline, step, steps) != "applied":
                return {"state": "sent_unknown", "steps": steps}
        if payload.get("mode") == "reconcile":
            bridge_id = _bridge_id(payload, target, steps)
            if not bridge_id:
                return {"state": "sent_unknown", "steps": steps}
            bridge, error = adapter_support.get_assignment(
                target["course_id"], bridge_id,
            )
            if error or not bridge:
                return {"state": "sent_unknown", "steps": steps}
            # Registration is durable authority.  Persist it only after a
            # fresh global source/bridge layout read proves every source is
            # present exactly once in the selected module and the bridge is
            # absent from every module.
            try:
                _validate_module_membership(
                    _capture_module_state(target["course_id"]),
                    str(baseline.get("module_id") or payload.get("module_id") or ""),
                    [str(value) for value in baseline.get("source_assignment_ids") or []],
                    bridge_id,
                )
            except (_BridgeInvariantError, _BridgeReadError):
                return {"state": "sent_unknown", "steps": steps}
            registration = {
                "family_title": payload["family_title"],
                "family_key": payload["family_key"],
                "source_assignment_ids": baseline["source_assignment_ids"],
                "source_titles": baseline["source_titles"],
                "bridge_assignment_id": bridge_id,
                "bridge_state_digest": differentiated_bridge.structural_digest(
                    differentiated_bridge.assignment_shape(bridge, [])
                ),
                "module_id": baseline.get("module_id"),
                "module_name": baseline.get("module_name"),
            }
            try:
                config.save_sis_grade_bridge(target["course_id"], registration)
                saved = config.get_sis_grade_bridge(
                    target["course_id"], payload["family_title"],
                )
            except Exception:
                return {"state": "sent_unknown", "steps": steps}
            if saved != registration:
                return {"state": "sent_unknown", "steps": steps}
        for step in steps:
            if step.get("state") in {"sent_unknown", "pending"}:
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


def _capture_module_state(course_id: str) -> dict:
    """Capture student-free exact module/item identity for drift validation."""
    modules = _get_all(f"/api/v1/courses/{course_id}/modules", {"per_page": 100})
    records = []
    for module in modules:
        module_id = str(module.get("id") or "")
        if not module_id:
            continue
        items = _get_all(
            f"/api/v1/courses/{course_id}/modules/{module_id}/items",
            {"per_page": 100},
        )
        records.append({
            "id": module_id,
            "name": str(module.get("name") or ""),
            "position": module.get("position"),
            "items": sorted([
                {
                    "id": str(item.get("id") or ""),
                    "type": str(item.get("type") or ""),
                    "title": str(item.get("title") or ""),
                    "position": item.get("position"),
                    "content_id": str(item.get("content_id") or ""),
                }
                for item in items if item.get("id") is not None
            ], key=lambda item: (item["position"] if item["position"] is not None else 10**9, item["id"])),
        })
    records.sort(key=lambda item: (item["position"] if item["position"] is not None else 10**9, item["id"]))
    return {"modules": records, "digest": models.sha256_dict(records)}


def _validate_module_membership(module_state: dict, module_id: str, source_ids: list[str], bridge_id: str) -> dict:
    module_id = str(module_id)
    module = next((row for row in module_state.get("modules", []) if row.get("id") == module_id), None)
    if not module:
        raise _BridgeInvariantError("module_exact_id_unverified")
    source_set = {str(value) for value in source_ids}
    occurrences = {source_id: [] for source_id in source_set}
    bridge_occurrences = []
    for other in module_state.get("modules", []):
        for item in other.get("items") or []:
            content_id = str(item.get("content_id") or "")
            if content_id in occurrences:
                occurrences[content_id].append((str(other.get("id")), str(item.get("id"))))
            if bridge_id and content_id == str(bridge_id):
                bridge_occurrences.append((str(other.get("id")), str(item.get("id"))))
    if any(len(found) != 1 or found[0][0] != module_id for found in occurrences.values()):
        raise _BridgeInvariantError("source_module_membership_invalid")
    if bridge_occurrences:
        raise _BridgeInvariantError("bridge_module_item_present")
    return module_state


def _resolve_reconciliation_module(module_state: dict, source_ids: list[str], bridge_id: str, *, requested_id: str | None = None) -> tuple[str, str]:
    modules = module_state.get("modules") or []
    source_set = {str(value) for value in source_ids}
    requested_module = None
    if requested_id:
        requested_module = next((row for row in modules if str(row.get("id")) == str(requested_id)), None)
        if not requested_module:
            raise _BridgeInvariantError("module_exact_id_unverified")
    source_candidates = []
    bridge_candidates = []
    for module in modules:
        contents = [str(item.get("content_id") or "") for item in module.get("items") or []]
        if any(value in source_set for value in contents):
            source_candidates.append(module)
        if bridge_id and bridge_id in contents:
            bridge_candidates.append(module)
    source_ids_by_module = {
        str(module["id"]): {value for value in (str(item.get("content_id") or "") for item in module.get("items") or []) if value in source_set}
        for module in source_candidates
    }
    # A complete source set in one module is the deterministic legacy repair
    # target even when bridge item(s) remain in another module.  Any source
    # occurrence outside that module is still a hard cross-module error.
    complete = [module for module in source_candidates
                if source_ids_by_module[str(module["id"])] == source_set]
    if len(complete) == 1:
        module = complete[0]
        if any(
            str(other.get("id")) != str(module.get("id"))
            and any(str(item.get("content_id") or "") in source_set
                     for item in other.get("items") or [])
            for other in modules
        ):
            raise _BridgeInvariantError("cross_module_source_placement")
        return str(module["id"]), str(module.get("name") or "")
    if len(complete) > 1:
        raise _BridgeInvariantError("ambiguous_module")
    if source_candidates:
        if requested_module is not None:
            if any(
                str(module.get("id")) != str(requested_module.get("id"))
                for module in source_candidates
            ):
                raise _BridgeInvariantError("cross_module_source_placement")
            return str(requested_module["id"]), str(requested_module.get("name") or "")
        raise _BridgeInvariantError("cross_module_source_placement")
    if requested_module is not None:
        return str(requested_module["id"]), str(requested_module.get("name") or "")
    if len(bridge_candidates) == 1:
        module = bridge_candidates[0]
        return str(module["id"]), str(module.get("name") or "")
    if len(bridge_candidates) > 1:
        raise _BridgeInvariantError("ambiguous_module")
    raise _BridgeInvariantError("module_selection_required")


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
        assignment, error = adapter_support.get_assignment(course_id, bridge_id)
        if error:
            raise _BridgeReadError(error)
    overrides = _get_all(
        f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides",
        {"per_page": 100},
    )
    return differentiated_bridge.assignment_shape(assignment or {}, overrides)


def _bridge_digest(state: dict) -> str:
    return differentiated_bridge.structural_digest(state)


def _source_submission_state(
    source_index: int, source_id: str, user_id: str, submission: dict | None,
) -> dict:
    if not submission:
        return {
            "source_index": source_index,
            "source_assignment_id": source_id,
            "user_id": user_id,
            "workflow_state": "absent",
            "submitted_at": None,
            "posted_at": None,
            "excused": False,
            "score": None,
            "final": False,
            "posted": False,
            "submitted_ungraded": False,
        }
    workflow_state = str(submission.get("workflow_state") or "").strip()
    excused = submission.get("excused") is True
    numeric_final = workflow_state == "graded" and _is_number(submission.get("score"))
    final = excused or numeric_final
    if final and "posted_at" not in submission:
        raise _BridgeInvariantError("source_posting_state_missing")
    return {
        "source_index": source_index,
        "source_assignment_id": source_id,
        "user_id": user_id,
        "workflow_state": workflow_state,
        "submitted_at": submission.get("submitted_at"),
        "graded_at": submission.get("graded_at"),
        "posted_at": submission.get("posted_at"),
        "excused": excused,
        "score": float(submission["score"]) if numeric_final else None,
        "final": final,
        "posted": final and bool(submission.get("posted_at")),
        "submitted_ungraded": (
            not final and (
                workflow_state in {"submitted", "pending_review"}
                or bool(submission.get("submitted_at"))
            )
        ),
    }


def _bridge_submission_state(user_id: str, submission: dict | None) -> dict:
    row = submission or {}
    return {
        "user_id": user_id,
        "score": float(row["score"]) if _is_number(row.get("score")) else None,
        "grade": row.get("grade"),
        "excused": row.get("excused") is True,
        "late_policy_status": _late_status(row),
    }


def _resolve_final_target(
    user_id: str, final_rows: list[dict],
) -> tuple[dict | None, bool]:
    if not final_rows:
        return None, False
    first = final_rows[0]
    for row in final_rows[1:]:
        if bool(row["excused"]) != bool(first["excused"]):
            return None, True
        if not row["excused"] and not _numbers_equal(row["score"], first["score"]):
            return None, True
    if first["excused"]:
        return {
            "user_id": user_id,
            "action": "excuse",
            "excused": True,
            "score": None,
            "late_policy_status": None,
        }, False
    return {
        "user_id": user_id,
        "action": "score",
        "excused": False,
        "score": _normalized_number(first["score"]),
        "late_policy_status": None,
    }, False


def _missing_entry(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "action": "missing",
        "excused": False,
        "score": 0,
        "late_policy_status": "missing",
    }


def _clear_entry(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "action": "clear",
        "excused": False,
        "score": None,
        "late_policy_status": None,
    }


def _past_due(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        raise _BridgeInvariantError("bridge_due_at_missing")
    try:
        due = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _BridgeInvariantError("bridge_due_at_invalid") from exc
    if due.tzinfo is None:
        raise _BridgeInvariantError("bridge_due_at_invalid")
    return datetime.now(timezone.utc) > due.astimezone(timezone.utc)


def _prior_routine_writes(
    course_id: str, family_title: str, bridge_id: str,
) -> dict[str, dict]:
    latest: dict[str, tuple[str, dict]] = {}
    for operation in operations.list_operations():
        if operation.get("kind") != KIND:
            continue
        if (operation.get("source_ref") or {}).get("type") != "sis_grade_bridge_routine":
            continue
        payload = operation.get("normalized_payload") or {}
        if (
            str(payload.get("course_id") or "") != course_id
            or str(payload.get("family_title") or "") != family_title
            or str(payload.get("bridge_assignment_id") or "") != bridge_id
        ):
            continue
        stamp = str(operation.get("updated_at") or operation.get("created_at") or "")
        for target in operation.get("targets") or []:
            baseline = target.get("baseline") or {}
            entries = baseline.get("grade_entries") or []
            for step in target.get("steps") or []:
                if step.get("state") != "applied":
                    continue
                key = str(step.get("step_key") or "")
                if not key.startswith("copy_grade:"):
                    continue
                try:
                    entry = entries[int(key.split(":", 1)[1])]
                except (IndexError, TypeError, ValueError):
                    continue
                user_id = str(entry.get("user_id") or "")
                if user_id and (user_id not in latest or stamp > latest[user_id][0]):
                    latest[user_id] = (stamp, copy.deepcopy(entry))
    return {user_id: row for user_id, (_stamp, row) in latest.items()}


def _current_matches_prior_write(current: dict, prior: dict | None) -> bool:
    if not prior or prior.get("action") == "clear":
        return False
    if not _grade_matches(current, prior):
        return False
    return _late_status(current) == _late_status(prior)


def _bridge_is_blank(submission: dict) -> bool:
    return (
        submission.get("excused") is not True
        and not _is_number(submission.get("score"))
        and str(submission.get("grade") or "").strip() == ""
        and _late_status(submission) is None
    )


def _grade_request(entry: dict) -> dict:
    if entry.get("action") == "clear":
        return {
            "posted_grade": "", "excuse": False,
            "late_policy_status": "none",
        }
    request = {"excuse": True} if entry.get("excused") else {
        "posted_grade": str(entry.get("score")),
        "excuse": False,
        "late_policy_status": entry.get("late_policy_status") or "none",
    }
    return request


def _grade_matches(submission: dict, entry: dict) -> bool:
    if entry.get("action") == "clear":
        return _bridge_is_blank(submission)
    if entry.get("excused"):
        if submission.get("excused") is not True:
            return False
    elif not _numbers_equal(submission.get("score"), entry.get("score")):
        return False
    expected_status = _late_status(entry)
    if _late_status(submission) != expected_status:
        return False
    return True


def _late_status(submission: dict) -> str | None:
    value = str(submission.get("late_policy_status") or "").strip()
    return None if value in {"", "none"} else value


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
