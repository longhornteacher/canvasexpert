# Execution brief: CanvasMirror functional read path

**Status:** Coding preflight complete; ready for execution slicing. Acceptance
environment is YELLOW (baseline failures below). Grading periods are agent
guidance, not a runtime gate. No implementation or reset performed.
**Target:** `dev`. **Inspected baseline:** `30b9465`, 2026-10-05.
**Owner:** one lead executor; senior accepts the integrated result.
**Workflow review:** 2026-10-05 at `a9a3c03`, documentation only. Preserve the
earlier baseline below; no implementation acceptance is implied by this review.
This replaces `canvasmirror-read-activation-repair.md`. It implements the teacher's
2026-10-05 decisions, not its mandatory historical-import requirement.

## 1. Outcome and authority

**Central requirement: make current work easy, and do not make historical
completeness anybody's problem.** This governs scope, scheduling, readiness, and
acceptance throughout this brief.

The agent uses the teacher's grading-period context to choose relevant work.
CE/CanvasMirror executes bounded requests without enforcing that teaching policy.
No mandatory calendar configuration, period-membership classification, rollover
state machine, three-week cutoff, or date-based refusal/cancellation belongs in
this batch. Missing or ambiguous period information never blocks reads or refresh.
An explicit request for older work remains valid. Older evidence returned
incidentally by a bounded Canvas collection read need not be filtered out.

Previously captured history remains useful where available; absent older work
does not cause backfill, readiness failure, or a requirement to reconstruct past
attempts. Reusable course context remains available independently of its age.
Calendar knowledge belongs in the teacher/agent's private guidance, not repository
defaults or a new CE settings prerequisite.

An agent reliably reads Current-course assignment context, submissions, and
available attachment text through CE, with explicit age, completeness, and gaps.
Restart and switching compatible computers require no import or activation ritual.
Canvas remains authoritative. This is a reading milestone, not scoring acceptance.

The practical test is whether the teacher can publish assignments/quizzes/pages
and obtain student work for scoring/feedback without managing the mirror. Never
introduce a mirror-wide readiness, OCR, attachment-backlog, or history-completeness
gate on authoring or unrelated content operations. Keep the actual operation's
privacy, target, review, and live verification requirements. A useful partial read
is success with gaps; `fully_ready` is status, not permission to work.

Teacher decisions: old/partial evidence remains readable; only work requiring
missing evidence is held. Compatible computer switching is automatic; incompatible
versions produce an actionable update requirement. Current functionality outweighs
historical retention. A maintenance window and fresh mirror reset are authorized
if needed; lossless migration is not an acceptance condition. Privacy, pseudonyms,
local-only serving, and Canvas write review/verification remain firm boundaries.

Required reading: `AGENTS.md`; `docs/reference/project-state.md`;
`docs/reference/canvasmirror-synced-store-direction.md` sections **Approved
decisions**, **Functional milestone and transition decisions — 2026-10-05**, and
**Continuing requirements**; `docs/contracts/agent-runtime-product-contract.md`
sections **Primary interface**, **Canonical cooperation loop**, **Runtime
boundaries**; `docs/contracts/canvasmirror-evidence-contract.md` sections
**Storage**, **Records**, **Reduction**, **Privacy**, **Views**; `docs/mirror.md`
sections **On-disk layout**, **Sync passes**, **Scheduling**. This brief explicitly
replaces conflicting reader-file immutability, activation/import, historical
completeness, and hard grading-period acquisition requirements.
Do not read the old vision or archived handoffs as authority.

## 2. Evidence and preflight

Read-only diagnosis found repeated `mirror.acquisition_publication` failures with
`IndexReadError`. The existing shared reader-v1 contract omits the current registry's
`roster` and `sections` views; other top-level fields match. Exact-byte conflict is
a deterministic refusal in the inspected publisher. Logs retain only the exception
class, so this is not a captured live traceback. The selected workspace has an
integrity-valid, schema-v2 index with three courses and no activation checkpoint.
The 46 operation-attention rows predate October; do not reset/retry them as mirror work.

Before editing: inspect `git status --short --branch`; fetch origin and compare
`dev...origin/dev` and `dev...origin/main`; record the baseline. Preserve unrelated
changes, especially the independently supplied late-penalty handoff. There is one
current **CanvasMirror** brief; do not reorganize that unrelated work to enforce a
directory-count rule. Confirm runtime install/repo paths before any field check;
do not assume the running process has loaded the checkout's current source.

Read the owners named in section 5 and their tests. Inventory all call sites before
deleting an owner. Reproduce the reader mismatch in an isolated regression using
an older registry-generated contract, not copied teacher files. Never import
`api.*` outside pytest isolation merely to inspect paths or exercise code.

## 3. Locked implementation decisions

### A. Keep the useful foundation; separate failure boundaries

Keep the existing immutable facts/commits, scope reducer, pseudonymization gate,
bounded query service, attachment jobs/adapters, and advisory acquisition owner.
No new storage engine, event framework, distributed lock, compatibility registry,
cloud adapter, or generalized migration system. Duplicate acquisition during
eventual synchronization remains tolerable under the existing evidence rules.

