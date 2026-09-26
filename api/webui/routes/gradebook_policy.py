"""Gradebook late-policy and grading-policy routes."""
import json

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from api.platform_services.canvas_client import canvas_get, _canvas_send
from api import operational_log
from api.mirror import store as mirror_store
from api.platform_services import config

router = APIRouter(tags=["gradebook"])


@router.get("/api/late-policy")
def get_late_policy(course_id: str):
    """Mirror-first, student-free late-policy display (acquire-on-read,
    mirroring ``list_groups`` in ``courses.py``): serve the local projection
    when current; otherwise fall back to the existing live Canvas read and
    seed the projection for next time. The 404 -> ``{"policy": None}`` and
    error behaviors are unchanged; only ``source``/``synced_at`` are added,
    matching the grade snapshot's freshness convention."""
    document = mirror_store.read_late_policy(course_id)
    if mirror_store.late_policy_is_current(document):
        return JSONResponse({"ok": True, "policy": document["policy"],
                             "source": "mirror", "synced_at": document["synced_at"]})

    data, err = canvas_get(f"/api/v1/courses/{course_id}/late_policy")
    if err and "404" in err:
        return JSONResponse({"ok": True, "policy": None,
                             "source": "canvas", "synced_at": ""})
    if err:
        return JSONResponse({"ok": False, "error": err})
    raw_policy = data.get("late_policy")
    if isinstance(raw_policy, dict):
        try:
            mirror_store.write_late_policy(course_id, raw_policy)
        except (OSError, ValueError) as exc:
            operational_log.emit("gradebook.late_policy_cache_write", "failed", error_class=type(exc))
    return JSONResponse({"ok": True, "policy": raw_policy,
                         "source": "canvas", "synced_at": ""})


@router.post("/api/late-policy/apply")
def apply_late_policy(courses: str = Form(...), policy: str = Form(...)):
    """Create or update the Canvas late policy in every target course."""
    try:
        targets = json.loads(courses)
        p = json.loads(policy)
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": f"bad request: {e}"})
    if not targets:
        return JSONResponse({"ok": False, "error": "no courses selected"})

    results = []
    for t in targets:
        cid = str(t.get("id", ""))
        cname = t.get("name", f"course {cid}")
        existing, gerr = canvas_get(f"/api/v1/courses/{cid}/late_policy")
        if gerr and "404" not in gerr:
            results.append({"course_name": cname, "ok": False, "error": gerr})
            continue
        method = "PATCH" if (existing and not gerr) else "POST"
        _, err = _canvas_send(method, f"/api/v1/courses/{cid}/late_policy",
                              {"late_policy": p})
        ok = not err
        if ok:
            # Post-apply reconcile (locked decision: writes stay live; local
            # policy never authorizes an apply). Invalidate rather than
            # blind-refresh from the submitted payload — Canvas may round or
            # default fields we did not send, so only a live re-read may ever
            # mark this scope "current" again. A reconcile failure (raised
            # OSError/ValueError) is swallowed here and left stale/absent —
            # never coerced to current.
            try:
                mirror_store.invalidate_late_policy(cid)
            except (OSError, ValueError) as exc:
                operational_log.emit("gradebook.late_policy_cache_invalidate", "failed", error_class=type(exc))
        results.append({"course_name": cname, "ok": ok, "error": err,
                        "title": f"late policy {'updated' if method == 'PATCH' else 'created'}"})
    return JSONResponse({"ok": all(r["ok"] for r in results), "results": results})


# --- Grading policy: effort credit and teacher-confirmed late days ----------

def _grading_policy_warnings(course_id: str, missing_percent) -> list:
    """Advisory-only warnings from the cached course late policy, never a
    live Canvas call. No cached policy means no warning."""
    document = mirror_store.read_late_policy(course_id)
    policy = document.get("policy") if isinstance(document, dict) else None
    if not isinstance(policy, dict):
        return []
    warnings = []
    if policy.get("missing_submission_deduction_enabled"):
        warnings.append(
            "Canvas's missing-submission policy is on for this course, so "
            "Canvas may fill in missing work on its own overnight."
        )
    floor = policy.get("late_submission_minimum_percent")
    if (missing_percent is not None and policy.get("late_submission_minimum_percent_enabled")
            and floor is not None):
        try:
            if float(floor) < float(missing_percent):
                warnings.append(
                    "Canvas's lowest possible grade is set below the missing "
                    "value entered here."
                )
        except (TypeError, ValueError):
            pass
    return warnings


@router.get("/api/grading-policy")
def get_grading_policy(course_id: str):
    policy = config.get_grading_policy(course_id)
    missing_percent = policy.get("missing_percent") if policy else None
    return JSONResponse({"ok": True, "policy": policy,
                         "warnings": _grading_policy_warnings(course_id, missing_percent)})


@router.post("/api/grading-policy")
def save_grading_policy(course_id: str = Form(...), policy: str = Form(...)):
    try:
        parsed = json.loads(policy)
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": f"bad request: {e}"})
    if not parsed:
        config.set_grading_policy(course_id, None)
        return JSONResponse({"ok": True, "policy": None, "warnings": []})
    try:
        floor_percent = int(parsed["floor_percent"])
        missing_percent = int(parsed["missing_percent"])
        sweep_after_school_days = int(parsed["sweep_after_school_days"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"ok": False,
                             "error": "Floor percent, missing percent, and sweep days must be whole numbers."})
    if not (0 <= missing_percent <= floor_percent <= 100):
        return JSONResponse({"ok": False,
                             "error": "The floor percent must be at or above the missing percent, both between 0 and 100."})
    if not (1 <= sweep_after_school_days <= 60):
        return JSONResponse({"ok": False, "error": "Sweep days must be between 1 and 60."})
    value = {"floor_percent": floor_percent, "missing_percent": missing_percent,
             "sweep_after_school_days": sweep_after_school_days}
    config.set_grading_policy(course_id, value)
    return JSONResponse({"ok": True, "policy": value,
                         "warnings": _grading_policy_warnings(course_id, missing_percent)})


@router.get("/api/no-school-dates")
def get_no_school_dates():
    return JSONResponse({"ok": True, "dates": config.get_no_school_dates()})


@router.post("/api/no-school-dates")
def save_no_school_dates(dates: str = Form(...)):
    try:
        parsed = json.loads(dates)
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": f"bad request: {e}"})
    if not isinstance(parsed, list):
        return JSONResponse({"ok": False, "error": "dates must be a list"})
    config.set_no_school_dates(parsed)
    return JSONResponse({"ok": True, "dates": config.get_no_school_dates()})
