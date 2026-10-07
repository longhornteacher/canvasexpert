"""CanvasMirror scheduling service — heartbeat passes, write-through notify,
and the status payload for the mirror routes.

The heartbeat uses a daemon thread, launch delay, exception isolation, and a
credential gate, and is started from the server
lifespan. All real work lives in plain pass functions that tests drive
directly with injected legacy and complete-only collection clients plus
``now=`` — the thread is never started in tests.

Cadence (per Current course): a full pass when none has succeeded in
FULL_MAX_AGE_HOURS (this is both first-run backfill and the nightly
reconcile), otherwise a delta every tick plus a daily roster refresh.
"""
from __future__ import annotations

import os
import sqlite3
import hashlib
import uuid
import threading
import time
import requests
from dataclasses import dataclass
from pathlib import Path

from api import operational_log
from api.mirror import coordinator, course_context, new_quizzes, store, sync
from api import course_catalog

from api.platform_services import config, workspace
from api.platform_services.canvas_client import (
    canvas_get as _platform_canvas_get,
    canvas_get_all as _platform_canvas_get_all,
    canvas_get_all_complete as _platform_canvas_get_all_complete,
    canvas_stream_get as _platform_canvas_stream_get,
    canvas_get_telemetry,
)
canvas_get = _platform_canvas_get
canvas_get_all = _platform_canvas_get_all
canvas_get_all_complete = _platform_canvas_get_all_complete
canvas_stream_get = _platform_canvas_stream_get
from api.platform_services.canvas_client import canvas_headers


LAUNCH_DELAY_SECONDS = 120        # let startup and the first agent reads settle
TICK_SECONDS = 900                # delta cadence while the app runs
DELTA_RECENT_SECONDS = TICK_SECONDS / 2  # a delta/full this recent covers the tick's delta
FULL_MAX_AGE_HOURS = 24.0         # backfill + nightly reconcile
ROSTER_MAX_AGE_HOURS = 24.0
NOTIFY_DELAY_SECONDS = 15.0       # write-through settle delay
INDEX_MAINTENANCE_SECONDS = 30.0
ATTACHMENT_IDLE_SECONDS = 30.0
ATTACHMENT_CHUNK_PAUSE_SECONDS = 1.0


_OWNER_LOCK = threading.RLock()
_OWNER = None
_OWNER_BINDING = None
_OWNER_ERROR = None
_FOCUSED_PLANS = {}
_REQUEST_QUEUE = None
_REQUEST_BINDING = None
FOCUSED_OWNER_WAIT_SECONDS = 2.0

STAGE_CODES = {
    "acquisition": frozenset({"canvas_unavailable", "auth_failed", "pagination_incomplete",
        "course_not_selected", "acquisition_owner_waiting", "acquisition_failed"}),
    "publication": frozenset({"publication_incomplete"}),
    "index": frozenset({"index_maintenance_pending", "index_rebuild_failed", "index_busy",
        "evidence_update_required", "course_evidence_not_arrived"}),
    "attachments": frozenset({"attachments_pending", "attachment_gaps"}),
}
_STAGE_FALLBACKS = {"acquisition": "acquisition_failed", "publication": "publication_incomplete",
                    "index": "index_rebuild_failed", "attachments": "attachment_gaps"}


def stage(name: str, state: str, code: str | None = None) -> dict:
    if name not in coordinator.STAGE_NAMES or state not in coordinator.STAGE_STATES:
        raise ValueError("invalid_stage")
    result = {"state": state}
    if code is not None:
        result["code"] = code if code in STAGE_CODES[name] else _STAGE_FALLBACKS[name]
    return result


def overall_ok(stages) -> bool:
    return (stages.get("acquisition", {}).get("state") == "ready"
            and stages.get("publication", {}).get("state") in {"ready", "not_run"})


def fully_ready(stages) -> bool:
    return bool(stages) and all(value.get("state") in {"ready", "not_run"}
                                for value in stages.values())


def _runner_stages(outcome) -> dict:
    outcome = outcome if isinstance(outcome, dict) else {}
    errors = {str(outcome.get(key) or "") for key in ("error_class", "error_code", "error")}
    publication = stage("publication", "not_run")
    if outcome.get("ok"):
        acquisition, publication = stage("acquisition", "ready"), stage("publication", "ready")
    elif "acquisition_owner_waiting" in errors or "waiting" in errors:
        acquisition = stage("acquisition", "pending", "acquisition_owner_waiting")
    elif "publication_incomplete" in errors:
        acquisition, publication = stage("acquisition", "ready"), stage("publication", "failed", "publication_incomplete")
    elif errors & {"course_not_selected", "course_unavailable"}:
        acquisition = stage("acquisition", "failed", "course_not_selected")
    elif errors & {"acquisition_owner_repair_required", "workspace not configured", "acquisition_failed"}:
        acquisition = stage("acquisition", "failed", "acquisition_failed")
    elif "pagination_incomplete" in errors:
        acquisition = stage("acquisition", "failed", "pagination_incomplete")
    elif any(course_catalog.error_code(error) in {"auth_unavailable", "auth_failed", "forbidden"} for error in errors):
        acquisition = stage("acquisition", "failed", "auth_failed")
    else:
        acquisition = stage("acquisition", "failed", "canvas_unavailable")
    return {"acquisition": acquisition, "publication": publication}


_STAGE_SEVERITY = {"failed": 4, "partial": 3, "pending": 2, "not_run": 1, "ready": 0}


def _worst_stage(name: str, candidates: list) -> dict:
    """Worst state across job stage dicts: failed > partial > pending > not_run > ready."""
    if not candidates:
        return stage(name, "not_run")
    return max(candidates, key=lambda value: _STAGE_SEVERITY.get(value.get("state"), 0))


def _plan_stages(plan_view: dict) -> dict:
    """Worst acquisition/publication across a plan's jobs plus course-scoped
    index/attachment stages. Queued/running jobs without an outcome are pending,
    never not_run or success."""
    jobs = [job for job in (plan_view.get("jobs") or []) if isinstance(job, dict)]
    acquisition, publication = [], []
    for job in jobs:
        job_stages = job.get("stages") if isinstance(job.get("stages"), dict) else {}
        if "acquisition" in job_stages:
            acquisition.append(job_stages["acquisition"])
        elif job.get("state") in {"queued", "running"}:
            acquisition.append(stage("acquisition", "pending"))
        if "publication" in job_stages:
            publication.append(job_stages["publication"])
    stages = {"acquisition": _worst_stage("acquisition", acquisition),
              "publication": _worst_stage("publication", publication)}
    course_ids = {str(job.get("course_id")) for job in jobs if job.get("course_id")}
    if len(course_ids) == 1:
        local = evidence_stages(course_id=next(iter(course_ids)))
        stages["index"] = local["index"]
        stages["attachments"] = local["attachments"]
    return stages


def acquisition_owner_status(*, tick=False):
    """Observe advisory acquisition ownership without touching work leases."""
    global _OWNER, _OWNER_BINDING, _OWNER_ERROR
    from api import local_runtime
    from api.mirror.acquisition_owner import AcquisitionOwner, OwnerStatus
    from api.mirror.evidence_paths import source_key_for_origin
    with _OWNER_LOCK:
        try:
            root = workspace.workspace_root()
            if root is None or not config.mirror_enabled() or not config.token_is_set():
                return None
            binding = (str(root), source_key_for_origin(config.get_canvas_base()))
            if binding != _OWNER_BINDING:
                if _OWNER is not None:
                    _OWNER.release()
                writer = hashlib.sha256(local_runtime.machine_id().encode("utf-8")).hexdigest()[:32]
                _OWNER = AcquisitionOwner(root, binding[1], writer)
                _OWNER_BINDING = binding
            _OWNER_ERROR = None
            return _OWNER.tick() if tick else _OWNER.observe()
        except Exception:
            _OWNER_ERROR = "repair_required"
            return OwnerStatus(None, None, None, False, "repair_required", issues=("owner_unavailable",))


