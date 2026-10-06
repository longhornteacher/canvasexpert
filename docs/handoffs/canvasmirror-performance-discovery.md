# Execution brief: fast, reliable CanvasMirror discovery

**Status:** READY FOR EXECUTION; senior planning only, 2026-10-06.
**Target:** `dev`. **Inspected baseline:** `c2e0b60`.
**Authority:** teacher selected the bounded performance/discovery batch.
**Acceptance:** senior accepts the integrated implementation and field evidence.

This supersedes `canvasmirror-functional-read-path.md`, whose incomplete field
acceptance remains YELLOW, not retroactively GREEN. Its relevant evidence and
open checks are preserved in the investigation below. Do not execute its old
slice plan or infer permission to reset from its former reset provision.

## 1. Teacher-visible outcome

“What needs grading?” promptly returns useful work from all Current courses
whose saved evidence is readable, even during background acquisition or indexing.
One course's missing evidence produces an honest gap, not an empty or failed
whole-workspace answer. No refresh, history scan, packet materialization, or
lease renewal happens as a side effect of discovery.

CM publication must stop rescanning retained course history for every new scope.
The teacher must be able to read while background work progresses. Improvements
are accepted against normal pilot scale and both actual computers, not just
small fixtures or a responsive ping alongside an unfinished tool.

Scoring preparation, feedback revision, and using extracted attachments in a
scoring packet are the **next** assessment, explicitly chosen by the teacher as
outside this batch. Discovery does not promise that every found item is ready
to score. Preserve the owning preparation/write checks.

## 2. Required reading and preflight

Read `AGENTS.md`, `docs/reference/project-state.md`, and:

- `docs/reference/canvasmirror-performance-discovery-investigation.md`, sections
  **1–7** (measured facts, limitations, carried field evidence).
- `docs/reference/canvasmirror-performance-discovery-slices.md`, sections **1–5**,
  then only the assigned slice in **6**, and acceptance in **7**.
- `docs/contracts/agent-runtime-product-contract.md`: **Primary interface**,
  **Canonical cooperation loop**, **Runtime boundaries → Read spine**.
- `docs/contracts/canvasmirror-evidence-contract.md`: **Storage**, **Records**,
  **Reduction**, **Privacy**, **Views**.
- `docs/guides/scoring-sessions.md` and
  `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`
  (required scoring context). Do not edit their pedagogical rules.

Inspect status; fetch and compare `dev` with both remote durable branches.
Preserve unrelated changes. Read assigned source/test owners before editing.
Do not read the previous slice plan or archived handoffs as authority.
Use pytest isolation for synthetic work. Before field work confirm the actual
runtime source and loaded revision; shell checkout identity alone is insufficient.

## 3. Locked decisions

1. **Discovery uses the evidence index.** One read transaction pins roster,
   assignment, submission facts and their coverage across all selected courses.
   No `scoring_local.load_scoring_snapshot`, private mirror documents, private
   refresh sidecars, or per-assignment vault opens in this path. Missing coverage
   is explicit; a stale available observation remains readable with its age.
2. **Keep privacy gates.** Reuse the runtime's workspace/source and vault-conflict
   checks once per discovery request. Do not remove the final boundary gate.
   Discovery returns student-free aggregates, course/assignment navigation, and
   opaque session IDs; no pseudonym lists, private paths, student data or bodies.
   No long-lived identity cache or replacement privacy policy.
3. **Keep existing work storage.** A summary-only read filters manifests by
   kind/course, folds each relevant event stream once, verifies the latest
   session snapshot blob, then projects resume metadata. It never loads SAFE
   bundle blobs, writes leases, quarantines events, or repairs work items.
   No new shared summary format, index database, or migration of live sessions.
4. **Publish in bounded batches.** One validated course scan establishes a
   receipt's publication context. Reuse that state plus records successfully
   published in the batch for dependency checks; do not rescan the entire course
   for each commit. Preserve exact-scope completeness, last-good reduction,
   graph validation, facts-before-commits, immutable publication, and safe refusal.
