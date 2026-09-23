"""Built-in routine runners for Canvas Expert.

Moved out of routines.py for maintainability.
No route handlers — only runner functions and their tight helpers.
"""
import json
import uuid as _uuid
from datetime import datetime, timedelta

from api.platform_services import config
from .. import mirror_service
from api.platform_services.canvas_client import canvas_get, canvas_get_all, _canvas_send
from ..gradebook_service import _load_curve_events, _save_curve_events, _apply_curve_model
from ..mirror_reads import students_or_live, submissions_or_live
from api import operational_log, routine_reads, sis_grade_bridge, student_packet
from api.powergrader import assignment_refresh


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def _run_routine_download(params):
    window = int(params.get("window_days", 14))
    cutoff = (datetime.now().date() - timedelta(days=window)).isoformat()
    if not config.get_token():
        return {"ok": False, "lines": ["✗ Canvas connection is unavailable"], "summary": "unavailable"}
    DOWNLOADABLE = {"online_text_entry", "online_upload", "online_url", "discussion_topic"}
    lines, ok, total, current, incomplete, failed = [], True, 0, 0, 0, 0
    for c in config.active_courses():
        asgns_result = routine_reads.read_scope(
            "assignments", c["id"], live_reader=canvas_get_all)
        if not asgns_result["ok"]:
            lines.append("✗ assignment listing failed")
            ok = False
            continue
        asgns = asgns_result["records"]
        ids = [str(a["id"]) for a in (asgns or [])
               if (a.get("due_at") or "")[:10] >= cutoff
               and set(a.get("submission_types") or []) & DOWNLOADABLE]
        if not ids:
            lines.append("· no eligible assignments in window")
            continue
        for assignment_id in ids:
            total += 1
            _, _, result = assignment_refresh.refresh_assignment(
                str(c["id"]), assignment_id, session_id=f"routine-{_uuid.uuid4()}")
            status = result.get("status")
            if result.get("error") or status == "unavailable":
                failed += 1; ok = False
            elif status == "current":
                current += 1
            else:
                incomplete += 1; ok = False
    lines.append(f"Focused refresh: {current} current, {incomplete} incomplete, {failed} failed.")
    return {"ok": ok, "lines": lines,
            "summary": f"{total} assignment refresh(es): {current} current, {incomplete} incomplete, {failed} failed"}


# --------------------------------------------------------------------------
# Differentiated bridge grade sync
# --------------------------------------------------------------------------

def _run_routine_sis_bridge_sync(_params):
    """Score reconciliation rule (contract §6): the highest of every tier
    source's final score and the bridge's own current score is canon. A
    bridge score is never lowered and a tier source is never written."""
    lines = []
    ok = True
    totals = {
        "families": 0,
        "raised_scores": 0,
        "copied_excused": 0,
        "already_matching": 0,
        "held": 0,
        "attention_families": 0,
    }
    for course in config.active_courses():
        course_id = str(course.get("id") or "").strip()
        try:
            registrations = config.list_sis_grade_bridges(course_id)
        except Exception:
            lines.append("✗ linked bridge families could not be read")
            totals["attention_families"] += 1
            ok = False
            continue
        for registration in registrations:
            family_title = str(registration.get("family_title") or "").strip()
            totals["families"] += 1
            preview = sis_grade_bridge.preview_sis_grade_bridge(
                course_id, family_title, write_origin="routine"
            )
            if not preview.get("ok"):
                lines.append(f"✗ {family_title}: {preview.get('error', 'preview failed')}")
                totals["attention_families"] += 1
                ok = False
                continue
            result = sis_grade_bridge.apply_sis_grade_bridge(
                preview["operation_id"], preview["batch_id"], preview["review_digest"]
            )
            counts = result.get("counts") or {}
            for key in ("raised_scores", "copied_excused", "already_matching", "held"):
                totals[key] += int(counts.get(key) or 0)
            attention = bool(counts.get("held") or not result.get("ok"))
            if attention:
                totals["attention_families"] += 1
            if not result.get("ok"):
                ok = False
            marker = "⚑" if attention else "✓"
            lines.append(
                f"{marker} {family_title}: "
                f"{int(counts.get('raised_scores') or 0)} raised, "
                f"{int(counts.get('copied_excused') or 0)} excused, "
                f"{int(counts.get('already_matching') or 0)} already canon, "
                f"{int(counts.get('held') or 0)} held"
            )
    if not totals["families"] and not lines:
        lines.append("· no linked differentiated families in Current courses")
    summary = (
        f"{totals['families']} families: {totals['raised_scores']} raised, "
        f"{totals['copied_excused']} excused, "
        f"{totals['already_matching']} already canon, "
        f"{totals['held']} held"
    )
    return {"ok": ok, "lines": lines, "summary": summary, "counts": totals}


# --------------------------------------------------------------------------
# Curve
# --------------------------------------------------------------------------

