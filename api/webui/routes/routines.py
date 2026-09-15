"""Routines router for Canvas Expert.

Local automation: run on demand, on launch, and every 30 min.
No external scheduler (locked-down district machines), no cloud, ever.
"""
import json
import threading
import time as _time
from datetime import datetime, timedelta

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from api.platform_services import config
from api import operational_log
from api.webui import readiness
from .routines_builtin import (
    _run_routine_sweep, _run_routine_download, _run_routine_curve,
    _run_routine_grading_debt, _run_routine_student_reports,
    _run_routine_sis_bridge_sync,
)


router = APIRouter(prefix="/api", tags=["routines"])

# --------------------------------------------------------------------------
# Routine definitions
# --------------------------------------------------------------------------

_ROUTINE_DEFS = {
    "sweep": {
        "label": "Auto-sweep late work",
        "writes": True,
        "default": {"enabled": False, "every_hours": 24,
                    "params": {"window_days": 30}},
    },
    "download": {
        "label": "Auto-download new student work",
        "writes": False,
        "default": {"enabled": False, "every_hours": 24,
                    "params": {"window_days": 14}},
    },
    "curve": {
        "label": "Auto-curve low assignment averages",
        "writes": True,
        "default": {"enabled": False, "every_hours": 168,
                    "params": {"floor": 80, "mode": "flag", "window_days": 30}},
    },
    "grading_debt": {
        "label": "Grading-debt report",
        "writes": False,
        "default": {"enabled": True, "every_hours": 24,
                    "params": {"school_days": 3}},
    },
    "student_reports": {
        "label": "Refresh monitored-student reports",
        "writes": False,
        "default": {"enabled": False, "every_hours": 168,
                    "params": {}},
    },
    "sis_bridge_sync": {
        "label": "Differentiated bridge grade sync",
        "writes": True,
        "default": {"enabled": False, "every_hours": 24, "params": {}},
    },
}

_ROUTINES_LOCK = threading.Lock()


def _routine_state(rid):
    saved = config.get_routine_states().get(rid, {})
    base = json.loads(json.dumps(_ROUTINE_DEFS[rid]["default"]))
    base["params"].update(saved.get("params", {}))
    for k in ("enabled", "every_hours", "last_run", "last_summary"):
        if k in saved:
            base[k] = saved[k]
    return base


def _routine_due(state):
    if not state.get("last_run"):
        return True
    try:
        last = datetime.fromisoformat(state["last_run"])
        hours = state.get("every_hours", 24)
        return datetime.now() >= last + timedelta(hours=hours)
    except (ValueError, TypeError):
        return True


def _routine(rid, label, writes=False, default=None):
    def deco(fn):
        if rid in _ROUTINE_DEFS:
            print(f"[routines] custom '{rid}' collides with a built-in — skipped")
            return fn
        _ROUTINE_DEFS[rid] = {"label": label, "writes": bool(writes),
                              "default": default or {"enabled": False, "every_hours": 24, "params": {}}, "custom": True}
        _ROUTINE_RUNNERS[rid] = fn
        return fn
    return deco

_ROUTINE_RUNNERS = {
    "sweep": _run_routine_sweep, "download": _run_routine_download,
    "curve": _run_routine_curve, "grading_debt": _run_routine_grading_debt,
    "student_reports": _run_routine_student_reports,
    "sis_bridge_sync": _run_routine_sis_bridge_sync,
}


from .routines_custom import load_custom_routines as _load_custom_routines_

def _load_custom_routines():
    _load_custom_routines_(_routine, _ROUTINE_DEFS, _ROUTINE_RUNNERS)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("/routines")
def api_routines():
    out = []
    for rid, meta in _ROUTINE_DEFS.items():
        st = _routine_state(rid)
        out.append({"id": rid, "label": meta["label"], "writes": meta["writes"],
                    "custom": meta.get("custom", False), "enabled": st["enabled"],
                    "every_hours": st["every_hours"], "params": st["params"],
                    "last_run": st.get("last_run"), "last_summary": st.get("last_summary"),
                    "due": _routine_due(st)})
    return JSONResponse({"ok": True, "routines": out})


@router.post("/routines/save")
def api_routines_save(routine_id: str = Form(...), patch: str = Form(...)):
    if routine_id not in _ROUTINE_DEFS:
        return JSONResponse({"ok": False, "error": "unknown routine"})
    try:
        p = json.loads(patch)
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": f"bad patch: {e}"})
    allowed = {k: v for k, v in p.items() if k in ("enabled", "every_hours", "params")}
    config.set_routine_state(routine_id, allowed)
    return JSONResponse({"ok": True})


@router.post("/routines/run")
def api_routines_run(ids: str = Form(""), force: bool = Form(False)):
    id_list = [i.strip() for i in ids.split(",") if i.strip()] or None
    if not _ROUTINES_LOCK.acquire(blocking=False):
        return JSONResponse({"ok": False, "error": "a routine run is already in progress"})
    try:
        report = {"ok": True, "ran": [], "skipped": []}
        for rid, meta in _ROUTINE_DEFS.items():
            if id_list is not None and rid not in id_list:
                continue
            state = _routine_state(rid)
            if not force and (not state["enabled"] or not _routine_due(state)):
                report["skipped"].append({"id": rid, "label": meta["label"],
                                          "reason": "disabled" if not state["enabled"] else "not due"})
                continue
            try:
                res = _ROUTINE_RUNNERS[rid](state["params"])
            except Exception as e:
                res = {"ok": False, "lines": [f"✗ crashed: {e}"], "summary": str(e)}
            report["ok"] = report["ok"] and res["ok"]
            report["ran"].append({"id": rid, "label": meta["label"], **res})
            config.set_routine_state(rid, {"last_run": datetime.now().isoformat(timespec="seconds"),
                                           "last_summary": res["summary"]})
        return JSONResponse(report)
    finally:
        _ROUTINES_LOCK.release()


# --------------------------------------------------------------------------
# Background thread
# --------------------------------------------------------------------------

def _routines_heartbeat():
    _time.sleep(90)
    while True:
        try:
            readiness.probe(force=True)
        except Exception as exc:
            operational_log.emit("routines.heartbeat_tick", "failed", error_class=type(exc))
        try:
            if config.token_is_set():
                _run_routines_bg()
        except Exception as exc:
            operational_log.emit("routines.heartbeat_tick", "failed", error_class=type(exc))
        _time.sleep(1800)


def _run_routines_bg():
    # Custom routines get the same three triggers as built-ins (Run now /
    # catch-up on launch / every 30 min here) -- see api/webui/README.md.
    # No parallel path: both kinds pass through the same _ROUTINE_DEFS /
    # _ROUTINE_RUNNERS registries.
    for rid, meta in _ROUTINE_DEFS.items():
        state = _routine_state(rid)
        if state["enabled"] and _routine_due(state):
            try:
                res = _ROUTINE_RUNNERS[rid](state["params"])
                config.set_routine_state(rid, {"last_run": datetime.now().isoformat(timespec="seconds"),
                                               "last_summary": res["summary"]})
            except Exception as exc:
                operational_log.emit("routines.scheduled_run", "failed", error_class=type(exc))
