"""verify_live: one cheap, read-only Live check after an approved push (AC1).

This is the only Live Canvas read an agent makes after a push (R4, locked
decision). It never feeds the catalog, mirror, or pending-writes state --
purely a read, reported back verbatim as a bounded, student-free
projection. Identity resolution costs exactly one Canvas call (a GET by id,
or one title-filtered list call); ``module_ids`` costs one additional call
only when Canvas does not already report it on the object itself, so a
single ``verify_live`` call costs at most two Canvas requests.
"""
from __future__ import annotations

from datetime import datetime, timezone

from api.platform_services import canvas_client, config

_SUPPORTED_KINDS = ("assignment", "page", "quiz")


def _current_course(course_id: str) -> bool:
    wanted = str(course_id or "").strip()
    return bool(wanted) and wanted in {
        str(course.get("id") or "").strip() for course in config.active_courses()
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_title(value: object) -> str:
    return str(value or "").strip().casefold()


def verify_live(course_id: str, kind: str, id: str = "", title: str = "") -> dict:
    """Confirm one Canvas object by exact id or exact title. Writes nothing.

    ``kind="quiz"`` reads the quiz's own underlying Assignment-type record
    (New Quizzes and classic quizzes both publish through an Assignment),
    so its result carries the assignment projection, addressed by the
    quiz's id.
    """
    course_key = str(course_id or "").strip()
    content_kind = str(kind or "").strip().lower()
    object_id = str(id or "").strip()
    search_title = str(title or "").strip()

    if content_kind not in _SUPPORTED_KINDS:
        return {
            "ok": False,
            "error": f"unknown kind '{content_kind}'; expected one of: {', '.join(_SUPPORTED_KINDS)}",
        }
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}
    if not object_id and not search_title:
        return {"ok": False, "error": "id or title is required"}

    if content_kind == "page":
        return _verify_page(course_key, object_id, search_title)
    return _verify_assignment(course_key, object_id, search_title)


def _verify_assignment(course_id: str, object_id: str, title: str) -> dict:
    if object_id:
        assignment, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{object_id}"
        )
        if error:
            return {"ok": False, "error": "Canvas could not be read for this check"}
        if not assignment:
            return {"ok": True, "found": False, "checked_at": _now_iso()}
    else:
        rows, error = canvas_client.canvas_get_all(
            f"/api/v1/courses/{course_id}/assignments",
            {"per_page": 100, "search_term": title},
        )
        if error:
            return {"ok": False, "error": "Canvas could not be read for this check"}
        matches = [row for row in (rows or [])
                   if _normalize_title(row.get("name")) == _normalize_title(title)]
        if not matches:
            return {"ok": True, "found": False, "checked_at": _now_iso()}
        if len(matches) > 1:
            return {
                "ok": True, "found": False, "ambiguous": True,
                "ids": sorted(str(row.get("id")) for row in matches),
                "checked_at": _now_iso(),
            }
        assignment = matches[0]

    module_ids = assignment.get("module_ids")
    if module_ids is None:
        assignment_id = str(assignment.get("id"))
        module_ids, module_error = _module_ids_containing(
            course_id,
            lambda item, target=assignment_id: str(item.get("content_id") or "") == target,
        )
        if module_error:
            module_ids = []
    return {
        "ok": True,
        "found": True,
        "id": str(assignment.get("id")),
        "title": assignment.get("name"),
        "published": assignment.get("published") is True,
        "points_possible": assignment.get("points_possible"),
        "module_ids": [str(value) for value in (module_ids or [])],
        "assignment_group_id": str(assignment.get("assignment_group_id") or ""),
        "omit_from_final_grade": assignment.get("omit_from_final_grade") is True,
        "post_to_sis": assignment.get("post_to_sis") is True,
        "due_at": assignment.get("due_at"),
        "url": assignment.get("html_url"),
        "checked_at": _now_iso(),
    }


def _verify_page(course_id: str, object_id: str, title: str) -> dict:
    if object_id:
        page, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/pages/{object_id}"
        )
        if error:
            return {"ok": False, "error": "Canvas could not be read for this check"}
        if not page:
            return {"ok": True, "found": False, "checked_at": _now_iso()}
    else:
        rows, error = canvas_client.canvas_get_all(
            f"/api/v1/courses/{course_id}/pages",
            {"per_page": 100, "search_term": title},
        )
        if error:
            return {"ok": False, "error": "Canvas could not be read for this check"}
        matches = [row for row in (rows or [])
                   if _normalize_title(row.get("title")) == _normalize_title(title)]
        if not matches:
            return {"ok": True, "found": False, "checked_at": _now_iso()}
        if len(matches) > 1:
            return {
                "ok": True, "found": False, "ambiguous": True,
                "ids": sorted(str(row.get("url") or "") for row in matches),
                "checked_at": _now_iso(),
            }
        page = matches[0]

    module_ids = page.get("module_ids")
    if module_ids is None:
        page_url = str(page.get("url") or "")
        module_ids, module_error = _module_ids_containing(
            course_id,
            lambda item, target=page_url: (
                str(item.get("type") or "") == "Page"
                and str(item.get("page_url") or "") == target
            ),
        )
        if module_error:
            module_ids = []
    return {
        "ok": True,
        "found": True,
        "url": page.get("url"),
        "title": page.get("title"),
        "published": page.get("published") is True,
        "module_ids": [str(value) for value in (module_ids or [])],
        "checked_at": _now_iso(),
    }


def _module_ids_containing(course_id: str, predicate) -> tuple[list[str], str | None]:
    """The one optional extra call: scan modules for one exact item (AC1)."""
    modules, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules",
        {"include[]": "items", "per_page": 100},
    )
    if error:
        return [], error
    found = []
    for module in modules or []:
        for item in module.get("items") or []:
            if predicate(item):
                found.append(str(module.get("id")))
                break
    return found, None


__all__ = ["verify_live"]
