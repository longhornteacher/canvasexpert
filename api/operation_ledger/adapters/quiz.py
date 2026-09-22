"""QuizForge adapter for the crash-safe ``content.quiz`` operation kind.

Pure plan subprocess, then checkpointed New Quiz create/item/assignment/module
writes with exact-ID reconciliation. Supports whole-class and differentiated
(variant-based) modes.
"""
from __future__ import annotations

import hashlib
import copy
import json
import math
from datetime import datetime, timezone

from .. import models
from . import differentiated_bridge, quiz_differentiated, quiz_whole
from .adapter_support import (
    as_list as _as_list,
    build_result as _build_result,
    ensure_step as _ensure_step,
    find_step as _find_step,
    has_outbound_marker as _has_outbound_marker,
    module_id_from_steps as _module_id_from_steps,
    normalize as _normalize,
    prepend_step as _step,
    replace_step as _replace_local_step,
)
from .module_placement import attach_assignment_type_module_item
from api.platform_services import canvas_client, config
from api.webui.runner import run_json_object


KIND = "content.quiz"


def _variant_identity(variant: dict, index: int) -> str:
    plan = variant.get("plan") or {}
    metadata = plan.get("metadata") or {}
    explicit = metadata.get("variant") or metadata.get("variant_label")
    return _normalize(explicit or f"variant_{index}")


