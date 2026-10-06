# CanvasMirror functional read path: implementation slices

Status: S11 synthetic implementation complete; S13 laptop direct read/restart
check and S12 dispatch responsiveness repair passed. Broader desktop field
acceptance remains open; see the current brief for evidence. Section 4.9 specifies refresh
continuation; section 5.1 records remaining workflow limits without widening
scoring scope.
Target branch: `dev`. Inspected baseline: `30b9465`.
Authority: [current execution brief](../handoffs/canvasmirror-functional-read-path.md).
Preflight findings and baseline failures live in that brief, section 7.

This plan locks names, interfaces, state rules, copy, and tests so an executor
implements rather than designs. It deliberately chooses the simplest mechanism
that meets each brief requirement for one teacher on two computers before
launch. Where this plan names a function, constant, column, code, or test, use
that name. If code has moved, recheck the affected seam and update obsolete line
references or equivalent helper names. Stop only if that changes a locked behavior,
privacy boundary, or scope decision; source drift alone is not a teacher question.
Do not add mechanisms this plan
removed (section 12 lists them) without a measured need.

## 1. Deliverable, boundaries, and how to use this plan

Deliver one complete reading milestone: an agent can discover an assignment,
refresh its relevant evidence, read available submissions and supported attachment
text with honest coverage, restart, and use a compatible second computer without
an activation or import ritual. Current work must progress despite unrelated
historical gaps and attachment backlog. Canvas remains authoritative.

The slices are reviewable units within one batch, not releases, handoffs, or
permission checkpoints. Record execution results only in the brief.

The job is publishing assignments/quizzes/pages and obtaining work for scoring
and feedback. Mirror readiness is not a prerequisite for authoring or an unrelated
content push. Preserve the owning write path's target, privacy, review, and live
verification requirements. Return useful read evidence immediately; repair only
the missing part. Course-wide `fully_ready` is display status, never permission
to read, prepare unrelated work, or apply an authorized operation.

Excluded:

- Changing scoring policy, holds, feedback delivery, grades, or Canvas writes.
- Retiring private projections still consumed by Names, scoring, or operations.
- Reconstructing a complete archive or requiring missing historical attempts.
- Grading calendars, period classification, cutoffs, rollover, or age cancellation.
- A second catalog, storage engine, job state machine, migration system, cloud
  adapter, filesystem watcher, or distributed locking framework.
- New console pages, routes, dashboards, or host-specific agent rendering.
- Repairing the separately baselined QuizForge golden-plan mismatch.
- Changing evidence `SCHEMA_VERSION` (stays `1`) or `INDEX_SCHEMA_VERSION` (stays `2`).

Reading routes:

| Reader | Reads |
|---|---|
| Lead | Brief sections 1–7, this plan sections 1–6, then the stage being executed |
| Index worker | Brief sections 1–4, this plan sections 1–5, then S02 and S03 only |
| Publication worker | Brief sections 1–4, this plan sections 1–5, then S01 and S04 only |
| Senior acceptance | Sections 10–12 plus the brief's execution result |

No slice needs scoring code edits. If one appears to, read the mandatory Scoring
Sessions/AssignmentForge resources in `AGENTS.md` and stop for a scope decision.

## 2. Stage map and dependencies

| Stage | Slice | Result | Owner | Prerequisites |
|---|---|---|---|---|
| A | S00 | Baseline inventory and fixture; prepare test environment | Lead | Brief |
| B | S01 | Shared descriptor cannot block publication; first-acquisition scrub gap closed | Publication worker | S00 |
| B | S02 | Future-version records get their own code, not "invalid" | Index worker | S00 |
| B | S03 | Read-side index checks, safe replacement, local descriptor | Index worker | S02 |
| B | S04 | Queue selection, retries, association-keyed extraction, cache removal | Publication worker | S01 |
| C | S05 | Stage results, refresh persistence, per-course rebuild | Lead | S01–S03 |
| C | S06 | Coalesced background index maintenance | Lead | S03, S05 |
| C | S07 | Attachment continuation worker | Lead | S04–S06 |
| D | S08 | One read resolver and resumable MCP refresh; legacy fallbacks removed | Lead | S03, S05–S07 |
| D | S09 | Activation/import/migration machinery deleted | Lead | S07, S08 |
| D | S10 | Console shows stages; status is side-effect free | Lead | S05–S07, S09 |
| E | S11 | Docs, MCP contract, integrated synthetic acceptance | Lead; senior reviews | S01–S10 |
| F | S12 | Updated pilot runtime; optional bounded reset | Lead with teacher | S11 accepted |
| F | S13 | Actual second computer; final acceptance and retirement | Lead; senior accepts | S12 |

Parallel schedule: lead finishes S00's inventory and fixture; the index worker runs S02 → S03 while the
publication worker runs S01 → S04. The lead may draft S05 tests against section 4
but merges no code that calls an unfinished worker interface. Join both workers
before S05 code lands. S05–S11 are sequential and lead-owned. One executor may run
everything alone. OCR environment repair does not hold independent code or
isolated tests; real-adapter checks must pass before attachment acceptance.

## 3. File ownership and agent handoff rules

| Owner | Exclusive implementation files | Exclusive tests |
|---|---|---|
| Index worker | `api/mirror/evidence_index.py`, `evidence_queries.py`, `evidence_schema.py`, `evidence_store.py`, `evidence_paths.py` | `api/tests/mirror/test_evidence_index.py`, `test_evidence_queries.py`, `test_evidence_schema.py`, `test_evidence_store.py`, `test_evidence_paths.py` |
| Publication worker | `api/mirror/evidence_acquisition.py`, `evidence_publish.py`, `evidence_jobs.py`, `evidence_extraction.py` | `api/tests/mirror/test_evidence_acquisition.py`, `test_evidence_publish.py`, `test_evidence_jobs.py`, `test_evidence_extraction.py` |
| Lead | `api/mirror/service.py`, `sync.py`, `coordinator.py`, `store.py` (refresh document only), `api/runtime.py`, `api/mcp_server/tools.py`, `server.py`, `contract.py`, schema snapshot, `api/webui/routes/mirror.py`, `api/webui/static/canvasagent.js`, retired modules, all docs | Every other test file, including shared fixtures (`api/tests/mirror/conftest.py`, `acquisition_samples.py`, `api/tests/mcp_server/conftest.py`, `api/tests/conftest.py`), service/sync/coordinator/runtime/MCP/route tests, `test_retired_paths.py` |

Exceptions decided now:

- `test_evidence_jobs.py::test_service_chunk_drains_queue_through_coordinated_transport`
  calls `service`. If S04 breaks it, the worker reports it and the lead fixes it in S07.
- Workers do not edit `api/mirror/extraction/*`, `original_archive.py`,
  `acquisition_owner.py`, or `acquisition_requests.py`.

Each worker assignment states slice IDs, writable files, the section 4 interfaces
to produce, the focused command, and excluded files. A worker returns: behavior
and limitations; changed files; focused commands with counts; final signatures and
result shapes; tests replaced because they enforced retired semantics; unresolved
dependencies. Workers never touch the schema counter, snapshot, brief, runtime,
live data, or deployment.

## 4. Locked interfaces and decisions

### 4.1 Stage vocabulary

In `api/mirror/coordinator.py` (structural vocabulary only):

```python
STAGE_NAMES = ("acquisition", "publication", "index", "attachments")
STAGE_STATES = frozenset({"ready", "partial", "pending", "failed", "not_run"})
```

In `api/mirror/service.py` (the code allowlist owner). These are the codes the
console or agent actually branches on; everything else maps to a fallback:

```python
STAGE_CODES = {
    "acquisition": frozenset({"canvas_unavailable", "auth_failed",
        "pagination_incomplete", "course_not_selected",
        "acquisition_owner_waiting", "acquisition_failed"}),
    "publication": frozenset({"publication_incomplete"}),
    "index": frozenset({"index_maintenance_pending", "index_rebuild_failed",
        "index_busy", "evidence_update_required", "course_evidence_not_arrived"}),
    "attachments": frozenset({"attachments_pending", "attachment_gaps"}),
}

def stage(name: str, state: str, code: str | None = None) -> dict:
    """{"state": state} or {"state": state, "code": code}. An unknown code becomes
    the stage fallback: acquisition_failed, publication_incomplete,
    index_rebuild_failed, attachment_gaps."""
```

Never place exception text, URLs, filenames, paths, or Canvas bodies in a stage.
`service.overall_ok(stages)`: acquisition `ready` and publication `ready` or
`not_run`. Index and attachment states never make `ok` false.
`service.fully_ready(stages)`: every stage `ready` or `not_run`.

`service._runner_stages(outcome)`:

| Runner outcome | acquisition | publication |
|---|---|---|
| `ok` true | `ready` | `ready` |
| `error_class`/`error` is `acquisition_owner_waiting` | `pending`, `acquisition_owner_waiting` | `not_run` |
| `error_code`/`error` is `publication_incomplete` | `ready` | `failed`, `publication_incomplete` |
| `error_class`/`error` is `course_not_selected` or `course_unavailable` | `failed`, `course_not_selected` | `not_run` |
| `error_class`/`error` is `acquisition_owner_repair_required` or `workspace not configured` | `failed`, `acquisition_failed`; specific configuration guidance accompanies the result outside the stage | `not_run` |
| `error`/`error_code` is `pagination_incomplete` | `failed`, `pagination_incomplete` | `not_run` |
| other `error` text: `course_catalog.error_code(error)` is `auth_unavailable`, `auth_failed`, or `forbidden` | `failed`, `auth_failed` | `not_run` |
| any other error | `failed`, `canvas_unavailable` | `not_run` |
| runner raised | `failed`, `acquisition_failed` | `not_run` |