def _focused_requests():
    global _REQUEST_QUEUE, _REQUEST_BINDING
    from api.mirror.acquisition_requests import AcquisitionRequests
    from api.mirror.evidence_paths import source_key_for_origin
    root = workspace.workspace_root()
    if root is None:
        raise ValueError("workspace_unconfigured")
    binding = (str(root), source_key_for_origin(config.get_canvas_base()))
    with _OWNER_LOCK:
        if binding != _REQUEST_BINDING:
            _FOCUSED_PLANS.clear()
            _REQUEST_QUEUE = AcquisitionRequests(*binding)
            _REQUEST_BINDING = binding
        return _REQUEST_QUEUE


def _service_focused_requests(owner):
    """Poll progress without blocking the ownership heartbeat behind Canvas I/O."""
    if owner is None or not owner.is_owner:
        return
    requests = _focused_requests()
    instance = coordinator_instance()
    for key, (plan_id, request_ids) in list(_FOCUSED_PLANS.items()):
        plans = instance.status(plan_id).get("plans", [])
        if not plans or plans[0]["state"] in {"succeeded", "failed", "cancelled"}:
            if plans and plans[0]["state"] == "succeeded":
                requests.acknowledge(request_ids, owner_writer_key=owner.owner_writer_key)
            _FOCUSED_PLANS.pop(key, None)
    for request in requests.pending(limit=32):
        key = (request.course_id, request.scope)
        if key in _FOCUSED_PLANS or _selected_course(request.course_id) is None:
            continue
        plan_id = instance.submit([request.course_id], [request.scope], priority="background")
        _FOCUSED_PLANS[key] = (plan_id, request.request_ids)


def _request_owner_first(course_id, scope):
    """Healthy non-owners give the owner a bounded opportunity before duplicate GETs."""
    owner = acquisition_owner_status()
    if owner is None or owner.is_owner:
        return False
    if owner.state == "repair_required":
        raise ValueError("acquisition_owner_repair_required")
    from api import local_runtime
    requests = _focused_requests()
    writer = hashlib.sha256(local_runtime.machine_id().encode("utf-8")).hexdigest()[:32]
    request_id = requests.submit(writer_key=writer, course_id=course_id, scope=scope)
    deadline = time.monotonic() + FOCUSED_OWNER_WAIT_SECONDS
    while time.monotonic() < deadline:
        if requests.is_acknowledged(request_id):
            # Publication may precede cloud delivery of evidence dependencies.
            # Local legacy projections still need their own bounded refresh until S08.
            return True
        time.sleep(0.05)
    return True


def release_acquisition_owner():
    global _OWNER, _OWNER_BINDING
    with _OWNER_LOCK:
        try:
            if _OWNER is not None:
                _OWNER.release()
        finally:
            _OWNER = None
            _OWNER_BINDING = None


def run_attachment_capture_chunk(*, limit=None, max_bytes=None, stop_event=None) -> dict:
    """Capture one bounded chunk; acquire the vault only for safe publication."""
    from api import local_runtime
    from api.mirror.evidence_acquisition import publish_attachment_status
    from api.mirror.evidence_jobs import (
        AttachmentJobStore, MAX_BYTES_PER_CHUNK, MAX_DOWNLOADS_PER_CHUNK,
        make_original_sink, resolve_canvas_file_url, run_attachment_chunk)
    from api.mirror.evidence_paths import control_store_path, local_source_root, source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    root = workspace.workspace_root()
    if root is None:
        raise ValueError("workspace_unconfigured")
    source_key = source_key_for_origin(config.get_canvas_base())
    jobs = AttachmentJobStore(control_store_path(source_key, root))
    writer = hashlib.sha256(local_runtime.machine_id().encode("utf-8")).hexdigest()[:32]
    run_id = uuid.uuid4().hex
    def store_original(job, digest, temp_path):
        with store._vault_transaction(root) as vault:
            publisher = EvidencePublisher(workspace_root=root, source_key=source_key,
                                          course_id=job.course_id, vault=vault)
            make_original_sink(workspace_root=root, publisher=publisher,
                writer_key=writer, run_id=run_id)(job, digest, temp_path)
    def publish_terminal(job, status):
        with store._vault_transaction(root) as vault:
            publisher = EvidencePublisher(workspace_root=root, source_key=source_key,
                                          course_id=job.course_id, vault=vault)
            publish_attachment_status(publisher=publisher, job=job, status=status,
                                      writer_key=writer, run_id=run_id)
    result = run_attachment_chunk(jobs,
        resolve_url=lambda job: resolve_canvas_file_url(job, canvas_get=canvas_get),
        stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
        original_exists=lambda digest: _original_exists(root, digest),
        store_original=store_original, publish_terminal=publish_terminal,
        staging_dir=local_source_root(source_key, root) / "staging" / "attachments",
        limit=MAX_DOWNLOADS_PER_CHUNK if limit is None else limit,
        max_bytes=MAX_BYTES_PER_CHUNK if max_bytes is None else max_bytes,
        stop_event=stop_event)
    if result.get("published", 0):
        request_index_maintenance("capture")
    return result


def _original_exists(root, digest) -> bool:
    from api.mirror.original_archive import blob_path
    try:
        return blob_path(root, digest).exists()
    except Exception:
        return False


def run_extraction_chunk(*, limit: int = 20, stop_event=None) -> dict:
    """Run adapters outside vault transactions and publish one job at a time."""
    from contextlib import contextmanager
    from api import local_runtime
    from api.mirror.evidence_extraction import extract_captured_attachments
    from api.mirror.evidence_jobs import AttachmentJobStore
    from api.mirror.evidence_paths import control_store_path, local_source_root, source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    from api.mirror.extraction.supervisor import run_adapter
    from api.mirror.original_archive import recover_original
    root = workspace.workspace_root()
    if root is None:
        raise ValueError("workspace_unconfigured")
    source_key = source_key_for_origin(config.get_canvas_base())
    jobs = AttachmentJobStore(control_store_path(source_key, root))
    writer = hashlib.sha256(local_runtime.machine_id().encode("utf-8")).hexdigest()[:32]
    @contextmanager
    def publisher_scope(course_id):
        with store._vault_transaction(root) as vault:
            yield EvidencePublisher(workspace_root=root, source_key=source_key,
                                    course_id=course_id, vault=vault)
    def adapter_runner(adapter_name, data, filename):
        import tempfile
        staging = local_source_root(source_key, root) / "staging" / "extraction"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".bin",
                                         delete=False, dir=str(staging)) as handle:
            handle.write(data)
            path = Path(handle.name)
        try:
            return run_adapter(adapter_name, path)
        finally:
            path.unlink(missing_ok=True)
    outcome = extract_captured_attachments(publisher_scope=publisher_scope, jobs=jobs,
        recover_original=lambda digest: recover_original(root, digest),
        run_adapter=adapter_runner, writer_key=writer, run_id=uuid.uuid4().hex,
        limit=limit, stop_event=stop_event)
    if outcome.published:
        request_index_maintenance("extraction")
    return {"processed": outcome.processed, "published": outcome.published,
            "gaps": list(outcome.gaps)}


