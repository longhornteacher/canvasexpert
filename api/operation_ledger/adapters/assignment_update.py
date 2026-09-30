"""Adapter for ``content.assignment_update``.

Patch the two facets of an existing Canvas assignment that block a live
week -- publish state and the three schedule dates -- addressed by the
Canvas assignment id the caller supplies. This is a sibling to
``content.assignment`` (the crash-safe create path in ``assignment.py``),
not a variant of it: see the locked decisions in
``docs/handoffs/assignment-update-write-path.md``.

- No title matching. The caller passes ``assignment_id``; this adapter GETs
  that exact assignment. Title matching stays a create-path concern.
- Patch semantics: only supplied fields are sent. ``None``/absent means
  *leave alone*, never *blank it*.
- ``description`` is permanently out of scope on this path: patch semantics
  mean this adapter never reads or resends a description, so nothing here
  can flatten it (docs/contracts/course-catalog-contract.md's no-raw-HTML
  rule stays satisfied for the same reason -- there is no description to
  round-trip through the catalog).
- One assignment per operation.

Drift is the live assignment's ``updated_at``: frozen at preview
(``capture_baseline``), compared against a fresh GET at apply
(``check_drift``). Same-title/approximate-content matching never proves
identity here the way it can on the create path (see
``docs/reference/assignment-differentiation-design.md`` -> *Idempotency,
drift, and retry*) because there is no title matching on this path at all --
identity is the supplied id, and freshness is the only question.
"""
from __future__ import annotations

from .. import models
from .adapter_support import (
    build_result as _build_result,
    ensure_step as _ensure_step,
    is_uncertain as _is_uncertain,
    replace_step as _replace_step,
)
from api.platform_services import canvas_client, config


KIND = "content.assignment_update"

_PATCH_FIELDS = ("published", "due_at", "unlock_at", "lock_at")
_STEP_KEY = "update_assignment"


def _assignment_path(course_id: str, assignment_id: str) -> str:
    return f"/api/v1/courses/{course_id}/assignments/{assignment_id}"


def _snapshot(assignment: dict) -> dict:
    """The only fields this adapter ever reads off a live assignment.

    No description, points_possible, or assignment_group_id: this adapter
    has no business with them, so it cannot echo, diff, or resend them.
    """
    return {
        "name": assignment.get("name"),
        "html_url": assignment.get("html_url"),
        "updated_at": assignment.get("updated_at"),
        "published": assignment.get("published"),
        "due_at": assignment.get("due_at"),
        "unlock_at": assignment.get("unlock_at"),
        "lock_at": assignment.get("lock_at"),
    }