### 4.2 Persistence (no new database, no event journal)

**Per-course refresh document** — `store`'s `_refresh.v1.json` (machine-local
`cache/Canvas Mirror/<course-id>/`). Keep `REFRESH_VERSION = 1`. Add two
**optional** keys that `validate_refresh` accepts and `read_refresh` defaults:

| Key | Type | Default | Written by |
|---|---|---|---|
| `last_success_at` | ISO-Z or `""` | `""` | `finish_refresh(ok=True)` sets it to `finished_at` |
| `failure_stage` | `""`, `"acquisition"`, `"publication"` | `""` | `finish_refresh(ok=False, failure_stage=...)`; cleared on success |

`validate_refresh` accepts `required ⊆ keys ⊆ required ∪ optional`. Existing files
stay valid. `finished_at` keeps meaning "last attempt finished".

**Per-source maintenance status** — atomic JSON
`evidence_paths.maintenance_status_path(source_key, root)` =
`local_source_root(...) / "maintenance.v1.json"`, owned by `service.py`:

```json
{"schema_version": 1, "state": "ready", "code": null, "revision": "<hex or null>",
 "last_attempt_at": "2026-10-05T14:00:03Z", "last_success_at": "2026-10-05T14:00:04Z",
 "not_arrived_courses": [], "update_required_courses": [],
 "descriptor": {"state": "ready", "code": null}}
```

Write with `tempfile` + `os.replace` under `service._MAINTENANCE_STATUS_LOCK`; on
`PermissionError` log `mirror.maintenance_status` and continue (status loss is
never an acquisition or index failure). `service._read_maintenance_status(root,
source_key)` returns `{"state": "not_run", ...empty}` for absent/invalid files and
never creates one. Course ids are Canvas navigation ids, not student data.

Index stage (`service.index_stage(status, index_present, *, course_id=None)`),
first match wins. Filter course lists to `course_id` for scoped results; only the
whole-source console summary aggregates unrelated courses:

1. A maintenance request is outstanding in this process → `pending`, `index_maintenance_pending`.
2. `status["state"] == "failed"` → `failed` with its code.
3. No index file → `pending`, `index_maintenance_pending` if safe evidence exists;
   otherwise `not_run`. Absent data must not be reported as fully ready.
4. `update_required_courses` non-empty → `partial`, `evidence_update_required`.
5. `not_arrived_courses` non-empty → `partial`, `course_evidence_not_arrived`.
6. Otherwise `ready`. Descriptor failures appear only in `descriptor`.

### 4.3 Index checks, replacement, and descriptor (index worker)

- `INDEX_SCHEMA_VERSION` stays `2`. The new behavior is that it is checked.
- `EvidenceIndex.read_connection()` raises `IndexReadError` with code
  `index_missing` (file absent), `index_schema_mismatch` (metadata absent or not
  `INDEX_SCHEMA_VERSION`), or `index_corrupt` (`sqlite3.DatabaseError` on open or
  the metadata query). It never creates the file, runs `_initialize`, or writes.
- `EvidenceIndex.ingest_many(...)` keeps its signature and full-replacement
  transaction. On an existing file it first reads metadata; a mismatch raises
  `IndexReadError("index_schema_mismatch")` instead of overwriting metadata.
- `EvidenceIndex.discard() -> None` for mismatch/corruption: unlink `query.sqlite3`,
  then `-wal`, then `-shm`, under the maintenance lock after closing this worker's
  connections. Never discard a healthy supported index; rebuild it transactionally.
  If main-file unlink fails, touch no sidecar. Any `PermissionError` raises
  `IndexBusy` (subclass of `IndexReadError`, code `index_busy`). If a later sidecar
  unlink fails, retry cleanup on the next cycle before creating a database; do
  not promise rollback of three filesystem deletes. Then call `ingest_many` to
  build fresh. A reader that opens
  between `discard` and the first commit gets `index_missing`, which the MCP
  resolver reports as pending. Persistent file locks remain explicit; do not
  promise a fixed recovery time or delete a healthy index to break the lock.
- `_initialize` writes `schema_version` only when no metadata row exists. Remove its
  duplicated `CREATE VIEW` block; views are created by `_project_derived` inside the
  ingest transaction.
- Descriptor: `evidence_queries.write_reader_descriptor(index_path, *, revision) ->
  Path` writes `index_path.parent / "reader.json"` atomically (`tempfile` +
  `os.replace`, overwrite allowed):

  ```json
  {"descriptor_version": 1, "index_schema_version": 2, "index_revision": "<rev>",
   "index_file": "query.sqlite3", "views": {"<view>": ["<col>", "..."]},
   "read_mode": "SQLite URI mode=ro with PRAGMA query_only=ON; no immutable=1",
   "revision_check": "SELECT value FROM index_metadata WHERE key='revision'",
   "sql_example": "<SQL_EXAMPLE>", "privacy": "<existing sentence>"}
  ```

  `reader_contract()` stays as the view-only generator for the MCP guide. Delete
  `publish_reader_contract` and `reader_contract_bytes` (S01 removes their callers).
  Direct readers compare `index_revision` with `index_metadata` in their own read
  transaction; no service-side matching helper is needed.
- `evidence_paths.reader_descriptor_path(source_key, root=None)` and
  `maintenance_status_path(source_key, root=None)`: paths only, no I/O.
- `EvidenceQueryService._read_attachment_page` adds an assignment-wide
  `attachment_summary` from the same transaction: `{"associations", "captured",
  "pending", "gaps", "extracted", "extraction_gaps"}` (`gaps`: association status in
  `too_large|unavailable|foreign_origin`; `extraction_gaps`: availability `partial`
  or `unavailable`). Preserve readable blocks and reasons; extracted is not
  necessarily completely read.

### 4.4 Future evidence versions (index worker)

- In `validate_fact` and `validate_commit`, check the version **before** the field
  allowlist: a dict whose `schema_version` is not `SCHEMA_VERSION` raises
  `EvidenceValidationError("unsupported_schema")`.
- `StoreIssue.code` allowlist gains `unsupported_schema`. In `EvidenceStore.scan`,
  catch that code separately: record the issue without a private diagnostics copy.
  For a commit, attach `scope`/`scope_id`/`source_key`/`course_id` only when the raw
  `scope` is in `SCOPE_KINDS` and `scope_id` passes `validate_component`; otherwise
  record digest only. The reducer is unchanged: the issue makes affected scopes
  `sync_pending` as any other issue does, and supported last-good refs stay current.
- The service (S05) reads the codes from `snapshot.issues` to fill
  `update_required_courses`; the MCP resolver turns that into a warning (4.7).

### 4.5 Attachment queue (publication worker)

Columns added to `attachment_jobs` in `_initialize` (check `PRAGMA table_info`;
`ALTER TABLE ... ADD COLUMN` only on the write path):

| Column | Type/default | Meaning |
|---|---|---|
| `extraction_state` | `TEXT NOT NULL DEFAULT 'needed'` | `needed`, `done`, `gap` |
| `extraction_error` | `TEXT` | sanitized code for `gap` |
| `extracted_with` | `TEXT` | adapter `EXTRACTOR_VERSION` + `:` + `PRIVACY_POLICY_REVISION` at `done`/`gap` |

Ordering everywhere: `created_at DESC, job_id` (newest first). Newly created jobs
run ahead of older backlog. Re-observing an association does not make it new:
`AttachmentJobStore.ensure` returns an existing row unchanged. Preserve that
idempotency, captured bytes, and extraction completion; do not rewrite `created_at`
to manufacture priority. There is no priority flag. At `30b9465` no production
path publishes a single-assignment receipt (PowerGrader's focused fetch passes no
receipt sink), so a flag would have no trigger. Newest-first is the teacher's
selected approximation, not a guarantee that a requested older job jumps the
queue. Report this limit; no dates, calendar, or scheduling framework.

- `claim`: SQL-bounded — `status IN ('pending','failed') AND file_id != '' AND
  (next_attempt_at IS NULL OR next_attempt_at <= :now)`, ordered as above,
  `LIMIT :limit * 4`; byte budgeting stays in Python over that set.
- Retry exhaustion: in `record(status="failed")`, at `attempts >= MAX_ATTEMPTS`
  persist `status="unavailable"`, `last_error="retry_exhausted"`, no due time.
  `AttachmentJobStore.reopen_exhausted_captures(*, course_id=None) -> int`, called
  once per process start and once after a successful explicit course refresh,
  resets those rows (only `last_error='retry_exhausted'`) to `pending` with
  `attempts=0`, so a long Canvas outage is not permanent. `too_large`,
  `foreign_origin`, and URL-unavailable rows stay terminal.
- Rows without `file_id` (only `reconstruct_from_facts` creates them; it has no
  production caller) are never claimed and are excluded from every count.
- `run_attachment_chunk(..., stop_event=None, publish_terminal=None)`: checks
  `stop_event` before each job; calls `publish_terminal(job, status)` once when a job
  reaches `too_large`, `unavailable`, or `foreign_origin`; result adds `published`.

Extraction:

- Delete `ExtractionCache` and the `cache` parameter. `extraction_state` is the
  single record of "done for this association". Old `extraction.sqlite3` files are
  ignored. Delete `captured_jobs`; add
  `AttachmentJobStore.extraction_candidates(*, limit) -> tuple[AttachmentJob, ...]`
  (`status='captured' AND digest IS NOT NULL AND extraction_state='needed'`, ordered
  as above) and `record_extraction(job_id, *, state, error=None, extracted_with=None)`.