class QuizAdapter:
    kind = KIND

    # ── Payload ──────────────────────────────────────────────────────────

    def build_payload(self, prepare_request: dict) -> dict:
        mode = prepare_request.get("mode", "whole")
        if mode == "differentiated":
            return self._build_payload_differentiated(prepare_request)

        if mode != "whole":
            raise ValueError(
                f"mode={mode!r} is not supported; "
                "use mode='whole' or mode='differentiated'"
            )
        path = prepare_request.get("path")
        if not path:
            raise ValueError("path is required")
        settings = prepare_request.get("settings") or {}

        # Run the no-network plan subprocess
        plan = run_json_object(
            ["qf_pusher.py", path, "--plan-json"],
            extra_env={"QF_PUSH_SETTINGS": json.dumps(settings)},
        )
        if not isinstance(plan, dict):
            raise ValueError("planner did not return a JSON object")
        if plan.get("version") != 1:
            raise ValueError(f"unsupported plan version {plan.get('version')}")
        if not plan.get("title"):
            raise ValueError("plan missing title")
        items = plan.get("items", [])
        if not items:
            raise ValueError("plan has no items")
        quiz_payload = plan.get("quiz_payload")
        if not isinstance(quiz_payload, dict) or "quiz" not in quiz_payload:
            raise ValueError("plan missing quiz_payload")

        return {
            "mode": mode,
            "path": path,
            "settings": settings,
            "plan": plan,
        }

    def _build_payload_differentiated(self, prepare_request: dict) -> dict:
        variants_in = prepare_request.get("variants")
        if not variants_in or not isinstance(variants_in, list) or len(variants_in) < 2:
            raise ValueError("differentiated mode requires at least two variants")
        settings = copy.deepcopy(prepare_request.get("settings") or {})
        due_at, module_name, bridge_due_at = (
            differentiated_bridge.require_family_delivery(
                settings.get("due_at"), settings.get("module_name"), settings.get("module_id"),
            )
        )
        variants = []
        for row in variants_in:
            path = row.get("path")
            if not path:
                raise ValueError("each variant requires a staged path")
            plan = run_json_object(
                ["qf_pusher.py", path, "--plan-json"],
                extra_env={"QF_PUSH_SETTINGS": json.dumps(settings)},
            )
            if not isinstance(plan, dict):
                raise ValueError("planner did not return a JSON object for variant")
            if plan.get("version") != 1:
                raise ValueError(f"unsupported plan version {plan.get('version')}")
            if not plan.get("title"):
                raise ValueError("variant plan missing title")
            items = plan.get("items", [])
            if not items:
                raise ValueError("variant plan has no items")
            quiz_payload = plan.get("quiz_payload")
            if not isinstance(quiz_payload, dict) or "quiz" not in quiz_payload:
                raise ValueError("variant plan missing quiz_payload")
            variants.append({
                "path": path,
                "plan": plan,
            })
        base_titles = [
            differentiated_bridge.normalize_base_title(v["plan"].get("title"))
            for v in variants
        ]
        if any(title != base_titles[0] for title in base_titles[1:]):
            raise ValueError(
                "All differentiated QuizForge files must use the same exact unsuffixed base title"
            )
        labels = []
        for variant in variants:
            metadata = variant["plan"].get("metadata") or {}
            labels.append(metadata.get("variant") or metadata.get("variant_label"))
        tags = differentiated_bridge.resolve_public_tags(labels)
        if any(
            base_titles[0].casefold().endswith(f" - {row['tag']}".casefold())
            for row in tags
        ):
            raise ValueError(
                "Differentiated QuizForge titles must be unsuffixed; Canvas Expert appends the public tag"
            )
        totals = [
            variant["plan"].get("quiz_payload", {}).get("quiz", {}).get("points_possible")
            for variant in variants
        ]
        try:
            numeric_totals = [float(total) for total in totals]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Differentiated QuizForge variants require numeric total points"
            ) from exc
        if any(not math.isfinite(total) or total < 0 for total in numeric_totals):
            raise ValueError(
                "Differentiated QuizForge variants require numeric total points"
            )
        if any(
            not math.isclose(numeric_totals[0], total, rel_tol=0.0, abs_tol=1e-6)
            for total in numeric_totals[1:]
        ):
            raise ValueError("Differentiated QuizForge variants must have equal total points")
        base_title = base_titles[0]
        settings.update({"due_at": due_at, "module_name": module_name,
                         "module_id": str(settings.get("module_id") or "").strip(),
                         "create_module": bool(settings.get("create_module"))})
        for variant, resolved in zip(variants, tags):
            title = differentiated_bridge.source_title(base_title, resolved["tag"])
            variant.update(resolved)
            variant["plan"]["title"] = title
            variant["plan"]["quiz_payload"]["quiz"]["title"] = title
            assignment_settings = variant["plan"].setdefault("assignment_settings", {})
            assignment_settings.update({
                "due_at": due_at,
                "published": True,
                "only_visible_to_overrides": False,
                "omit_from_final_grade": True,
                "post_to_sis": False,
            })
            variant["plan"]["module"] = {}
        return {
            "mode": "differentiated",
            "variants": variants,
            "settings": settings,
            "base_title": base_title,
            "due_at": due_at,
            "module_name": module_name,
            "module_id": settings.get("module_id") or "",
            "create_module": bool(settings.get("create_module")),
            "bridge_due_at": bridge_due_at,
            "bridge_description": differentiated_bridge.bridge_description(),
            "unrestricted_tiers": True,
        }

    def source_digest(self, payload: dict) -> str:
        if payload.get("mode") == "differentiated":
            variants = payload.get("variants", [])
            return models.sha256_dict({
                "mode": "differentiated",
                "variants": [
                    {
                        "plan": {
                            "version": v["plan"].get("version"),
                            "title": v["plan"].get("title"),
                            "metadata": v["plan"].get("metadata"),
                            "quiz_payload": v["plan"].get("quiz_payload"),
                            "items": [
                                {k: item.get(k) for k in ("index", "source_item_id", "source_type", "payload")}
                                for item in v["plan"].get("items", [])
                            ],
                            "assignment_settings": v["plan"].get("assignment_settings"),
                            "module": v["plan"].get("module"),
                        },
                    }
                    for v in variants
                ],
                "settings": payload.get("settings"),
                "base_title": payload.get("base_title"),
                "due_at": payload.get("due_at"),
                "module_name": payload.get("module_name"),
                "module_id": payload.get("module_id"),
                "create_module": payload.get("create_module"),
                "bridge_due_at": payload.get("bridge_due_at"),
                "bridge_description": payload.get("bridge_description"),
                "unrestricted_tiers": payload.get("unrestricted_tiers"),
            })
        plan = payload.get("plan", {})
        return models.sha256_dict({
            "version": plan.get("version"),
            "title": plan.get("title"),
            "quiz_payload": plan.get("quiz_payload"),
            "items": [
                {k: item.get(k) for k in ("index", "source_item_id", "source_type", "payload")}
                for item in plan.get("items", [])
            ],
            "assignment_settings": plan.get("assignment_settings"),
            "module": plan.get("module"),
            "settings": payload.get("settings"),
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
        if payload.get("mode") == "differentiated":
            titles = "|".join(
                _normalize(v["plan"].get("title", ""))
                for v in payload.get("variants", [])
            )
            return models.sha256_hex(
                f"{self.source_digest(payload)}|{course_id}|{titles}"
            )
        plan = payload.get("plan", {})
        return models.sha256_hex(
            f"{self.source_digest(payload)}|{course_id}"
            f"|{_normalize(plan.get('title'))}"
        )

    # ── Baseline / drift ─────────────────────────────────────────────────

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        if payload.get("mode") == "differentiated":
            baseline = self._capture_baseline_differentiated(course_id, payload)
            return baseline
        plan = payload.get("plan", {})
        title = plan.get("title", "")
        baseline: dict = {"existing_quiz": None}

        assignments, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments",
            params={"per_page": 100, "search_term": title},
        )
        if error:
            baseline["canvas_error"] = error
            return baseline

        for assignment in _as_list(assignments):
            if _normalize(assignment.get("name")) == _normalize(title):
                is_nq = bool(assignment.get("new_quizzes"))
                baseline["existing_quiz"] = {
                    "id": str(assignment.get("id")),
                    "name": assignment.get("name"),
                    "new_quizzes": is_nq,
                    "published": assignment.get("published"),
                    "html_url": assignment.get("html_url"),
                }
                break
        return baseline

    def _capture_baseline_differentiated(self, course_id: str, payload: dict) -> dict:
        """Capture existing quiz assignments per variant without student placement reads."""
        variants = payload.get("variants", [])
        existing_by_title = {}
        existing_by_variant = {}
        titles = [differentiated_bridge.bridge_title(payload.get("base_title", "")), *[
            v["plan"].get("title", "") for v in variants
        ]]
        for title in titles:
            assignments, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments",
                params={"per_page": 100, "search_term": title},
            )
            if error:
                return {"canvas_error": error}
            matches = [
                {"id": str(row.get("id")), "html_url": row.get("html_url")}
                for row in _as_list(assignments)
                if row.get("id") is not None
                and _normalize(row.get("name")) == _normalize(title)
            ]
            existing_by_title[title] = matches
        for index, v in enumerate(variants):
            existing_by_variant[_variant_identity(v, index)] = existing_by_title.get(
                v["plan"].get("title", ""), []
            )

        return {
            "existing_by_title": existing_by_title,
            "existing_by_variant": existing_by_variant,
        }

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        if baseline is None:
            return False
        if "canvas_error" in baseline:
            return True
        if payload.get("mode") == "differentiated":
            fresh = self.capture_baseline(payload, target)
            if "canvas_error" in fresh:
                return True
            known = {
                str(step.get("returned_object_id"))
                for step in target.get("steps", [])
                if (
                    step.get("step_key", "").startswith("create_quiz:")
                    or step.get("step_key") == "create_bridge"
                )
                and step.get("returned_object_id") is not None
            }
            existing_map = fresh.get("existing_by_title", {})
            for matches in existing_map.values():
                current = {m["id"] for m in matches}
                if current - known:
                    return True
            return False
        existing = baseline.get("existing_quiz")
        if existing is None:
            return False
        # Before first write, any same-title match blocks.
        known_ids = {
            str(step.get("returned_object_id"))
            for step in target.get("steps", [])
            if step.get("returned_object_id") is not None
        }
        if existing["id"] not in known_ids:
            return True  # unknown same-title drift
        return True  # known match still means drift (title collision)

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

        if payload.get("mode") == "differentiated":
            return self._freeze_review_differentiated(
                payload, course_name, baseline
            )

        plan = payload.get("plan", {})
        items = plan.get("items", [])
        quiz_payload = plan.get("quiz_payload", {})
        quiz = quiz_payload.get("quiz", {})
        quiz_settings = quiz.get("quiz_settings", {})
        assignment_settings = plan.get("assignment_settings", {})
        module = plan.get("module", {})

        item_types: dict[str, int] = {}
        for item in items:
            t = str(item.get("source_type") or "unknown")
            item_types[t] = item_types.get(t, 0) + 1

        attempts = quiz_settings.get("multiple_attempts", {})
        time_limit = quiz_settings.get("session_time_limit_in_seconds")
        result_view = quiz_settings.get("result_view_settings", {})

        review: dict = {
            "course_name": course_name,
            "title": plan.get("title"),
            "mode": "whole",
            "item_count": len(items),
            "item_types": item_types,
            "total_points": quiz.get("points_possible"),
            "due_at": assignment_settings.get("due_at"),
            "unlock_at": assignment_settings.get("unlock_at"),
            "lock_at": assignment_settings.get("lock_at"),
            "published": assignment_settings.get("published", False),
            "post_to_sis": assignment_settings.get("post_to_sis", False),
            "assignment_group_name": assignment_settings.get("assignment_group_name"),
            "module_name": module.get("module_name"),
            "shuffle_answers": quiz_settings.get("shuffle_answers"),
            "shuffle_questions": quiz_settings.get("shuffle_questions"),
            "access_code": bool(quiz_settings.get("student_access_code")),
            "multiple_attempts": attempts.get("multiple_attempts_enabled", False),
            "score_to_keep": attempts.get("score_to_keep"),
            "allowed_attempts": attempts.get("max_attempts"),
            "time_limit_minutes": (
                round(time_limit / 60) if isinstance(time_limit, (int, float)) else None
            ),
            "calculator_type": quiz_settings.get("calculator_type"),
            "one_at_a_time": quiz_settings.get("one_at_a_time_type") == "question",
            "allow_backtracking": quiz_settings.get("allow_backtracking", True),
            "hide_results": result_view.get("result_view_restricted")
                and not result_view.get("display_items", True),
        }

        existing = baseline.get("existing_quiz")
        review["baseline_has_existing"] = existing is not None
        if existing:
            review["baseline_existing_id"] = existing["id"]
            review["baseline_existing_url"] = existing["html_url"]
            review["baseline_existing_new_quizzes"] = existing["new_quizzes"]
        else:
            review["baseline_existing_id"] = None
            review["baseline_existing_url"] = None
            review["baseline_existing_new_quizzes"] = None

        return review

    def _freeze_review_differentiated(
        self, payload: dict, course_name: str, baseline: dict,
    ) -> dict:
        variants = payload.get("variants", [])
        existing_by_title = baseline.get("existing_by_title", {})

        variant_summaries = []
        for v in variants:
            plan = v["plan"]
            items = plan.get("items", [])
            item_types: dict[str, int] = {}
            for item in items:
                t = str(item.get("source_type") or "unknown")
                item_types[t] = item_types.get(t, 0) + 1
            variant_summaries.append({
                "tier": v.get("tier"),
                "public_tag": v.get("tag"),
                "title": plan.get("title"),
                "item_count": len(items),
                "item_types": item_types,
                "total_points": plan.get("quiz_payload", {}).get("quiz", {}).get("points_possible"),
            })

        review: dict = {
            "course_name": course_name,
            "mode": "differentiated",
            "variant_count": len(variants),
            "variants": variant_summaries,
            "bridge": {
                "title": differentiated_bridge.bridge_title(payload.get("base_title")),
                "due_at": payload.get("bridge_due_at"),
                "module_name": payload.get("module_name"),
                "post_to_sis": True,
            },
            "only_visible_to_overrides": False,
            "unrestricted_tiers": True,
            "tier_warning": (
                "Canvas will create one configured-tag-suffixed New Quiz per tier and one "
                "gradebook-only '<family> - Bridge'; only the source assignments are module items. Review them in Canvas Live; "
                "the teacher initiates SIS sync there."
            ),
        }

        # Existing quiz info
        has_existing = any(matches for matches in existing_by_title.values())
        review["baseline_has_existing"] = has_existing
        review["baseline_existing_id"] = None
        review["baseline_existing_url"] = None

        return review

    # ── Execute ──────────────────────────────────────────────────────────

    def execute(
        self, payload: dict, target: dict, baseline: dict,
        claim: dict, context,
    ) -> dict:
        if payload.get("mode") == "differentiated":
            return quiz_differentiated.execute(
                payload,
                target,
                baseline,
                context,
                ordered_steps=_ordered_steps,
            )
        return quiz_whole.execute(payload, target, context, ordered_steps=_ordered_steps)

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        if payload.get("mode") == "differentiated":
            return quiz_differentiated.reconcile(payload, target)
        return quiz_whole.reconcile(payload, target, ordered_steps=_ordered_steps)

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