def _curve_apply_core(course_id, assignment_id, curve_type, settings, rows):
    a, err = canvas_get(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
    if err:
        return False, None, [{"error": err}]
    # Audit-only baseline (recorded on the curve event as score_at_apply_time,
    # not used to compute the curved value written below) — safe to serve
    # from the mirror. course_submissions() returns all of the course's
    # submissions, so filter down to this assignment.
    current_subs, _current_err, _source = submissions_or_live(course_id)
    current_score_by_uid = {str(sub["user_id"]): sub.get("score")
                            for sub in (current_subs or [])
                            if str(sub.get("assignment_id")) == str(assignment_id)}
    event_id = f"curve_{_uuid.uuid4().hex[:8]}"
    event_students, push_results = [], []
    for r in rows:
        uid = str(r["user_id"])
        curved = r["curved_score"]
        _, err = _canvas_send(
            "PUT",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{uid}",
            {"submission": {"posted_grade": str(curved)}})
        push_results.append({"student": r.get("student_name", uid),
                             "original": r["original_score"],
                             "curved": curved, "ok": not err, "error": err})
        event_students.append({"user_id": uid, "student_name": r.get("student_name", uid),
                               "original_score": r["original_score"], "curved_score": curved,
                               "score_at_apply_time": current_score_by_uid.get(uid),
                               "changed": r.get("changed", True)})
    events = _load_curve_events()
    events.append({"id": event_id, "course_id": str(course_id),
                   "assignment_id": str(assignment_id),
                   "assignment_name": a.get("name", assignment_id),
                   "curve_type": curve_type, "curve_settings": settings,
                   "applied_at": datetime.now().isoformat(timespec="seconds"),
                   "reverted": False, "students": event_students})
    _save_curve_events(events)
    all_ok = all(r["ok"] for r in push_results)
    return all_ok, event_id, push_results


def _run_routine_curve(params):
    floor = float(params.get("floor", 80))
    mode = params.get("mode", "flag")
    window = int(params.get("window_days", 30))
    cutoff = (datetime.now().date() - timedelta(days=window)).isoformat()
    curved_already = {(e["course_id"], e["assignment_id"])
                      for e in _load_curve_events() if not e.get("reverted")}
    lines, ok, flagged, applied = [], True, 0, 0
    for c in config.active_courses():
        cid = str(c["id"])
        course_applied = 0
        asgns, err = canvas_get_all(f"/api/v1/courses/{cid}/assignments", {"per_page": 100})
        if err:
            lines.append(f"✗ {c['nickname']}: {err}")
            ok = False
            continue
        for a in (asgns or []):
            pts = a.get("points_possible") or 0
            if not a.get("published", True) or pts <= 0:
                continue
            if (a.get("due_at") or "")[:10] < cutoff:
                continue
            aid = str(a["id"])
            if (cid, aid) in curved_already:
                continue
            subs, err = canvas_get_all(f"/api/v1/courses/{cid}/assignments/{aid}/submissions",
                                        {"per_page": 100})
            if err:
                continue
            scores = [s_.get("score") for s_ in (subs or [])
                      if s_.get("workflow_state") == "graded" and s_.get("score") is not None]
            if len(scores) < 3:
                continue
            avg_pct = sum(scores) / len(scores) / pts * 100
            if avg_pct >= floor:
                continue
            flagged += 1
            if mode != "apply":
                lines.append(f"⚑ {c['nickname']}: \"{a.get('name','')}\" avg {avg_pct:.1f}% < {floor:g}%")
                continue
            students, err, _source = students_or_live(cid)
            name_by_id = {str(st["id"]): (st.get("sortable_name") or st.get("name", ""))
                          for st in (students or [])}
            scored = [{"user_id": str(s_["user_id"]),
                       "student_name": name_by_id.get(str(s_["user_id"]), str(s_["user_id"])),
                       "score": s_.get("score")}
                      for s_ in (subs or []) if s_.get("workflow_state") == "graded"]
            settings = {"target_avg_pct": floor, "do_no_harm": True}
            rows = _apply_curve_model(scored, "target_average", settings, pts)
            rows = [r for r in rows if r["changed"]]
            if not rows:
                continue
            all_ok, event_id, _ = _curve_apply_core(cid, aid, "target_average", settings, rows)
            ok = ok and all_ok
            applied += 1
            course_applied += 1
            lines.append(f"✓ {c['nickname']}: curved \"{a.get('name','')}\" {avg_pct:.1f}% → {floor:g}% ({len(rows)} students)")
        # Coalesced write-through: after this course's whole curve batch, fire one
        # per-course submissions refresh so mirror-backed grade reads pick up the
        # curved scores. Fire-and-forget (self-guarding, swallows its own errors);
        # the heartbeat's full delta pass repairs any miss — same contract as the
        # A course that curved nothing fires no write-through refresh.
        if course_applied:
            try:
                mirror_service.notify_course_changed(cid)
            except Exception as exc:
                operational_log.emit("mirror.notify_course_changed", "failed", error_class=type(exc))
    summary = f"{applied} assignment(s) curved" if mode == "apply" else f"{flagged} assignment(s) flagged below {floor:g}%"
    return {"ok": ok, "lines": lines or ["· all assignment averages at or above the floor"], "summary": summary}


# --------------------------------------------------------------------------
# Student reports
# --------------------------------------------------------------------------

def _run_routine_student_reports(params):
    mon = config.get_monitored_students()
    if not mon:
        return {"ok": True, "lines": ["· no monitored students"], "summary": "0 students"}
    base, token = config.get_canvas_base(), config.get_token()
    if not token:
        return {"ok": False, "lines": ["✗ no token"], "summary": "no token"}
    root = config.get_student_reports_root()
    courses = [{"id": c["id"], "name": c["name"]} for c in config.active_courses()]
    events = _load_curve_events()
    lines, ok, n = [], True, 0
    for uid, v in mon.items():
        try:
            for line in student_packet.build_packet(uid, v["name"], student_packet.SECTIONS, courses,
                                                    base, token, root, events, skip_unchanged=True):
                if line.startswith("FOLDER:"):
                    continue
                if line.startswith("!!"):
                    ok = False
                lines.append("  " + line)
            n += 1
        except Exception as e:
            ok = False
            lines.append(f"✗ {v['name']}: {e}")
    return {"ok": ok, "lines": lines, "summary": f"{n} monitored student packet(s) refreshed"}