- One rule for outcomes in `extract_captured_attachments(*, publisher_scope, jobs,
  recover_original, run_adapter, writer_key, run_id, limit=20, stop_event=None)`:

  | Outcome | Publish | State |
  |---|---|---|
  | Adapter returns a validated result | extraction fact preserving availability/reasons | `done` for `complete`/`empty`; `gap` for `partial`/`unavailable` |
  | No adapter for the type | `unavailable` fact, reasons `["unsupported_type"]`, `extractor_version="unsupported"` | `gap` |
  | `ExtractionError` / exception / timeout | `unavailable` fact; preserve allowlisted reason, map recognition failure to `recognition_gap`, unclassified adapter failure to `corruption` | `gap` |
  | `recover_original` fails | terminal association `unavailable` through `publish_attachment_status`; no invented extraction text | `gap`, local error `original_missing` |

- `AttachmentJobStore.reopen_extractions(current_version: Callable[[str], str | None], *, course_id=None) -> int`,
  called once per process start and once after a successful explicit course
  refresh: resets to `needed` every `gap` row, and every
  `done` row whose `extracted_with` differs from `current_version(filename)`. That
  covers a later dependency install and an extractor upgrade with no backoff table.
  `course_id` filters both helpers for an explicit refresh; startup uses all local
  rows. No reopening on heartbeat, status polling, read retries, or worker chunks.
  One refresh gives failed files one new bounded opportunity, not a retry loop.
- `ExtractionOutcome` fields: `processed`, `published`, `gaps` (tuple of codes).
- Extraction identity is the association:
  `extraction_entity_key(assignment_id, pseudonym, attempt, attachment_key)` →
  `f"extraction:{assignment_id}:{pseudonym}:{attempt if attempt is not None else 'none'}:{attachment_key}"`.
  `publish_extraction` replaces prior refs in the scope whose fact is an
  `attachment_extraction` with the same `(pseudonym, attempt, attachment_key)`
  regardless of entity key, so old-format keys are superseded without a reset. If
  the new fact digest is already current, return without a new commit.
- `publisher_scope(course_id)` returns a context manager yielding an
  `EvidencePublisher`. Adapters run outside it; only publication runs inside it.
- `publish_attachment_status(*, publisher, job, status, writer_key, run_id)` lives in
  `evidence_acquisition.py` (S01) and republishes the association with
  `original_digest=None` and the terminal status.

Read-only summary: `AttachmentJobStore.summary(*, course_id=None,
assignment_id=None) -> dict`. Absent file → zeros, nothing created. Open with
`file:...?mode=ro` and `PRAGMA query_only=ON`; missing new columns read as
`extraction_state='needed'`. Keys: `total`, `by_status`, `pending`,
`captured` (the three existing ones keep their meaning), `capture_gaps`,
`extraction_needed`, `extraction_done`, `extraction_gaps`, `remaining` (capture
retryable + extraction needed).

`service.attachment_stage(summary)`: `not_run` if `total == 0`; `pending`,
`attachments_pending` if `remaining > 0`; `partial`, `attachment_gaps` if
`capture_gaps + extraction_gaps > 0`; else `ready`. Use a course- or
assignment-scoped summary for refresh results, so unrelated courses never affect
them. New jobs progress ahead of backlog through ordering, not stage math.
An empty queue on computer B does not prove attachments ready: use indexed
association/extraction summaries for evidence availability. Queue counts describe
this computer's work only. Pending associations without local jobs mean pending
synced evidence, never zero missing attachments.

### 4.6 Maintenance and worker lifecycle (lead)

```python
def request_index_maintenance(reason: str) -> None      # in-memory only
def run_index_maintenance(*, root=None, source_key=None) -> dict
def index_maintenance_worker(stop_event) -> None         # thread "ce-evidence-index"
def run_attachment_capture_chunk(*, limit=None, max_bytes=None, stop_event=None) -> dict
def run_extraction_chunk(*, limit=20, stop_event=None) -> dict
def attachment_work_worker(stop_event) -> None           # thread "ce-evidence-work"
def wake_evidence_workers() -> None
def prepare_evidence_work(*, course_id=None) -> None      # reopen gaps and exhausted captures
def evidence_stages(*, course_id=None, assignment_id=None) -> dict   # read-only
def evidence_status() -> dict                                        # read-only
```

`rebuild_evidence_index` is renamed `run_index_maintenance`; `recover_evidence_work_chunk`
is deleted. Constants: `INDEX_MAINTENANCE_SECONDS = 30.0`,
`ATTACHMENT_IDLE_SECONDS = 30.0`, `ATTACHMENT_CHUNK_PAUSE_SECONDS = 1.0`.

- `_MAINTENANCE_LOCK` allows one rebuild per process. `request_index_maintenance`
  sets `_maintenance_requested = True` and `_index_wake`. Worker loop: wait on
  `_index_wake` up to 30 s or stop; clear the flag and event; run maintenance. A
  request arriving during a rebuild sets the flag again, so the next loop runs.
  Every loop rebuilds, requested or not: `ingest_many` already skips the write when
  the revision is unchanged, which the brief accepts until measured otherwise.
- Capture `(root, source_key)` at loop start; if either changes before the write,
  discard the result and write no status.
- Neither worker takes `_OWNER_LOCK`. Network capture runs only when
  `acquisition_owner_status()` reports `is_owner`, the token is set, and the mirror
  is enabled. Extraction of local originals runs regardless of ownership.
- Vault transactions are opened per job publication, never across a download or
  adapter run.
- Index maintenance never holds the vault lock while scanning. At the start of each
  run, take one short `store._vault_transaction(root)` and copy what verification
  needs into `service._VaultSnapshot` (frozen: `entries()` and
  `all_real_identifiers()` returning copies). Build each course's
  `EvidencePublisher(vault=snapshot)` from it and scan and verify outside the lock.
  The snapshot supports only those two methods; any other vault call raises, which
  proves maintenance never assigns or mutates identities. The privacy check is
  unchanged; only lock duration changes.
- `_publish_acquisition` stops rebuilding inline; it calls
  `request_index_maintenance("publication")` and `wake_evidence_workers()`. Chunks
  request maintenance only when they published something. `mirror_heartbeat_worker`
  stops rebuilding.
- `runtime.start()`: after operation recovery and before the heartbeat thread, call
  `request_index_maintenance("startup")`, then start both evidence threads.
  `prepare_evidence_work()` runs once inside the attachment worker (exceptions
  noted, never fatal); database migration/reopening must not delay MCP readiness.
  `runtime.stop()` sets both stop events, calls `wake_evidence_workers()`, and joins
  each thread with `timeout=5.0`.
- Second computers do not rebuild a capture queue. They read synced extraction facts
  through their own index. Their queue fills only from their own acquisitions.

### 4.7 MCP read semantics (lead)

Resolver in `tools.py`, replacing `_activated_evidence_lane`:

```python
def _evidence_reader() -> tuple[EvidenceQueryService | None, str | None, dict | None]:
    """(service, source_key, None) or (None, None, refusal)."""
```

| Condition | Refusal `code` | Guidance |
|---|---|---|
| No workspace | `workspace_unconfigured` | teacher sets workspace in Settings |
| Canvas origin unset/invalid | `canvas_origin_unconfigured` | teacher configures Canvas |
| No supported readable index and maintenance reports unsupported evidence | `evidence_update_required` | update this computer; do not retry acquisition/indexing in a loop; last-good supported rows, if available, remain readable with a warning |
| `index_missing`/`index_schema_mismatch`/`index_corrupt`/`index_busy`, safe evidence exists for the source | `evidence_index_pending` | signal `request_index_maintenance("read_miss")`; retry this read, not acquisition; include sanitized last maintenance code and `retry_after_seconds: 5` |
| Index missing and no safe evidence for the source | `evidence_refresh_required` | call `refresh_mirror(course_id)` |

The resolver writes nothing to disk. Add `_evidence_coverage(page) -> dict`:

```json
{"state": "complete|incomplete|unknown", "synchronization": "<scope status or unknown>",
 "last_success_at": "...", "age_seconds": 0}
```

and a `warnings` list from: `membership_incomplete`, `membership_unknown`,
`sync_pending`, `evidence_update_required` (course is in
`update_required_courses`, read from the maintenance status file read-only),
`section_label_missing`, `assignment_context_missing`,
`assignments_excluded_from_totals`.

Refuse only when nothing for the requested scope has ever been indexed:

| Tool | Refuse with `evidence_not_acquired` (guidance `refresh_mirror`) when | Otherwise |
|---|---|---|
| `get_roster` | roster coverage `unknown` and zero rows | serve; missing section label → `"Unknown section"` + `section_label_missing`; incomplete group labels → omit that group |
| `get_submissions` | submission coverage `unknown` and zero rows | serve; missing assignment row → `{"id": <id>, "title": ""}` + `assignment_context_missing` |
| `get_submissions(history=True)` | history coverage `unknown` and zero rows | serve with coverage |
| `get_gradebook_snapshot` | roster and assignment coverage both `unknown` | serve per-assignment rows with coverage; compute `class_avg`, `total_missing`, `total_ungraded`, and per-student stats only over assignments whose submission coverage is `complete`; list the others in `coverage.excluded_assignment_ids` and add warning `assignments_excluded_from_totals` |
| `get_assignment_evidence` | never | add `coverage`, `warnings` |

