"""Custom routine loader for Canvas Expert.

Moved out of routines.py for maintainability.
"""
import glob
import os
import traceback

from api.platform_services import config
from api.platform_services.canvas_client import canvas_get, canvas_get_all, _canvas_send
from ..deps import _CUSTOM_DIR
from api import routine_reads
from datetime import datetime, timedelta


def _canvas_read(scope, course_id):
    """Custom-routine ``canvas_read`` tool — delegates to the typed mirror-first
    reader. Returns the full ``{ok, records, error, source, synced_at,
    generation}`` dict so freshness is available without a second read."""
    return routine_reads.read_scope(scope, course_id, live_reader=canvas_get_all)


def _routine_sdk(routine_fn):
    """Build the SDK dict passed to custom routine files at load time."""
    return {
        "routine": routine_fn,
        "canvas_get": canvas_get,
        "canvas_get_all": canvas_get_all,
        "canvas_send": _canvas_send,
        "canvas_read": _canvas_read,
        "active_courses": config.active_courses,
        "datetime": datetime,
        "timedelta": timedelta,
    }


def load_custom_routines(routine_fn, _ROUTINE_DEFS, _ROUTINE_RUNNERS):
    """Load custom routine .py files from the custom routines directory."""
    if not os.path.isdir(_CUSTOM_DIR):
        return
    for path in sorted(glob.glob(os.path.join(_CUSTOM_DIR, "*.py"))):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            g = _routine_sdk(routine_fn)
            g["__name__"] = "custom_routine_" + os.path.splitext(os.path.basename(path))[0]
            g["__file__"] = path
            exec(compile(src, path, "exec"), g)
        except Exception:
            print(f"[routines] failed to load {os.path.basename(path)}:\n" + traceback.format_exc())