Distinguish acquisition, safe publication, indexing, and attachment processing.
A privacy/validation failure cannot publish unsafe bytes. Successful independent
scopes can remain useful, but failed/incomplete scopes cannot claim complete
membership or advance their watermarks. Index or generated-document failure must
not roll back accepted safe evidence or cause another Canvas fetch to rebuild it.
An attachment failure cannot turn available submission text into unavailable data.
Coverage describes the requested scope, not the completeness of a historical
archive. Gaps in needed current work remain explicit; unrelated historical gaps
cannot hold that work or make the whole course unavailable.
Keep the existing per-course acquisition locks and bounded coordinator.

Use a small additive stage-status result shared by service, MCP refresh, and console:
`stages` keyed by `acquisition`, `publication`, `index`, `attachments`, each with
`state` (`ready`, `partial`, `pending`, `failed`, `not_run`) and optional allowlisted
`code`. Overall `ok` means acquisition and required safe publication succeeded;
index failure or attachment backlog within the requested scope is explicitly
reported and must not be labeled fully ready. An unrelated queue backlog is not a
global readiness gate. Keep existing coordinator terminal states; no second job state machine.
Refresh continuation observes the same coordinator plan through MCP; it does not
enqueue again. Index pending/failure requests local repair, not another Canvas
acquisition. Failed reads name their actual recovery action; do not send the
teacher to an equivalent browser button or ask permission to repeat an authorized
read. Plan section 4.9 defines the protocol.
Persist last attempt, last successful publication, index readiness, and sanitized
failure stage/code in the existing machine-local refresh/status owners. Status GETs
must not create databases, acquire Canvas data, extract files, or rebuild indexes.

### B. Reader documentation is derived local data

Remove `publish_reader_contract` from evidence publication entirely. Generate the
reader descriptor beside the machine-local index, from the registry used to build
that index, after successful maintenance/rebuild. Include index schema version and
revision; direct readers check these against index metadata. Write atomically.
Failure to write the descriptor is a local diagnostic, not acquisition failure.
Old shared `reader.v1.json` is ignored and no longer published; no perpetual dual
publication. Update guide/discovery paths to the local descriptor. Direct SQL and
MCP retain the same named views and meanings.

Evidence format, index schema, and descriptor presentation are separate concerns.
Do not bump the evidence schema solely for this descriptor relocation. An unsupported
fact/commit schema version produces `update_required` with no unsafe ingestion; do
not mislabel it generic corruption. Keep last-good supported evidence readable with
an incomplete/update warning. Incompatible future record semantics require an actual
format-version change, not a promise of indefinite old-reader compatibility.
Index schema mismatch requests maintenance rebuild, never silently reuses old views.

### C. No pilot-import activation gate on ordinary reads

Replace `_activated_evidence_lane` with one evidence-reader resolver based on
configured workspace/source, supported index schema, and index availability.
MCP `get_roster`, `get_submissions` (including history), `get_gradebook_snapshot`,
and `get_assignment_evidence` use that reader directly. Remove their legacy fallback
branches and activation/checkpoint reporting. Keep the four read tools' parameters,
tabular shapes, pagination, course selection,
identity privacy, and final outbound gates. `refresh_mirror` gains only optional
`operation_id` for status-only continuation, as specified in plan section 4.9.
Missing index means `refresh_required`/maintenance pending, not an empty course.
Pure reads never fetch, open private originals, or extract; do not add per-read
vault work (existing outbound privacy checks remain mandatory).

Available supported records remain readable despite age or incomplete coverage;
return warnings/coverage instead of the existing blanket coverage refusal. Uncertain
membership is explicitly unknown, never a complete absence. Preserve existing
revision pinning; related reads within one call must not mix revisions. For
offset-only calls, expose revision changes and restart that collection; do not
promise a cross-call snapshot without a revision/cursor input.

Assignment discovery stays through existing Course Catalog/get_course_content;
assignment context in submission results comes from the evidence index. They must
agree on assignment identity after one refresh. Do not build a second catalog.

Delete migration/import/activation-proof machinery and tests whose only consumer
is the retired cutover. Move `recover_evidence_work` out of `evidence_activation.py`
to the existing service owner before removing that module. No dummy active
checkpoint, automatic fake import proof, permanent feature flag, or new fallback.

The private projections still used by Names, scoring, and existing internal
consumers are **not** removed in this read milestone. Keep their current producers
from the same acquisition receipt; they are not fallback authorities for the MCP
reads above. Do not rewrite scoring or Canvas write behavior here. The next senior
batch assesses these remaining consumers; their existence must be reported rather
than claiming all old projections have been retired.

### D. Attachments progress without restarting the app