def _attach_quiz_to_module(
    course_id: str, quiz_id: str, title: str,
    module_name: str, steps: list[dict], context,
) -> dict:
    return attach_assignment_type_module_item(
        course_id=course_id,
        content_id=quiz_id,
        title=title,
        module_name=module_name,
        steps=steps,
        context=context,
        attach_step_key="attach_module:0",
        returned_object_id=quiz_id,
        deterministic_failure_state="failed",
    )


# ── Step ordering helpers ────────────────────────────────────────────────


def _ordered_steps(target: dict) -> list[dict]:
    existing = {
        step.get("step_key"): step
        for step in target.get("steps", [])
    }

    def step_order(step):
        key = str(step.get("step_key") or "")
        if key == "create_module":
            return (-1, 0, 0)
        prefix, _, suffix = key.partition(":")
        rank = {
            "create_quiz": 0,
            "create_item": 1,
            "patch_assignment": 2,
        }.get(prefix, 9)
        parts = suffix.split(":") if suffix else ["0"]
        variant = int(parts[0]) if parts[0].isdigit() else 0
        item_idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        family_rank = {
            "create_bridge": 100,
            "create_module": 101,
            "activate_bridge": 103,
            "register_family": 104,
        }.get(key)
        if key.startswith("attach_source_module:"):
            source_index = key.rsplit(":", 1)[-1]
            return (999999, 102, int(source_index) if source_index.isdigit() else 0)
        if family_rank is not None:
            return (999999, family_rank, 0)
        return (variant, rank, item_idx)

    return sorted(existing.values(), key=step_order)