5. **Shorten the vault critical section.** Register/resolve receipt identities
   and freeze a private request-local snapshot under existing vault semantics.
   Course-history scans, fact/commit file publication and attachment-queue work
   run outside it. Frozen publication cannot assign a previously unresolved
   identity. All safe records still pass scrubbing/schema/privacy validation.
6. **No blanket readiness gate.** A background `syncing`/`failed` private refresh
   cannot refuse readable evidence. Incomplete or unsupported evidence is scoped
   to affected courses/assignments; no complete-empty inference from a gap.
   A resume lookup failure leaves grading discovery usable with an explicit
   resume warning, rather than claiming there is no existing session.
7. **Maintain runtime responsiveness.** Keep discovery's off-event-loop dispatch.
   Do not add thread pools, worker counts, speculative caches or timers that
   return while unbounded abandoned work continues. Retain current coordinator
   and ownership semantics; fix work amplification rather than masking it.

8. **Validate each immutable file once per verifier state (D07, teacher-approved
   2026-10-06).** A runtime process may memoize, in memory only, the outcome of
   validating one evidence file under an exact verification key: a digest of
   every input `verify_safe` depends on (scope, stable pseudonyms, folded name
   tokens, identifiers). A new, changed (size/mtime) or removed file, a racy file
   (mtime within 2 s of the scan start), or any key change is re-read and fully
   re-checked. Graph validation and reduction still run over the whole set. This
   is an exact memo of a pure result, not a speculative cache under decision 7.
9. **Persisted pass fingerprint (D08, pre-approved 2026-10-06, conditional).**
   Only if the restart/background gates still miss after D07: persist a per-course
   input fingerprint so a restart skips an unchanged pass. It must not put any
   identifier-derived value in the agent-readable index (keyed digest in the
   private local area), and it must include a validation-code digest so a code
   change forces one full pass.

No evidence format/index schema change is planned. No new safe fact fields are
needed for discovery's current counting rules. MCP result changes in the slice
plan are explicit; the lead owns version/snapshot/inventory/budget settlement.

## 4. Scope, exclusions and workstreams

Execute D00–D06 in the linked plan. After D00, publication and work-summary
workers may run in parallel; give each the exact owned files. The lead handles
the query/discovery path and shared integration. One executor may do it all.

| Owner | Exclusive source ownership | Tests |
|---|---|---|
| Publication worker | `api/mirror/evidence_store.py`, `evidence_publish.py`, `evidence_acquisition.py` | Matching `api/tests/mirror/` files and assigned new batch-performance tests |
| Work-summary worker | `api/shared_work.py`, `api/powergrader/session_store.py` | `api/tests/test_shared_work.py`, matching session-store tests; no shared conftest edits |
| Lead | `api/mirror/service.py`, `evidence_queries.py`, `api/powergrader/scoring_discovery.py`, `api/mcp_server/tools.py`, `server.py`, schema/inventory owners, all docs and shared fixtures | Matching service/query/discovery/MCP tests and integration/performance harness |

Lead integrates the publication snapshot into `service.py`; the worker must not
edit that shared owner. No change to `identity_vault_service.py`,
`shared_vault.py`, `storage_support.py`, or `shared_storage.py` is assumed.
If a helper there is truly needed, the lead first records a narrow design that
preserves their other callers; never assign overlapping files to workers.

Excluded: new Web UI surfaces, live Canvas writes, scoring semantics/holds,
session replacement or cleanup, full scoring-preparation migration, general
shared-work redesign, retirement of private projections with remaining consumers,
evidence resets/deletion, calendar eligibility, attachment/history backfill,
new storage engines, and persistent incremental-validation caches (except D08
under decision 9).
Keep the 30-second maintenance mechanism and its current validation guarantees;
measure it, but do not turn this batch into a general indexing rewrite.

## 5. Acceptance and gate

All functional, structural-cost and field gates in plan section **7** are
required. Key results:

- Actual three-course discovery returns useful evidence-based results while the
  old private lifecycle is `syncing`/`failed`. Unknown counts are not zero work.