Wake bounded attachment capture/extraction after new jobs are enqueued. Prioritize
the explicitly requested assignment and newly observed work over existing backlog;
use existing request/job metadata rather than a calendar classifier or a new
scheduling framework. Teacher decision, 2026-10-05: this is satisfied by
newest-first job order for newly created jobs; no priority flag,
because no production path publishes a single-assignment receipt. Inspection shows
re-observed jobs retain their original order; requested older attachments are not
guaranteed priority. Preserve this accepted approximation and report it honestly.
Do not enumerate and download historical attachments merely
to fill an archive. Existing backlog may progress in bounded maintenance chunks,
but draining it is not a startup, current-read, or acceptance prerequisite. Do not
cancel jobs simply because a calendar date passed. Continue pending work with
stop-event cancellation and existing job claims/retry limits; no tight retry loop
for permanently failed files. Reuse the
service/runtime lifecycle; do not perform downloads while holding the ownership
heartbeat lock or block runtime readiness on draining the entire queue.
Rebuild the index after newly published extraction facts. Return readable blocks
and locators for supported DOCX/PDF/PPTX/XLSX/JPG; missing OCR/support is an explicit
gap, not an empty successful extraction. Preserve current-attempt association and
existing scoring holds. No scoring-rule changes or additional extraction formats.

### E. Startup, sync arrival, and visible status

Startup schedules local index maintenance independently of Canvas acquisition.
Rescan local evidence on the existing 30-second owner-maintenance cadence, with
at most one rebuild per process; do not put scanning or Canvas I/O under the owner
heartbeat lock. Coalesce publication-triggered requests. A short scheduled task
is sufficient; no filesystem-watcher/provider framework. Existing unchanged-input
optimization is enough until measurements justify more.
Keep a usable index transactionally intact during rebuild. A malformed or missing
scope must not suppress unrelated safe courses; retain last-good affected rows with
explicit gaps where available. Within a course, last-good comes from the existing
commit reducer; a course whose whole directory is absent is reported as not yet
arrived rather than retained from old index rows. A missing/corrupt disposable index can be recreated
from safe files without Canvas calls. Readers see an old complete transaction or a
new one, not half a replacement. Handle Windows open handles without deleting a
healthy index or installing a half-built replacement.

The existing root-page Canvas data panel and refresh result show the failed stage
and actionable reason, e.g. saved evidence awaiting local indexing or update needed.
Show attachment backlog separately from submission availability. No new dashboard
or routes. Existing mirror status routes may carry the additive status. Keep
historical operations/receipts unchanged; they are outside this batch.

## 4. Reset and live acceptance boundary

No deletion is needed for coding or synthetic tests. Do not reset just to make a
failing regression pass. The teacher authorizes a fresh start if field acceptance
needs it, without a historical import or backup project. Before reset, produce a
private manifest of resolved absolute targets and their purposes; reject paths
outside the selected workspace/local partition or containing reparse-point escapes.

Allowed targets: `<workspace>/CanvasMirror/sources/<selected-source-key>/`;
the obsolete `<workspace>/CanvasMirror/reader.v1.json`; the selected workspace/source
machine-local `cache/CanvasMirror/<workspace-key>/<source-key>/` (including index,
attachment jobs, activation checkpoint and extraction cache); and Current courses'
disposable directories under local `cache/Canvas Mirror/<course-id>/` if needed to
force fresh acquisition. Resolve keys through CE's configured path owners, never
guess or glob-delete whole application/cache directories. Delete/reset counterpart
local partitions on the other machine only when that machine is accessible.

Keep credentials, Identity Vault/pseudonym mappings, settings/course selection,
authored work, receipts, operation/score ledgers, scoring sessions, and all live
Canvas objects outside the reset. Existing original/history archives can simply
remain unused or be reused if validated; neither migration nor cleanup is required.
Do not delete shared ownership/control directories containing other source state.

Announce exact scope before live work. Stop both runtimes and ensure both computers
are updated before resetting shared data; let deletion synchronization settle before
restarting acquisition. If the second machine cannot be stopped/updated, finish
coding and synthetic verification but defer the destructive/shared reset, reporting
that concrete field blocker. Never simulate two-machine acceptance on one machine.
All field acquisition goes through CE's existing client; no direct token handling,
raw student output, Canvas writes, or operation retries. Report aggregates only.

## 5. Ownership and execution order

Detailed slice plan:
[`docs/reference/canvasmirror-functional-read-path-slices.md`](../reference/canvasmirror-functional-read-path-slices.md).
This brief remains execution authority. The plan's section **4** locks names,
signatures, state tables, codes, and console copy, including **4.9** for refresh
continuation; section **5** lists code findings and **5.1** distinguishes fixes
from accepted workflow limits. Lead reads plan sections **1–6** and the
stage being executed; each worker reads sections **1–5** and only its assigned
slices in sections **7–9**. Sections **10–12** govern integrated acceptance and closure.
The plan stages the work within this batch; do not create queued slice handoffs.

Lead owns `service.py`, `sync.py`, `coordinator.py`, `store.py` (refresh document
only), `api/runtime.py`, final integration,
schema counter/snapshot/budget, this brief's result, and document reconciliation.
One executor may do all work sequentially. If delegated, use these disjoint owners:

- Index worker: `evidence_index.py`, `evidence_queries.py`, `evidence_schema.py`,
  `evidence_store.py`, `evidence_paths.py`, matching tests. Own local descriptor,
  schema diagnostics, rebuild/read transaction behavior; coordinate service hooks.
- Publication worker: `evidence_acquisition.py`, `evidence_publish.py`,
  `evidence_jobs.py`, `evidence_extraction.py`, matching tests. Remove descriptor gate,
  preserve publication completeness/privacy, expose bounded continuation hooks.