Here "never" means a valid query with an available supported index may return
unknown coverage; configuration, privacy, invalid-input, and index refusals still
apply. Unknown coverage includes the exact course refresh hint. Missing history
means "not observed", not "refresh until history is complete". After one successful
acquisition, do not prescribe repeated refreshes for attempts Canvas did not return.

Zero rows with coverage not `complete` always carry `membership_unknown` or
`membership_incomplete`; never present them as a complete empty set. `freshness`
keeps its current shape.

Revision pinning: related reads in one call pass `revision=<first revision>`. On
`IndexReadError("revision_changed")` retry the evidence section once; a second
change returns `{"ok": False, "code": "evidence_revision_changed", "error":
"CanvasMirror updated during this read. Call the same tool again."}`.

Pin related views within a call. Across separate calls, preserve any existing
revision/cursor contract. Where a tool accepts only offsets, expose its revision
and instruct the agent to restart that collection at offset zero if it changes;
do not promise a cross-call snapshot the signature cannot request. No acquisition
or teacher approval is needed to repeat a read.

### 4.8 Console copy (lead, S10)

| Situation | Text |
|---|---|
| Index `pending` | "Saved Canvas data is waiting for local indexing. It updates automatically." |
| Index `failed` | "The local index could not be rebuilt yet. Saved Canvas data is safe and will be retried automatically." |
| `evidence_update_required` | "Some saved Canvas data was written by a newer Canvas Expert. Update Canvas Expert on this computer." |
| `course_evidence_not_arrived` | "Some course data has not finished syncing to this computer yet." |
| Attachment backlog (`remaining > 0`) | "{n} attachment(s) are still being read." (appended; never changes card state) |
| Refresh: acquisition failed | "Canvas refresh failed: Canvas could not be read. Check your Canvas connection and try again." |
| Refresh: publication failed | "Canvas data was read, but some of it could not be saved safely. Try again; if it repeats, check Diagnostics." |
| Refresh: ok, index not ready | "Refresh saved. Local indexing is still finishing." |
| Refresh: index ready, attachments pending | "Refresh saved. Submission text is available; some attachments are still being read." |
| Refresh: index ready, attachment gaps | "Refresh saved. Available work can be read; some attachments could not be read." |
| Refresh: fully ready | "Refresh complete." |

### 4.9 MCP refresh continuation and recovery (lead, S08)

Add one parameter to the existing tool and its `server.py` wrapper:

```python
def refresh_mirror(course_id: str, include_comments: bool = False,
                   structure_only: bool = False, operation_id: str = "") -> dict:
    ...
```

- Empty `operation_id`: retain acquisition dispatch and the existing 25-second
  bounded wait. Return the coordinator `plan_id` as continuation `operation_id`,
  not a reused job's originating operation id.
- Non-empty `operation_id`: look up that plan through coordinator status; verify
  every job belongs to the requested course and the scopes for the specified
  `include_comments`/`structure_only` mode. Return current status immediately.
  No enqueue, wait loop, Canvas call, vault access, rebuild, or refresh-loop
  counter increment. Keep Current-course selection checks. Unknown/expired plan
  returns `refresh_operation_unavailable`; mismatched course/mode returns
  `refresh_operation_mismatch`, without exposing another course's details.
- Plans remain process-local. After restart/eviction, guidance is "Read requested
  evidence first; start one refresh if still needed." Do not add durable plans or
  silently enqueue from the status branch.
- Both branches return sanitized `stages`, `fully_ready`, `operation_id`, and
  existing identity fields. `syncing` means acquisition queued/running; `synced`
  means acquisition/publication finished, even if attachments remain pending.
  Include `retry_after_seconds: 5` while work is pending. Status combines the
  retained acquisition result with current course-scoped index/attachment state.
  Structure-only results retain catalog diagnostics and leave unrequested
  submission/attachment stages `not_run`.
- Recovery: transient Canvas or safe-publication failure permits one agent retry
  when needed; recurring failure stops automatic acquisition retries. Index
  failure requests local maintenance/read retry, never another Canvas fetch.
  Attachment gaps affect only the items needing those files. Auth/configuration
  and version errors name the actual teacher action. Ownership waiting is pending
  coordination, not evidence of failed credentials.
- Remove generic `ask_teacher_confirmation` from failed read-only refreshes in
  both `refresh_mirror` and `_refresh_catalog`. The browser button runs the same
  machinery and is not a repair. Keep repeated-acquisition detection as advisory
  `refresh_not_progressing`; count new dispatches only, not status calls. Explain
  the recurring failure and continue unaffected work. Read-only refresh needs no
  permission. Existing Canvas-write approvals remain unchanged.

Connected-agent guidance: start one refresh when needed, check that operation
after the suggested delay, and retry the original read when usable. Do not poll
until all course attachments finish. After two unchanged delayed checks, explain
the pending stage and proceed with available work instead of monopolizing the
conversation. This is host guidance, not a new runtime refusal or cancellation.

## 5. Defects found during planning (what the slices fix)

Verified by reading `30b9465`.

| ID | Defect | Where | Slice |
|---|---|---|---|
| D1 | Shared `reader.v1.json` conflict raises inside publication; the pass fails as `publication_incomplete` | `evidence_acquisition.py:422`, `evidence_publish.py:361`, `sync._publication_ok` | S01 |
| D2 | Retry-exhausted capture jobs stay `failed` with no due time and are reclaimed every chunk | `evidence_jobs.record` | S04 |
| D3 | `claim()` loads every retryable row into Python | `evidence_jobs.claim` | S04 |
| D4 | Extraction picks the oldest captured rows, including cache hits, so later jobs starve | `captured_jobs`, `extract_captured_attachments` | S04 (cache removed, per-job state) |
| D5 | Extraction key is assignment+digest+version; identical files from two students collide | `extraction_entity_key`, `publish_extraction` | S04 |
| D6 | A cache hit skips publication, so a second student's attachment never gets text | `extract_captured_attachments` | S04 (cache removed) |
| D7 | Capture and extraction hold the Identity Vault transaction across downloads and adapter runs | `service.run_attachment_capture_chunk`, `run_extraction_chunk` | S07 |
| D8 | Capture/extraction run once at startup; nothing continues the queue | `runtime.start` | S07 |
| D9 | Future-version records fail the field allowlist first and look like corruption | `validate_fact`, `validate_commit` | S02 |
| D10 | Rebuild runs inline in the coordinator worker after every receipt and chunk | `service._publish_acquisition` etc. | S06 |
| D11 | Any store issue or absent course returns `pending` for the whole source | `service.rebuild_evidence_index` | S05 |
| D12 | `jobs.summary()` creates directories, database, and schema from a status GET | `evidence_jobs._connect`, `service.evidence_status` | S04, S10 |
| D13 | MCP reads refuse unless every scope is `ready` and `complete` | `tools.py` evidence branches | S08 |
| D14 | `get_submissions` reads three views without cross-view revision pinning | `tools.get_submissions` | S08 |
| D15 | Startup tests enforce the synchronous recovery ordering S06 replaces | `test_runtime_startup.py` | S06 |
| D16 | `test_rebuild_preserves_all_courses_until_delayed_files_arrive` enforces the whole-source pending rule | `test_service_evidence.py` | S05 |
| D17 | On a course's first acquisition, scrubbing happens before SIS IDs and nicknames are in the vault (`publish_course_receipt` registers only Canvas id + name; `store.write_roster` adds the rest after publication), so they can reach the safe store unscrubbed | `evidence_acquisition.publish_course_receipt`, `evidence_publish.publish_text_assignment` | S01 |
| D18 | Rebuilding the index holds the Identity Vault's inter-process lock for the whole scan; at a 30 s cadence this would stall vault-dependent MCP reads | `service.rebuild_evidence_index` | S05 |
| D19 | Slow refresh says retry but offers no MCP status-only continuation; after completion another call can enqueue another acquisition | `tools.refresh_mirror`, `server.refresh_mirror`, `coordinator.submit` | S08, section 4.9 |
| D20 | Both refresh modes label failure as needing teacher confirmation and send the teacher to an equivalent browser refresh | `tools.refresh_mirror`, `_refresh_catalog`, `_refresh_loop_attention` | S08 |
| D21 | Plan assumed every refresh makes attachment jobs new; `ensure` preserves existing rows and their original creation time | `evidence_jobs.AttachmentJobStore.ensure` | S04: preserve idempotency and test the actual guarantee |

### 5.1 Workflow audit and residual limits

These are execution consequences, not additional permission gates. D1–D21 are
code findings; the following include risks in the proposed implementation.

| Teacher's job / hang-up | Required treatment | Acceptance / remaining limit |
|---|---|---|
| Publish an assignment, quiz, or page while mirror/OCR is unhealthy | No new mirror-wide prerequisite; use existing content preparation, review, and verified apply | S11 checks existing content-operation tests; no live write needed for this read milestone |
| Refresh finishes but reads still say pending | Section 4.9 status continuation and read-side maintenance hint; never refresh Canvas to repair an index | S08 proves status calls enqueue nothing, including after plan completion |
| One unavailable PDF stalls the class | Return other text and explicit attachment gaps; only existing scoring holds decide affected rows | S07/S11 preserve scoring-hold tests; `fully_ready` is never a global workflow gate |
| Second computer has no jobs and claims files are ready | Read availability from synced associations/extractions, not local queue size | S11 checks a pending association in partition B with no local jobs |
| Requested attachment already deep in backlog | Newest-first helps newly created jobs only; existing job keeps its place | Accepted scheduling approximation. Do not claim assignment-targeted priority or add a flag |
| Missing original or failed publication leaves attachment forever "pending" | Publish a typed gap when bytes are unavailable; persist completion only after safe publication succeeds | S04 failure-order tests below; no infinite retry on a malformed file |
| A resolved download/extraction problem requires an app restart | One successful explicit course refresh reopens its failed work and wakes the worker | S07 tests course scoping and that status/heartbeat do not reopen failures |
| Agent reads evidence, but scoring preparation rejects its private projection | State explicitly that read readiness is not scoring readiness; refresh once through existing scoring guidance | Private scoring consumers and publication gate stay; next senior assessment owns convergence |
| Teacher asks to score writing inside New Quizzes | Keep current supported boundary explicit | Existing writing remains in Canvas; future writing uses a separate assignment. This batch adds no New Quiz scoring |