- No network calls or discovery-time filesystem mutations; constant-count vault
  and shared conflict scans; no SAFE bundle reads or per-assignment queries.
- Publication scan count is independent of scope count; expensive evidence I/O
  is outside the vault lock; privacy and graph failures remain fail-closed.
- One pinned discovery revision, deterministic resume selection, bounded
  partial failure, unchanged preparation/apply safeguards.
- Pilot discovery: warmed p95 ≤2 s and max ≤5 s; first call after restart ≤5 s;
  under background publication/index work p95 ≤5 s and max ≤10 s. Measure tool
  completion, not just ping. These are batch acceptance targets, not a promise
  about Canvas network speed or cloud-provider hydration.

Focused commands are in each slice. Before synthetic GREEN run:
`.venv\Scripts\python.exe -m pytest api/tests engine/tests -p no:randomly -q`.
Do not rerun a broad deselected matrix after every slice. No browser route is
changed in this batch; a UI rendering gate is owed only if that scope changes.

Announced field work: local discovery/read checks; one bounded CE GET-only
course refresh to exercise background publication; normal local cache/receipt
writes; orderly runtime restart after checking active work. No Canvas writes,
operation retries, scoring-session takeover, destructive reset, or direct token
handling. Read only SAFE evidence or emit CE-owned aggregate diagnostics.
Observe and report other normal runtime activity honestly.

## 6. Stop conditions and closure

Ask the senior for changes to scoring/write/identity semantics, a new persistent
store, destructive live-state changes, or a field target requiring a different
architecture. Report a measured target miss with the responsible stage; do not
weaken it or call the batch GREEN. Routine refactors, measured SQL tuning,
fixture construction, and result-schema settlement are executor work.

If the second computer is unavailable, code/synthetic work can finish but field
acceptance stays YELLOW. Carry desktop S12 text/attachment/restart evidence from
the investigation into D06 rather than losing it on handoff. No prior test count
substitutes for the current integrated gate.

Record results here: light, commits, files, exact commands/counts, before/after
metrics, field environment/loaded revision (no private identifiers), deviations
and unresolved questions. On actual GREEN the senior accepts and retires this
brief in the same batch; mark the plan completed and leave one current pointer
to the scoring-input assessment described in investigation section **6**.

## 6A. Amendment (2026-10-06): work proportional to change (D07, D08)

Why: the background gate misses because whole-course re-validation runs in
four places: every 30 s maintenance tick even when nothing changed, each
publication context, two unpaced scans per extracted attachment, and notes.
`EvidenceIndex.ingest` then re-validates every record the scan just validated,
and `StoreSnapshot.scopes` re-reduces every scope on each access (once per
assignment in `_project_derived`). I/O-heavy tools (discovery resume: 10.7 s)
stall behind these CPU-bound threads; more yielding cannot fix that.

**D07 (decision 8).** Interface already on `dev` worktree in `evidence_store.py`:
`EvidenceStore(..., verification_key=callable|None)`, `StoreSnapshot.sealed`,
`is_validated_scan`, `seal_if_validated`; `scan()` seals its result.

| Owner | Exclusive files | Work |
|---|---|---|
| W1 (Sonnet) | `api/mirror/evidence_store.py`, `api/tests/mirror/test_evidence_store*.py` | Process-lifetime memo in `scan()` keyed by course root, diagnostics root and `verification_key()`; listing by `os.scandir` (size, mtime_ns); racy rule; containment for every read; memoize `scopes` per snapshot; full graph validation each scan. `verification_key=None` keeps today's behavior. |
| W2 (Sonnet) | `api/mirror/evidence_publish.py`, `api/tests/mirror/test_evidence_publish.py` | `EvidencePublisher.verification_key()` derived from the same inputs as `_privacy_context` (one source of truth); pass it to the publisher's `EvidenceStore`. Never logged or persisted. |
| W3 (Sonnet) | `api/mirror/evidence_index.py`, `api/tests/mirror/test_evidence_index.py` | Skip per-record re-validation only for `is_validated_scan` snapshots; `ingest_many` seals the aggregate via `seal_if_validated`; read `scopes` once per ingest. Unsealed input keeps every check. |
| Lead | `api/mirror/service.py`, `api/tests/mirror/test_service_evidence.py`, conftests, docs, this brief | Maintenance passes `verification_key`; integration laws; full gate; field re-measurement. |