- Lead handles MCP `tools.py`/`server.py`, activation/import deletions, runtime
  scheduling, console route/script changes, and all shared integration files. No two
  workers edit the same file. Review combined changes before committing.

For console work read `api/webui/README.md` sections **Pages and browser owners** and
**Presentation and verification**, and `docs/reference/webui-presentation-system.md`
sections **Template API**, **Who these pages are for**, **CSS ownership**.
Owners: `api/webui/routes/mirror.py`, `api/webui/static/canvasagent.js`, and its
template only if needed. Keep shared services canonical.

Execute: (1) reproduce and fix publication/descriptor boundary; (2) make local index
maintenance and attachment continuation reliable; (3) remove read activation/fallback
and reconcile MCP/status; (4) integrate, gate, reconcile docs; (5) announced field
acceptance/reset only after code acceptance. Do not deploy an intermediate slice.

## 6. Acceptance and test gate

Use isolated synthetic identities/documents, never teacher files in fixtures.
Required regression/integration evidence:

1. Existing older shared reader descriptor and fresh empty workspace both acquire
   successfully without import/activation. Descriptor/index failure leaves accepted
   evidence intact; retrying maintenance performs zero Canvas calls.
2. One selected course with assignment context, text submission, supported attachment,
   and partial/unreadable sibling reaches MCP reads; sibling failure preserves useful
   text with gaps. More than one capture/extraction chunk drains without restart.
3. Missing/corrupt/schema-old index rebuilds; interrupted rebuild preserves usable
   prior data. Two courses with delayed/malformed dependencies retain independent
   availability and honest coverage. Subsequent local file arrival becomes visible
   via maintenance without acquisition. No teacher-directed storage manipulation.
4. Stale/incomplete supported evidence remains readable; empty-complete differs from
   not-acquired. Unknown schema gives update guidance. Direct SQL/MCP match, including
   paginated current/history/attachment reads and revision changes.
5. Compatible synthetic switching (two machine-local partitions over one shared
   safe root) and restart need no activation proof.
   No seeded real identifiers, names, raw filenames, URLs, originals, or credentials
   enter the safe root/index or agent output. Existing write and attachment-scoring
   safety tests stay green; do not weaken them to satisfy read availability.
6. Console refresh failure reports the actual stage. Status after failure preserves
   last-success but advances last-attempt/failure information. Load `/` and `/settings`
   with required state and zero new browser console errors; add any other affected
   route if shared code is changed.
7. With no grading calendar or period assignment configured, current reads and
   refresh still work. A requested assignment progresses despite unrelated older
   attachment backlog and absent historical attempts; no historical backfill is
   triggered solely to satisfy readiness. Existing older records remain readable,
   and explicit older-work requests are not refused based on dates. These are
   behavioral regressions, not tests of source wording.
8. A refresh exceeding its bounded wait can be observed through the same MCP
   tool using `operation_id`; queued, completed, and failed continuation calls
   make zero new Canvas calls and do not trigger loop-confirmation warnings.
   Usable text does not wait for attachment completion. A second computer with
   no queue still reports pending associations honestly. Preserve existing content
   operation tests; add no global mirror-readiness gate to publishing.

Run the plan's focused commands as each seam changes. At integration, before
synthetic GREEN, run
`py -m pytest api/tests engine/tests -p no:randomly -q` in a verified OCR-capable
environment. The prior brief recorded 2,466 passed/2 skipped before its final
contract assertion adjustment; that is context, not this batch's acceptance.
Record interpreter, exact commands, counts, and any independently baselined failures.
Reuse the recorded baseline instead of repeating the full suite before coding.
Reuse a suitable isolated interpreter; OCR setup does not block independent
index/publication work. The full integration and real-extraction gates remain.
Synchronize MCP registry, generated inventory/schema snapshot, and listing budget
when contracts change; lead takes the next free schema number at integration.

Field acceptance: announce CE GET-only acquisition for relevant work in one Current
course first; verify requested assignment/submission/attachment reads, restart,
then sample relevant work in remaining Current courses. Do not require every past
assignment or attachment to be present. Verify on the second updated computer after sync without manual
activation/import. Distinguish Canvas acquisition, saved evidence, local index, and
attachment backlog. Missing/unavailable documents remain explicit gaps. Do not claim
successful scoring, complete historical preservation, or two-machine field success
from synthetic checks.

Reconcile `docs/mirror.md`, the evidence and agent-runtime contracts, the direct-read
guide, and the synced-store direction with actual behavior. Update the evidence
view list, local descriptor location, lifecycle/status, and removed activation rules.

## 7. Stop conditions and execution result

Ask the senior only for a new scope decision: changing Canvas write/scoring semantics,
needing an unlisted destructive target, replacing the evidence format/storage engine,
or a demonstrated privacy flaw requiring a different plan. Routine code/test choices,
the already authorized mirror reset within section 4, and dropping obsolete import
tests do not need another permission round.

### Workflow-review amendment — 2026-10-05

Planning updated at `a9a3c03`; application source and live state untouched.
Existing pending edits to this brief and its slice plan were preserved. The plan
now specifies refresh continuation, recovery without browser detours, scope-local
readiness, attachment publication ordering, first-acquisition failure privacy,
and honest queue/second-computer semantics. Removed duplicate preflight full-suite
work, environment-wide coding stops, routine source-drift questions, reset
re-approval, and zero-match documentation gates. Private scoring projections,
newest-first scheduling, and New Quiz writing limits remain explicit; this read
milestone does not claim to remove them.