Do not reopen teacher decisions to solve these remaining limits covertly. The
next assessment should start from the scoring preparation failure the teacher
actually encounters, not another storage cleanup or historical-import project.

## 6. Stage A — S00: baseline and execution preparation

**Owner:** lead.

1. `git status --short --branch`; `git fetch origin`; record
   `git rev-list --left-right --count dev...origin/dev`, `dev...origin/main`, and
   `git rev-parse HEAD`. Preserve pending doc/brief changes and newer work. If `dev`
   moved past `30b9465`, run
   `git diff --stat 30b9465..HEAD -- api/mirror api/mcp_server api/runtime.py api/webui`
   and re-verify any section 5 row whose file changed.
2. Keep this inventory's output for S09:

   ```powershell
   git grep -n -E "evidence_activation|evidence_import|evidence_migration|evidence_activation_proof|_activated_evidence_lane|read_activation|recover_evidence_work|rebuild_evidence_index|publish_reader_contract|reader_contract_bytes|reader\.v1|captured_jobs|ExtractionCache" -- api docs tools
   ```

3. Reuse a working repo-local, gitignored `.venv`; create it only if absent. Never
   touch `%CE_DATA%\venv`. Use that interpreter for every command in this plan
   (the later `py -m pytest` examples are shorthand, not a second environment).

   ```powershell
   py -3.13 -m venv .venv
   .venv\Scripts\python -m pip install -r api/requirements.txt pytest==9.0.1
   .venv\Scripts\python -m pytest api/tests/mirror/extraction -p no:randomly -q
   ```

   Record interpreter, versions, and counts. If installation is impossible,
   continue independent implementation/tests and record the exact blocker. Real
   extraction acceptance remains unverified until the supported environment works.
4. Reuse the brief's baseline; do not rerun the whole suite just to start coding.
   Reproduce an affected failure only if the environment/source change makes its
   old baseline inapplicable. S11 owns the integrated full run. Do not edit golden
   data to hide the known QuizForge mismatch.
5. Shared fixture in `api/tests/mirror/acquisition_samples.py`:
   - Move `SyntheticVault` there from `test_evidence_publish.py` and update its
     test imports; no compatibility re-export. Map `synthetic-user-01` → `Pikachu`, `synthetic-user-02` → `Eevee`.
     Extend it to the real vault surface `roster_service.upsert_roster` uses:
     `get_or_assign(raw, real_name="", sis_id="")`, `remember_identity(raw, name, sis_id)`,
     and `add_nicknames(raw, names)`, with SIS IDs and nicknames appearing in
     `entries()` and SIS IDs in `all_real_identifiers()`. Existing callers keep working.
   - `course_receipt_sample("read_path")`: the `full` scopes plus `course.sections`
     (section `500`) and two attachments on `synthetic-user-01` attempt 3:
     `{"id": "9001", "filename": "essay.docx", ...}` and
     `{"id": "9002", "filename": "broken.pdf", ...}`.
   - `synthetic_documents()`: `{"9001": <docx built in memory with python-docx
     containing "Synthetic thesis sentence.">, "9002": b"%PDF-1.4 not a real pdf"}`.
   - `course_receipt_sample("second_course")`: course `"2"`, assignment `20`, one
     submission from `synthetic-user-02`.
6. Workers can start after inventory and fixture setup; extraction environment
   preparation need not block index/publication work.

## 7. Stage B — foundation workstreams

### S01: detach reader documentation from publication

**Owner:** publication worker. **Files:** `evidence_acquisition.py`,
`evidence_publish.py`, their tests.

1. Remove the `publish_reader_contract` import and calls from `publish_course_receipt`
   and `publish_text_assignment`.
2. Add `publish_attachment_status` (section 4.5) to `evidence_acquisition.py`;
   `status` outside `{"too_large", "unavailable", "foreign_origin"}` raises
   `PublicationRefused("invalid_status")`.
3. Close D17. In `publish_course_receipt`, replace the roster pre-registration
   loop (the `publisher._pseudo(...)` calls before `vault.save()`) with
   `roster_service.upsert_roster(publisher.vault, rows)`, where `rows` are the
   `course.roster` scope's dict rows; this is the call `store.write_roster` already
   makes. Then call `publisher.vault.require_stable(...)` per row, recording
   `identity_unresolved` gaps exactly as today. Do the same with the `roster`
   argument of `publish_text_assignment` before its replacement map is rebuilt.
   If upsert raises, record `identity_registration_failed` and omit that receipt's
   student-bearing scopes; do not proceed with a partially populated replacement
   map. Preserve independently verifiable student-free context and prior safe
   evidence. Return publication incomplete; never claim complete membership for
   omitted scopes. The successful path rebuilds the replacement map after
   registration, as today. This closes D17 without weakening the privacy boundary.

Tests:

- `test_evidence_acquisition.py::test_publication_ignores_conflicting_shared_descriptor`
  (parametrize both entry points): write an old `reader.v1.json` built from
  `VIEW_COLUMNS` minus `roster`/`sections`; publish; every scope succeeds; the old
  bytes are unchanged; no other `reader*.json` appears.
- `test_evidence_acquisition.py::test_attachment_status_republishes_gap_without_digest`.
- `test_evidence_acquisition.py::test_first_acquisition_scrubs_sis_id_and_nickname`
  (parametrize both entry points): an empty `SyntheticVault`; a roster row with
  `sis_user_id` and `short_name`; a submission body containing both. No safe file
  contains either value, and the vault now holds both.
- Change `test_evidence_publish.py:81` to assert `reader.v1.json` does **not** exist.
- Extend the first-acquisition privacy test with an upsert failure after one
  identity: no student text from that receipt is published and prior safe facts
  survive. A caught registration exception must never become unsafe success.

```powershell
py -m pytest api/tests/mirror/test_evidence_acquisition.py api/tests/mirror/test_evidence_publish.py -p no:randomly -q
```

Done when no publication path reads or writes a descriptor and existing privacy,
acknowledgement, and watermark tests pass unchanged.

### S02: future-version diagnostics

**Owner:** index worker. **Files:** `evidence_schema.py`, `evidence_store.py`, tests.

Implement section 4.4.

Tests:

- `test_evidence_schema.py::test_future_version_is_unsupported_not_invalid`
  (parametrize fact and commit, each with an extra unknown field).
- `test_evidence_store.py::test_future_record_is_reported_and_last_good_stays_current`:
  a complete v1 commit, then a v2 fact plus a v1 commit referencing it → issue code
  `unsupported_schema`, no diagnostics copy, scope `sync_pending`, v1 refs current,
  other scopes `ready`.

```powershell
py -m pytest api/tests/mirror/test_evidence_schema.py api/tests/mirror/test_evidence_store.py -p no:randomly -q
```

### S03: index checks, replacement, and descriptor

**Owner:** index worker. **Files:** `evidence_index.py`, `evidence_queries.py`,
`evidence_paths.py`, tests.

Implement section 4.3. Replace
`test_generated_reader_contract_has_one_registry_and_refuses_conflict` with the
descriptor test below.

Tests:

- `test_evidence_index.py::test_read_refuses_missing_mismatched_and_corrupt_index_without_writing`
  (parametrize; assert code, unchanged bytes, and no file created when missing).
- `test_evidence_index.py::test_mismatch_ingest_refuses_and_discard_with_open_handle_is_busy`
  (simulate the Windows handle by monkeypatching the main-file unlink to raise
  `PermissionError`; all existing files remain; malformed/schema-old files are
  still refused by reads, never described as readable).
- `test_evidence_index.py::test_interrupted_ingest_rolls_back` (`_project_derived` raises).
- `test_evidence_queries.py::test_descriptor_is_local_registry_derived_and_revision_bound`
  (views equal `VIEW_COLUMNS` including `roster`/`sections`; revision matches).
- `test_evidence_queries.py::test_direct_sql_matches_query_service` (contract test
  parametrized over `VIEW_COLUMNS`, read with `mode=ro`).
- `test_evidence_queries.py::test_attachment_summary_counts_pending_gaps_and_extractions`.

The existing pinned-revision test stays as the transaction law.

```powershell
py -m pytest api/tests/mirror/test_evidence_index.py api/tests/mirror/test_evidence_queries.py api/tests/mirror/test_evidence_paths.py -p no:randomly -q
```

Return final signatures of `discard`, `IndexBusy`, `write_reader_descriptor`, the
path helpers, and the `attachment_summary` shape.

### S04: queue selection, retries, association-keyed extraction

**Owner:** publication worker. **Files:** `evidence_jobs.py`,
`evidence_extraction.py`, tests. **Depends on:** S01.