D07 acceptance:
- **Law:** memoized scan equals a full scan (facts, commits, issues, revision) for
  unchanged, added, removed, rewritten, invalid, unsupported-schema and
  graph-invalid files, and after a key change in either direction (new name
  refuses a cached-safe record; new stable pseudonym admits a cached-refused one).
- **Law:** an unsealed snapshot with an unsafe or malformed record is still refused by `ingest`.
- Repeat scan with nothing changed: zero file reads, zero verifier calls. One new
  file: one read, one verify. A second maintenance pass with nothing changed: zero
  verifier calls, same revision, no SQLite rewrite.
- `reduce_scope` runs once per scope per snapshot.
- Full suite green; field: idle maintenance tick duration, then the background
  gate in section 5 re-measured on the laptop.

**D08 (decision 9)** starts only if the D07 field run still misses the restart or
background gate; the lead records the measurement here first.

## 7. Execution result

**YELLOW: D00-D05 implemented synthetically; D06 field acceptance not started.** Base `c2e0b60`; nothing committed.

- **D01 publication:** one validated whole-course scan per receipt (was 42 at 40 scopes); verifier calls at 40 scopes 3,647/7,951 (first/repeat) -> 285/448; 40-scope repeat receipt about 17.5 s -> about 2.1 s (about 8x, noisy machine). Identity snapshot is frozen under the vault, and scan, file publication and queue work run outside it (`test_publish_acquisition_holds_vault_only_for_identity_resolution`). Extraction and notes publishers still scan twice per call.
- **D02 resume summaries:** 25-session fixture, zero bundle reads, zero lease heartbeats/writes/quarantines, one conflict inventory (was 126). Late events are reported as `notices`, not `attention` (open question for the senior).
- **D03/D04 discovery:** evidence-index only; 6 data SELECTs in one pinned transaction regardless of size; per-course scoping for update-required courses. `mirror_revision` is now the opaque index revision.
- **D05:** `TOOL_SCHEMA_VERSION` 83 (inputs unchanged, snapshot renamed). Docs updated: `docs/mcp-server.md`, `docs/guides/scoring-sessions.md`.
- **Gate:** full suite `api/tests engine/tests -p no:randomly -q`: **2637 passed, 1 skipped, 0 failed** (after the D06 changes below). Not done: D00 shared conftest fixtures and pilot-scale fixture (local fixtures used).

### D06 field results (laptop, `c2e0b60` + uncommitted batch, 2026-10-06)

Environment: Windows laptop, workspace in OneDrive (files hydrated at measurement: about 0.1 ms/file), three Current courses, about 5.6k safe evidence records. Real stdio transport via `tools/`-style client harness (scratchpad `field_harness.py`, aggregates only; not committed). Codex-hosted CE runtimes running pre-fix code were stopped at the teacher's instruction. Each harness run starts a fresh runtime inside the client process.

| Gate | Result | Target |
|---|---|---|
| Discovery correctness | 5 assignments, 45 ungraded, 3/3 courses usable, all `coverage=complete`; 20/20 identical | useful, honest |
| Cold first call after restart | 1.08 s (3.0 s in an earlier run with startup work overlapping); runtime ready 2.5 s | <=5 s |
| Warm, 20 sequential (90 s settle) | p95 1.91 s, max 2.26 s, median 1.41 s | p95 <=2, max <=5: **met** |
| Background (20 calls overlapping startup index maintenance + one CE GET-only refresh of one course) | discovery p95 14.4 s, max 35 s; ping p95 9.5 s, max 25 s | p95 <=5, max <=10, ping p95 <=1: **missed** |