### Coding preflight — 2026-10-05 (original; not rerun by documentation review)

**YELLOW for acceptance; coding scope and ownership are ready.** No new teacher
scope decision was identified. This is preflight evidence, not implementation
acceptance. No agents were delegated, runtime started, Canvas called, reset
performed, or commit created. The temporary synthetic reproduction was removed.

- Baseline: `30b9465ebe6027eccbfcaedc8c2124c11bddb0ca`, branch `dev`.
  `git fetch origin` succeeded; `git rev-list --left-right --count
  dev...origin/dev` = `0 0`; `dev...origin/main` = `17 0`.
- Initial pending changes: deleted replacement predecessor, modified synced-store
  direction, and this untracked brief. They were preserved. No independent
  late-penalty brief was present in `docs/handoffs/` at this inspection.
- Required references and implementation seams were inspected. Repository-wide
  call-site search found activation consumers in MCP and service, migration's
  activation call, proof's activation dependency, and their tests. Move recovery
  before deleting that chain; repeat the inventory at execution because shared
  `dev` may move. No developer-helper consumer was found in `tools/`.
- Synthetic reproduction: generate `reader.v1.json` using the current registry
  minus `roster`/`sections`, restore the registry, then call
  `publish_reader_contract` again. All non-view fields agree; it raises
  `IndexReadError("reader_contract_conflict")` and preserves the older bytes.
  Temporary pytest under `api/tests/mirror/` used the existing isolation and
  monkeypatch, never teacher files. Command:
  `py -m pytest api/tests/mirror/test_preflight_reader_mismatch.py -p no:randomly -q`
  — **1 passed**. Carry this case into the first slice as a permanent behavioral
  regression proving acquisition succeeds after the fix, not merely descriptor
  conflict detection. Both `publish_course_receipt` and
  `EvidencePublisher.publish_text_assignment` contain the publication gate.

Integration seams to carry into the eventual slices:

- Index/service: rebuild currently returns early for the whole source when one
  course is missing, empty, or invalid. Preserve last-good affected rows while
  letting independent safe courses advance. Schema v2 is written on initialization
  without validating an existing index version at the read boundary. Unknown
  evidence schema is currently collapsed into `invalid_fact`/`invalid_commit`.
- Attachment worker: `captured_jobs(limit)` selects oldest captured jobs before
  extraction-cache checks. Repeated chunks can repeatedly select cached jobs and
  starve newer work. Continuation must select eligible work, prioritize the
  requested assignment, and bound permanent failures; waking alone is insufficient.
- Status: `evidence_status()` calls `jobs.summary()`, whose connection creates
  directories/database/schema. Add a genuinely read-only absent-store path.
  Acquisition acknowledgements, coordinator terminal results, MCP refresh, and
  console status need the same stage meanings; lead owns their integration.
- Lifecycle: recovery currently runs one synchronous chunk during startup;
  publication rebuilds inline. Coalesce maintenance independently of the owner
  heartbeat lock, with stop-aware worker continuation and one rebuild at a time.
- Reads: retain pinned revisions and outbound privacy checks while removing
  activation/fallback. `get_assignment_evidence` also reads activation directly.
  Schema counter is currently **81**; allocate its next value only at integration.
- Remaining private consumers include Names, PowerGrader assignment refresh,
  scoring/feedback services, operation adapters, and internal MCP helpers.
  Preserve their producers; this batch does not retire those projections.

Use section 5's disjoint publication/index owners for the eventual parallel
workstreams. Lead should define their returned hooks before delegation, then own
service/sync/coordinator/runtime integration, MCP retirement, console, and docs.
Service-level tests stay lead-owned; workers own tests matching their modules.
Do not queue separate future handoffs or deploy intermediate slices.

Baseline gate at the commit above:

`py -m pytest api/tests engine/tests -p no:randomly -q`