Implement section 4.5. `summary()` uses a separate read-only connection helper.
Replace `test_extraction_entity_key_is_content_and_version_addressed` and
`test_stale_extractor_version_reprocesses_only_affected` (both tested the cache).

Durable ordering: publish extraction/terminal association first, then record its
job completion. Compute terminal capture outcomes (including retry exhaustion)
before saving them; `record` must not strand a terminal job whose publication
failed. If publication fails, leave work eligible for a later bounded
chunk. If the process stops after publication but before completion, retry the
same fact idempotently and then mark complete. For a missing original, publish
the unavailable association specified in section 4.5; do not leave
other computers with an indefinitely pending association. Preserve adapter-provided
`partial`/`unavailable` availability and reasons; a returned result is not necessarily
complete text. Sanitize exception codes; do not call every failure corruption.

Tests:

- `test_evidence_jobs.py::test_retry_exhausted_capture_is_terminal_until_reopened_at_start` (D2).
- `test_evidence_jobs.py::test_claim_orders_newest_first_and_skips_rows_without_file_id` (D3).
- `test_evidence_jobs.py::test_summary_creates_nothing_and_reads_old_schema_unaltered` (D12).
- `test_evidence_jobs.py::test_terminal_capture_status_is_published_once`.
- `test_evidence_extraction.py::test_identical_files_for_two_students_publish_two_extractions` (D5, D6).
- `test_evidence_extraction.py::test_more_than_one_chunk_extracts_every_job` (D4: 25
  jobs, limit 20, two calls, all `done`).
- `test_evidence_extraction.py::test_failures_publish_explicit_gaps_and_reopen_on_start`
  (unsupported type, missing dependency, timeout → `unavailable` facts with reasons;
  `reopen_extractions` resets them and version-changed `done` rows).
- `test_evidence_extraction.py::test_adapter_runs_outside_publisher_scope`.
- `test_evidence_jobs.py::test_ensure_preserves_existing_job_and_new_jobs_sort_first`.
- `test_evidence_extraction.py::test_publication_failure_never_marks_extraction_done`
  (also retry after publication succeeds but completion recording fails).
- `test_evidence_extraction.py::test_missing_original_publishes_gap_for_peer_reader`.

```powershell
py -m pytest api/tests/mirror/test_evidence_jobs.py api/tests/mirror/test_evidence_extraction.py -p no:randomly -q
```

Return final signatures and whether the service-chunk test broke.

## 8. Stage C — lead-owned service and lifecycle integration

### S05: stage results, refresh persistence, per-course rebuild

**Files:** `service.py`, `sync.py`, `coordinator.py`, `store.py` (refresh document), tests.

1. Add section 4.1 constants and helpers.
2. `_selected_runner.run()` sets `result["stages"] = _runner_stages(result)`; a raised
   runner becomes `{"ok": False, "error_class": "acquisition_failed", "stages": ...}`.
3. Coordinator: `_Job.stages` (default empty dict) copies `outcome["stages"]` only
   when it is a dict keyed by `acquisition`/`publication` with valid states;
   `_job_view` includes it when non-empty. Plan states and terminal rules unchanged.
4. `store.finish_refresh(..., failure_stage="")` per section 4.2. `sync.refresh`
   passes `"publication"` for `publication_incomplete`, else `"acquisition"` on failure.
   The pass-level publication gate in `sync.py` is otherwise unchanged.
5. `run_index_maintenance`:
   - Take the vault snapshot (section 4.6) in one short transaction; build
     publishers from it. No vault lock is held from here on.
   - Scan each course directory in its own `try`. A course with any facts or commits
     is healthy. An empty or unreadable directory, or one that raises, is not indexed
     and is listed in `not_arrived_courses`. A previously indexed course whose
     directory is absent simply drops out and is listed there too.
   - Collect `update_required_courses` from `unsupported_schema` issues.
   - No healthy courses: report `partial`/`course_evidence_not_arrived` (or
     `evidence_update_required` for unsupported records). Do not advertise an
     existing index's old rows as current evidence. With a usable index, ingest
     an empty validated `StoreSnapshot` through `EvidenceIndex.ingest` (not
     `ingest_many`, which rejects an empty aggregate). Its verifier refuses any
     record, its facts/commits/issues are empty, and its revision is a stable
     empty-source digest. This transaction removes absent whole-course rows
     under the already chosen rule. With no usable index, return missing
     evidence guidance; never write `ready` merely because no work was ingested.
   - `ingest_many(healthy, selected_courses=...)`. On `index_schema_mismatch`,
     `index_corrupt`, or `sqlite3.DatabaseError`: `discard()` then `ingest_many`
     again. On `IndexBusy`: status `failed`/`index_busy`, keep the old file.
   - After a successful ingest, `write_reader_descriptor`; failure sets
     `descriptor` to `{"state": "failed", "code": "descriptor_write_failed"}` only.
   - Write the maintenance status and return `{"state", "code", "revision",
     "courses": {"indexed": n, "not_arrived": [...], "update_required": [...]},
     "descriptor": {...}}`. Never call Canvas. Privacy verification still runs for
     every record; a rejected record stays out without blocking other records.
6. In S05, keep the existing inline call sites pointing at `run_index_maintenance`
   so the tree stays green; S06 replaces them.

Tests:

- Rewrite D16 as `test_service_evidence.py::test_one_course_problem_never_blocks_another`:
  courses 1 and 2 indexed; then course 2 removed and a malformed file added beside
  a course 1 change → course 1 change visible, course 2 listed `not_arrived`,
  malformed record excluded; restore → `ready`.
- `test_service_evidence.py::test_rebuild_from_safe_files_makes_zero_canvas_calls`
  (corrupt index and mismatched metadata cases; every `service.canvas_*` raises if called).
- `test_service_evidence.py::test_index_failure_after_publication_keeps_safe_files`
  (ingest raises once → status `failed`, files unchanged, rerun → `ready`).
- `test_service_evidence.py::test_descriptor_failure_keeps_index_ready`.
- Extend the missing-course example to all course directories absent: no stale
  rows returned as current, `not_arrived` reported, no Canvas calls.
- `test_service_evidence.py::test_maintenance_scans_without_holding_vault_lock` (D18):
  the vault transaction context records enter/exit; the scan stub asserts it is
  not inside; a privacy-trap record is still refused by the snapshot verifier.
- `test_sync.py::test_refresh_failure_records_stage_and_keeps_last_success`.
- `test_service_selection.py::test_runner_outcomes_map_to_stage_table` (parametrized
  over the section 4.1 table; also covers coordinator stage copying).

```powershell
py -m pytest api/tests/mirror/test_service_evidence.py api/tests/mirror/test_sync.py api/tests/mirror/test_coordinator.py api/tests/mirror/test_service_selection.py api/tests/mirror/test_store.py -p no:randomly -q
```

### S06: background index maintenance

**Files:** `service.py`, `api/runtime.py`, tests.

1. Implement section 4.6 maintenance: flag, wake event, worker loop, binding guard,
   and removal of inline rebuilds from `_publish_acquisition`, the chunk functions,
   and `mirror_heartbeat_worker`.
2. Runtime start/stop per section 4.6; delete the synchronous startup recovery and
   rebuild blocks. The worker accepts an injected `wait=` callable for tests.

Tests:

- Replace D15: `test_runtime_startup.py::test_start_starts_evidence_workers_after_recovery_and_runs_nothing_synchronously`
  and keep the idempotent stop assertions.
- `test_service_evidence.py::test_requests_coalesce_and_are_not_lost_during_rebuild`.
- `test_service_evidence.py::test_synced_file_arrival_is_indexed_on_the_next_cycle`.
- `test_service_evidence.py::test_owner_heartbeat_progresses_during_slow_rebuild`
  (block `ingest_many` on a barrier; `acquisition_owner_status(tick=True)` returns within 1 s).

```powershell
py -m pytest api/tests/test_runtime_startup.py api/tests/mirror/test_service_evidence.py api/tests/mirror/test_acquisition_owner.py api/tests/mirror/test_acquisition_requests.py -p no:randomly -q
```

### S07: attachment continuation

**Files:** `service.py`, tests.

1. `attachment_work_worker(stop_event)`: each iteration runs a capture chunk (only
   when capture is allowed) and an extraction chunk; `progressed` is true when any
   job changed state. Wait `ATTACHMENT_CHUNK_PAUSE_SECONDS` after progress, else
   `ATTACHMENT_IDLE_SECONDS`, waking early on `wake_evidence_workers()` or stop.
   Log exceptions as `mirror.evidence_work` (class only) and continue.
2. `run_attachment_capture_chunk`: no vault transaction around the download loop;
   `store_original` and `publish_terminal` each open `store._vault_transaction(root)`
   for one job. Request maintenance when `published > 0`.
3. `run_extraction_chunk`: `publisher_scope` is a `contextmanager` opening the vault
   transaction; request maintenance when `published > 0`.
4. `prepare_evidence_work(course_id=...)` calls `reopen_extractions` and
   `reopen_exhausted_captures` only when the control store exists. Startup calls it
   once in the worker. After a successful manual `course.refresh` or
   `course.feedback_refresh`, call it once for that course and wake the worker;
   do not hook individual receipts (one pass can publish several), roster/groups,
   structure-only refresh, background refresh, or continuation/status calls.

Tests:

- `test_service_evidence.py::test_attachments_drain_across_chunks_in_one_lifetime`:
  45 capture jobs and their extractions with synthetic transport/adapter; drive the
  worker with an injected wait until idle; all terminal; the newest refresh's jobs
  finish before older backlog.