Findings:
1. **The original 200 s+ symptom is reproduced and attributed.** The old code's discovery logged 222 s (`refused`) and later 89 s (`error`) while a runtime burned a full core. Cause: `EvidencePublisher.verify_safe` ran one regex per name token and per identifier for every string (30.6M searches per maintenance pass); one normal maintenance pass cost about 390 s CPU (profiled), repeated back-to-back by the 30 s worker, starving every tool of the GIL.
2. **Fix (behavior-preserving):** one combined matcher per frozen vault snapshot (`_privacy_context`), same accept/refuse decision; equivalence tested against `feedback_scrub.find_token_matches` and the per-identifier search (`test_evidence_privacy_matcher.py`, 37 cases). Pass: 390 s -> 75 s profiled (verify 350 s -> 8 s); in the runtime a pass logged 27-50 s.
3. **Remaining miss is the maintenance pass itself**, not discovery: while a pass runs (startup and after each publication) other tools stall (query stage 13.5 s, ping 9 s, discovery 14-35 s); once it ends discovery returns to about 1.2 s. The pass still re-reads, re-hashes and re-validates every record each time (scan + `_contained` realpath + ingest). Per the brief this is the measured boundary to report; no persistent cache or dropped check was added. Senior decision needed: bounded incremental maintenance (skip unchanged course scopes), or yielding between records.
4. Instrumentation added (fixed events, durations only): `mcp.discovery_reader/_query/_resume`, `mirror.index_maintenance`, `mirror.evidence_work_chunk`, `mirror.publication_identity_lock/_scan`, `mirror.publication`. Publication was not separately timed in the field because the refresh returned `syncing` at its 25 s limit while maintenance was running.
5. A teacher-requested clarification: late/resubmitted-work notices stay quiet `notices`, not attention.

Not done: second computer (desktop) discovery/restart check, S12 attachment evidence carry-over, per-computer fingerprint matched/unverified, D00 conftest fixtures. Light stays **YELLOW**: background-overlap gate missed with responsible stage identified.

### D06 re-measurement after yield + refresh dispatch (laptop, `77891dd` + `refresh_mirror` off-loop, 2026-10-06)

Found: earlier harness runs attached as a stdio proxy to an already-running older runtime, so they did not exercise the fixes; that runtime was stopped (teacher-approved) and runs repeated on current code. `refresh_mirror` was a sync tool run on the event loop for its 25 s wait, so it stalled every other tool; it now dispatches via `asyncio.to_thread` (`test_refresh_mirror_runs_off_the_event_loop_thread`; `api/tests/mcp_server` 369 passed).

| Gate | Result | Target |
|---|---|---|
| Warm, 20 sequential | p95 2.06 s, max 3.46 s, median 1.57 s | p95 <=2, max <=5: p95 marginal miss |
| Background (refresh + maintenance overlap) | discovery p95 8.2 s, max 20.6 s; ping p95 3.3 s, max 9.8 s | p95 <=5, max <=10, ping p95 <=1: **missed**, improved from 14.4/35/9.5 |

Light stays **YELLOW**. Remaining stall is index maintenance after publication (yield slices did not remove it); candidates: larger sleep share, yield in the SQLite insert/projection loop, or skip-unchanged-scope maintenance (needs senior decision). Desktop check and S12 carry-over still not done. Finding for senior: receipt publication drops Canvas `excused` (`evidence_acquisition._submission`), so the index cannot apply the non-excused counting rule.

### D06 option 1 (yield inside index ingest insert/projection loops, 2026-10-06)

`EvidenceIndex.ingest`/`_project_derived` now checkpoint per fact, scope, projection record and comparison bucket (`api/tests/mirror` 633 passed). Field, current code: warm p95 1.46 s, max 1.57 s, median 1.27 s (**met**). Background: discovery p95 8.26 s, max 21.1 s (median 2.5 s); ping p95 4.52 s, max 5.6 s (max improved from 9.8 s, p95 not). **Background gate still missed**, so the stall is not only GIL starvation in the ingest loops; a single discovery call still takes about 21 s once. Next step is stage timing (the `mcp.discovery_*` and `mirror.publication_identity_lock` events) to see whether discovery waits on the vault lock held by publication's identity step or on another lock, before choosing between that and skip-unchanged-scope maintenance.
