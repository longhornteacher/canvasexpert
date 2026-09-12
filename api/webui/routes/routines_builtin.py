"""Built-in routine runners for Canvas Expert.

Moved out of routines.py for maintainability.
No route handlers — only runner functions and their tight helpers.
"""
import json
import uuid as _uuid
from datetime import datetime, timedelta

from api.platform_services import config
from .. import deps, mirror_service, school_calendar
from api.platform_services.canvas_client import canvas_get, canvas_get_all, _canvas_send
from ..gradebook_service import _load_curve_events, _save_curve_events, _apply_curve_model
from ..mirror_reads import students_or_live, submissions_or_live
from ..schooldays import _school_days_late, parse_iso_local
from api import operational_log, routine_reads, student_packet
from api.powergrader import assignment_refresh

from api.nq_report import html_to_text


# --------------------------------------------------------------------------
# Sweep
# --------------------------------------------------------------------------

def _run_routine_sweep(params):
    from ..schooldays import school_days_late_detail
    window = int(params.get("window_days", 30))
    cutoff = (datetime.now().date() - timedelta(days=window)).isoformat()

    lines, ok = [], True
    per_course = {}
    span_start = span_end = None
    for c in config.active_courses():
        cid = str(c["id"])
        asgns, err = canvas_get_all(f"/api/v1/courses/{cid}/assignments", {"per_page": 100})
        if err:
            lines.append(f"✗ {c['nickname']}: {err}")
            ok = False
            continue
        amap = {a["id"]: a for a in (asgns or [])
                if a.get("published", True) and (a.get("points_possible") or 0) > 0
                and ((a.get("due_at") or "")[:10] >= cutoff)}
        if not amap:
            lines.append(f"· {c['nickname']}: nothing due in window")
            continue

        subs, err = canvas_get_all(f"/api/v1/courses/{cid}/students/submissions",
                                    {"student_ids[]": "all", "per_page": 100}, timeout=90)
        if err:
            lines.append(f"✗ {c['nickname']}: {err}")
            ok = False
            continue

        students, err, _source = students_or_live(cid)
        if err:
            lines.append(f"✗ {c['nickname']}: {err}")
            ok = False
            continue
        name_by_id = {str(s["id"]): (s.get("sortable_name") or s.get("name", ""))
                      for s in students}

        extra = {str(e["id"]): int(e.get("days", 1))
                 for e in config.get_extra_time(cid)} if params.get("honor_extra_time", True) else {}

        candidates = []
        for sub in (subs or []):
            if sub.get("excused") or not sub.get("submitted_at"):
                continue
            a = amap.get(sub.get("assignment_id"))
            if not a:
                continue
            due = parse_iso_local(sub.get("cached_due_date") or a.get("due_at"))
            submitted = parse_iso_local(sub.get("submitted_at"))
            if not due or not submitted:
                continue
            candidates.append((sub, a, due, submitted))
            span_start = due.date() if span_start is None else min(span_start, due.date())
            span_end = submitted.date() if span_end is None else max(span_end, submitted.date())
        per_course[cid] = (c, candidates, name_by_id, extra)

    no_count: set[str] = set()
    if span_start is not None:
        bell_schedules, _problems = deps.load_bell_schedules()
        result = school_calendar.resolve_instructional_range(
            span_start.isoformat(), span_end.isoformat(), set(bell_schedules))
        if result["state"] != "ready":
            return {"ok": False, "lines": [f"✗ {school_calendar.CALENDAR_REPAIR_MESSAGE}"],
                    "summary": "calendar not configured"}
        no_count = set(result["no_count_dates"])

    total = 0
    for cid, (c, candidates, name_by_id, extra) in per_course.items():
        for sub, a, due, submitted in candidates:
            raw_days, excluded = school_days_late_detail(due, submitted, no_count)
            if raw_days <= 0:
                continue
            uid = str(sub.get("user_id"))
            school_days = max(raw_days - extra.get(uid, 0), 0)
            if school_days <= 0:
                continue

            _, err = _canvas_send("PUT",
                f"/api/v1/courses/{cid}/assignments/{a['id']}/submissions/{uid}",
                {"submission": {"late_policy_status": "late",
                                "seconds_late_override": school_days * 86400}})
            if err:
                lines.append(f"✗ {name_by_id.get(uid, uid)}/{a.get('name','')}: {err}")
                ok = False
            else:
                total += 1
            lines.append(f"✓ {name_by_id.get(uid, uid)}/{a.get('name','')}: {school_days} school days late")

    return {"ok": ok, "lines": lines,
            "summary": f"{total} late submission(s) corrected"}


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
# Grading debt
# --------------------------------------------------------------------------

def _run_routine_grading_debt(params):
    min_days = int(params.get("school_days", 3))
    now_dt = datetime.now().astimezone()
    lines, ok = [], True
    per_course = {}
    span_start = None
    for c in config.active_courses():
        cid = str(c["id"])
        asgns_result = routine_reads.read_scope(
            "assignments", cid, live_reader=canvas_get_all)
        if not asgns_result["ok"]:
            lines.append(f"✗ {c['nickname']}: {asgns_result['error']}")
            ok = False
            continue
        aname = {str(a["id"]): a.get("name", "") for a in (asgns_result["records"] or [])}
        subs_result = routine_reads.read_scope(
            "submissions", cid, live_reader=canvas_get_all)
        if not subs_result["ok"]:
            lines.append(f"✗ {c['nickname']}: {subs_result['error']}")
            ok = False
            continue

        candidates = []
        for s_ in (subs_result["records"] or []):
            if s_.get("workflow_state") != "submitted" or not s_.get("submitted_at"):
                continue
            sub_dt = parse_iso_local(s_["submitted_at"])
            if not sub_dt:
                continue
            candidates.append((sub_dt, aname.get(str(s_.get("assignment_id")), "?")))
            span_start = sub_dt.date() if span_start is None else min(span_start, sub_dt.date())
        per_course[cid] = (c, candidates)

    no_count: set[str] = set()
    if span_start is not None:
        bell_schedules, _problems = deps.load_bell_schedules()
        result = school_calendar.resolve_instructional_range(
            span_start.isoformat(), now_dt.date().isoformat(), set(bell_schedules))
        if result["state"] != "ready":
            return {"ok": False, "lines": [f"✗ {school_calendar.CALENDAR_REPAIR_MESSAGE}"],
                    "summary": "calendar not configured"}
        no_count = set(result["no_count_dates"])

    total = 0
    for cid, (c, candidates) in per_course.items():
        debts = []
        for sub_dt, name in candidates:
            days = _school_days_late(sub_dt, now_dt, no_count)
            if days >= min_days:
                debts.append((days, name))
        total += len(debts)
        if debts:
            debts.sort(reverse=True)
            oldest = ", ".join(f"\"{n}\" ({d}d)" for d, n in debts[:3])
            lines.append(f"⚑ {c['nickname']}: {len(debts)} ungraded > {min_days} school days — oldest: {oldest}")
        else:
            lines.append(f"✓ {c['nickname']}: no grading debt")
    return {"ok": ok, "lines": lines,
            "summary": f"{total} ungraded submission(s) older than {min_days} school days"}


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