def prepare_evidence_work(*, course_id=None) -> None:
    """Reopen extraction work that can change; an explicit course refresh retries all.

    Worker start (no course) reopens only version-changed or transient work. A
    manual course refresh also reopens that course's settled gaps and exhausted
    captures once.
    """
    from api.mirror.evidence_jobs import AttachmentJobStore
    from api.mirror.evidence_paths import control_store_path, source_key_for_origin
    from api.mirror.extraction import registry
    from api.mirror.extraction.schema import PRIVACY_POLICY_REVISION
    root = workspace.workspace_root()
    if root is None:
        return
    source = source_key_for_origin(config.get_canvas_base())
    path = control_store_path(source, root)
    if not path.exists():
        return
    def current_version(filename):
        try:
            version = registry.extractor_version(filename)
        except Exception:
            return None
        return None if version is None else f"{version}:{PRIVACY_POLICY_REVISION}"
    jobs = AttachmentJobStore(path)
    explicit = course_id is not None
    if explicit:
        jobs.reopen_exhausted_captures(course_id=course_id)
    jobs.reopen_extractions(current_version, course_id=course_id,
                            retry_settled_gaps=explicit)


_MAINTENANCE_LOCK = threading.Lock()
_MAINTENANCE_STATUS_LOCK = threading.Lock()
_maintenance_requested = False
_INDEX_REQUEST_LOCK = threading.Lock()
_index_wake = threading.Event()
_work_wake = threading.Event()


def request_index_maintenance(reason: str) -> None:
    """Coalesce a local maintenance request without any disk or Canvas work."""
    global _maintenance_requested
    with _INDEX_REQUEST_LOCK:
        _maintenance_requested = True
        _index_wake.set()


def wake_evidence_workers() -> None:
    _index_wake.set()
    _work_wake.set()


def index_maintenance_worker(stop_event, *, wait=None) -> None:
    global _maintenance_requested
    wait = wait or _index_wake.wait
    while not stop_event.is_set():
        if not _index_wake.is_set():
            wait(INDEX_MAINTENANCE_SECONDS)
        if stop_event.is_set():
            break
        with _INDEX_REQUEST_LOCK:
            _maintenance_requested = False
            _index_wake.clear()
        started = time.perf_counter()
        try:
            run_index_maintenance()
            operational_log.emit("mirror.index_maintenance", "ok",
                                 duration_ms=int((time.perf_counter() - started) * 1000))
        except Exception as exc:
            operational_log.emit("mirror.evidence_index_worker", "failed", error_class=type(exc))


def attachment_work_worker(stop_event, *, wait=None) -> None:
    """Bounded continuation; capture obeys ownership, local extraction does not."""
    wait = wait or _work_wake.wait
    try:
        prepare_evidence_work()
    except Exception as exc:
        operational_log.emit("mirror.evidence_work", "failed", error_class=type(exc))
    while not stop_event.is_set():
        _work_wake.clear()
        progressed = False
        chunk_started = time.perf_counter()
        try:
            if config.token_is_set() and config.mirror_enabled():
                owner = acquisition_owner_status()
                if owner is not None and owner.is_owner:
                    result = run_attachment_capture_chunk(stop_event=stop_event)
                    progressed = bool(result.get("published") or result.get("captured") or result.get("skipped") or result.get("failed"))
            if not stop_event.is_set():
                result = run_extraction_chunk(stop_event=stop_event)
                progressed = progressed or bool(result.get("published"))
        except Exception as exc:
            operational_log.emit("mirror.evidence_work", "failed", error_class=type(exc))
        operational_log.emit("mirror.evidence_work_chunk", "ok",
                             duration_ms=int((time.perf_counter() - chunk_started) * 1000),
                             count=1 if progressed else 0)
        if stop_event.is_set():
            break
        wait(ATTACHMENT_CHUNK_PAUSE_SECONDS if progressed else ATTACHMENT_IDLE_SECONDS)


@dataclass(frozen=True)
class _VaultSnapshot:
    """Read-only privacy verification data, copied under a short vault lock."""
    frozen_verification = True  # lets the verifier compile its matcher once

    def __init__(self, vault):
        from copy import deepcopy
        object.__setattr__(self, "_entries", deepcopy(vault.entries()))
        names, identifiers = vault.all_real_identifiers()
        object.__setattr__(self, "_names", frozenset(names))
        object.__setattr__(self, "_identifiers", frozenset(identifiers))

    def entries(self):
        from copy import deepcopy
        return deepcopy(self._entries)

    def all_real_identifiers(self):
        return set(self._names), set(self._identifiers)


def _read_maintenance_status(root, source_key):
    import json
    from api.mirror.evidence_paths import maintenance_status_path
    empty = {"schema_version": 1, "state": "not_run", "code": None, "revision": None,
             "last_attempt_at": "", "last_success_at": "", "not_arrived_courses": [],
             "update_required_courses": [], "descriptor": {"state": "not_run", "code": None}}
    try:
        document = json.loads(maintenance_status_path(source_key, root).read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or set(document) != set(empty)
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or document["state"] not in coordinator.STAGE_STATES
                or document["code"] not in STAGE_CODES["index"] | {None}
                or not isinstance(document["descriptor"], dict)
                or set(document["descriptor"]) != {"state", "code"}
                or document["descriptor"].get("state") not in coordinator.STAGE_STATES
                or document["descriptor"].get("code") not in {None, "descriptor_write_failed"}
                or any(not isinstance(document[key], list)
                       or any(not isinstance(cid, str) or not cid.isdecimal() for cid in document[key])
                       for key in ("not_arrived_courses", "update_required_courses"))
                or any(not isinstance(document[key], str)
                       or (document[key] and not store._valid_iso_z(document[key]))
                       for key in ("last_attempt_at", "last_success_at"))
                or (document["revision"] is not None
                    and (not isinstance(document["revision"], str)
                         or len(document["revision"]) != 64
                         or any(c not in "0123456789abcdef" for c in document["revision"])))):
            return empty
        return document
    except (OSError, ValueError, TypeError):
        return empty


