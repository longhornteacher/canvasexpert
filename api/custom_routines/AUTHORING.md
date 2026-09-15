# Custom Routines — LLM Authoring Guide

You are helping a teacher write a **custom routine** for Canvas Expert. A routine is a
Python function that checks or modifies Canvas data on a schedule. Drop the finished file
into `api/custom_routines/` and restart the app — it appears in the Dashboard Routines table
alongside the built-ins.

## Runner contract

Every routine function follows this exact shape:

```python
def my_routine(params: dict) -> dict:
    # ...
    return {"ok": bool, "lines": list[str], "summary": str}
```

- `params` — the user's saved settings (editable from the dashboard).
- Return `{"ok": True, "lines": […], "summary": "…"}` on success, `{"ok": False, …}` on failure.
- `lines` — one string per course (or per notable event). Prefix markers:
  `✓` (success/ok), `✗` (error), `⚑` (flag/warning), `·` (info/no-op).
- `summary` — one short line shown in the "Last run" column and the Activity Log.

## The `@routine` decorator

```python
@routine(rid, label, writes=False, default=None)
def run(params):
    ...
```

| Argument | Description |
|---|---|
| `rid` | Unique id — must not collide with built-ins (`sweep`, `download`, `curve`, `grading_debt`, `student_reports`, `sis_bridge_sync`) |
| `label` | Human-readable name shown in the Routines table |
| `writes` | Set to `True` if the routine calls `canvas_send` (shows the ✎ marker) |
| `default` | Dict with `enabled`, `every_hours`, and `params` — the user's starting config |

Example default:
```python
default={"enabled": False, "every_hours": 24, "params": {"min_missing": 3}}
```

Only one decorator argument is required: `rid`. The decorated function name doesn't matter.

## Injected names (no imports needed)

Everything below is already in scope when your file runs. There are no imports to write.

| Name | Signature | Returns |
|---|---|---|
| `canvas_read(scope, course_id)` | `"assignments"\|"roster"\|"submissions", str` | `{ok, records, error, source, synced_at, generation}` — mirror-first, falls back live |
| `canvas_get(path)` | `str` — e.g. `"/api/v1/courses/123"` | `(data, err)` — `data` is parsed JSON or `None` |
| `canvas_get_all(path, params)` | `str, dict` — paginated GET | `([…], err)` — concatenated list |
| `canvas_send(method, path, body)` | `"PUT"|"POST"|"PATCH", str, dict` | `(data, err)` — `data` is parsed JSON or `{}` |
| `active_courses()` | no args | `[{id, name, nickname, active}]` — only bookmarked+active courses |
| `sweep_settings()` | no args | `{honor_extra_time: bool}` |
| `combined_calendar(date_from, date_to)` | optional `date_from`, `date_to` YYYY-MM-DD (whole configured year if omitted) | success: `{ok: true, readiness: {…}, no_count_dates: [str], grading_periods: […], events: […]}`; failure: `{ok: false, state, problems: […], repair_url, readiness: {…}}` — always check `ok` before trusting `no_count_dates` |
| `school_days_late(due_dt, sub_dt, no_count_dates)` | two datetimes + a `{date_str, …}` set | `int` — school-day count |
| `parse_iso_local(s)` | Canvas ISO string | `datetime` or `None` |
| `datetime` | Python's `datetime` module | — |
| `timedelta` | Python's `timedelta` class | — |

**Important:** `canvas_read` is the preferred way to read course assignments, roster, or
submissions for an ordinary report — it serves the local mirror when current and falls back
live only when the mirror is missing, stale, or corrupt, always labeling `source`. Use
`canvas_get`/`canvas_get_all` only for endpoints outside those three scopes.
`canvas_get_all` follows Canvas pagination (Link headers) automatically.
`canvas_send` is the **write** verb — if your routine calls it, set `writes=True`.

## Rules

1. **Set `writes=True` if the routine calls `canvas_send`.** This shows the user a ✎
   marker so they know it modifies Canvas data.
2. **Read-only routines are the safe default — prefer them.** Flag/check/report without
   writing. The user can always enable `apply` mode later.
3. **Use the ✗ ✓ ⚑ · markers** in `lines` for consistent dashboard rendering.
4. **Wrap numbers** (`\u2265` for ≥) to avoid encoding issues.

## Worked example — missing-work nudge

This read-only routine checks how many students haven't submitted past-due assignments:

```python
@routine("missing_work",
         label="Missing-work nudge",
         writes=False,
         default={"enabled": False, "every_hours": 24, "params": {"min_missing": 3}})
def run(params):
    min_missing = int(params.get("min_missing", 3))
    today = datetime.now().date().isoformat()
    lines, ok, total = [], True, 0
    for c in active_courses():
        cid = str(c["id"])
        asgns = canvas_read("assignments", cid)
        if not asgns["ok"]:
            lines.append(f"\u2717 {c['nickname']}: {asgns['error']}"); ok = False; continue
        due_ids = {str(a["id"]) for a in asgns["records"]
                   if (a.get("due_at") or "")[:10] and (a.get("due_at") or "")[:10] <= today}
        subs = canvas_read("submissions", cid)
        if not subs["ok"]:
            lines.append(f"\u2717 {c['nickname']}: {subs['error']}"); ok = False; continue
        missing = {}
        for s_ in subs["records"]:
            if (str(s_.get("assignment_id")) in due_ids
                    and s_.get("workflow_state") == "unsubmitted"):
                missing[s_["user_id"]] = missing.get(s_["user_id"], 0) + 1
        flagged = [u for u, n in missing.items() if n >= min_missing]
        total += len(flagged)
        lines.append(
            f"\u2691 {c['nickname']}: {len(flagged)} student(s) with \u2265{min_missing} missing"
            if flagged else f"\u2713 {c['nickname']}: none at the threshold")
    return {"ok": ok, "lines": lines,
            "summary": f"{total} student(s) flagged across active courses"}
```

Copy this as a starting template, then change the logic to do what you need.