**2,460 passed, 8 failed, 1 skipped**, 184.55 seconds. Interpreter: Windows Store
Python **3.13.14**, pytest **9.0.1** (`py` selects the profile's WindowsApps Python).
The default environment lacks `rapidocr`, `pypdfium2`, and `pptx`; the installed
`C:/Python313/python.exe` also lacks pytest and these dependencies. No dependencies
were installed during preflight. Use the project's supported OCR-capable test
environment before synthetic GREEN; do not relax the extraction acceptance.

Exact failing nodes (all pre-existing; no application source edits):

- `api/tests/mirror/extraction/test_format_adapters.py::test_pptx_follows_slide_order_and_preserves_notes`
- `api/tests/mirror/extraction/test_format_adapters.py::test_image_and_scanned_pdf_use_the_real_local_ocr_runtime`
- `api/tests/mirror/extraction/test_ocr_assets.py::test_required_packaged_assets_are_verified`
- `api/tests/mirror/extraction/test_ocr_runtime.py::test_first_inference_is_local_and_recognizes_synthetic_text[jpg]`
- `api/tests/mirror/extraction/test_ocr_runtime.py::test_first_inference_is_local_and_recognizes_synthetic_text[scanned_pdf]`
- `api/tests/mirror/extraction/test_ocr_runtime.py::test_real_worker_timeout_is_typed`
- `api/tests/test_qf_pusher.py::test_new_engine_plan_is_unchanged_for_existing_files`
- `api/tests/test_readiness_routes.py::test_probe_runs_once_until_forced`

The first six have missing extraction dependency/recognition failures. Readiness
also probes OCR (the test mocks only Canvas/privacy), so missing OCR degrades it.
The QuizForge golden-plan mismatch is separately baselined and not diagnosed or
repaired in this read milestone. Independent reproduction:
`py -m pytest api/tests/test_qf_pusher.py::test_new_engine_plan_is_unchanged_for_existing_files api/tests/test_readiness_routes.py::test_probe_runs_once_until_forced -p no:randomly -vv --tb=short`
— **2 failed**, 0.55 seconds.

Field prerequisites remain unchecked: installed runtime source path/version,
both computers' availability and updates, selected private reset manifest, and
actual two-machine sync. Confirm these only in announced field acceptance; no
live-state inspection or browser acceptance is claimed by this preflight.

**Execution result: IN PROGRESS — YELLOW.** S00 started 2026-10-05 at `5600db2`.
S08 follow-up (2026-10-05): the refresh-continuation bug was fixed and the
activation-free resolver completed; see the S08 row.
S09 (2026-10-05): the obsolete activation/import/migration cutover machinery was
deleted; see the S09 row. The repo-local `.venv` was recreated (Python 3.14) and
`api/requirements.txt` reinstalled, so the OCR/extraction dependency failures are
resolved.
S10 (2026-10-05): console stages and §4.8 copy landed; see the S10 row. Browser
gate passed on `/` and `/settings` in fully-ready and failed-refresh states with
zero console errors. Full-suite state after S10: **2494 passed, 1 skipped,
0 failed** — synthetic GREEN.
S11 (2026-10-05): docs reconciled to actual behavior and the integrated
`test_evidence_read_path.py` module landed; see the S11 row. Full-suite state
after S11: **2499 passed, 1 skipped, 0 failed** — synthetic GREEN. S12/S13 field
acceptance remain and require the teacher, the live runtime, Canvas, and the
second computer.
Clean `dev`; fetch succeeded; `dev...origin/dev` = `0 0`,
`dev...origin/main` = `19 0`. No application source drift from `30b9465` in
the declared owners. Inventory confirms the activation/import/recovery,
descriptor publication, and extraction-cache consumers documented above.
No live runtime, Canvas acquisition, reset, or deployment performed.

| Slice | State | Commit(s)/files | Focused command and count | Acceptance evidence | Deviation/blocker |
|---|---|---|---|---|---|
| S00 | GREEN coding preparation; extraction environment pending | Commit containing this row; shared acquisition samples and SyntheticVault import consumers | `py -m pytest api/tests/mirror/test_evidence_publish.py api/tests/mirror/test_acquisition_samples.py -p no:randomly -q` — 22 passed | SyntheticVault moved to shared builder with SIS/nickname registration; read_path, second_course, in-memory documents added; inventory complete | Repo-local `.venv` installation in progress; focused fixture check used baseline Python 3.13.14 |
| S02 | GREEN slice | Commit containing this row; `evidence_schema.py`, `evidence_store.py`, matching tests | `.venv\Scripts\python -m pytest api/tests/mirror/test_evidence_schema.py api/tests/mirror/test_evidence_store.py -p no:randomly -q` — 54 passed | Unsupported versions diagnosed before field allowlists; no private diagnostic copy; supported last-good scope and unrelated scope remain readable; future commit metadata sanitized | None |
| S01 | GREEN slice | Commit containing this row; `evidence_acquisition.py`, `evidence_publish.py`, matching tests | `.venv\Scripts\python -m pytest api/tests/mirror/test_evidence_acquisition.py api/tests/mirror/test_evidence_publish.py -p no:randomly -q` — 41 passed | Registry-generated conflicting descriptor ignored by both entry points; empty-vault SIS/nickname scrubbing; prior student refs survive registration failure; terminal attachment status publication | Unregistrable roster now withholds student scopes after failed upsert; old sibling test renamed to match required failure behavior |
| S03 | GREEN slice | Commit containing this row; `evidence_index.py`, `evidence_queries.py`, `evidence_paths.py`, matching tests | `.venv\Scripts\python -m pytest api/tests/mirror/test_evidence_index.py api/tests/mirror/test_evidence_queries.py api/tests/mirror/test_evidence_paths.py -p no:randomly -q` — 57 passed | Missing/schema-mismatched/corrupt reads refuse without writes; transaction rollback; Windows denied unlink preserves sidecars; local atomic revision-bound descriptor; registry SQL parity; attachment summary | Index write connections now explicitly close to permit Windows repair. Discard is authorized only after a failed schema/corruption read of the same file and resets after success. No schema version change |
| S05 | GREEN slice | Commit containing this row; `service.py`, `coordinator.py`, `store.py`, `sync.py`, runtime rename and matching/shared tests | `.venv\Scripts\python -m pytest api/tests/mirror/test_service_evidence.py api/tests/mirror/test_sync.py api/tests/mirror/test_coordinator.py api/tests/mirror/test_service_selection.py api/tests/mirror/test_store.py -p no:randomly -q` — 154 passed | Sanitized stages; refresh last success/failure stage; independent course rebuild and absent-course removal; local corrupt/schema repair with zero Canvas calls; safe files survive index failure; descriptor failure diagnostic only; short frozen vault snapshot preserves verification | Checkpoint follows verified S01–S03 dependencies while independent S04 worker continues, honoring per-slice commit request. No unfinished S04 interface is called by this slice; deployment remains deferred |
| S06 | GREEN index/lifecycle slice | Commit containing this row; `service.py`, `runtime.py`, lifecycle/service tests and isolated fixtures | `.venv\Scripts\python -m pytest api/tests/test_runtime_startup.py api/tests/mirror/test_service_evidence.py api/tests/mirror/test_acquisition_owner.py api/tests/mirror/test_acquisition_requests.py -p no:randomly -q` — 39 passed | No synchronous startup indexing/extraction; both workers start after operation recovery; wake/join on stop; 30-second rescans, coalescing and requests during rebuild; owner heartbeat not held by slow indexing | Existing attachment chunk/recovery internals deliberately remain for S07 integration after S04; never deploy this intermediate tree. Read-only Luna review found no material lifecycle/coalescing defects |
| S04 | GREEN slice | `2a4513e`, `70c90b7`; durable attachment jobs, extraction, registry version lookup, matching tests | `.venv\Scripts\python -m pytest api/tests/mirror/test_evidence_jobs.py api/tests/mirror/test_evidence_extraction.py -p no:randomly -q` — 39 passed | Newest-first bounded queue, terminal capture publication before completion, association-keyed extraction, explicit gaps and restart reopening, no extraction cache, adapter version tracking | S04 was committed after S06’s independent lifecycle checkpoint so each verified slice remains recoverable; no deployment |
| S07 | GREEN coding slice | `ffd8aa6`; `service.py`, scoring/startup fixture migration, worker integration tests | `.venv\Scripts\python -m pytest api/tests/mirror/test_evidence_jobs.py api/tests/mirror/test_evidence_extraction.py api/tests/mirror/test_evidence_workers.py api/tests/mirror/test_service_evidence.py api/tests/test_runtime_startup.py api/tests/mirror/test_evidence_scoring.py -p no:randomly -q` — 82 passed, 1 warning | Capture and extraction workers run in bounded chunks; downloads/adapters occur outside vault transactions; restart reuses captured bytes; non-owner extraction is allowed; explicit refresh reopening is course-scoped; 45-job scheduling fixture drains across chunks | Worker orchestration test uses a fast durable queue double for the 45-job drain; direct transport/vault-boundary and restart tests exercise the real service chunks. Field, MCP, console, and second-computer acceptance remain |
| S08 | GREEN coding slice | `951e7b2` + follow-up; `tools.py`, `server.py`, `service.py`, MCP tests/fixtures | `.venv\Scripts\python -m pytest api/tests/mcp_server -p no:randomly -q` — 356 passed | One `_evidence_reader()` resolver replaces `_activated_evidence_lane`; the four reads serve from the evidence index with `coverage`/`warnings` and honest partial/unknown handling; refresh continuation fixed to read the coordinator plan view and report real stages/`fully_ready`; legacy typed-mirror helpers (`_roster_sections`, `_roster_groups`, `_mirror_submission_bundle`, `_load_snapshot`, `_submission_history`) deleted; vault-conflict fail-closed preserved | `_mirror_roster_doc` and `_freshness_attention` retained (still used by roster settings and catalog reads). Schema settled to v82; listing budget 15400 |
| S09 | GREEN slice | `7184607`, `51d747f` + follow-up; deleted `evidence_activation.py`, `evidence_activation_proof.py`, `evidence_import.py`, `evidence_migration.py`, `legacy_samples.py` and their five test modules; `service.py` `evidence_status()` shape; `test_retired_paths.py` | `.venv\Scripts\python -m pytest api/tests/test_retired_paths.py api/tests/mirror api/tests/mcp_server -p no:randomly -q` — 887 passed | Obsolete activation/import/migration cutover machinery deleted; `evidence_status()` returns `{state, index, attachments(+stage), acquisition_owner, gaps}`; RETIRED_PATHS rows added; no dangling references; `activation.v1.json`/`extraction.sqlite3` left on disk | None |
| S10 | GREEN slice | `7184607`, `51d747f` + follow-up; `service.py` (`status()` per-course `refresh` + `evidence_status()` shape), `canvasagent.js` (§4.8 detail + backlog + `refreshText`), `test_desk_routes.py` | `.venv\Scripts\python -m pytest api/tests/test_desk_routes.py api/tests/test_route_contract.py api/tests/test_readiness_routes.py api/tests/mirror/test_service_evidence.py api/tests/mcp_server -p no:randomly -q` — 396 passed | `status()` adds per-course `refresh` and top-level `evidence`; `canvasagent.js` applies §4.8 detail with precedence update required > index failed > index pending > not arrived, appends the backlog sentence, and chooses refresh text from `plan.stages`; status GETs create no file; browser gate passed on `/` and `/settings` in fully-ready and failed-refresh states with zero console errors | Fixed a latent `AttachmentJobStore` NameError in `evidence_stages()` |
| S11 | GREEN slice | Commit containing this row; new `api/tests/mcp_server/test_evidence_read_path.py`; `tools.py` guide descriptor line; `test_beta075_mcp.py` evidence-bound set; `test_tools.py` guide assertion; docs `canvasmirror-evidence-contract.md`, `agent-runtime-product-contract.md`, `mirror.md`, `canvasmirror-agent-reading.md`, `canvasmirror-synced-store-direction.md`, `mcp-server.md` | `.venv\Scripts\python -m pytest api/tests engine/tests -p no:randomly -q` — 2499 passed, 1 skipped, 0 failed | Five composed scenarios over the real publisher/index/queue/query/MCP path (only Canvas transport and the adapter subprocess stubbed): old descriptor + fresh workspace reaches MCP with text, docx blocks, and an explicit pdf gap; delayed second course and corrupt index recover locally with zero Canvas calls; partial/future-version/empty-complete/history read honestly; two partitions over one safe root and restart need no activation with no seeded identity in safe files/index/output; failed refresh keeps last success and reports the stage, newest-first jobs beat backlog, gradebook excludes an incomplete assignment and says so. Docs reconciled to actual behavior; `reader.v1.json` guide line replaced with the local `reader.json`; `get_assignment_evidence` added to the evidence-bound doc set | None |

### Field acceptance — 2026-10-05 (desktop only; laptop battery died)

Both computers were updated by `git pull` from `dev` (the in-app self-updater is
not the supported path; its `REPO_SLUG` also disagrees with the git remote). A
desktop MCP agent ran the field check against three Current courses (120669 ELA 7
PAP, 121046 CS 8, 120638 ELA 7). The laptop was unavailable, so two-machine
acceptance is deferred.

**Read path — PASS.** `get_gradebook_snapshot`, `get_roster`, and
`get_assignment_evidence` returned `ok:true` with `coverage`, `warnings`, and
`revision`; restart needed no activation/import; no `evidence_index_pending`,
`evidence_refresh_required`, or `evidence_update_required` was observed. Direct
timing against the real workspace: `get_submissions` 0.24s (29 rows),
`final_response_gate` 0.15s, server wrapper 0.38s, history 0.11s; with the
runtime's workers running, `get_submissions` 2.73s `ok:true`. A full pass in
isolation: 83s, `ok:true`, 24 assignments, 551 rows.

**Defect 1 — `get_submissions` appeared to hang (4 min) in the live agent.**
Not a read-path bug. The operational log shows coordinator `queue_wait_ms` of
637,533–1,484,808 ms (10–25 min) and `mirror.refresh` "full" passes failing after
501–1,218 s. The coordinator runs exactly two workers; long background full passes
occupy both, so manual reads queue behind them. Compounded by the vault lock:
`storage_support.interprocess_lock` uses a blocking `msvcrt.locking(LK_LOCK)` with
no timeout, and every mirror write (`write_roster`, `merge_submissions`, capture,
extraction `publisher_scope`) takes it, so a slow vault op blocks all others.
A thread dump with the runtime running showed `ce-evidence-work` blocked in
`run_extraction_chunk` → `publisher_scope` → `vault.transaction()` →
`msvcrt.locking`. (An earlier "persistently held lock" reading was the
investigator's own stray server processes, not a code defect.)

**Defect 2 — `section_label_missing` on every roster.** The live index has
`roster` rows (79) but **zero** `section` facts, and `course.sections` scope_status
is `ready` with `membership_complete=0`. `_fetch_sections` returns 1 section live,
so the empty-but-ready scope is a publication/coverage bug, not a Canvas problem.

**Not yet fixed.** Both defects are code fixes, not field resets. No reset was
performed. Overall remains YELLOW.

S00 commit: `036ab61`; S02: `caf7b22`; S01: `c2439a8`; S03: `22d4d10`;
S05: `51b917e`; S06: `952788e`.
The repo-local `.venv` is ready: Python 3.13.14, pytest 9.0.1, dependencies
installed from `api/requirements.txt` (including RapidOCR 3.9.2, ONNX Runtime
1.29.0, pypdfium2 5.1.0, python-pptx 1.0.2).
`.venv\Scripts\python -m pytest api/tests/mirror/extraction -p no:randomly -q`
— **49 passed**, 44.52 seconds; real OCR and supported adapters verified.

Lead owns integration and commits. Luna index worker owns S02 → S03; Luna
publication worker owns S01 → S04, with disjoint files and slice review pauses.
User requests a commit after every verified slice. Preserve intermediate slices
without deployment; overall acceptance remains YELLOW through field checks.

Record commits, changed files, commands/counts,
boundary regressions proved, remaining private-projection consumers, deviations,
reset performed/not performed, and field evidence here. Synthetic GREEN means the
code gate passed; overall acceptance stays YELLOW until required pilot/two-machine
checks pass. On final acceptance retire this brief in the same batch and leave one
current pointer in the direction document to the next senior assessment: scoring
consumption and remaining private projections. Do not queue future briefs now.
