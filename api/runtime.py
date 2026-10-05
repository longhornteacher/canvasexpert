"""Own process startup and shutdown for the local Canvas Expert runtime."""
from __future__ import annotations

import threading
import sys

from api import operational_log


_lock = threading.RLock()
_started = False
_stopped = False
_heartbeat_stop: threading.Event | None = None
_heartbeat_thread: threading.Thread | None = None
_owner_stop: threading.Event | None = None
_owner_thread: threading.Thread | None = None
_index_stop: threading.Event | None = None
_index_thread: threading.Thread | None = None
_evidence_stop: threading.Event | None = None
_evidence_thread: threading.Thread | None = None


def _note(step: str, exc: Exception) -> None:
    # Startup diagnostics stay useful without exposing paths or private state.
    operational_log.emit(f"runtime.startup.{step}", "failed", error_class=type(exc))
    print(f"Canvas Expert startup note ({step}: {type(exc).__name__}).", file=sys.stderr)


def start() -> None:
    """Run process startup steps once, in safety order."""
    global _started, _stopped, _heartbeat_stop, _heartbeat_thread, _owner_stop, _owner_thread
    global _index_stop, _index_thread, _evidence_stop, _evidence_thread
    with _lock:
        if _started:
            return
        _stopped = False

        from api.platform_services import config, workspace
        from api import runtime_paths, ai_authoring
        from api.operation_ledger import recovery

        for name, action in (
            ("workspace", workspace.ensure_workspace),
            ("workspace_pin", config.ensure_workspace_pinned),
        ):
            try:
                action()
            except Exception as exc:
                _note(name, exc)

        try:
            target = runtime_paths.ai_ta_dir()
            if target is not None:
                ai_authoring.build_library(target)
        except Exception as exc:
            _note("authoring_library", exc)

        try:
            recovery.recover_pending_operations()
        except Exception as exc:
            _note("operation_recovery", exc)

        try:
            from api.mirror.service import (
                request_index_maintenance, index_maintenance_worker, attachment_work_worker)
            request_index_maintenance("startup")
            _index_stop = threading.Event()
            _index_thread = threading.Thread(target=index_maintenance_worker,
                args=(_index_stop,), name="ce-evidence-index", daemon=True)
            _index_thread.start()
            _evidence_stop = threading.Event()
            _evidence_thread = threading.Thread(target=attachment_work_worker,
                args=(_evidence_stop,), name="ce-evidence-work", daemon=True)
            _evidence_thread.start()
        except Exception as exc:
            _note("evidence_workers", exc)

        try:
            from api.mirror.service import mirror_heartbeat_worker, acquisition_owner_worker

            _owner_stop = threading.Event()
            _owner_thread = threading.Thread(target=acquisition_owner_worker,
                args=(_owner_stop,), name="ce-acquisition-owner", daemon=True)
            _owner_thread.start()

            _heartbeat_stop = threading.Event()
            _heartbeat_thread = threading.Thread(
                target=mirror_heartbeat_worker,
                args=(_heartbeat_stop,),
                name="ce-mirror-heartbeat",
                daemon=True,
            )
            _heartbeat_thread.start()
        except Exception as exc:
            _heartbeat_stop = None
            _heartbeat_thread = None
            _note("mirror_heartbeat", exc)

        _started = True


def stop() -> None:
    """Stop background work and release process-wide work leases once."""
    global _stopped, _heartbeat_stop, _heartbeat_thread, _owner_stop, _owner_thread
    global _index_stop, _index_thread, _evidence_stop, _evidence_thread
    with _lock:
        if _stopped:
            return
        _stopped = True
        evidence_threads = ((_index_stop, _index_thread), (_evidence_stop, _evidence_thread))
        _index_stop = _index_thread = _evidence_stop = _evidence_thread = None
        owner_stop, owner_thread = _owner_stop, _owner_thread
        _owner_stop = None
        _owner_thread = None
        stop_event, thread = _heartbeat_stop, _heartbeat_thread
        _heartbeat_stop = None
        _heartbeat_thread = None
    try:
        for event, _worker in evidence_threads:
            if event is not None:
                event.set()
        from api.mirror.service import wake_evidence_workers
        wake_evidence_workers()
        for _event, worker in evidence_threads:
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=5.0)
        if owner_stop is not None:
            owner_stop.set()
        if owner_thread is not None and owner_thread is not threading.current_thread():
            owner_thread.join(timeout=5.0)
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
    except Exception as exc:
        _note("mirror_heartbeat_stop", exc)
    finally:
        try:
            from api.mirror.service import release_acquisition_owner
            release_acquisition_owner()
        except Exception as exc:
            _note("acquisition_owner_release", exc)
        try:
            from api.shared_work import heartbeat_service

            heartbeat_service().release_all()
        except Exception as exc:
            _note("work_lease_release", exc)