- `test_service_evidence.py::test_vault_is_not_held_during_download_or_adapter`.
- `test_service_evidence.py::test_restart_resumes_without_redownloading_captured_original`
  (the recovery law moved from `test_evidence_activation.py`).
- `test_service_evidence.py::test_non_owner_extracts_but_never_downloads`.
- `test_service_evidence.py::test_explicit_refresh_retries_course_gaps_without_restart`
  (other course untouched; heartbeat and repeated status calls reopen nothing).
- `test_evidence_scoring.py` unchanged and green.

```powershell
py -m pytest api/tests/mirror/test_evidence_jobs.py api/tests/mirror/test_evidence_extraction.py api/tests/mirror/test_service_evidence.py api/tests/test_runtime_startup.py api/tests/mirror/test_evidence_scoring.py -p no:randomly -q
```

## 9. Stage D — agent and console surfaces

### S08: MCP reads through one resolver

**Files:** `api/mcp_server/tools.py`, `server.py` (only if a description becomes
false), MCP tests and fixtures.

1. Implement section 4.7; delete `_activated_evidence_lane`.
   Implement section 4.9 in `tools.py` and `server.py`; adjust refresh-loop
   guidance and `_SERVER_INSTRUCTIONS` for status continuation and partial reads.
2. Rewrite the evidence paths of `get_roster` (non-settings), `get_submissions`
   (current and history), `get_gradebook_snapshot`, and `get_assignment_evidence`.
   Keep parameters, gate order (`_saved_course_gate_check`, `_course_gate_check`),
   columns, text bounds, pagination, `final_response_gate`, and `freshness`. Add
   `coverage` and `warnings`; remove `activation_state`.
3. Delete the legacy branches and helpers with no other caller at `30b9465`:
   `_roster_sections`, `_roster_groups`, `_mirror_submission_bundle`, `_load_snapshot`,
   `_submission_history` (and `read_service.private_submission_history` if no caller
   remains). **Keep** `_mirror_roster_doc` and `_freshness_attention`. Re-run
   `git grep -n "<name>\b" -- api` before each deletion.
4. `_build_canvasmirror_guide`: replace the `reader.v1.json` line with
   "- Reader descriptor: `reader.json` beside `query.sqlite3`; compare its
   `index_revision` with `index_metadata` in the same read transaction."
5. Test migration. Add an `evidence_mirror` fixture to `api/tests/mcp_server/conftest.py`
   that isolates workspace, local cache, and vault like `_mount_mirror`, publishes
   `course_receipt_sample(...)` through the real `EvidencePublisher` with
   `SyntheticVault`, runs `run_index_maintenance`, and sets active courses. Triage
   every hit of
   `git grep -n -E "get_roster\(|get_submissions\(|get_gradebook_snapshot\(|_activated_evidence_lane|_mount_mirror" -- api/tests`:

   | Category | Action |
   |---|---|
   | A law that still holds (pseudonyms only, outbound gate, text bounds, columns, Current-course gate) | Convert to `evidence_mirror` |
   | Legacy staleness/`attention` behavior of these four tools | Rewrite to `coverage`/`warnings` |
   | Activation, checkpoint, or legacy fallback | Delete; list in the brief |
   | Settings path, writes, other tools | Leave unchanged |
   | `test_submission_history.py` cases bound to `_submission_history` | Keep laws that apply to evidence history (never blank an attempt, omitted text marked); delete private-file extraction cases |

   Tie-break: if a test might protect a privacy or pseudonym rule, convert it;
   never delete it. Summarize removed tests by file/category in the brief; enumerate
   individual cases only when the reason is not obvious from the diff.

New tests:

- `test_tools.py::test_reads_need_no_checkpoint_and_distinguish_missing_index`
  (fresh workspace serves; index absent with safe files → `evidence_index_pending`;
  nothing published → `evidence_refresh_required`).
- `test_tools.py::test_partial_evidence_serves_with_coverage_and_never_claims_complete_empty`
  (parametrize `partial_submissions`, `empty_submissions`, nothing published).