def _write_maintenance_status(root, source_key, document):
    import json
    import tempfile
    from api.mirror.evidence_paths import maintenance_status_path
    target = maintenance_status_path(source_key, root)
    with _MAINTENANCE_STATUS_LOCK:
        temporary = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                             dir=target.parent, suffix=".tmp") as handle:
                temporary = handle.name
                json.dump(document, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            operational_log.emit("mirror.maintenance_status", "failed", error_class=type(exc))
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass


def index_stage(status, index_present, *, course_id=None):
    if _maintenance_requested:
        return stage("index", "pending", "index_maintenance_pending")
    if status.get("state") == "failed":
        return stage("index", "failed", status.get("code") or "index_rebuild_failed")
    if not index_present:
        return stage("index", "pending", "index_maintenance_pending") if status.get("state") != "not_run" else stage("index", "not_run")
    updated = status.get("update_required_courses", [])
    absent = status.get("not_arrived_courses", [])
    if course_id is not None:
        updated = [cid for cid in updated if str(cid) == str(course_id)]
        absent = [cid for cid in absent if str(cid) == str(course_id)]
    if updated:
        return stage("index", "partial", "evidence_update_required")
    if absent:
        return stage("index", "partial", "course_evidence_not_arrived")
    return stage("index", "ready")


def attachment_stage(summary):
    if not summary.get("total", 0):
        return stage("attachments", "not_run")
    if summary.get("remaining", 0):
        return stage("attachments", "pending", "attachments_pending")
    if summary.get("capture_gaps", 0) + summary.get("extraction_gaps", 0):
        return stage("attachments", "partial", "attachment_gaps")
    return stage("attachments", "ready")


def run_index_maintenance(*, root=None, source_key=None) -> dict:
    """Independently reduce safe courses; rebuild only the disposable local index."""
    from api.mirror.cooperative import TimeSlice
    from api.mirror.evidence_index import EvidenceIndex, IndexReadError, IndexBusy
    from api.mirror.evidence_paths import local_source_root, source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    from api.mirror.evidence_queries import write_reader_descriptor
    from api.mirror.evidence_store import EvidenceStore, StoreSnapshot
    root = root or workspace.workspace_root()
    if root is None:
        return {"state": "not_run", "code": None, "revision": None}
    try:
        source_key = source_key or source_key_for_origin(config.get_canvas_base())
    except ValueError:
        return {"state": "not_run", "code": None, "revision": None}
    with _MAINTENANCE_LOCK:
        binding = (workspace.workspace_root(), config.get_canvas_base())
        previous = _read_maintenance_status(root, source_key)
        status = {**previous, "state": "ready", "code": None,
                  "last_attempt_at": store.now_iso(), "not_arrived_courses": [],
                  "update_required_courses": [], "descriptor": {"state": "not_run", "code": None}}
        count = 0
        try:
            evidence_root = Path(workspace.canvas_mirror_evidence_root(root))
            courses_root = evidence_root / "sources" / source_key / "courses"
            course_ids = {path.name for path in courses_root.iterdir()
                          if path.is_dir() and path.name.isdecimal()} if courses_root.exists() else set()
            selected = {str(course.get("id")) for course in config.active_courses()
                        if str(course.get("id") or "").isdecimal()}
            index = EvidenceIndex(local_source_root(source_key, root) / "query.sqlite3")
            indexed = set()
            usable = False
            if index.path.exists():
                try:
                    with index.read_connection() as db:
                        indexed = {row[0] for row in db.execute(
                            "SELECT course_id FROM course_selection WHERE source_key=?", (source_key,))}
                        usable = True
                except IndexReadError:
                    pass
            absent = (selected | indexed) - course_ids
            snapshots = []
            pacer = TimeSlice()  # background pass: yield between records, same checks
            with store._vault_transaction(root) as vault:
                snapshot_vault = _VaultSnapshot(vault)
            for cid in sorted(course_ids):
                try:
                    publisher = EvidencePublisher(workspace_root=root, source_key=source_key,
                                                  course_id=cid, vault=snapshot_vault)
                    snapshot = EvidenceStore(evidence_root, source_key, cid,
                        verify_safe=publisher.verify_safe,
                        verification_key=publisher.verification_key,
                        private_diagnostics_root=index.path.parent / "staging" / "diagnostics").scan(pacer=pacer)
                    if any(issue.code == "unsupported_schema" for issue in snapshot.issues):
                        status["update_required_courses"].append(cid)
                    if not (snapshot.facts or snapshot.commits):
                        absent.add(cid)
                    else:
                        snapshots.append(snapshot)
                except Exception as exc:
                    absent.add(cid)
                    operational_log.emit("mirror.evidence_course_scan", "failed", error_class=type(exc))
            status["not_arrived_courses"] = sorted(absent)
            count = len(snapshots)
            if binding != (workspace.workspace_root(), config.get_canvas_base()):
                return {"state": "pending", "code": "index_maintenance_pending", "revision": None}
            if status["update_required_courses"]:
                status.update(state="partial", code="evidence_update_required")
            elif absent or not snapshots:
                status.update(state="partial", code="course_evidence_not_arrived")
            def ingest():
                if snapshots:
                    return index.ingest_many(snapshots, selected_courses=selected, pacer=pacer)
                if usable:
                    empty = StoreSnapshot({}, {}, (), hashlib.sha256(b"empty-source").hexdigest(),
                                          verify_safe=lambda _: (_ for _ in ()).throw(ValueError("empty_source")))
                    return index.ingest(empty, selected_courses=selected, pacer=pacer)
                return None
            try:
                revision = ingest()
            except (IndexReadError, sqlite3.DatabaseError) as exc:
                if isinstance(exc, IndexBusy):
                    raise
                if isinstance(exc, IndexReadError) and str(exc) not in {"index_schema_mismatch", "index_corrupt"}:
                    raise
                index.discard()
                revision = ingest()
            status["revision"] = revision
            if revision:
                status["last_success_at"] = store.now_iso()
                try:
                    write_reader_descriptor(index.path, revision=revision)
                    status["descriptor"] = {"state": "ready", "code": None}
                except Exception as exc:
                    status["descriptor"] = {"state": "failed", "code": "descriptor_write_failed"}
                    operational_log.emit("mirror.reader_descriptor", "failed", error_class=type(exc))
        except Exception as exc:
            status.update(state="failed", code="index_busy" if isinstance(exc, IndexBusy) else "index_rebuild_failed")
            operational_log.emit("mirror.evidence_index_rebuild", "failed", error_class=type(exc))
        if binding != (workspace.workspace_root(), config.get_canvas_base()):
            return {"state": "pending", "code": "index_maintenance_pending", "revision": None}
        _write_maintenance_status(root, source_key, status)
        return {"state": status["state"], "code": status["code"], "revision": status["revision"],
                "courses": {"indexed": count, "not_arrived": status["not_arrived_courses"],
                            "update_required": status["update_required_courses"]},
                "descriptor": status["descriptor"]}


def evidence_status() -> dict:
    """Report durable-store phase, coverage, owner state, and actionable gaps.

    Errors are sanitized codes; no private path, filename, or student value is
    returned. Distinguishes local publication from cloud delivery and original
    capture from complete extraction.
    """
    from api.mirror.evidence_jobs import AttachmentJobStore
    from api.mirror.evidence_paths import control_store_path, local_source_root, source_key_for_origin
    root = workspace.workspace_root()
    if root is None:
        return {"state": "unconfigured", "reason": "workspace_unconfigured"}
    try:
        source_key = source_key_for_origin(config.get_canvas_base())
    except Exception:
        return {"state": "unconfigured", "reason": "canvas_origin_unconfigured"}
    maintenance = _read_maintenance_status(root, source_key)
    jobs = AttachmentJobStore(control_store_path(source_key, root))
    summary = jobs.summary()
    index_path = local_source_root(source_key, root) / "query.sqlite3"
    owner = acquisition_owner_status()
    return {
        "state": index_stage(maintenance, index_path.exists()).get("state"),
        "index": maintenance,
        "attachments": {**summary, "stage": attachment_stage(summary)},
        "acquisition_owner": {"state": owner.state if owner else "disabled",
                              "is_owner": bool(owner and owner.is_owner)},
        "gaps": list(owner.issues) if owner else [],
    }


def evidence_stages(*, course_id=None, assignment_id=None) -> dict:
    """Read-only local index and attachment stages for status and MCP results."""
    from api.mirror.evidence_jobs import AttachmentJobStore
    from api.mirror.evidence_paths import control_store_path, local_source_root, source_key_for_origin
    root = workspace.workspace_root()
    if root is None:
        return {"index": stage("index", "not_run"), "attachments": stage("attachments", "not_run")}
    source = source_key_for_origin(config.get_canvas_base())
    maintenance = _read_maintenance_status(root, source)
    index_path = local_source_root(source, root) / "query.sqlite3"
    jobs = AttachmentJobStore(control_store_path(source, root))
    summary = jobs.summary(course_id=course_id, assignment_id=assignment_id)
    return {"index": index_stage(maintenance, index_path.exists(), course_id=course_id),
            "attachments": attachment_stage(summary), "summary": summary}



def acquisition_owner_worker(stop_event):
    """Heartbeat independently of acquisition duration and the 900s cadence."""
    from api.mirror.acquisition_owner import HEARTBEAT_INTERVAL, STALE_AFTER
    previous = time.monotonic()
    try:
        while not stop_event.is_set():
            current = time.monotonic()
            with _OWNER_LOCK:
                if _OWNER is not None and current - previous >= STALE_AFTER:
                    _OWNER.reobserve()
            owner = acquisition_owner_status(tick=True)
            try:
                _service_focused_requests(owner)
            except Exception as exc:
                operational_log.emit("mirror.focused_requests", "failed", error_class=type(exc))
            previous = current
            if stop_event.wait(HEARTBEAT_INTERVAL):
                break
    finally:
        release_acquisition_owner()


def _publish_acquisition(receipt):
    """Publish the same private rows acquired for temporary legacy projections."""
    from api import local_runtime
    from api.mirror.evidence_acquisition import prepare_receipt_identities, publish_course_receipt
    from api.mirror.evidence_jobs import AttachmentJobStore, enqueue_from_receipt
    from api.mirror.evidence_paths import control_store_path, source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    root = workspace.workspace_root()
    if root is None:
        raise ValueError("workspace_unconfigured")
    started = time.perf_counter()
    writer = hashlib.sha256(local_runtime.machine_id().encode("utf-8")).hexdigest()[:32]
    source_key = source_key_for_origin(config.get_canvas_base())
    # The vault is held only to register and resolve this receipt's identities;
    # the frozen snapshot then serves the course scan, file publication and
    # queue work outside the lock.
    locked = time.perf_counter()
    with store._vault_transaction(root) as vault:
        snapshot = prepare_receipt_identities(vault=vault, receipt=receipt, source_key=source_key)
    operational_log.emit("mirror.publication_identity_lock", "ok",
                         duration_ms=int((time.perf_counter() - locked) * 1000))
    publisher = EvidencePublisher(workspace_root=root, source_key=source_key,
                                  course_id=receipt.course_id, vault=snapshot)
    result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                    writer_key=writer, run_id=uuid.uuid4().hex)
    stats = getattr(result, "stats", None) or {}
    operational_log.emit("mirror.publication_scan", "ok",
                         duration_ms=int(float(stats.get("scan_seconds", 0)) * 1000),
                         count=int(stats.get("scans", 0)))

    def _pseudonym(raw):
        try:
            return snapshot.get_or_assign(str(raw))
        except Exception:
            return None  # an identity missed by preparation is skipped, never assigned
    # Queue durable attachment capture from the same receipt; the private
    # job store is machine-local and never a synchronized authority.
    try:
        jobs = AttachmentJobStore(control_store_path(source_key, root))
        enqueue_from_receipt(jobs, receipt, source_key=source_key, pseudonym_for=_pseudonym)
    except Exception:
        operational_log.emit("mirror.attachment_enqueue", "failed")
    operational_log.emit("mirror.publication", "ok",
                         duration_ms=int((time.perf_counter() - started) * 1000))
    request_index_maintenance("publication")
    wake_evidence_workers()
    return result