class AssignmentUpdateAdapter:
    kind = KIND

    # ── Payload ──────────────────────────────────────────────────────────

    def build_payload(self, prepare_request: dict) -> dict:
        assignment_id = str(prepare_request.get("assignment_id") or "").strip()
        if not assignment_id:
            raise ValueError("assignment_id is required")

        fields = {}
        if prepare_request.get("published") is not None:
            fields["published"] = bool(prepare_request["published"])
        for key in ("due_at", "unlock_at", "lock_at"):
            value = prepare_request.get(key)
            if value:
                fields[key] = str(value).strip()
        if not fields:
            raise ValueError(
                "at least one of published, due_at, unlock_at, or lock_at is required"
            )
        return {"assignment_id": assignment_id, "fields": fields}

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "assignment_id": payload.get("assignment_id"),
            "fields": payload.get("fields"),
        })

    # ── Target verification ─────────────────────────────────────────────

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        active_ids = {str(course["id"]) for course in config.active_courses()}
        verified = []
        for target in targets:
            course_id = str(target.get("course_id") or "")
            if not course_id:
                raise ValueError("target missing course_id")
            if course_id not in active_ids:
                raise ValueError(f"course {course_id} is not in active courses")
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
            f"{self.source_digest(payload)}|{course_id}|{payload.get('assignment_id')}"
        )

    # ── Baseline / drift ─────────────────────────────────────────────────

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        assignment_id = payload.get("assignment_id")
        # The teacher's default read reports an override's dates when overrides
        # exist; this path patches the base dates, so it must read them.
        assignment, error = canvas_client.canvas_get(
            _assignment_path(course_id, assignment_id),
            params={"override_assignment_dates": "false"},
        )
        if error or not isinstance(assignment, dict):
            return {"canvas_error": error or "assignment not found"}
        return {"assignment": _snapshot(assignment)}

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        frozen = (baseline or {}).get("assignment")
        if not frozen:
            return True
        fresh = self.capture_baseline(payload, target)
        if "canvas_error" in fresh:
            return True
        live = fresh.get("assignment") or {}
        return live.get("updated_at") != frozen.get("updated_at")

    # ── Review ───────────────────────────────────────────────────────────

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        course_name = course_id
        for course in config.active_courses():
            if str(course["id"]) == str(course_id):
                course_name = (
                    course.get("name") or course.get("nickname") or course_id
                )
                break
        frozen = (baseline or {}).get("assignment") or {}
        fields = payload.get("fields", {})
        changes = [
            {"field": field, "from": frozen.get(field), "to": fields[field]}
            for field in _PATCH_FIELDS if field in fields
        ]
        return {
            "course_name": course_name,
            "assignment_id": payload.get("assignment_id"),
            "assignment_name": frozen.get("name"),
            "changes": changes,
        }

    # ── Execute ──────────────────────────────────────────────────────────

    def execute(
        self, payload: dict, target: dict, baseline: dict, claim: dict, context,
    ) -> dict:
        course_id = target["course_id"]
        assignment_id = payload.get("assignment_id")
        steps = list(target.get("steps", []))
        fresh = (baseline or {}).get("assignment") or {}
        assignment_url = fresh.get("html_url")

        step = _ensure_step(steps, _STEP_KEY)
        if step.get("state") in ("applied", "skipped"):
            step["state"] = "skipped"
            _replace_step(steps, step)
            return _build_result(
                "applied", steps=steps,
                returned_object_id=assignment_id,
                returned_object_url=step.get("returned_object_url") or assignment_url,
            )

        path = _assignment_path(course_id, assignment_id)
        request = {"assignment": dict(payload.get("fields", {}))}
        digest = models.sha256_dict({"method": "PUT", "path": path, "payload": request})
        step = context.before_send(_STEP_KEY, digest)
        _replace_step(steps, step)

        response, error = canvas_client._canvas_send("PUT", path, request)
        if error:
            state = "sent_unknown" if _is_uncertain(error) else "failed"
            step["state"] = state
            step["error_code"] = (
                "timeout_or_disconnect" if state == "sent_unknown" else "canvas_rejected"
            )
            step["private_diagnostic"] = error
            step = context.checkpoint_step(step)
            _replace_step(steps, step)
            return _build_result(
                state, steps=steps,
                returned_object_id=assignment_id,
                returned_object_url=assignment_url,
                error_code=step["error_code"],
                private_diagnostic=error,
            )

        returned_url = (response or {}).get("html_url") or assignment_url
        step["state"] = "applied"
        step = context.checkpoint_step(
            step, returned_object_id=assignment_id, returned_object_url=returned_url,
        )
        _replace_step(steps, step)
        return _build_result(
            "applied", steps=steps,
            returned_object_id=assignment_id,
            returned_object_url=returned_url,
        )

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        assignment_id = payload.get("assignment_id")
        assignment, error = canvas_client.canvas_get(
            _assignment_path(course_id, assignment_id)
        )
        if error or not isinstance(assignment, dict):
            return {"state": "failed", "returned_object_id": None}
        return {
            "state": "applied",
            "returned_object_id": str(assignment.get("id") or assignment_id),
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
