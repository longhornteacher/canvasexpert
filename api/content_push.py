"""Assistant-facing staged-content push use case.

This is the shared non-HTTP boundary used by MCP, and it mirrors
``api/sis_grade_bridge.py``: live Canvas behavior stays inside the Operation
Ledger adapters, while this module resolves one staged draft, freezes one
operation against one Current course, and shapes only course-only results.

The teacher's own push tabs stay exactly as they are. Both routes prepare the
same adapter payload, freeze the same batch, and apply through the same
executor, so a draft landed from the chat and a draft landed from the web UI
are the same write with the same drift checks, claims, and receipts.

The draft must already be staged in the per-kind To Review Inbox
(``runtime_paths.inbox_folder``). Only a draft's label crosses this boundary,
never an absolute path: the assistant names what it staged, and this module
resolves that label against the marker-gated listing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from api import runtime_paths
from api.operation_ledger import batches, executor, models, operations, registry
from api.operation_ledger.adapters.assignment import KIND as ASSIGNMENT_KIND
from api.operation_ledger.adapters.assignment_update import KIND as ASSIGNMENT_UPDATE_KIND
from api.operation_ledger.adapters.page import KIND as PAGE_KIND
from api.operation_ledger.adapters.quiz import KIND as QUIZ_KIND
from api.platform_services import config
from api.webui import deps

# Teacher-facing kind -> ledger operation kind. These are the three kinds that
# have an Inbox, an authoring contract, and a push tab; they stay in step with
# ``mcp_server.tools._STAGED_CONTRACT_KINDS``.
_LEDGER_KINDS = {
    "quiz": QUIZ_KIND,
    "assignment": ASSIGNMENT_KIND,
    "page": PAGE_KIND,
}

# Delivery options each kind can actually carry. Naming one a kind does not
# accept is refused rather than dropped: a teacher who says "due Friday" about
# a page should hear that pages have no due date, not watch it vanish.
_SCHEDULE_OPTIONS = ("due_at", "unlock_at", "lock_at")
_KIND_OPTIONS = {
    "quiz": ("published", "module_name", "assignment_group_name",
             "post_to_sis", *_SCHEDULE_OPTIONS),
    "assignment": ("published", "module_name", "assignment_group_name",
                   "post_to_sis", *_SCHEDULE_OPTIONS),
    "page": ("published", "module_name"),
}

_TEXT_OPTIONS = ("module_name", "assignment_group_name", *_SCHEDULE_OPTIONS)
_FLAG_OPTIONS = ("published", "post_to_sis")


def _current_course(course_id: str) -> bool:
    wanted = str(course_id or "").strip()
    return bool(wanted) and wanted in {
        str(course.get("id") or "").strip()
        for course in config.active_courses()
    }


def _kind_error(kind: str) -> str:
    return (f"unknown kind '{kind}'; expected one of: "
            f"{', '.join(_LEDGER_KINDS)}")


def _resolve_staged_draft(kind: str, label: str) -> tuple[str | None, str | None]:
    """Return ``(path, None)`` for one staged label, or ``(None, error)``.

    Matches the exact label first, then case-insensitively, then with ``.txt``
    appended, so an assistant that named the draft without its extension still
    resolves. An ambiguous label is refused rather than guessed at.
    """
    wanted = str(label or "").strip()
    if not wanted:
        return None, "label is required; call list_staged_content for the staged labels"
    entries = deps.list_inbox_files(kind)
    if not entries:
        return None, (
            f"no {kind} draft is staged for review; stage the draft first "
            "(see get_authoring_contract) and then push it"
        )

    for candidates in (
        [entry for entry in entries if entry["label"] == wanted],
        [entry for entry in entries if entry["label"].casefold() == wanted.casefold()],
        [entry for entry in entries
         if entry["label"].casefold() == f"{wanted}.txt".casefold()],
    ):
        if len(candidates) == 1:
            return candidates[0]["path"], None
        if len(candidates) > 1:
            return None, (
                f"'{wanted}' matches more than one staged {kind} draft; "
                "use the exact label from list_staged_content"
            )

    known = ", ".join(entry["label"] for entry in entries)
    return None, (
        f"no staged {kind} draft is labeled '{wanted}'; staged {kind} drafts "
        f"are: {known}"
    )


def _collect_options(kind: str, options: dict) -> tuple[dict, str | None]:
    """Normalize the delivery options, refusing any this kind cannot carry."""
    named = {}
    for key in _FLAG_OPTIONS:
        if bool(options.get(key)):
            named[key] = True
    for key in _TEXT_OPTIONS:
        value = str(options.get(key) or "").strip()
        if value:
            named[key] = value

    allowed = _KIND_OPTIONS[kind]
    unsupported = sorted(key for key in named if key not in allowed)
    if unsupported:
        return {}, (
            f"a {kind} push does not take {', '.join(unsupported)}; "
            f"it takes {', '.join(allowed)}"
        )
    # published is always meaningful, and always explicit in the payload.
    named["published"] = bool(options.get("published"))
    return named, None


def _prepare_request(kind: str, path: str, named: dict) -> dict:
    """Build the adapter's prepare request from one resolved draft and options.

    A quiz carries its options as QuizForge push settings rather than as
    top-level fields; ``qf_pusher.SETTING_KEYS`` covers every one of them and
    drops anything it does not recognize.
    """
    if kind == "quiz":
        return {"mode": "whole", "path": path, "settings": dict(named)}
    return {"path": path, **named}


def preview_content_push(
    course_id: str,
    kind: str,
    label: str,
    *,
    published: bool = False,
    module_name: str = "",
    assignment_group_name: str = "",
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
    post_to_sis: bool = False,
) -> dict:
    """Freeze one staged draft into a persisted, digest-protected review.

    Makes no Canvas write. Reads Canvas only to capture the baseline the apply
    step drift-checks against, exactly as the push tab's prepare step does.
    """
    course_key = str(course_id or "").strip()
    content_kind = str(kind or "").strip()
    if content_kind not in _LEDGER_KINDS:
        return {"ok": False, "error": _kind_error(content_kind)}
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}

    named, option_error = _collect_options(content_kind, {
        "published": published,
        "module_name": module_name,
        "assignment_group_name": assignment_group_name,
        "due_at": due_at,
        "unlock_at": unlock_at,
        "lock_at": lock_at,
        "post_to_sis": post_to_sis,
    })
    if option_error:
        return {"ok": False, "error": option_error}

    path, resolve_error = _resolve_staged_draft(content_kind, label)
    if resolve_error:
        return {"ok": False, "error": resolve_error}

    ledger_kind = _LEDGER_KINDS[content_kind]
    adapter = registry.get_adapter(ledger_kind)
    try:
        payload = adapter.build_payload(_prepare_request(content_kind, path, named))
        target = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        baseline = adapter.capture_baseline(payload, target)
        target_record = models.new_target(
            target_key=target["target_key"],
            idempotency_key=target["idempotency_key"],
            course_id=target["course_id"],
            baseline=baseline,
        )
        operation_id = models.new_operation_id()
        operation = models.new_operation(
            operation_id=operation_id,
            kind=ledger_kind,
            source_ref={"type": "staged_inbox", "value": content_kind},
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload,
            targets=[target_record],
        )
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "blocking": True}
    except Exception:
        return {"ok": False, "error": f"the {content_kind} push could not be prepared"}

    return {
        "ok": True,
        "kind": content_kind,
        "label": label,
        "operation_id": operation_id,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "preview": _scrub_paths(frozen),
    }


def preview_differentiated_quiz_push(
    course_id: str,
    variants: list,
    *,
    published: bool = False,
    module_name: str = "",
    assignment_group_name: str = "",
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
    post_to_sis: bool = False,
) -> dict:
    """Freeze a group-restricted QuizForge operation from staged labels."""
    course_key = str(course_id or "").strip()
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}
    if not isinstance(variants, list) or len(variants) < 2:
        return {"ok": False, "error": "variants must be a list of at least two variants"}
    named, option_error = _collect_options("quiz", {
        "published": published, "module_name": module_name,
        "assignment_group_name": assignment_group_name, "due_at": due_at,
        "unlock_at": unlock_at, "lock_at": lock_at, "post_to_sis": post_to_sis,
    })
    if option_error:
        return {"ok": False, "error": option_error}

    resolved = []
    seen_labels = set()
    seen_groups = set()
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) != {"label", "group_name"}:
            return {"ok": False, "error": "each variant must contain exactly label and group_name"}
        label = variant["label"]
        group_name = variant["group_name"]
        if not isinstance(label, str) or not isinstance(group_name, str):
            return {"ok": False, "error": "variant label and group_name must be strings"}
        label = label.strip()
        group_name = group_name.strip()
        if not label or not group_name:
            return {"ok": False, "error": "variant label and group_name cannot be blank"}
        if os.path.isabs(label) or "/" in label or "\\" in label or ".." in label:
            return {"ok": False, "error": "variant label must be a staged label, not a path"}
        path, resolve_error = _resolve_staged_draft("quiz", label)
        if resolve_error:
            return {"ok": False, "error": resolve_error}
        resolved_label = Path(path).name.casefold()
        if resolved_label in seen_labels:
            return {"ok": False, "error": "variant labels must be unique after resolution"}
        group_key = group_name.casefold()
        if group_key in seen_groups:
            return {"ok": False, "error": "group names must be unique after trim and case-folding"}
        seen_labels.add(resolved_label)
        seen_groups.add(group_key)
        resolved.append({"label": Path(path).name, "path": path, "group_name": group_name})

    adapter = registry.get_adapter(QUIZ_KIND)
    try:
        payload = adapter.build_payload({"mode": "differentiated", "variants": resolved,
                                         "settings": dict(named)})
        target = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        baseline = adapter.capture_baseline(payload, target)
        safe = baseline.get("group_snapshot") if isinstance(baseline, dict) else None
        tiers = safe.get("tiers") if isinstance(safe, dict) else None
        if (not isinstance(safe, dict) or "canvas_error" in baseline or
                not isinstance(tiers, list) or len(tiers) != len(resolved) or
                any(not isinstance(tier, dict) or "student_count" not in tier
                    for tier in tiers)):
            return {"ok": False, "error": "the differentiated quiz baseline could not resolve every requested group", "blocking": True}
        target_record = models.new_target(target_key=target["target_key"],
                                          idempotency_key=target["idempotency_key"],
                                          course_id=target["course_id"], baseline=baseline)
        operation_id = models.new_operation_id()
        operation = models.new_operation(operation_id=operation_id, kind=QUIZ_KIND,
                                         source_ref={"type": "staged_inbox_differentiated", "value": "quiz"},
                                         source_digest=adapter.source_digest(payload),
                                         normalized_payload=payload, targets=[target_record])
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "blocking": True}
    except Exception:
        return {"ok": False, "error": "the differentiated quiz push could not be prepared"}
    return {"ok": True, "kind": "quiz", "variants": [
        {"label": row["label"], "group_name": row["group_name"]} for row in resolved
    ], "operation_id": operation_id, "batch_id": batch["batch_id"],
            "review_digest": batch["review_digest"], "preview": _scrub_paths(frozen)}


def preview_assignment_update(
    course_id: str,
    assignment_id: str,
    *,
    published: bool | None = None,
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
) -> dict:
    """Freeze a publish/date patch against one existing Canvas assignment.

    Unlike the staged-content family above, there is no draft and no label:
    the caller names the exact Canvas ``assignment_id`` and only that
    assignment's ``published`` state and three schedule dates are ever read
    or changed. Canvas is read once, live, to capture the baseline apply
    drift-checks against and to freeze the field diff shown here -- never
    the mirror or the Course Catalog. Supplying no field at all is refused
    before anything reaches Canvas.
    """
    course_key = str(course_id or "").strip()
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}

    prepare_request = {"assignment_id": assignment_id}
    if published is not None:
        prepare_request["published"] = published
    for key, value in (("due_at", due_at), ("unlock_at", unlock_at), ("lock_at", lock_at)):
        text = str(value or "").strip()
        if text:
            prepare_request[key] = text

    adapter = registry.get_adapter(ASSIGNMENT_UPDATE_KIND)
    try:
        payload = adapter.build_payload(prepare_request)
        target = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        baseline = adapter.capture_baseline(payload, target)
        if not isinstance(baseline, dict) or baseline.get("canvas_error"):
            return {
                "ok": False,
                "error": "the assignment could not be read from Canvas",
                "blocking": True,
            }
        target_record = models.new_target(
            target_key=target["target_key"],
            idempotency_key=target["idempotency_key"],
            course_id=target["course_id"],
            baseline=baseline,
        )
        operation_id = models.new_operation_id()
        operation = models.new_operation(
            operation_id=operation_id,
            kind=ASSIGNMENT_UPDATE_KIND,
            source_ref={"type": "assignment_id", "value": str(assignment_id)},
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload,
            targets=[target_record],
        )
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "blocking": True}
    except Exception:
        return {"ok": False, "error": "the assignment update could not be prepared"}

    return {
        "ok": True,
        "operation_id": operation_id,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "preview": frozen,
    }


# A label becomes a filename in the teacher's synced workspace, so it is
# validated rather than sanitized: a label that cannot be used verbatim is
# refused and renamed by the caller, never silently rewritten into a different
# file than the one the assistant told the teacher about.
_UNSAFE_LABEL_CHARS = re.compile(r'[<>:"/\\|?*]')
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)
_MAX_LABEL = 120


def _safe_inbox_filename(label: str) -> tuple[str | None, str | None]:
    """Return ``(filename, None)`` for one usable Inbox label, else an error."""
    raw = str(label or "").strip()
    if not raw:
        return None, "label is required"
    if raw != os.path.basename(raw) or os.path.isabs(raw) or ".." in raw:
        return None, "label must be a plain file name, with no folders or path separators"
    if any(ord(char) < 32 for char in raw):
        return None, "label contains control characters"
    if _UNSAFE_LABEL_CHARS.search(raw):
        return None, 'label cannot contain any of < > : " / \\ | ? *'
    if raw.startswith("."):
        return None, "label cannot start with a dot"
    filename = raw if raw.casefold().endswith(".txt") else f"{raw}.txt"
    stem = filename[:-4]
    if not stem or stem != stem.rstrip(" ."):
        return None, "label cannot be empty or end with a space or dot"
    if stem.upper() in _RESERVED_STEMS:
        return None, f"'{stem}' is a reserved file name on Windows; choose another label"
    if len(filename) > _MAX_LABEL:
        return None, f"label is too long; keep it under {_MAX_LABEL} characters"
    return filename, None


def stage_content(kind: str, label: str, content: str) -> dict:
    """Write one authored draft into the per-kind To Review Inbox.

    Does for an assistant exactly what the staging appendix asks a
    file-capable one to do by hand: the envelope goes to ``<label>.txt`` and a
    sibling ``<label>.txt.done`` marker records the byte count measured from
    the file on disk, so ``deps.list_inbox_files`` only lists it once both
    agree. Written as bytes, so no text-mode line-ending translation can put
    the marker out of step with the draft.

    Refuses rather than overwrites: a label already staged belongs to whoever
    staged it. Nothing here touches Canvas.
    """
    content_kind = str(kind or "").strip().lower()
    if content_kind not in _LEDGER_KINDS:
        return {"ok": False, "error": _kind_error(content_kind)}
    filename, label_error = _safe_inbox_filename(label)
    if label_error:
        return {"ok": False, "error": label_error}
    text = content if isinstance(content, str) else ""
    if not text.strip():
        return {"ok": False, "error": f"content is required to stage a {content_kind} draft"}

    folder = runtime_paths.inbox_folder(content_kind)
    if not folder:
        return {
            "ok": False,
            "error": ("the Canvas Expert workspace is not configured, so there is "
                      "nowhere to stage this draft"),
        }
    target = Path(folder) / filename
    marker = Path(f"{target}.done")
    if target.exists() or marker.exists():
        return {
            "ok": False,
            "error": (f"a {content_kind} draft is already staged as '{filename}'; "
                      "choose another label"),
        }
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        marker.write_text(str(target.stat().st_size), encoding="utf-8")
    except OSError:
        return {"ok": False, "error": f"the {content_kind} draft could not be staged"}
    return {"ok": True, "kind": content_kind, "label": filename, "staged": True}


def push_content_live(
    course_id: str,
    kind: str,
    label: str,
    content: str,
    published: bool = False,
    module_name: str = "",
    assignment_group_name: str = "",
    post_to_sis: bool = False,
) -> dict:
    """Stage one authored draft and land it in Canvas in a single call.

    The teacher asking for this is the authorization. Staging still happens, so
    the draft is on disk as the artifact of record and can be read afterwards;
    what goes away is the teacher having to drop the file by hand before saying
    push.

    The freeze is not skipped, only made internal: the same baseline capture,
    persisted review, and drift check run between staging and applying, so a
    course that changed underneath is still refused rather than overwritten. A
    draft that stages but fails to push is left staged on purpose, so the
    teacher can see what was authored and fix it.

    Carries no due/unlock/lock dates. Every parameter here is paid for in the
    tool listing of every session, and dated work is the case that most wants a
    look before it lands, so scheduling stays on preview_content_push: stage the
    draft, preview it with the dates, then apply.
    """
    staged = stage_content(kind, label, content)
    if not staged.get("ok"):
        return staged

    review = preview_content_push(
        course_id, kind, staged["label"],
        published=published, module_name=module_name,
        assignment_group_name=assignment_group_name,
        post_to_sis=post_to_sis,
    )
    if not review.get("ok"):
        return {
            **review,
            "staged_label": staged["label"],
            "note": (f"The {staged['kind']} draft is staged as '{staged['label']}' and "
                     "nothing reached Canvas. Fix the draft and push it again."),
        }

    landed = apply_content_push(
        review["operation_id"], review["batch_id"], review["review_digest"],
    )
    return {
        **landed,
        "staged_label": staged["label"],
        "preview": review.get("preview"),
    }


def apply_content_push(
    operation_id: str, batch_id: str, review_digest: str
) -> dict:
    """Write exactly the frozen staged-content review to Canvas."""
    operation_key = str(operation_id or "").strip()
    batch_key = str(batch_id or "").strip()
    digest = str(review_digest or "").strip()
    if not operation_key or not batch_key or not digest:
        return {
            "ok": False,
            "error": "operation_id, batch_id, and review_digest are required",
        }
    operation = operations.get_operation(operation_key)
    if operation is None or operation.get("kind") not in set(_LEDGER_KINDS.values()):
        return {"ok": False, "error": "content push operation was not found"}
    try:
        result = executor.apply_operation(operation_key, batch_key, digest)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "the content push could not complete"}

    # The executor returns a deliberately bounded target result; differentiated
    # creation receipts are reconstructed from the persisted checkpoint steps.
    latest_operation = operations.get_operation(operation_key) or operation
    return _result_projection(latest_operation, result)


def apply_assignment_update(
    operation_id: str, batch_id: str, review_digest: str
) -> dict:
    """Write exactly the frozen assignment field patch to Canvas.

    A sibling to ``apply_content_push``, not a reuse of it: this operation
    kind is never staged-inbox content, so it is refused by
    ``apply_content_push``'s kind gate on purpose. Same coordinates
    contract, same Operation Ledger apply -- one claim, a drift check
    against the frozen ``updated_at``, and a durable receipt.
    """
    operation_key = str(operation_id or "").strip()
    batch_key = str(batch_id or "").strip()
    digest = str(review_digest or "").strip()
    if not operation_key or not batch_key or not digest:
        return {
            "ok": False,
            "error": "operation_id, batch_id, and review_digest are required",
        }
    operation = operations.get_operation(operation_key)
    if operation is None or operation.get("kind") != ASSIGNMENT_UPDATE_KIND:
        return {"ok": False, "error": "assignment update operation was not found"}
    try:
        result = executor.apply_operation(operation_key, batch_key, digest)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "the assignment update could not complete"}

    latest_operation = operations.get_operation(operation_key) or operation
    projected = _result_projection(latest_operation, result)
    projected["kind"] = "assignment_update"
    return projected


def _content_kind(ledger_kind: str) -> str:
    for name, value in _LEDGER_KINDS.items():
        if value == ledger_kind:
            return name
    return ledger_kind


def _result_projection(operation: dict, result: dict) -> dict:
    """Course-only outcome: what landed, where, and what needs attention.

    ``target_key`` and the private diagnostics stay out; the Canvas URL of the
    created object stays in, because that is the one thing the teacher wants
    from a push they asked for in chat.
    """
    targets = []
    normalized = operation.get("normalized_payload") or {}
    differentiated = (
        normalized.get("mode") == "differentiated" or bool(normalized.get("tiers"))
    )
    assignment_tiered = bool(normalized.get("tiers")) and operation.get("kind") == ASSIGNMENT_KIND
    variants = normalized.get("variants") or normalized.get("tiers") or []
    for target_index, target in enumerate(result.get("target_results") or []):
        row = {"state": target.get("state")}
        if target.get("returned_object_url"):
            row["url"] = target["returned_object_url"]
        if target.get("error_code"):
            row["error_code"] = target["error_code"]
        if target.get("failed_items"):
            row["failed_items"] = target["failed_items"]
        for key in ("cleanup_required", "rollback_state", "rollback_error_code"):
            if target.get(key) is not None:
                row[key] = target[key]
        stored_target = (operation.get("targets") or [])[target_index] if target_index < len(operation.get("targets") or []) else {}
        step_source = target.get("steps") or (stored_target.get("steps") if differentiated else [])
        steps = [
            {"step": step.get("step_key"), "state": step.get("state"),
             "error_code": step.get("error_code")}
            for step in step_source
            if step.get("state") not in (None, "applied")
        ]
        if steps:
            row["unfinished_steps"] = steps
        if differentiated:
            created = []
            create_prefix = (
                "create_quiz" if normalized.get("mode") == "differentiated"
                else "create_tier_assignment"
            )
            for index, variant in enumerate(variants):
                step = next((step for step in step_source
                             if step.get("step_key") == f"{create_prefix}:{index}"
                             and step.get("state") in ("applied", "skipped")
                             and step.get("returned_object_url")), None)
                if step:
                    created.append({"group_name": (variant.get("group_name")
                                                   or variant.get("group")),
                                    "title": ((variant.get("plan") or {}).get("title")
                                              or variant.get("title")),
                                    "url": step["returned_object_url"]})
            if assignment_tiered:
                created = []
                for index, variant in enumerate(variants):
                    step = next((step for step in step_source
                                 if step.get("step_key") == f"create_tier_assignment:{index}"
                                 and step.get("state") in ("applied", "skipped")
                                 and step.get("returned_object_id")), None)
                    if step:
                        created.append({
                            "label": variant.get("label"),
                            "assignment_id": step.get("returned_object_id"),
                            "name": ((variant.get("plan") or {}).get("title")
                                     or variant.get("title")),
                            "html_url": step.get("returned_object_url"),
                        })
                if created:
                    row["created"] = created
                row["teacher_action"] = (
                    "In Canvas, assign each draft to the intended students or groups, "
                    "then publish the drafts."
                )
            elif created:
                row["created"] = created
            bridge_step = next((step for step in step_source
                                if step.get("step_key") == "create_bridge"
                                and step.get("returned_object_id")), None)
            if bridge_step and not assignment_tiered:
                row["bridge"] = {
                    "title": normalized.get("base_title"),
                    "url": bridge_step.get("returned_object_url"),
                }
        targets.append(row)

    return {
        "ok": bool(result.get("ok")),
        "kind": _content_kind(operation.get("kind", "")),
        "operation_id": result.get("operation_id"),
        "status": result.get("status"),
        "targets": targets,
    }


def _scrub_paths(value):
    """Drop any local filesystem path a frozen review carries.

    No option this boundary accepts sets one today (printable attachments are
    web-UI only), so this is a guard against a future adapter field, not a
    known leak.
    """
    if isinstance(value, dict):
        return {key: _scrub_paths(item) for key, item in value.items()
                if not str(key).endswith("path")}
    if isinstance(value, list):
        return [_scrub_paths(item) for item in value]
    return value


__all__ = [
    "apply_assignment_update",
    "apply_content_push",
    "preview_assignment_update",
    "preview_content_push",
    "preview_differentiated_quiz_push",
    "push_content_live",
    "stage_content",
]