def due_passes(state: dict, now_iso: str, *,
               serve_max_age_hours: float | None = None) -> list[str]:
    """Which passes one course needs this tick.

    The roster refreshes before it can age past the serve window, not merely
    once a day: the roster file is rewritten only by a full or roster pass
    (never a delta), and reads serve it only while it is younger than the
    serve threshold (~6h). Left at a flat 24h cadence it went unservable for
    most of every day — roster reads refusing while deltas
    kept the gradebook fresh. The daily figure stays as a floor via ``min``,
    so an unusually large serve window still refreshes the roster at least
    once a day.

    A delta or full that succeeded within ``DELTA_RECENT_SECONDS`` (usually a
    manual refresh just before the launch tick) already covers this tick's
    delta. Passes record their start time, so the regular cadence, at least
    ``TICK_SECONDS`` apart, always runs.
    """
    full_age = store.age_hours(state["passes"]["full"]["last_success_at"], now_iso)
    if full_age is None or full_age >= FULL_MAX_AGE_HOURS:
        return ["full"]  # covers roster and resets watermarks
    delta_age = store.age_hours(state["passes"]["delta"]["last_success_at"], now_iso)
    newest = full_age if delta_age is None else min(full_age, delta_age)
    passes = [] if 0 <= newest * 3600 < DELTA_RECENT_SECONDS else ["delta"]
    if serve_max_age_hours is None:
        serve_max_age_hours = config.mirror_serve_max_age_hours()
    roster_due_age = min(ROSTER_MAX_AGE_HOURS, serve_max_age_hours)
    roster_age = store.age_hours(state["passes"]["roster"]["last_success_at"], now_iso)
    if roster_age is None or roster_age >= roster_due_age:
        passes.append("roster")
    return passes


_PASS_RUNNERS = {"full": sync.full_pass, "delta": sync.delta_pass,
                 "roster": sync.roster_pass}


def _selected_course(course_id: str) -> dict | None:
    return next((course for course in config.active_courses()
                 if str(course.get("id")) == str(course_id)), None)


def _selected_runner(runner, scope=None):
    """Recheck at execution; a queued job cannot refresh a Previous course."""
    def acquire(course_id):
        if _selected_course(course_id) is None:
            return {"ok": False, "error_class": "course_not_selected"}
        if coordinator.current_worker_context().get("priority") in {"background", "concluded"}:
            owner = acquisition_owner_status()
            if owner is None or not owner.is_owner:
                return {"ok": False, "error_class": "acquisition_owner_repair_required"
                        if owner is not None and owner.state == "repair_required"
                        else "acquisition_owner_waiting"}
        duplicate = False
        if scope and coordinator.current_worker_context().get("priority") in {"manual", "post_write"}:
            try:
                duplicate = _request_owner_first(str(course_id), scope)
            except ValueError:
                return {"ok": False, "error_class": "acquisition_owner_repair_required"}
        result = runner(course_id)
        if duplicate and isinstance(result, dict):
            result = {**result, "acquisition_mode": "bounded_duplicate", "owner_requested": True}
        return result

    def run(course_id):
        try:
            result = acquire(course_id)
            result = dict(result) if isinstance(result, dict) else {"ok": True}
        except Exception as exc:
            operational_log.emit("mirror.acquisition", "failed", error_class=type(exc))
            result = {"ok": False, "error_class": "acquisition_failed"}
        result["stages"] = _runner_stages(result)
        if (result.get("ok") and scope in {"course.refresh", "course.feedback_refresh"}
                and coordinator.current_worker_context().get("priority") == "manual"):
            try:
                prepare_evidence_work(course_id=str(course_id))
                wake_evidence_workers()
            except Exception as exc:
                operational_log.emit("mirror.evidence_work_prepare", "failed", error_class=type(exc))
        return result
    return run


def _telemetry(scope: str):
    context = coordinator.current_worker_context()
    return canvas_get_telemetry(scope, context.get("priority", "manual"),
                                queue_wait_ms=context.get("queue_wait_ms", 0))


