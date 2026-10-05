"""Workspace-bound locations for safe evidence and disposable local indexes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from api import runtime_paths
from api.platform_services import workspace


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COURSE_ID = re.compile(r"[0-9]+\Z")


def source_key_for_origin(canvas_base: str) -> str:
    """Hash only the normalized Canvas origin, never a URL with credentials."""
    parsed = urlsplit(canvas_base.strip())
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("invalid_canvas_origin")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("invalid_canvas_origin") from None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    origin = f"{scheme}://{host}" + (f":{port}" if port and port != default else "")
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()


def workspace_key(root: str | Path) -> str:
    """Partition a machine-local index by the selected absolute workspace."""
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(root)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validated_source(source_key: str) -> str:
    if not _DIGEST.fullmatch(source_key):
        raise ValueError("invalid_source_key")
    return source_key


def _validated_course(course_id: int | str) -> str:
    value = str(course_id)
    if not _COURSE_ID.fullmatch(value):
        raise ValueError("invalid_course_id")
    return value


def safe_course_root(source_key: str, course_id: int | str,
                     root: str | Path | None = None) -> Path:
    base = workspace.canvas_mirror_evidence_root(root)
    if base is None:
        raise ValueError("workspace_unconfigured")
    return Path(base) / "sources" / _validated_source(source_key) / "courses" / _validated_course(course_id)


def local_source_root(source_key: str, root: str | Path | None = None) -> Path:
    selected = Path(root) if root is not None else runtime_paths.workspace_root()
    if selected is None:
        raise ValueError("workspace_unconfigured")
    return (runtime_paths.local_cache_dir() / "CanvasMirror" /
            workspace_key(selected) / _validated_source(source_key))


def control_store_path(source_key: str, root: str | Path | None = None) -> Path:
    """Machine-local private job/checkpoint database; never cloud-synced."""
    return local_source_root(source_key, root) / "control.sqlite3"


def reader_descriptor_path(source_key: str, root: str | Path | None = None) -> Path:
    """Machine-local reader descriptor beside the disposable SQLite projection."""
    return local_source_root(source_key, root) / "reader.json"


def maintenance_status_path(source_key: str, root: str | Path | None = None) -> Path:
    """Machine-local maintenance status; this helper performs no filesystem I/O."""
    return local_source_root(source_key, root) / "maintenance.v1.json"
