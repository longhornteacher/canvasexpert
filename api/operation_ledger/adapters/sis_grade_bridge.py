"""Crash-safe SIS grade-bridge operation adapter.

Mirror-backed baseline capture, exact grade writes, live postconditions after
approval, retry decisions, and reconciliation for ``gradebook.sis_bridge`` live
here. The adapter refuses non-mirror preview baselines. Canvas access is limited
to the explicit approved push boundary and its postconditions. Assistant-facing
projections are owned by ``api.sis_grade_bridge`` and never expose this adapter's
private baselines.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from api import course_catalog, operational_log
from api.mirror import read_service
from api.platform_services import canvas_client, config

from .. import models
from . import adapter_support, differentiated_bridge


KIND = "gradebook.sis_bridge"
_SCORE_TOLERANCE = 1e-6


class _BridgeReadError(RuntimeError):
    """A local catalog/mirror read the preview needs was not current.

    Every raise site reachable from ``capture_baseline`` (through
    ``_read_mirror_snapshot``) is a local-currency problem, never a live
    Canvas failure or a real structural mismatch -- AC2 needs that
    distinguished from ``drift_detected`` at both preview and apply.
    """

    def __init__(self, message: str, *, sections: dict[str, str] | None = None):
        super().__init__(message)
        self.sections = dict(sections or {})


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
                "read_source": "mirror",
                "bridge_only": True,
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
            "read_source": "mirror",
            "bridge_only": True,
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
        """Freeze mirror-backed family facts into the private payload."""
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
                "source_setting_repairs": copy.deepcopy(
                    baseline.get("source_setting_repairs") or []
                ),
                "repair_plan": copy.deepcopy(baseline.get("repair_plan") or {}),
            })
            return frozen
        bridge_state = baseline.get("bridge_state") or {}
        frozen.update({
            "source_assignment_ids": list(baseline["source_assignment_ids"]),
            "source_titles": list(baseline["source_titles"]),
            "points_possible": baseline["points_possible"],
            "assignment_group_id": baseline["assignment_group_id"],
            "due_at": None,
            "bridge_due_at": None,
            "bridge_description": bridge_state.get("description"),
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
            "bridge_description": payload.get("bridge_description"),
            "module_id": payload.get("module_id"),
            "module_name": payload.get("module_name"),
            "read_source": payload.get("read_source", "mirror"),
            "bridge_only": bool(payload.get("bridge_only")),
            "write_origin": payload.get("write_origin", "assistant"),
            "source_setting_repairs": payload.get("source_setting_repairs") or [],
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
        if payload.get("read_source") != "mirror":
            return {
                "blocking_error": "mirror_required",
                "private_diagnostic": "bridge preview and drift checks require the local sync/mirror",
            }
        try:
            if payload.get("mode") == "reconcile":
                return self._capture_mirror_reconciliation_baseline(payload, target)
            return self._capture_mirror_baseline(payload, target)
        except _BridgeInvariantError as exc:
            result = {
                "blocking_error": exc.code,
                "private_diagnostic": exc.detail or exc.code,
            }
            if exc.fields:
                result["drift_fields"] = exc.fields
            return result
        except _BridgeReadError as exc:
            # AC2: this is the local catalog/mirror not being current, never a
            # real Canvas mismatch -- distinct from drift_detected downstream.
            return {
                "blocking_error": "catalog_not_current",
                "sections": dict(exc.sections),
                "private_diagnostic": str(exc),
            }

    def _capture_mirror_reconciliation_baseline(self, payload: dict, target: dict) -> dict:
        """Prepare bridge structure from the local catalog, never CanvasLive."""
        course_id = target["course_id"]
        snapshot = _read_mirror_snapshot(course_id)
        assignments = snapshot["assignments"]
        source_ids = [str(value) for value in payload.get("source_assignment_ids") or []]
        source_rows = []
        for source_id in source_ids:
            source = assignments.get(source_id)
            if not source:
                raise _BridgeInvariantError("source_exact_id_unverified")
            source_rows.append(source)
        if len(source_rows) < 2:
            raise _BridgeInvariantError("two_source_threshold_not_met")
        _validate_source_identity(payload, source_rows)
        points = [row.get("points_possible") for row in source_rows]
        if any(not _numbers_equal(points[0], value) for value in points[1:]):
            raise _BridgeInvariantError("mixed_points_possible")
        groups = [str(row.get("assignment_group_id") or "") for row in source_rows]
        if len(set(groups)) != 1:
            raise _BridgeInvariantError("mixed_assignment_groups")

        bridge_id = str(payload.get("bridge_assignment_id") or "") or None
        bridge = assignments.get(bridge_id) if bridge_id else None
        if bridge_id and bridge is None:
            raise _BridgeInvariantError("bridge_exact_id_unverified")
        first = source_rows[0]
        points = _normalized_number(first.get("points_possible"))
        if bridge is None:
            expected = {
                "name": differentiated_bridge.bridge_title(payload["family_title"]),
                "description": differentiated_bridge.bridge_description(),
                "points_possible": points,
                "assignment_group_id": str(first.get("assignment_group_id") or ""),
                "grading_type": "points", "submission_types": ["none"],
                "published": True, "only_visible_to_overrides": False,
                "omit_from_final_grade": False, "post_to_sis": True,
            }
            action = "create"
        else:
            # A local mirror can prove the exact bridge identity, but it is not
            # a reason to re-check or repair unrelated Canvas structure.
            expected = {"name": str(bridge.get("name") or payload["family_title"])}
            action = "register"
        source_setting_repairs = _mirror_source_setting_repairs(source_rows)
        bridge_state = _mirror_assignment_shape(bridge or {})
        source_shapes = [_mirror_assignment_shape(row) for row in source_rows]
        return {
            "source_assignment_ids": source_ids,
            "source_titles": [str(row.get("name") or "") for row in source_rows],
            "bridge_assignment_id": bridge_id,
            "bridge_exists": bridge is not None,
            "bridge": bridge or {}, "expected_bridge": expected,
            "bridge_state": bridge_state, "source_members": [[] for _ in source_rows],
            "source_target_evidence": [], "active_student_ids": [],
            "effective_due_dates": [], "drift_fields": [], "action": action,
            "validation_digest": models.sha256_dict({
                "source_assignment_ids": source_ids,
                "source_titles": [str(row.get("name") or "") for row in source_rows],
                "bridge_assignment_id": bridge_id,
                "bridge": bridge_state,
                "source_rows": source_shapes,
                "mirror_updated_at": snapshot["updated_at"],
            }),
            "revision": str(bridge.get("updated_at") or "") if bridge else "",
            "module_id": payload.get("module_id"),
            "module_name": payload.get("module_name"),
            "module_state": snapshot["modules"],
            "source_setting_repairs": source_setting_repairs,
            "repair_plan": {
                "source_assignment_ids": source_ids,
                "bridge_assignment_id": bridge_id,
                "action": action,
                "source_setting_repairs": source_setting_repairs,
            },
        }

    def _capture_mirror_baseline(self, payload: dict, target: dict) -> dict:
        """Freeze bridge score projection from mirrored submissions."""
        course_id = target["course_id"]
        snapshot = _read_mirror_snapshot(course_id, include_submissions=True)
        assignments = snapshot["assignments"]
        source_ids = [str(value) for value in payload.get("source_assignment_ids") or []]
        source_rows = [assignments.get(source_id) for source_id in source_ids]
        if any(row is None for row in source_rows):
            raise _BridgeInvariantError("family_link_source_missing")
        _validate_source_identity(payload, source_rows)
        bridge_id = str(payload.get("bridge_assignment_id") or "")
        bridge = assignments.get(bridge_id)
        if not bridge:
            raise _BridgeInvariantError("family_link_bridge_missing_or_renamed")

        submissions_by_assignment = snapshot["submissions"]
        active_ids = set()
        source_submissions: dict[str, list[dict]] = {}
        for source_index, source_id in enumerate(source_ids):
            rows = submissions_by_assignment.get(source_id, {})
            active_ids.update(rows)
            source_submissions[source_id] = rows
        bridge_rows = submissions_by_assignment.get(bridge_id, {})
        active_ids.update(bridge_rows)

        per_user: dict[str, list[dict]] = {user_id: [] for user_id in active_ids}
        for source_index, source_id in enumerate(source_ids):
            rows = source_submissions.get(source_id, {})
            for user_id in sorted(active_ids):
                per_user[user_id].append(
                    _source_submission_state(
                        source_index, source_id, user_id, rows.get(user_id)
                    )
                )
        bridge_by_user = {
            user_id: _bridge_submission_state(user_id, bridge_rows.get(user_id))
            for user_id in sorted(active_ids)
        }
        grade_entries: list[dict] = []
        counts = {
            "active_students": len(active_ids), "assigned_active": len(active_ids),
            "uncovered_active": 0, "overlapping_active": 0,
            "inactive_assignees": 0, "copied_scores": 0, "copied_excused": 0,
            "missing_zeroes": 0, "cleared_prior_values": 0, "already_matching": 0,
            "held": 0, "conflicting_final_values": 0, "planned_changes": 0,
        }
        for user_id in sorted(active_ids):
            final_rows = [row for row in per_user[user_id] if row["final"]]
            target_entry, conflict = _resolve_final_target(user_id, final_rows)
            if conflict:
                counts["conflicting_final_values"] += 1
                continue
            if target_entry is None:
                continue
            current = bridge_by_user[user_id]
            if _grade_matches(current, target_entry):
                counts["already_matching"] += 1
                continue
            grade_entries.append(target_entry)
            counts["planned_changes"] += 1
            counts["copied_excused" if target_entry["excused"] else "copied_scores"] += 1

        first = source_rows[0]
        bridge_state = _mirror_assignment_shape(bridge)
        baseline = {
            "source_assignment_ids": source_ids,
            "source_titles": [str(row.get("name") or "") for row in source_rows],
            "source_states": [
                {"assignment_id": str(row.get("id")), "published": row.get("published")}
                for row in source_rows
            ],
            "source_memberships": [[] for _ in source_rows],
            "source_target_evidence": [], "source_targeting_kind": "mirror",
            "active_student_ids": sorted(active_ids),
            "source_submissions": per_user,
            "bridge_submissions": bridge_by_user,
            "grade_entries": grade_entries,
            "points_possible": _normalized_number(first.get("points_possible")),
            "assignment_group_id": str(first.get("assignment_group_id") or ""),
            "due_at": None, "bridge_due_at": None,
            "bridge_state": bridge_state, "counts": counts, "warnings": [],
            "module_id": payload.get("module_id"),
            "module_name": payload.get("module_name"),
            "module_state": snapshot["modules"],
        }
        baseline["validation_digest"] = models.sha256_dict({
            "source_assignment_ids": source_ids,
            "source_titles": baseline["source_titles"],
            "source_submissions": per_user,
            "bridge_submissions": bridge_by_user,
            "grade_entries": grade_entries,
            "bridge_state": bridge_state,
            "mirror_updated_at": snapshot["updated_at"],
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
                "source_titles": list(baseline.get("source_titles") or []),
                "source_setting_repairs": [
                    {
                        "source_assignment_id": item.get("assignment_id"),
                        "fields": sorted((item.get("fields") or {}).keys()),
                    }
                    for item in baseline.get("source_setting_repairs") or []
                ],
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
            "common_due_date": None,
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
            steps = [
                models.new_step(f"repair_source:{index}")
                for index, _item in enumerate(
                    baseline.get("source_setting_repairs") or []
                )
            ]
            steps.append(models.new_step({
                "create": "create_bridge", "repair": "repair_bridge",
                "register": "register_bridge", "link": "register_bridge",
            }.get(action, "reconcile_bridge")))
            return steps
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

    def _execute_reconciliation(self, payload: dict, target: dict, baseline: dict, context) -> dict:
        if baseline.get("blocking_error"):
            return adapter_support.build_result("blocked", steps=copy.deepcopy(target.get("steps") or []), error_code=baseline["blocking_error"])
        course_id = target["course_id"]
        steps = copy.deepcopy(target.get("steps") or [])
        # Source-setting PUTs are the first mutation in the reviewed repair
        # sequence. Their exact assignment reads are also the only recovery
        # proof accepted after an uncertain transport outcome.
        for index, repair in enumerate(payload.get("source_setting_repairs") or []):
            step_key = f"repair_source:{index}"
            step = adapter_support.ensure_step(steps, step_key)
            source_id = str(repair.get("assignment_id") or "")
            fields = {
                str(key): value for key, value in (repair.get("fields") or {}).items()
                if key in {"omit_from_final_grade", "post_to_sis"}
            }
            if not source_id or not fields:
                return adapter_support.build_result(
                    "blocked", steps=steps, error_code="source_setting_repair_invalid"
                )
            if step.get("state") == "applied":
                continue
            if step.get("state") == "sent_unknown":
                actual, read_error = adapter_support.get_assignment(course_id, source_id)
                if read_error or not actual or any(actual.get(key) != value for key, value in fields.items()):
                    step["error_code"] = "source_repair_unverified"
                    step = context.checkpoint_step(step)
                    adapter_support.replace_step(steps, step)
                    return adapter_support.build_result(
                        "sent_unknown", steps=steps, error_code=step["error_code"]
                    )
                step["state"] = "applied"
                step["error_code"] = None
                step = context.checkpoint_step(step)
                adapter_support.replace_step(steps, step)
                continue
            path = f"/api/v1/courses/{course_id}/assignments/{source_id}"
            request = {"assignment": copy.deepcopy(fields)}
            marked = context.before_send(
                step_key,
                models.sha256_dict({"method": "PUT", "path": path, "payload": request}),
            )
            adapter_support.replace_step(steps, marked)
            step = marked
            _response, error = canvas_client._canvas_send("PUT", path, request)
            if error:
                step["state"] = "sent_unknown" if adapter_support.is_uncertain(error) else "blocked"
                step["error_code"] = "source_repair_uncertain" if step["state"] == "sent_unknown" else "source_repair_rejected"
                step = context.checkpoint_step(step)
                adapter_support.replace_step(steps, step)
                return adapter_support.build_result(step["state"], steps=steps, error_code=step["error_code"])
            actual, read_error = adapter_support.get_assignment(course_id, source_id)
            if read_error or not actual or any(actual.get(key) != value for key, value in fields.items()):
                step["state"] = "sent_unknown"
                step["error_code"] = "source_repair_unverified"
                step = context.checkpoint_step(step)
                adapter_support.replace_step(steps, step)
                return adapter_support.build_result("sent_unknown", steps=steps, error_code=step["error_code"])
            step["state"] = "applied"
            step["error_code"] = None
            step = context.checkpoint_step(step)
            adapter_support.replace_step(steps, step)
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
        registration = {
            "family_title": payload["family_title"], "family_key": payload["family_key"],
            "source_assignment_ids": baseline["source_assignment_ids"],
            "source_titles": baseline["source_titles"], "bridge_assignment_id": bridge_id,
            "bridge_state_digest": differentiated_bridge.structural_digest(
                _mirror_assignment_shape(bridge)
            ),
            "module_id": baseline.get("module_id"),
            "module_name": baseline.get("module_name"),
        }
        if not config.save_sis_grade_bridge_verified(course_id, registration):
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
        if key.startswith("attach_source_module:") or key.startswith("remove_bridge_module:"):
            # Module recovery belonged to the retired structure-repair path.
            # Bridge operations are now bridge-only, so never re-enter it.
            return "sent_unknown"
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
            registration = {
                "family_title": payload["family_title"],
                "family_key": payload["family_key"],
                "source_assignment_ids": baseline["source_assignment_ids"],
                "source_titles": baseline["source_titles"],
                "bridge_assignment_id": bridge_id,
                "bridge_state_digest": differentiated_bridge.structural_digest(
                    _mirror_assignment_shape(bridge)
                ),
                "module_id": baseline.get("module_id"),
                "module_name": baseline.get("module_name"),
            }
            try:
                verified = config.save_sis_grade_bridge_verified(
                    target["course_id"], registration,
                )
            except Exception:
                return {"state": "sent_unknown", "steps": steps}
            if not verified:
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


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _read_mirror_snapshot(
    course_id: str, *, include_submissions: bool = False,
) -> dict:
    """Read the local synced course model used by bridge preview."""
    read_result = course_catalog.read_catalog(course_id)
    catalog = read_result.get("catalog") if isinstance(read_result, dict) else None
    if not isinstance(catalog, dict):
        raise _BridgeReadError(
            "local course catalog is unavailable",
            sections={"assignments": "unavailable"},
        )
    assignments_scope = catalog.get("assignments") or {}
    if assignments_scope.get("state") != "current":
        raise _BridgeReadError(
            "local assignment sync is not current",
            sections={"assignments": str(assignments_scope.get("state") or "unavailable")},
        )
    records = assignments_scope.get("records")
    if not isinstance(records, dict):
        raise _BridgeReadError(
            "local assignment projection is invalid",
            sections={"assignments": "incomplete"},
        )
    assignments = {
        str(assignment_id): {
            "course_id": str(course_id), "id": str(assignment_id), **dict(row)
        }
        for assignment_id, row in records.items()
        if isinstance(row, dict)
    }
    modules_scope = catalog.get("modules") or {}
    modules = modules_scope.get("records") if isinstance(modules_scope, dict) else []
    if not isinstance(modules, list):
        modules = []
    submissions: dict[str, dict[str, dict]] = {}
    if include_submissions:
        result = read_service.private_submissions(course_id)
        if result.get("source") != "mirror" or result.get("state") != "current":
            raise _BridgeReadError(
                "local submission sync is not current",
                sections={"submissions": str(result.get("state") or "unavailable")},
            )
        for row in result.get("records") or []:
            if not isinstance(row, dict):
                continue
            assignment_id = str(row.get("assignment_id") or "")
            user_id = str(row.get("user_id") or "")
            if not assignment_id or not user_id:
                continue
            submissions.setdefault(assignment_id, {})[user_id] = row
    return {
        "assignments": assignments,
        "modules": modules,
        "submissions": submissions,
        "updated_at": str(catalog.get("updated_at") or ""),
    }


def _mirror_assignment_shape(row: dict) -> dict:
    """Return the mirror shape used for identity/drift, excluding due dates."""
    shape = differentiated_bridge.assignment_shape(row, [])
    shape.pop("due_at", None)
    return shape


def _mirror_source_setting_repairs(source_rows: list[dict]) -> list[dict]:
    """Use optional synced flags when present; never infer missing fields."""
    repairs = []
    for row in source_rows:
        fields = {}
        if "omit_from_final_grade" in row and row.get("omit_from_final_grade") is not True:
            fields["omit_from_final_grade"] = True
        if "post_to_sis" in row and row.get("post_to_sis") is not False:
            fields["post_to_sis"] = False
        if fields:
            repairs.append({"assignment_id": str(row.get("id")), "fields": fields})
    return repairs


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
    # A score already present on a tier is the source of truth for the bridge.
    # Canvas may omit or delay the separate SIS/posting marker, but that marker
    # must not make a real tier score disappear from the gradebook bridge.
    numeric_score = _is_number(submission.get("score"))
    final = excused or numeric_score
    return {
        "source_index": source_index,
        "source_assignment_id": source_id,
        "user_id": user_id,
        "workflow_state": workflow_state,
        "submitted_at": submission.get("submitted_at"),
        "graded_at": submission.get("graded_at"),
        "posted_at": submission.get("posted_at"),
        "excused": excused,
        "score": float(submission["score"]) if numeric_score else None,
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
