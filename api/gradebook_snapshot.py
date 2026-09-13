"""Pure gradebook aggregation plus the shared Canvas snapshot loader."""
from __future__ import annotations

from types import SimpleNamespace

from api import gradebook_queries


def needs_grading(submission: dict) -> bool:
    """Return whether Canvas still marks a submitted row for teacher grading."""
    workflow_state = str(submission.get("workflow_state") or "").strip().casefold()
    return (
        not submission.get("excused")
        and bool(submission.get("submitted_at"))
        and workflow_state in {"submitted", "pending_review"}
    )


def build_snapshot(students, assignments, subs) -> dict:
    """Pure aggregation: preserve the existing route/MCP snapshot shape."""
    smap = {s["id"]: {"name": s.get("sortable_name") or s.get("name", ""),
                      "missing": 0, "late": 0, "ungraded": 0,
                      "score": 0.0, "possible": 0.0}
            for s in students}
    amap = {}
    for a in assignments:
        if not a.get("published", True):
            continue
        amap[a["id"]] = {
            "name":     a.get("name", ""),
            "due_at":   (a.get("due_at") or "")[:10],
            "points":   a.get("points_possible") or 0,
            "html_url": a.get("html_url", ""),
            "submitted": 0, "graded": 0, "missing": 0, "late": 0,
            "ungraded": 0, "partially_scored": 0,
            "score_sum": 0.0, "score_n": 0,
        }

    for sub in subs:
        a = amap.get(sub.get("assignment_id"))
        s = smap.get(sub.get("user_id"))
        if not a or not s:
            continue
        state = str(sub.get("workflow_state") or "").strip().casefold()
        ungraded = needs_grading(sub)
        if sub.get("excused"):
            continue
        if sub.get("submitted_at"):
            a["submitted"] += 1
        if sub.get("missing"):
            a["missing"] += 1
            if s:
                s["missing"] += 1
        if sub.get("late"):
            a["late"] += 1
            if s:
                s["late"] += 1
        if state == "graded" and sub.get("score") is not None:
            a["graded"] += 1
            a["score_sum"] += sub["score"]
            a["score_n"] += 1
            if s and a["points"]:
                s["score"] += sub["score"]
                s["possible"] += a["points"]
        if ungraded:
            a["ungraded"] += 1
            s["ungraded"] += 1
            if sub.get("score") is not None:
                a["partially_scored"] += 1

    out_assignments = []
    for aid, a in amap.items():
        avg = (round(a["score_sum"] / a["score_n"] / a["points"] * 100)
               if a["score_n"] and a["points"] else None)
        out_assignments.append({
            "id": str(aid), "name": a["name"], "due_at": a["due_at"],
            "points": a["points"], "html_url": a["html_url"],
            "submitted": a["submitted"], "graded": a["graded"],
            "ungraded": a["ungraded"],
            "partially_scored": a["partially_scored"],
            "missing": a["missing"], "late": a["late"], "avg_pct": avg,
        })
    out_assignments.sort(key=lambda a: a["due_at"] or "0000-00-00", reverse=True)

    out_students = []
    for sid, s in smap.items():
        pct = (round(s["score"] / s["possible"] * 100, 1)
               if s["possible"] else None)
        out_students.append({"name": s["name"], "user_id": str(sid),
                             "missing": s["missing"],
                             "late": s["late"], "ungraded": s["ungraded"],
                             "pct": pct})
    out_students.sort(key=lambda s: s["name"].lower())

    graded_pcts = [s["pct"] for s in out_students if s["pct"] is not None]
    class_avg = round(sum(graded_pcts) / len(graded_pcts), 1) if graded_pcts else None

    return {
        "ok": True,
        "class_avg": class_avg,
        "student_count": len(out_students),
        "total_missing": sum(s["missing"] for s in out_students),
        "total_ungraded": sum(a["ungraded"] for a in out_assignments),
        "assignments": out_assignments,
        "students": out_students,
    }


def load_snapshot(course_id: str, *, queries=None) -> tuple[dict | None, str | None]:
    """Load the three gradebook datasets in the established order.

    Mirror-first (design law #4: freshness visible, never silent): when no
    explicit ``queries`` override is given and the CanvasMirror is fresh
    enough to serve, read from it — otherwise fall back to live Canvas. The
    snapshot is labeled with ``source`` ("mirror" | "canvas") and
    ``synced_at`` ("" when live) either way.
    """
    source, synced_at = "canvas", ""
    if queries is None:
        from api.mirror import queries as mirror_queries
        mirror, synced_at = mirror_queries.snapshot_queries(course_id)
        if mirror is not None:
            queries, source = mirror, "mirror"
        else:
            synced_at = ""
    queries = queries or SimpleNamespace(
        course_students=gradebook_queries.course_students,
        course_assignments=gradebook_queries.course_assignments,
        course_submissions=gradebook_queries.course_submissions,
    )
    students, error = queries.course_students(course_id)
    if error:
        return None, error
    assignments, error = queries.course_assignments(course_id)
    if error:
        return None, error
    subs, error = queries.course_submissions(course_id)
    if error:
        return None, error
    snapshot = build_snapshot(students, assignments, subs)
    snapshot["source"] = source
    snapshot["synced_at"] = synced_at
    return snapshot, None
