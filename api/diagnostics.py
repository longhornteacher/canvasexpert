"""Privacy-minimized local health and support artifacts."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from api import __version__, operational_log, runtime_paths
from api.mcp_server.contract import TOOL_SCHEMA_VERSION
from api.platform_services import config, workspace


__all__ = ["health_snapshot", "build_support_bundle"]

_PROXY_ENV_NAMES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)
_CLEANUP_EVENT = "diagnostics.workspace_probe_cleanup"


def _canvas_configured() -> bool:
    try:
        return bool(config.get_canvas_base() and config.token_is_set())
    except Exception:
        return False


def _workspace_status() -> tuple[bool, bool, bool]:
    try:
        root_value = workspace.workspace_root()
    except Exception:
        return False, False, False
    if not root_value:
        return False, False, False

    root = Path(root_value)
    try:
        system_value = workspace.system_root()
        catalog_value = workspace.canvas_catalog_root()
    except Exception:
        return True, False, False
    if not system_value:
        return True, False, bool(catalog_value and Path(catalog_value).is_dir())

    system = Path(system_value)
    system_existed = system.exists()
    probe_path: Path | None = None
    cleanup_failed = False
    writable = False
    try:
        system.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=system,
            prefix=".diagnostic-", suffix=".probe", delete=False,
        ) as probe:
            probe_path = Path(probe.name)
            probe.flush()
            os.fsync(probe.fileno())
        writable = True
    except Exception:
        writable = False
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink()
            except Exception:
                cleanup_failed = True
        if not system_existed:
            try:
                if system.exists() and not any(system.iterdir()):
                    system.rmdir()
            except Exception:
                cleanup_failed = True

    if cleanup_failed:
        writable = False
        try:
            operational_log.emit(_CLEANUP_EVENT, "failed", error_class="OSError")
        except Exception:
            pass
    catalog_present = bool(catalog_value and Path(catalog_value).is_dir())
    return True, writable, catalog_present


def _mcp_status() -> tuple[bool, bool]:
    try:
        importlib.import_module("api.mcp_server.server")
        importable = True
    except Exception:
        importable = False
    try:
        entrypoint_present = Path(runtime_paths.mcp_entrypoint()).is_file()
    except Exception:
        entrypoint_present = False
    return importable, entrypoint_present


def _pseudonym_registry_status() -> dict:
    """Words total/assigned/remaining and low_runway for this machine's
    Identity Vault, so a teacher running low on the pseudonym registry finds
    out here -- in the same place every other quiet health fact lives --
    long before `PseudonymRegistryError` would actually stop a new student
    from being pseudonymized. See `feedback_vault.registry_runway` for the
    forecast itself; this just locates this machine's vault and never raises,
    matching every other helper in this module.
    """
    try:
        from api import feedback_vault
    except Exception:
        return {
            "configured": False, "words_total": 0, "words_assigned": 0,
            "words_remaining": 0, "low_runway": False,
        }
    try:
        root = workspace.identity_vault_dir()
    except Exception:
        root = None
    if not root:
        return {"configured": False, **feedback_vault.registry_runway(0)}
    try:
        vault = feedback_vault.Vault(os.path.join(root, "vault.json"))
        runway = vault.registry_runway()
    except Exception:
        return {"configured": False, **feedback_vault.registry_runway(0)}
    return {"configured": True, **runway}


def health_snapshot() -> dict:
    """Return public booleans, version strings, and a few non-identifying
    counts (the pseudonym registry's own runway) without revealing local
    state."""
    workspace_configured, workspace_writable, catalog_present = _workspace_status()
    mcp_importable, entrypoint_present = _mcp_status()
    try:
        python_available = bool(sys.executable)
        python_version = ".".join(str(part) for part in sys.version_info[:3])
    except Exception:
        python_available = False
        python_version = "unknown"
    return {
        "app_version": __version__,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "python": {"available": python_available, "version": python_version},
        "canvas": {"configured": _canvas_configured()},
        "workspace": {
            "configured": workspace_configured,
            "writable": workspace_writable,
            "catalog_present": catalog_present,
        },
        "mcp": {
            "importable": mcp_importable,
            "entrypoint_present": entrypoint_present,
        },
        "pseudonym_registry": _pseudonym_registry_status(),
        "environment": {
            "proxy_configured": any(bool(os.environ.get(name)) for name in _PROXY_ENV_NAMES),
        },
    }


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _operations_text() -> str:
    records = operational_log.tail()
    if not records:
        return ""
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )


def _errors_text() -> str:
    # Unlike the other three members, this one is not built from an
    # allowlisted/validated source -- it may contain arbitrary text from
    # anywhere in the app (see operational_log._ERROR_LOGGER). Capped so one
    # pathological loop can't balloon the bundle.
    return operational_log.tail_traceback_text(max_bytes=200_000)


def _zip_text_member(archive: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, content.encode("utf-8"))


def build_support_bundle(destination: Path) -> Path:
    """Build the fixed four-member support ZIP and atomically replace destination.

    errors.log (B3/B4) is the one member not built from an allowlisted or
    validated source -- it may carry raw text from anywhere in the app and
    is included only because sending the bundle is the teacher's own
    explicit action, never automatic.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    health = health_snapshot()
    manifest = {
        "schema_version": 1,
        "app_version": __version__,
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            members = {
                "health.json": _json_text(health),
                "manifest.json": _json_text(manifest),
                "operations.jsonl": _operations_text(),
                "errors.log": _errors_text(),
            }
            for name in sorted(members):
                _zip_text_member(archive, name, members[name])
        os.replace(temporary, destination)
        return destination
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
