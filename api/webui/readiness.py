"""Process-local readiness probes for the Desk/Workbench confidence strip."""

import importlib
import os
import tempfile
import threading
from datetime import datetime, timezone

from api.platform_services import config, workspace
from api.platform_services.canvas_client import canvas_get


_LOCK = threading.RLock()
_LAST_PROBE = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _component(status: str, code: str = "") -> dict:
    result = {"status": status}
    if code:
        result["code"] = code
    return result


def _code(error: str, status_code: int | None = None) -> str:
    text = str(error or "").lower()
    if status_code == 401 or "401" in text or "unauthorized" in text:
        return "unauthorized"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "unavailable" in text or "network" in text or "connection" in text:
        return "network"
    return "network"


def _probe_canvas() -> dict:
    if not config.token_is_set() or not config.get_canvas_base():
        return _component("unconfigured", "unconfigured")
    data, error = canvas_get("/api/v1/users/self/profile", timeout=5)
    if data is not None:
        return _component("ready")
    return _component("degraded", _code(error))


def _probe_privacy() -> dict:
    root = workspace.workspace_root()
    if not root:
        return _component("unconfigured", "unconfigured")
    system = workspace.system_root()
    probe_path = None
    try:
        os.makedirs(system, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=system,
            prefix=".readiness-", suffix=".probe", delete=False,
        ) as probe:
            probe.write("ok")
            probe.flush()
            os.fsync(probe.fileno())
            probe_path = probe.name
        importlib.import_module("api.feedback_safety")
        importlib.import_module("api.feedback_scrub")
        return _component("ready")
    except OSError:
        return _component("degraded", "workspace_unwritable")
    except Exception:
        return _component("degraded", "protection_unavailable")
    finally:
        if probe_path:
            try:
                os.unlink(probe_path)
            except OSError:
                pass


def _overall(components: dict) -> str:
    statuses = [item["status"] for item in components.values()]
    if any(status == "unknown" for status in statuses):
        return "unknown"
    if all(status == "unconfigured" for status in statuses):
        return "unconfigured"
    if all(status == "ready" for status in statuses):
        return "ready"
    return "degraded"


def _public(components: dict, checked_at: str | None) -> dict:
    return {
        "ok": True,
        "status": _overall(components),
        "checked_at": checked_at,
        "components": components,
    }


def snapshot() -> dict:
    with _LOCK:
        if _LAST_PROBE is None:
            unknown = {name: _component("unknown") for name in ("canvas", "privacy")}
            return _public(unknown, None)
        return _public(_LAST_PROBE["components"], _LAST_PROBE["checked_at"])


def probe(force: bool = False) -> dict:
    global _LAST_PROBE
    with _LOCK:
        if _LAST_PROBE is not None and not force:
            return snapshot()
        components = {
            "canvas": _probe_canvas(),
            "privacy": _probe_privacy(),
        }
        _LAST_PROBE = {"checked_at": _now(), "components": components}
        return snapshot()


def _reset_for_tests() -> None:
    global _LAST_PROBE
    with _LOCK:
        _LAST_PROBE = None