def _run_course_context(course_id: str):
    with _telemetry("course_context"):
        result = course_context.refresh_course_context(
            course_id, canvas_get=canvas_get, canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete, receipt_sink=_publish_acquisition)
        return {"ok": result.get("state") == "current", "state": result.get("state", "failed")}


def _run_roster(course_id: str):
    with _telemetry("roster"):
        return sync.roster_pass(course_id, canvas_get_all=canvas_get_all,
                                canvas_get_all_complete=canvas_get_all_complete, receipt_sink=_publish_acquisition)


def _run_groups(course_id: str):
    with _telemetry("groups"):
        result = _refresh_groups_on_maintenance(course_id, load_groups=lambda cid: load_group_categories(cid, complete_client=canvas_get_all_complete),
                                                now=store.now_iso())
        return {"ok": result.get("state") == "current", "state": result.get("state", "failed")}


def _run_submission_delta(course_id: str):
    with _telemetry("submissions.course_delta"):
        return sync.refresh_submissions_course_delta(
            course_id, canvas_get_all=canvas_get_all,
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            canvas_get_all_complete=canvas_get_all_complete, receipt_sink=_publish_acquisition)


def _run_new_quiz_metadata(course_id: str):
    """Metadata-only refresh from the existing local assignment projection."""
    with _telemetry("new_quizzes.metadata"):
        assignment_map = (store.read_assignments(course_id) or {}).get("assignments")
        if not isinstance(assignment_map, dict):
            return {"ok": False, "error_class": "assignment_projection_unavailable"}
        return new_quizzes.sync_metadata(
            course_id, list(assignment_map.values()), canvas_get_all=canvas_get_all,
            bypass_cooldown=coordinator.current_worker_context().get("priority") == "manual")


def _run_course_refresh(course_id: str):
    """Run one durable, revision-producing course refresh."""
    context = coordinator.current_worker_context()
    with _telemetry("course.refresh"):
        if context.get("priority") in {"background", "concluded"}:
            course = next((item for item in config.active_courses()
                           if str(item.get("id")) == str(course_id)), None)
            return (_run_heartbeat_course(
                course, stream_get=canvas_stream_get,
                canvas_origin=config.get_canvas_base()) if course else
                {"ok": False, "error_class": "course_unavailable"})
        course = _selected_course(course_id)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id, receipt_sink=_publish_acquisition,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"),
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            force=True,
        )


def _run_structure_refresh(course_id: str):
    """Refresh selected-course structure, including full modules."""
    with _telemetry("course.structure_refresh"):
        course = _selected_course(course_id)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        result = course_catalog.refresh_catalog(
            course_id,
            course.get("name") or course_id,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            receipt_sink=_publish_acquisition,
        )
        return {
            "ok": not bool(getattr(result.get("evidence"), "gaps", ())),
            "state": result.get("result", "partial"),
            "source": result.get("source"),
            "sections": result.get("sections", {}),
            "oldest_section": result.get("oldest_section", ""),
            "oldest_last_success_at": result.get("oldest_last_success_at", ""),
        }


def _run_scoring_course_refresh(course_id: str):
    """Rebuild the course projections used by a scoring session.

    Scoring cannot rely on the ordinary delta watermark: a teacher may have
    changed an assignment or submission in Canvas Live after the last mirror
    pass without changing the delta window in a way that repairs an incomplete
    local projection.  Comments are deliberately excluded because scoring
    preparation does not read them and they are an independent mirror concern.
    """
    with _telemetry("course.scoring_refresh"):
        course = _selected_course(course_id)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id, receipt_sink=_publish_acquisition,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"),
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            force=True, full=True, with_comments=False,
        )


def _run_feedback_course_refresh(course_id: str):
    """Acquire complete comment identities/staff proof only on teacher opt-in."""
    with _telemetry("course.feedback_refresh"):
        course = _selected_course(course_id)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id, receipt_sink=_publish_acquisition, canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"), force=True, full=True, with_comments=True)


def _run_scoring_discovery_refresh(course_id: str):
    """Run the discovery-owned full refresh scope.

    The coordinator reuses this scope's recent successful job for discovery
    continuations. The runner stays a real full refresh when a new job is
    needed, while assignment preparation keeps its stricter separate scope.
    """
    with _telemetry("course.scoring_discovery_refresh"):
        course = _selected_course(course_id)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id, receipt_sink=_publish_acquisition,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"),
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            force=False, full=True, with_comments=False,
        )


def coordinator_instance() -> coordinator.MirrorCoordinator:
    """The sole production registry.  Every runner above is read-only."""
    runners = {
        "course.refresh": _run_course_refresh,
        "course_context": _run_course_context,
        "roster": _run_roster,
        "groups": _run_groups,
        "course.scoring_refresh": _run_scoring_course_refresh,
        "course.feedback_refresh": _run_feedback_course_refresh,
        "course.scoring_discovery_refresh": _run_scoring_discovery_refresh,
        "submissions.course_delta": _run_submission_delta,
        "new_quizzes.metadata": _run_new_quiz_metadata,
        "course.structure_refresh": _run_structure_refresh,
    }
    return coordinator.configure_default({scope: _selected_runner(runner, scope)
                                          for scope, runner in runners.items()})


def enqueue_sync(course_id: str | None = None, scopes: list[str] | None = None,
                 *, reuse_completed_within_seconds: float = 0) -> str:
    """Queue manual read-only work; HTTP callers receive the opaque plan ID."""
    courses = [course for course in config.active_courses()
               if not course_id or str(course.get("id")) == str(course_id)]
    if not courses:
        raise ValueError("Course is not Current.")
    kwargs = {"priority": "manual"}
    if reuse_completed_within_seconds:
        kwargs["reuse_completed_within_seconds"] = reuse_completed_within_seconds
    return coordinator_instance().submit(
        (str(course.get("id")) for course in courses), scopes, **kwargs
    )


def refresh_course_structure(course_id: str, *, timeout_seconds: float = 30.0) -> dict:
    """Refresh a Current course through the coordinator without exposing rows."""
    if _selected_course(course_id) is None:
        raise ValueError("Course is not Current.")
    plan_id = coordinator_instance().submit(
        [str(course_id)], ["course.structure_refresh"], priority="manual",
    )
    plan = wait_for_plan(plan_id, timeout_seconds=timeout_seconds)
    catalog = course_catalog.read_catalog(str(course_id)).get("catalog") or {}
    module_scope = catalog.get("modules") if isinstance(catalog, dict) else {}
    summary = course_catalog.catalog_status_summary(catalog)
    return {
        "ok": plan.get("state") == "succeeded",
        "status": plan.get("status") or plan.get("state"),
        "operation_id": plan_id,
        "revision": str((module_scope or {}).get("last_success_at") or ""),
        "state": (module_scope or {}).get("state", "unavailable"),
        "result": summary["result"],
        "sections": summary["sections"],
        "oldest_section": summary["oldest_section"],
        "oldest_last_success_at": summary["oldest_last_success_at"],
        "error_code": next((job.get("error_code") for job in plan.get("jobs", []) if job.get("error_code")), ""),
    }


