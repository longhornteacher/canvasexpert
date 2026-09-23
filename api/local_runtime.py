"""Machine-local runtime identity and single-process ownership."""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.request
from pathlib import Path

from api import runtime_paths
from api.storage_support import atomic_write_json


_MACHINE_ID_RE = re.compile(r"^[A-Z0-9_.-]+-[A-F0-9]{8}$")


def machine_id() -> str:
    """Return a stable host label with a random suffix, persisted locally."""
    path = runtime_paths.machine_identity_path()
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Canvas Expert machine identity is unreadable.") from exc
        value = document.get("machine_id") if isinstance(document, dict) else None
        if not isinstance(value, str) or not _MACHINE_ID_RE.fullmatch(value):
            raise RuntimeError("Canvas Expert machine identity is invalid.")
        return value

    host = str(os.environ.get("COMPUTERNAME") or socket.gethostname() or "LOCAL")
    host = re.sub(r"[^A-Z0-9_.-]+", "-", host.strip().upper()).strip("-._") or "LOCAL"
    value = f"{host}-{secrets.token_hex(4).upper()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "machine_id": value}
    try:
        atomic_write_json(path, document)
    except OSError:
        # Another CE process may have won the first-run race. The persisted
        # value is authoritative; never mint a second identity on this host.
        if not path.exists():
            raise
    return machine_id()


def publish_runtime(port: int) -> None:
    atomic_write_json(runtime_paths.runtime_instance_path(), {
        "version": 1,
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": int(port),
    })


def clear_runtime() -> None:
    path = runtime_paths.runtime_instance_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(document, dict) and document.get("pid") == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def running_runtime(timeout: float = 0.35) -> str | None:
    """Return the local HTTP runtime base URL if its published endpoint answers."""
    try:
        document = json.loads(runtime_paths.runtime_instance_path().read_text(encoding="utf-8"))
        port = int(document.get("port"))
        if document.get("host") != "127.0.0.1" or not 1 <= port <= 65535:
            return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    base = f"http://127.0.0.1:{port}"
    request = urllib.request.Request(base + "/api/runtime/ping", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return base if isinstance(body, dict) and body.get("ok") is True else None


class ProcessLock:
    """Non-blocking OS lock held for the lifetime of one CE runtime process."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or runtime_paths.process_lock_path())
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    handle.close()
                    return False
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    return False
        except Exception:
            handle.close()
            raise
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Canvas Expert is already running on this machine.")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False