- `test_tools.py::test_revision_change_between_related_reads_retries_then_refuses`.
- `test_tools.py::test_old_assignment_is_served_with_age_not_refused`.
- `test_tools.py::test_gradebook_totals_exclude_incomplete_assignments`
  (two assignments, one `partial_submissions`; class average and missing totals
  equal the complete assignment's alone; the other id is listed and warned).
- `test_tools.py::test_catalog_and_submission_assignment_ids_agree_after_one_refresh`.
- `test_tools.py::test_refresh_continuation_never_enqueues_or_counts_a_retry`
  (queued, running, succeeded with index pending, fully ready, failed; both modes).
- `test_tools.py::test_refresh_continuation_rejects_wrong_course_mode_and_expired_id`.
- `test_tools.py::test_refresh_failure_guidance_is_stage_specific_without_browser_detour`.
- `test_tools.py::test_unrelated_course_gap_does_not_block_requested_evidence`.
- Extend future-version coverage with no supported index: actionable update
  refusal rather than perpetual `evidence_index_pending`.

```powershell
py -m pytest api/tests/mcp_server api/tests/mirror/test_evidence_queries.py api/tests/mirror/test_evidence_index.py api/tests/test_beta075_mcp.py api/tests/test_beta075_runtime.py api/tests/test_vault_conflict.py api/tests/test_roster_mcp_write.py -p no:randomly -q
```

Do not change `TOOL_SCHEMA_VERSION` here; S11 settles it.

### S09: delete obsolete cutover machinery

1. Re-run the S00 inventory. Expected remaining hits: only the modules below, their
   tests, and docs.
2. Delete `api/mirror/evidence_activation.py`, `evidence_activation_proof.py`,
   `evidence_import.py`, `evidence_migration.py` and their four test modules, after
   confirming S07 moved the recovery test. Delete `legacy_samples.py` and
   `test_legacy_samples.py` only if nothing else imports them.
3. Add `RETIRED_PATHS` rows in `api/tests/test_retired_paths.py`, reason
   `'CanvasMirror functional read path: no activation/import cutover'`.
4. `evidence_status()` shape: `{"state": <index stage state>, "index":
   <maintenance status>, "attachments": <summary + stage>, "acquisition_owner": {...},
   "gaps": [...]}`.
5. Do not delete `activation.v1.json` or `extraction.sqlite3` on disk.

```powershell
py -m pytest api/tests/test_runtime_startup.py api/tests/test_retired_paths.py -p no:randomly -q
```

Record the remaining private-projection consumers from
`git grep -n -E "store\.read_(roster|submissions|assignments)|read_service\." -- api ':!api/tests'`
in the brief. Their producers stay.

### S10: stages on existing console surfaces

**Files:** `api/webui/routes/mirror.py`, `api/webui/static/canvasagent.js`, service
status functions, tests. Read the brief's named console reference sections first.

1. `service.status()` adds per course `"refresh": {"state", "last_attempt_at",
   "last_success_at", "failure_stage", "error_code"}` and top-level
   `"evidence": evidence_status()`. `status(plan_id)` adds `"stages"`:
   acquisition/publication as the worst across plan jobs
   (`failed > partial > pending > not_run > ready`) plus course-scoped `index` and
   `attachments` from `evidence_stages(course_id=...)`.
   Queued/running jobs without an outcome contribute `pending` acquisition, not
   `not_run` or success. Do not let the terminal plan state erase owner-waiting or
   partial stage information. `not_run` means an unrequested stage, never a
   completed stage whose work has not started.
2. `status()` and `evidence_status()` open no write connection and create no file
   (existing acquisition-owner observation is out of scope). Corrupt status JSON
   reads as defaults.
3. `canvasagent.js`: keep the freshness summary, then apply section 4.8 detail text
   with precedence update required > index failed > index pending > not arrived,
   and append the backlog sentence. Those four states set card state `"attention"`.
   After `pollPlan`, choose the refresh text from `plan.stages`; print
   "Refresh complete." only when `fully_ready`.
4. Keep `/api/mirror/evidence-status`. No new routes.

Tests:

- `test_desk_routes.py::test_mirror_status_has_no_side_effects_on_absent_stores`.
- `test_desk_routes.py::test_mirror_status_reports_failed_stage_and_last_success`.
- `test_route_contract.py` and `test_readiness_routes.py` unchanged and green.

Browser gate: use an isolated launch environment (config, local cache, credentials,
and workspace) before starting the app. `.claude/launch.json` alone does not isolate
the teacher's stores; do not switch the live teacher configuration in Settings to
set up a test. Reuse the pytest isolation conventions in a test-only launch helper
if needed, with a fake Canvas transport. Load `/` and
`/settings` in two states: fully ready (publish `course_receipt_sample("full")`,
let maintenance run) and failed refresh stage (seed `_refresh.v1.json` with
`state: "failed"`, `failure_stage: "publication"`, a `last_success_at`). Confirm
the section 4.8 text, required globals, and zero new console errors; screenshot
both. Verify the ready, indexing-pending, and attachment-pending text through route
tests; the two rendered states above suffice unless a different route changes.
Clean up only the scratch files created for this run.

## 10. Stage E — S11: integration, documents, synthetic acceptance

### 10.1 Documents

- `docs/contracts/canvasmirror-evidence-contract.md`: **Storage** (local
  `reader.json`, no shared descriptor), **Records** (`unsupported_schema`),
  **Views** (registry-derived list including `roster`/`sections`; checked index
  schema), extraction keyed by association, terminal attachment statuses.
- `docs/contracts/agent-runtime-product-contract.md`: background maintenance,
  resolver refusal codes, coverage/warnings, no activation.
- `docs/mirror.md`: **On-disk layout** (`maintenance.v1.json`, `reader.json`, job
  columns, no extraction cache), **Sync passes** (stage table), **Scheduling** (two
  evidence workers, 30 s cycle, owner rules); remove activation text.
- `docs/guides/canvasmirror-agent-reading.md`: descriptor location, revision check,
  pending/refresh guidance, equivalent MCP views.
- `docs/reference/canvasmirror-synced-store-direction.md`: status, limitations,
  next-assessment pointer (finalized in S13).
- `docs/mcp-server.md` where it describes these reads.

Then search `git grep -n -E "reader\.v1|activation checkpoint|activate_read_authority|import proof|migrate_legacy" -- docs api`.
Classify hits: current runtime references must be removed; regression fixtures,
retired-path assertions, and this plan's explicit removal instructions are valid.
Do not require zero text matches or delete useful regression coverage to get them.

### 10.2 MCP contract settlement

`git fetch origin`; take the next free `TOOL_SCHEMA_VERSION` above `origin/dev`
(81 at baseline; never pre-reserve). Regenerate the snapshot through the existing
pytest path, confirm the only intended input change is optional `operation_id`
on `refresh_mirror`, re-measure the listing budget in
`test_server_instructions.py`, and update the inventory.

### 10.3 Integrated scenarios

One composed module, `api/tests/mcp_server/test_evidence_read_path.py`, uses the
real publisher, index, queue, query service, and MCP tools; only Canvas transport
and adapters are stubbed. Five examples cover the brief's criteria together:

| Test | Covers |
|---|---|
| `test_old_descriptor_fresh_workspace_text_and_attachments_reach_mcp` — `read_path` receipt beside an old `reader.v1.json`; worker drains; `get_submissions` text, `get_assignment_evidence` docx blocks, pdf gap explicit | 1, 2 |
| `test_second_course_delay_and_corrupt_index_recover_locally` — `second_course` absent while course 1 updates; corrupt the index; maintenance restores both after arrival with zero Canvas calls | 1, 3 |
| `test_partial_and_future_version_evidence_reads_honestly` — partial submissions, a v2 record, empty-complete vs never acquired, paginated history pinned to one revision | 4 |
| `test_switching_partitions_and_restart_need_no_activation` — two local cache roots over one safe root; partition B indexes A's evidence; stop/start runtime; identity traps seeded in receipts are absent from safe files, index, and outputs | 5 |
| `test_failure_after_success_and_no_calendar_backlog` — failed refresh keeps last success and shows the stage in `refresh_mirror` and `/api/mirror/status`; newly created attachment jobs progress ahead of 30 older backlog jobs; gradebook totals exclude an incomplete assignment and say so; old assignment served | 6, 7 |

The existing scoring-hold tests remain the proof that read availability does not
authorize unsafe scoring (criterion 5).

Extend the partition example with pending associations and an empty local job
store on B: availability remains pending. Extend the failure example with
section 4.9 continuation: after acquisition completes, polling causes zero new
Canvas calls and text reads proceed before every attachment completes.
Reuse `test_content_push_tools.py` coverage in the MCP/full gate to guard the
publishing workflow; do not add live pushes or require mirror/OCR readiness for it.

### 10.4 Gate and accounting

Slice commands provide focused feedback while implementing. Run the full suite
once on the integrated tree; do not precede it with another overlapping broad
subset merely to obtain a second check mark. Repeat only after relevant edits or
a failure. S09 deletion checks supplement S08 rather than rerunning all its tests.

```powershell
.venv\Scripts\python -m pytest api/tests engine/tests -p no:randomly -q
git diff --check
```

Real DOCX/PDF/PPTX/XLSX/JPG adapters and local OCR must pass in `.venv`. Remaining
failures must be independently baselined and outside changed behavior (the brief
records the QuizForge node). New or relevant failures prevent synthetic GREEN;
do not assume a predetermined failure count. Confirm no private
fixtures, manifests, or logs in `git status`. Senior reviews the MCP contract diff
and the failure, idempotency, and revision seams. Overall stays YELLOW until S12/S13.

## 11. Stage F — field acceptance and closure

### S12: updated pilot runtime and bounded optional reset

1. Find the installed runtime (`CE_DATA` in `Open Canvas Expert.bat`/`Repair.bat`)
   and confirm its source revision. Announce: GET-only acquisition for one Current
   course, local maintenance writes, which computer, no Canvas writes or operation
   retries.
2. Update both computers through the supported procedure before any shared reset.
3. Try without reset first: existing safe files should index; old
   `activation.v1.json`, `reader.v1.json`, and `extraction.sqlite3` are ignored.
4. Reset only if step 3 fails in a way the code cannot repair: private manifest
   outside the repo per brief section 4, containment and reparse checks, announce
   the listed targets, both runtimes stopped, sync settled after deletion. The
   bounded reset is already authorized; ask only if a target falls outside it.
5. Acquire one Current course; verify assignment context, text, supported
   attachment blocks, gaps, coverage/age, and stages. Restart once; verify. Sample
   one assignment in each remaining Current course.

Report aggregates only.

### S13: actual second computer, final acceptance, retirement

On the actual second computer, confirm the tested `dev` revision, same selected
workspace and Identity Vault transfer-key fingerprint, and completed hydration of
`_Shared/`, `CanvasMirror/`, and CanvasMirror control files. Resolve any shared-store
conflict before student-data reads. Restart an older runtime so it loads that code.
Before any Canvas refresh, let startup index the synced safe evidence locally; read
the sampled assignment context, submissions, and available attachment/extraction
evidence through CE. Confirm `ok`, coverage, warnings, revision, section labels,
and explicit attachment gaps. Restart the runtime and repeat those reads without
activation, import, or a Canvas refresh. Distinguish sync delay, update required,
index pending/failure, acquisition failure, and attachment gap; local index repair
may be needed. Report only aggregate counts, durations, stage/error codes, and
whether restart required intervention. Never claim two-machine success from local
partitions. This acceptance covers CanvasMirror read portability, not the separate
one-week scoring/work-handoff checks in `docs/guides/more-than-one-computer.md`.

On GREEN: retire the brief in the same batch; mark this plan "Completed —
reference only"; leave one pointer in the direction document to the next senior
assessment (scoring consumption and remaining private projections) with exact
sections and any open decision.

## 12. Result recording, stop conditions, and settled decisions

Per slice, one row in the brief's execution result:

| Slice | State | Commit(s)/files | Focused command and count | Acceptance evidence | Deviation/blocker |
|---|---|---|---|---|---|

Commits are optional until integration; if made, keep them on `dev`, reviewable,
and free of unrelated work.

Stop for the senior/teacher only at the brief's boundaries: Canvas write/scoring
changes, an unlisted destructive target, replacing the evidence format/storage
engine, a demonstrated privacy flaw requiring a different plan, or changed code
that invalidates a locked behavior. Routine source drift is not a stop condition.

Settled:

- Evidence `SCHEMA_VERSION` stays 1 and `INDEX_SCHEMA_VERSION` stays 2; the index
  schema is now checked on read and ingest.
- Descriptor: `reader.json` beside `query.sqlite3`; the shared one is ignored, not deleted by code.
- Status lives in the refresh document (per course) and `maintenance.v1.json` (per source).
- Last-good data within a course comes from the commit reducer. A course whose
  directory is absent is reported `not_arrived`, not retained from old index rows.
- Future-version records get code `unsupported_schema` and the course is listed as
  update required; the reducer treats them like any other issue.
- Queue order is newest first for newly created jobs. Existing associations keep
  their original order; no assignment-targeted priority is promised. No priority
  flag, tiers, dates, or calendar (teacher, 2026-10-05; clarified by code inspection).
- No extraction cache. One `extraction_state` per job; any failure is an explicit
  gap, reopened at startup or once per successful explicit course refresh.
  Extraction identity is the association.
- Retry-exhausted capture becomes `unavailable`, is published as a gap, and is
  reopened at startup or once per successful explicit course refresh.
- Second computers read synced extraction facts; they do not rebuild a capture queue.
- The maintenance worker rebuilds every 30 s, relying on the unchanged-revision
  skip; add a cheaper change check only if measured slow. It verifies against a
  short-lived vault snapshot and never holds the vault lock while scanning
  (teacher, 2026-10-05).
- Gradebook totals use only assignments with complete submission coverage; the rest
  are listed and warned, never silently averaged in (teacher, 2026-10-05).
- First acquisition registers name, SIS ID, and nickname before scrubbing (D17;
  teacher, 2026-10-05).
- The pass-level publication gate in `sync.py` is preserved; reads no longer depend on it.
- Agent reads (evidence index) and scoring (private projections) can disagree
  after a refresh with a publication problem. Accepted for this milestone; the
  next senior assessment owns it.
- Legacy MCP read fallbacks and their helpers are deleted; `_mirror_roster_doc` and
  `_freshness_attention` stay.
- `refresh_mirror(operation_id=...)` observes an existing plan without dispatch;
  generic browser detours and confirmation for read-only refresh are removed.
- Switching is proven synthetically with two local partitions in one process; the
  actual second computer (S13) is the real proof.

Removed during simplification (do not reintroduce without a measured need):
per-course index row retention, a priority tier scheme, extraction backoff and
attempt counters, the extraction cache, a file-stat change fingerprint, publication
acknowledgement counts, a new reducer status for future versions, timestamp-derived
index pending rules, a two-subprocess test harness, and five-state browser seeding.