def enqueue_heartbeat_refreshes() -> list[str]:
    """Queue one compatibility refresh job per configured course, never direct GET work."""
    if not config.token_is_set() or not config.mirror_enabled() or workspace.workspace_root() is None:
        return []
    owner = acquisition_owner_status()
    if owner is None or not owner.is_owner:
        return []
    instance = coordinator_instance()
    plans = []
    for course in config.active_courses():
        course_id = str(course.get("id") or "")
        if not course_id:
            continue
        context = store.read_course_context(course_id)
        priority = ("concluded" if context["lifecycle"] == "concluded" and
                    context["state"] in {"current", "stale"} else "background")
        plans.append(instance.submit([course_id], ["course.refresh"], priority=priority))
    return plans


def wait_for_plan(plan_id: str, *, poll_seconds: float = 0.05,
                  timeout_seconds: float | None = None) -> dict:
    """Heartbeat/timer helper: workers own I/O while this helper only observes state.

    ``timeout_seconds`` bounds the wait for callers that must not block a
    request indefinitely (e.g. the MCP refresh tool); the default of None
    preserves the original unbounded behavior used by the heartbeat."""
    instance = coordinator_instance()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while True:
        plans = instance.status(plan_id).get("plans", [])
        if not plans or plans[0]["state"] in {"succeeded", "failed", "cancelled"}:
            return plans[0] if plans else {"state": "failed"}
        if deadline is not None and time.monotonic() >= deadline:
            return plans[0]
        time.sleep(poll_seconds)


def run_coordinated_heartbeat_tick() -> None:
    """One daemon tick: workers acquire and refresh configured courses."""
    for plan_id in enqueue_heartbeat_refreshes():
        wait_for_plan(plan_id)


def _refresh_groups_on_maintenance(course_id: str, *, load_groups, now: str) -> dict:
    """Best-effort private group refresh nested under roster/full maintenance."""
    try:
        categories, error, _message = load_groups(course_id)
        if error:
            document = store.mark_groups_stale(course_id, attempted_at=now)
            return {"state": document["state"] if document else "unavailable",
                    "error_code": "refresh_failed"}
        store.write_groups(course_id, categories, attempted_at=now)
        from api.mirror.evidence_acquisition import CourseAcquisitionReceipt, ScopeReceipt
        rows = []
        for category in categories or []:
            rows.append({"_category_only": True,
                         "group_category_id": category.get("category_id"),
                         "group_category_name": category.get("category_name")})
            for group in category.get("groups") or []:
                rows.append({**group, "group_category_id": category.get("category_id"),
                             "group_category_name": category.get("category_name"),
                             "user_ids": group.get("student_ids", [])})
        # Only the complete-client lane proves group and membership pagination.
        # Legacy injected loaders still publish useful, explicitly partial facts.
        publication = _publish_acquisition(CourseAcquisitionReceipt(str(course_id), now,
            max(now, store.now_iso()), (ScopeReceipt("course.groups", str(course_id),
            tuple(rows), complete=bool(getattr(categories, "membership_complete", False)),
            error_code=None if getattr(categories, "membership_complete", False) else "pagination_incomplete"),)))
        return {"state": "current", "error_code": "",
                "evidence_gaps": list(publication.gaps)}
    except Exception:
        try:
            document = store.mark_groups_stale(course_id, attempted_at=now)
        except Exception:
            document = None
        return {"state": document["state"] if document else "unavailable",
                "error_code": "refresh_failed"}


def _run_heartbeat_course(course: dict, *, canvas_get=None, canvas_get_all=None,
                          canvas_get_all_complete=None, load_groups=None, now=None,
                          stream_get=None, canvas_origin="") -> dict:
    """One course's legacy cadence, called inside a background coordinator job."""
    canvas_get = canvas_get or globals()["canvas_get"]
    canvas_get_all = canvas_get_all or globals()["canvas_get_all"]
    canvas_get_all_complete = canvas_get_all_complete or globals()["canvas_get_all_complete"]
    load_groups = load_groups or (lambda cid: load_group_categories(cid, complete_client=canvas_get_all_complete))
    now_iso = now or store.now_iso()
    course_id = str((course or {}).get("id") or "")
    if not course_id:
        return {"ok": False, "error_class": "course_unavailable", "results": []}
    if _selected_course(course_id) is None:
        return {"ok": False, "error_class": "course_not_selected", "results": []}
    try:
        context = course_context.ensure_course_context(
            course_id, canvas_get=canvas_get, canvas_get_all=canvas_get_all, now=now_iso,
            canvas_get_all_complete=canvas_get_all_complete, receipt_sink=_publish_acquisition)
    except Exception:
        context = store.read_course_context(course_id)
    state = store.read_sync(course_id)
    pass_names = due_passes(state, now_iso)
    concluded = (context["lifecycle"] == "concluded" and context["state"] in {"current", "stale"})
    if concluded and "full" not in pass_names:
        return {"ok": True, "results": []}
    summaries = []
    for pass_name in pass_names:
        if _selected_course(course_id) is None:
            return {"ok": False, "error_class": "course_not_selected", "results": summaries}
        pass_started = time.monotonic()
        try:
            kwargs = {"canvas_get_all": canvas_get_all, "now": now_iso,
                      "receipt_sink": _publish_acquisition}
            if pass_name in {"full", "delta", "roster"}:
                kwargs["canvas_get_all_complete"] = canvas_get_all_complete
            if pass_name in {"full", "delta"}:
                kwargs["course_name"] = course.get("name")
                kwargs["stream_get"] = stream_get
                kwargs["canvas_origin"] = canvas_origin
            if concluded and pass_name == "full":
                kwargs["skip_new_quiz_metadata"] = True
            result = _PASS_RUNNERS[pass_name](course_id, **kwargs)
        except Exception as error:
            result = {"ok": False, "error_class": type(error).__name__}
        if result.get("ok") and pass_name == "full":
            # Catalog consumes its acquired receipt; sync has no module/page GETs.
            result = {**result, "structure": _run_structure_refresh(course_id)}
        if result.get("ok") and pass_name in {"full", "roster"}:
            result = {**result, "groups": _refresh_groups_on_maintenance(
                course_id, load_groups=load_groups, now=now_iso)}
        _emit_refresh_outcome(
            pass_name, result,
            duration_ms=max(0, int(round((time.monotonic() - pass_started) * 1000))),
        )
        summaries.append({"course_id": course_id, "pass": pass_name, **result})
    return {"ok": all(item.get("ok") for item in summaries), "results": summaries}


def _emit_refresh_outcome(scope: str, result: dict, *, duration_ms: int = 0) -> None:
    if duration_ms is None:
        duration_ms = 0
    outcome = "ok" if result.get("ok") else (
        "unconfigured" if result.get("state") == "unconfigured" else "failed")
    operational_log.emit("mirror.refresh", outcome, scope=scope,
                         duration_ms=duration_ms)


def notify_course_changed(course_id, *, delay_seconds: float = NOTIFY_DELAY_SECONDS):
    """Write-through hook for a narrow post-write submission refresh.

    The ordinary heartbeat still owns the full delta pass.  This delayed,
    fire-and-forget hook must not imply structure or New Quiz freshness.
    """

    def _run():
        try:
            if not config.token_is_set() or not config.mirror_enabled():
                return
            if workspace.workspace_root() is None or _selected_course(course_id) is None:
                return
            plan_id = coordinator_instance().submit(
                [str(course_id)], ["submissions.course_delta"], priority="post_write")
            wait_for_plan(plan_id)
        except Exception as exc:
            operational_log.emit("mirror.notify_course_changed", "failed", error_class=type(exc))

    timer = threading.Timer(delay_seconds, _run)
    timer.daemon = True
    timer.start()
    return timer


