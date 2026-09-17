"""Quick Assignment adapter for the crash-safe ``content.quick_assignment`` kind.

Single-step assignment creation: validates form fields, checks for existing
assignments by name, creates via Canvas API, and returns the new assignment ID
and URL.  No module or rubric support — those belong to the full AssignmentForge
adapter.
"""

from .. import models
from .adapter_support import (
    as_list as _as_list,
    build_result as _build_result,
    has_outbound_marker as _has_outbound_marker,
    is_uncertain as _is_uncertain,
    normalize as _normalize,
    ordered_steps as _ordered_steps_from_order,
    prepend_step as _step,
    replace_step as _replace_local_step,
)
from api.platform_services import canvas_client, config
from api.student_text import normalize_student_text


KIND = "content.quick_assignment"


class QuickAssignmentAdapter:
    kind = KIND

    # ── Payload ──────────────────────────────────────────────────────────

    def build_payload(self, prepare_request: dict) -> dict:
        name = normalize_student_text(prepare_request.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        points = float(prepare_request.get("points") or 100)
        submission_type = str(
            prepare_request.get("submission_type") or "none"
        ).strip()
        published = bool(prepare_request.get("published"))
        payload = {
            "name": name,
            "points": points,
            "submission_type": submission_type,
            "published": published,
        }
        due_at = prepare_request.get("due_at")
        if due_at:
            payload["due_at"] = str(due_at).strip()
        assignment_group_name = prepare_request.get("assignment_group_name")
        if assignment_group_name:
            payload["assignment_group_name"] = str(assignment_group_name).strip()
        return payload

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "name": payload.get("name"),
            "points": payload.get("points"),
            "submission_type": payload.get("submission_type"),
            "published": payload.get("published"),
            "due_at": payload.get("due_at"),
            "assignment_group_name": payload.get("assignment_group_name"),
        })

    # ── Target verification ──────────────────────────────────────────────

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        active_ids = {str(course["id"]) for course in config.active_courses()}
        verified = []
        for target in targets:
            course_id = str(target.get("course_id") or "")
            if not course_id:
                raise ValueError("target missing course_id")
            if course_id not in active_ids:
                raise ValueError(
                    f"course {course_id} is not in active courses"
                )
            verified.append({
                "course_id": course_id,
                "target_key": self.target_key(payload, course_id),
                "idempotency_key": self.idempotency_key(payload, course_id),
            })
        return verified

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{KIND}|{self.source_digest(payload)}|{course_id}"
        )

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{self.source_digest(payload)}|{course_id}"
            f"|{_normalize(payload.get('name'))}"
        )

    # ── Baseline / drift ─────────────────────────────────────────────────

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        name = payload.get("name", "")
        baseline = {"existing_assignment": None}
        assignments, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments",
            params={"per_page": 100, "search_term": name},
        )
        if error:
            baseline["canvas_error"] = error
            return baseline
        for assignment in _as_list(assignments):
            if _normalize(assignment.get("name")) == _normalize(name):
                baseline["existing_assignment"] = {
                    "id": str(assignment.get("id")),
                    "name": assignment.get("name"),
                    "points_possible": assignment.get("points_possible"),
                    "published": assignment.get("published"),
                    "html_url": assignment.get("html_url"),
                }
                break
        return baseline

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        if baseline is None:
            return False
        if "canvas_error" in baseline:
            return True
        existing = baseline.get("existing_assignment")
        if existing is None:
            return False
        # An assignment with the same normalized name already exists —
        # drift is detected.  The server must decide whether to skip or block.
        return True

    # ── Review ───────────────────────────────────────────────────────────

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        course_name = course_id
        for course in config.active_courses():
            if str(course["id"]) == str(course_id):
                course_name = (
                    course.get("name")
                    or course.get("nickname")
                    or course_id
                )
                break
        existing = baseline.get("existing_assignment")
        return {
            "course_name": course_name,
            "assignment_name": payload.get("name"),
            "points": payload.get("points"),
            "submission_type": payload.get("submission_type"),
            "published": payload.get("published"),
            "due_at": payload.get("due_at"),
            "baseline_has_existing": existing is not None,
            "baseline_existing_id": existing.get("id") if existing else None,
            "baseline_existing_url": existing.get("html_url") if existing else None,
        }

    # ── Execute ──────────────────────────────────────────────────────────

    def execute(
        self, payload: dict, target: dict, baseline: dict,
        claim: dict, context,
    ) -> dict:
        course_id = target["course_id"]
        name = normalize_student_text(payload.get("name", "Untitled"))
        steps = _ordered_steps(target)
        step = _step(steps, "create_assignment")
        assignment_id = target.get("returned_object_id") or step.get(
            "returned_object_id"
        )
        assignment_url = target.get("returned_object_url") or step.get(
            "returned_object_url"
        )

        # ── Idempotency: if already applied, verify by ID ──────────────
        if step.get("state") in ("applied", "skipped") and assignment_id:
            assignment, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            )
            if not error and assignment:
                step["state"] = "skipped"
                assignment_url = (
                    assignment.get("html_url") or assignment_url
                )
                return _build_result(
                    "applied", steps=steps,
                    returned_object_id=assignment_id,
                    returned_object_url=assignment_url,
                )
            else:
                return _build_result(
                    "sent_unknown", steps=steps,
                    returned_object_id=assignment_id,
                    returned_object_url=assignment_url,
                    error_code="assignment_exact_id_unverified",
                )
        elif assignment_id:
            assignment, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            )
            if not error and assignment:
                step["state"] = "skipped"
                assignment_url = (
                    assignment.get("html_url") or assignment_url
                )
                return _build_result(
                    "applied", steps=steps,
                    returned_object_id=assignment_id,
                    returned_object_url=assignment_url,
                )
            elif step.get("outbound_started_at"):
                return _build_result(
                    "sent_unknown", steps=steps,
                    returned_object_id=assignment_id,
                    returned_object_url=assignment_url,
                    error_code="assignment_exact_id_unverified",
                )
            else:
                assignment_id = None

        # ── Build the Canvas assignment dict ──────────────────────────
        assignment_data = {
            "name": name,
            "submission_types": [payload.get("submission_type") or "none"],
            "points_possible": float(payload.get("points") or 100),
        }
        if payload.get("due_at"):
            assignment_data["due_at"] = payload["due_at"]
        if payload.get("published"):
            assignment_data["published"] = True

        # Resolve assignment group name to Canvas group ID
        ag_name = payload.get("assignment_group_name")
        if ag_name:
            ag_id = _find_assignment_group(course_id, ag_name)
            if ag_id is not None:
                assignment_data["assignment_group_id"] = ag_id

        # ── Create the assignment ─────────────────────────────────────
        if not assignment_id:
            request = {"assignment": assignment_data}
            path = f"/api/v1/courses/{course_id}/assignments"
            digest = models.sha256_dict({
                "method": "POST", "path": path, "payload": request,
            })
            step = context.before_send("create_assignment", digest)
            _replace_local_step(steps, step)
            response, error = canvas_client._canvas_send(
                "POST", path, request
            )
            if error:
                state = "sent_unknown" if _is_uncertain(error) else "failed"
                step["state"] = state
                step["error_code"] = (
                    "timeout_or_disconnect"
                    if state == "sent_unknown"
                    else "canvas_rejected"
                )
                step["private_diagnostic"] = error
                step = context.checkpoint_step(step)
                _replace_local_step(steps, step)
                return _build_result(
                    state, steps=steps,
                    error_code=step["error_code"],
                    private_diagnostic=error,
                )
            assignment_id = (
                str(response.get("id"))
                if isinstance(response, dict) and response.get("id") is not None
                else None
            )
            assignment_url = (
                response.get("html_url")
                if isinstance(response, dict)
                else None
            )
            if not assignment_id:
                step["state"] = "sent_unknown"
                step["error_code"] = "unparseable_response"
                step["private_diagnostic"] = "missing assignment id"
                step = context.checkpoint_step(step)
                _replace_local_step(steps, step)
                return _build_result(
                    "sent_unknown",
                    steps=_ordered_steps({"steps": steps}),
                    error_code="unparseable_response",
                )
            step["state"] = "applied"
            step = context.checkpoint_step(
                step,
                returned_object_id=assignment_id,
                returned_object_url=assignment_url,
            )
            _replace_local_step(steps, step)

        return _build_result(
            "applied", steps=steps,
            returned_object_id=assignment_id,
            returned_object_url=assignment_url,
        )

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        steps = _ordered_steps(target)
        step = _step(steps, "create_assignment")
        assignment_id = target.get("returned_object_id") or step.get(
            "returned_object_id"
        )
        has_marker = _has_outbound_marker(steps)

        if not assignment_id:
            name = payload.get("name", "")
            assignments, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments",
                params={"per_page": 100, "search_term": name},
            )
            if error:
                return {"state": "sent_unknown"}
            if any(
                _normalize(a.get("name")) == _normalize(name)
                for a in _as_list(assignments)
            ):
                return {"state": "sent_unknown"}
            return {
                "state": "sent_unknown" if has_marker else "pending",
            }

        assignment, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
        )
        if error:
            if "404" in str(error) and not step.get("outbound_started_at"):
                return {"state": "pending"}
            return {"state": "sent_unknown"}
        if not assignment:
            return {"state": "sent_unknown"}
        return {
            "state": "applied",
            "returned_object_id": assignment_id,
            "returned_object_url": assignment.get("html_url"),
        }

    # ── Retry / reversal ─────────────────────────────────────────────────

    def retry_selector(self, operation: dict) -> list[dict]:
        return [
            target
            for target in operation.get("targets", [])
            if models.is_unresolved_target_state(
                target.get("state", "pending")
            )
        ]


# ── Module-level helpers ─────────────────────────────────────────────────


def _find_assignment_group(course_id: str, name: str) -> int | None:
    """Resolve an assignment group name to its Canvas numeric ID, or None."""
    data, err = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/assignment_groups",
        {"per_page": 100},
    )
    if err:
        return None
    for group in data or []:
        if _normalize(group.get("name")) == _normalize(name):
            gid = group.get("id")
            return int(gid) if gid is not None else None
    return None


def _ordered_steps(target: dict) -> list[dict]:
    return _ordered_steps_from_order(target, ("create_assignment",))