def status(plan_id: str | None = None) -> dict:
    courses = []
    for course in config.active_courses():
        course_id = str(course.get("id") or "")
        if not course_id:
            continue
        state = store.read_sync(course_id)
        refresh = store.read_refresh(course_id)
        courses.append({
            "course_id": course_id,
            "course_name": config.course_display_name(course_id),
            "passes": state["passes"],
            "watermarks": state["watermarks"],
            "context": store.read_course_context(course_id),
            "refresh": {"state": refresh.get("state", ""),
                        "last_attempt_at": refresh.get("finished_at", ""),
                        "last_success_at": refresh.get("last_success_at", ""),
                        "failure_stage": refresh.get("failure_stage", ""),
                        "error_code": refresh.get("error_code", "")},
            "evidence": evidence_stages(course_id=course_id),
        })
    owner = acquisition_owner_status()
    payload = {
        "acquisition_owner": {"state": owner.state if owner else "disabled",
                              "is_owner": bool(owner and owner.is_owner),
                              "issues": list(owner.issues) if owner else []},
        "ok": True,
        "enabled": config.mirror_enabled(),
        "workspace_configured": workspace.workspace_root() is not None,
        "serve_max_age_hours": config.mirror_serve_max_age_hours(),
        "courses": courses,
        "vault_conflict": _vault_conflict_files(),
        "evidence": evidence_status(),
    }
    if plan_id:
        plan_view = coordinator_instance().status(plan_id)
        payload["plan"] = plan_view
        plans = plan_view.get("plans") or []
        if plans:
            payload["stages"] = _plan_stages(plans[0])
    return payload


def _vault_conflict_files() -> list[str]:
    """Basenames of conflict copies anywhere in _Shared.

    This status path must work even when the Identity Vault seed itself cannot
    be opened, so it scans filenames without constructing the vault or reading
    any file content.
    """
    try:
        from api.shared_storage import scan_conflicts
        return [os.path.basename(item["path"]) for item in scan_conflicts()]
    except Exception:
        return []


def mirror_heartbeat_worker(stop_event):
    """Run the mirror cadence until the runtime asks this worker to stop."""
    if stop_event.wait(LAUNCH_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        try:
            run_coordinated_heartbeat_tick()
        except Exception as exc:
            operational_log.emit("mirror.heartbeat_tick", "failed", error_class=type(exc))
        if stop_event.wait(TICK_SECONDS):
            return


def fetch_group_category_groups(course_id: str, category_id: str, *, complete_client=None) -> tuple[list[dict] | None, str | None]:
    """Fetch one group category's groups + memberships live from Canvas.

    Factored out of ``load_group_categories``'s whole-course loop so both the
    full course refresh and a single-category targeted reconciliation share
    one Canvas-shape-normalization path (id/name/student_ids/memberships per
    group). Returns ``(groups_out, None)`` on success, ``(None, err)`` on any
    non-200/transport failure — never raises.
    """
    hdrs, base = canvas_headers()
    if not hdrs:
        return None, "No token saved."

    completeness = []
    def get(path, params=None):
        if complete_client is not None:
            rows, error, complete = complete_client(path, params or {})
            completeness.append(complete is True and not error)
            return (200 if not error else 0, rows)
        completeness.append(False)
        try:
            r = requests.get(f"{base}{path}", headers=hdrs,
                             params=params or {}, timeout=20)
            return (r.status_code, r.json() if r.status_code == 200 else None)
        except Exception:
            return (0, None)

    def memberships(group_id):
        """Return list of membership dicts for a group."""
        st, members = get(f"/api/v1/groups/{group_id}/memberships", {"per_page": 200})
        return members or []

    st, groups_raw = get(f"/api/v1/group_categories/{category_id}/groups", {"per_page": 100})
    if st != 200:
        return None, f"Canvas returned {st} for group_categories/{category_id}/groups."

    groups_out = []
    for grp in (groups_raw or []):
        grp_id = str(grp["id"])
        mems = memberships(grp_id)
        groups_out.append({
            "id":          grp_id,
            "name":        grp["name"],
            "student_ids": [m["user_id"] for m in mems],
            "memberships": mems,
        })
    if complete_client is not None and not all(completeness):
        return groups_out, "pagination_incomplete"
    return groups_out, None


class _GroupCategories(list):
    def __init__(self, values, complete=False):
        super().__init__(values)
        self.membership_complete = complete


def load_group_categories(course_id: str, *, complete_client=None) -> tuple[list[dict], str | None, str]:
    """Return (categories, error, message) for a course's group sets.

    Canvas note (confirmed live 2026-06): teacher PATs may get 403 on every
    /group_categories endpoint (district permission), while
    /courses/:id/groups still returns the same groups with their
    group_category_id. So: try group_categories for proper set names, fall
    back to bucketing /courses/:id/groups by category id.

    V3: Also returns membership IDs for Canvas write operations.
    """
    hdrs, base = canvas_headers()
    if not hdrs:
        return [], "No token saved.", ""

    completeness = []
    def get(path, params=None):
        if complete_client is not None:
            rows, error, complete = complete_client(path, params or {})
            completeness.append(complete is True and not error)
            return (200 if not error else 0, rows)
        completeness.append(False)
        try:
            r = requests.get(f"{base}{path}", headers=hdrs,
                             params=params or {}, timeout=20)
            return (r.status_code, r.json() if r.status_code == 200 else None)
        except Exception as e:
            return (0, None)

    def memberships(group_id):
        """Return list of membership dicts for a group."""
        st, members = get(f"/api/v1/groups/{group_id}/memberships", {"per_page": 200})
        return members or []

    # Preferred path: real group sets with names.
    st, cats = get(f"/api/v1/courses/{course_id}/group_categories", {"per_page": 50})
    if st == 200 and cats:
        result = []
        for cat in cats:
            # Status intentionally ignored here (unchanged from prior
            # behavior): a per-category fetch failure degrades to an empty
            # groups list for that one category rather than failing the
            # whole-course load.
            groups_out, _err = fetch_group_category_groups(course_id, cat["id"], **({"complete_client": complete_client} if complete_client else {}))
            completeness.append(complete_client is not None and not _err)
            result.append({"category_id":   str(cat["id"]),
                           "category_name": cat["name"],
                           "groups":        groups_out or []})
        return _GroupCategories(result, bool(complete_client) and all(completeness)), None, ""

    # Fallback: course groups bucketed by category id (category names 403-gated).
    st2, groups_raw = get(f"/api/v1/courses/{course_id}/groups", {"per_page": 100})
    if st2 != 200:
        return [], (f"Canvas returned {st or st2} for course groups "
                    f"(group_categories: {st}; groups: {st2})."), ""

    if not groups_raw:
        return _GroupCategories([], bool(complete_client) and completeness[-1]), None, "No group sets found in this course."

    completeness = completeness[-1:]
    buckets = {}
    for grp in groups_raw:
        buckets.setdefault(str(grp.get("group_category_id") or "0"), []).append(grp)
    result = []
    for i, (cat_id, grps) in enumerate(sorted(buckets.items()), start=1):
        normalized = []
        for grp in grps:
            members = memberships(grp["id"])
            normalized.append({"id": str(grp["id"]), "name": grp["name"],
                               "student_ids": [m["user_id"] for m in members],
                               "memberships": members})
        result.append({
            "category_id":   cat_id,
            "category_name": "Group set" if len(buckets) == 1 else f"Group set {i}",
            "groups": normalized,
        })
    return _GroupCategories(result, bool(complete_client) and all(completeness)), None, ""
