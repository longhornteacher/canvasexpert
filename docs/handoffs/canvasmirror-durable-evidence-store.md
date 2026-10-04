# Execution brief: CanvasMirror durable evidence store

**Status:** ready for executor preflight; implementation has not started.

**Target:** `dev`. **Lead:** Sol. **Workers:** Sol for risky integration; Luna for
bounded adapters, synthetic fixtures, and documentation.

**Planning baseline:** `b854d9a`, inspected 2026-10-04. This is a local checkout
baseline, not a claim that remotes are current.

**Scope:** one program, delivered in ordered vertical slices in this one brief.
The teacher explicitly requested detailed slices and multi-agent execution.

## 1. Outcome, authority, and scope

CanvasMirror becomes the teacher's durable, cloud-synchronized evidence store for
agent-led ELA/CS scoring. An agent can obtain assignment context, every observed
attempt, original submission timestamps, extracted attachments, and comparison
evidence without repeatedly reconstructing storage or refresh procedures.

The teacher's decisions are canonical in
`docs/reference/canvasmirror-synced-store-direction.md`, sections **Approved
decisions** and **Teacher priorities and constraints**. This brief specifies the
engineering choices needed to execute them. The current product contract's
**Read spine** points at that direction. Earlier disposable-cache/MCP-only
descriptions are current implementation history, not permission to reverse these
decisions. Update those descriptions as each behavior actually changes.

### 1.1 Locked teacher decisions

1. Acquire only explicitly selected courses; acquire all available text and
   submitted files for those courses. No submitted external-URL acquisition.
2. Retain observed history indefinitely. Deselection stops acquisition; it never
   deletes records. Preserve every attempt's submission timestamp when later
   attempts or Canvas lateness state change.
3. Student identities in the agent-readable store are stable Pokemon pseudonyms.
   Real names, raw student/author IDs, identity mappings, filenames containing
   PII, credentials, transport URLs, and unprocessed originals stay outside it.
4. Direct agent reads are supported under a written contract. The teacher accepts
   this access model; do not add a mandatory MCP gateway, per-read approval,
   sandbox product, or new privacy confirmation. MCP reads use the same data and
   meanings. CE still owns pseudonymization before publication and Canvas access.
5. Required submission formats: DOCX, PDF, PPTX, XLSX, JPG/JPEG, plus existing
   supported text and PNG behavior. PDF includes image-only pages; JPG requires
   useful extracted evidence. Do not call raw-byte retention alone support.
6. Preserve useful punctuation/formatting, available document revision evidence,
   comments, assignment/rubric context, grades, due dates and Canvas overrides.
   Teacher-agent conversation determines accommodation decisions; never infer
   extra-time entitlement from the document or automatically override the teacher.
7. Default comparisons are students within one assignment and successive attempts
   of that assignment. No default cross-course or multi-year integrity search.
8. Windows desktop/laptop normally alternate. Switching must be automatic; a
   visible current acquisition owner has priority. Accidental overlap is safe.
9. Cloud-provider agnostic folder storage. No OneDrive API dependency or speculative
   provider adapter framework. ZIP is permitted in the current tenant; some raw
   extensions, including `.py`, `.js`, `.exe`, are blocked.
10. Use complete locally available evidence when synchronization is delayed, with
    focused acquisition for needed gaps. Failure of one file must not stop useful
    work for other files/students. Notify the teacher about actionable gaps.
11. Reliability and visible progress outrank arbitrary timing targets. Systematic
    diffing, extraction reuse, and resumption belong in CE, not model reasoning.
12. Agent-derived notes/analysis may persist in one contained area, separate from
    acquired evidence and never automatically posted to Canvas.

Automatic computer switching in this program covers acquisition and evidence
reads. An already-open Scoring Session still uses its existing private work lease;
fully automatic transfer of posting/session ownership is a separate UX gap, not
an outcome claimed here. Agents can use existing work tools, but this program does
not weaken their confirmation or write-safety rules.

### 1.2 Non-goals and boundaries

- No changes to pedagogy, authored points, authenticity verdicts, automatic
  plagiarism penalties, or late/extra-time policy. Comparison results are evidence.
- No new Canvas mutation API, automatic writes, or weakened stage/review/apply,
  idempotency, live drift checks, score verification, receipts, or privacy scans.
- Do not move the private Identity Vault, Score Ledger, Operation Ledger, or
  scoring-session work state into the direct-readable mirror. Acquisition ownership
  is separate from their existing work/write leases.
- No New Quiz item grading, Classic Quiz writing scoring, cloud OCR/inference,
  external document crawling, multimedia transcription, or arbitrary archive/code
  execution. Preserve existing New Quiz capability/response behavior privately.
- No general dashboard or local scoring UI. The existing console may show readiness,
  acquisition/extraction progress, repair guidance, and safe read-access location.
- No broad settings redesign, compatibility population, cloud backup service,
  distributed transaction framework, vector database, or semantic-search service.
- No automatic deletion of legacy pilot files, receipts, sessions, or originals.
  A clean code cutover does not authorize deletion of live teacher history.

## 2. Required reading and verified starting points

Every agent reads `AGENTS.md`, this brief's sections 1, 3, 4, 9, and its assigned
slice. The lead reads the whole brief and `docs/reference/project-state.md`.
Workers read only their slice references and exact source owners, not all module
maps, archived handoffs, or the old information-spine vision.

### 2.1 References by responsibility

| Work | Required reference sections |
|---|---|
| All runtime work | `docs/contracts/agent-runtime-product-contract.md`: Primary interface, Canonical cooperation loop, Runtime boundaries |
| Storage/acquisition | `docs/mirror.md`: On-disk layout, Sync passes, Scheduling; `docs/contracts/canvasmirror-coordinator-contract.md` (short, whole file) |
| Migration/originals | `docs/contracts/submission-history-contract.md`: Storage and capture, Agent read; `docs/guides/more-than-one-computer.md` |
| Course structure | `docs/contracts/course-catalog-contract.md`: Root and scope schema, Published page scope, Reads and refresh, Durability and forbidden material |
| Scoring work | `docs/guides/scoring-sessions.md` and `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`, as required by AGENTS; `docs/contracts/feedback-scoring-contract.md`: Direction 1 and Session consumption and write safety |
| Score-history seams | `docs/contracts/score-ledger-contract.md`: 1, 4, 5; read `api/README.md` before touching a Canvas write owner |
| Console | `api/webui/README.md`, then only the affected route/load-order sections of `docs/reference/webui-presentation-system.md` |

Some prose is already stale: `docs/mirror.md` still describes age-refusing reads,
real IDs at rest, and OneDrive benefits for now-local mirror files. Actual MCP reads
at the baseline serve stale structurally sound records with labels. The scoring
contract also contains older write-lane wording; preserve the current tested live
preflight/verification implementation, do not remove safeguards to match old prose.

### 2.2 Source owners confirmed during planning

| Owner | Existing behavior / seam |
|---|---|
| `api/platform_services/workspace.py`, `api/runtime_paths.py` | Mirror/Catalog under machine-local cache; history under workspace Archive |
| `api/mirror/store.py` | Strict versioned JSON, pseudonymized roster/submission storage, attempt merges; also invokes history and score-ledger observation owners |
| `api/mirror/submission_history.py` | Mutable per-assignment `history.v1.json`; immutable originals with raw filename suffixes; local locks do not coordinate two computers |
| `api/mirror/sync.py`, `service.py`, `coordinator.py` | Full/delta/focused receipts, overlap watermarks, two-worker GET coordinator and foreground priority |
| `api/course_catalog.py` | Richer student-free assignments/rubrics, modules, groups, plain-text pages; independent scope completeness |
| `api/mirror/read_service.py`, `queries.py` | Local projection readers; private adapters can rehydrate real IDs through the vault and are NOT direct-readable exports |
| `api/shared_storage.py`, `shared_work.py`, `shared_vault.py` | Existing immutable publication helpers, leases and sync-gap experience; do not repurpose session leases for mirror acquisition |
| `api/powergrader/assignment_refresh.py` | `prepare_assignment_from_mirror` currently holds/blankets attachment-bearing rows as unreadable |
| `api/powergrader/student_attachments.py` | DOCX/text/raster routing; PDF/PPTX/XLSX currently local-only for student evidence |
| `api/source_material_extractors.py` | PDF/Office text precedent, but whitespace collapsing, truncation and shallow ZIP/XML parsing are not the required evidence contract |
| `api/powergrader/writing_timeline.py`, `overlap.py` | Reuse tracked DOCX evidence and assignment-scoped overlap rather than building parallel integrity engines |
| `api/feedback_artifacts.py`, `feedback_safety.py` | SAFE publication/scrubbing and outbound checks; originals/pixels require distinct handling |
| `api/powergrader/scoring_preparation.py`, `scoring_packet.py`, `scoring_artifacts.py` | Session preparation, frozen packets, bounded pagination and explicit session refresh |
| `api/mcp_server/tools.py`, `server.py`, `contract.py` | Existing read tools and versioned registry; schema 80 at planning baseline, NOT reserved for this program |
| `api/runtime.py`, `local_runtime.py` | One runtime per PC, mirror startup/shutdown, separate work-lease release |

## 3. Preflight, baseline, and authorization

### 3.1 Executor preflight

Run before editing production code:

```powershell
git status --short --branch
git fetch origin
git rev-list --left-right --count dev...origin/dev
git rev-list --left-right --count dev...origin/main
git log -5 --oneline
py --version
py -m py_compile api/mcp_server/tools.py api/mirror/service.py
```

Stay on `dev`; do not merge, reset, stash unrelated work, or create durable branches.
Record the actual starting SHA and dirty files. The planning changes to this brief,
the direction reference and the product-contract pointer are expected. If new work
has changed an owner, reconcile the brief with the current code before assigning it.
Remote divergence is information, not authorization to merge histories.

Run `py -m pytest api/tests engine/tests -p no:randomly -q` once to establish the
execution baseline. All tests importing `api.*` run under repository pytest
isolation; do not inspect live state with ad-hoc Python imports.

### 3.2 Known unrelated failure and retired brief

The previous active brief, `remove-pointless-freshness-blockers.md`, is superseded
as an execution route by this program. Its implementation is committed at
`b854d9a`; do not undo it. Its unresolved QuizForge golden-plan discrepancy is
carried here, not falsely accepted as GREEN or assigned to this program.

Planning reproduced on `b854d9a` (only planning documents dirty):

```powershell
py -m pytest api/tests/test_qf_pusher.py::test_new_engine_plan_is_unchanged_for_existing_files -p no:randomly -q
```

Observed **1 failed**, assertion against `GOLDEN`, differing
`all_types_rich_settings`. The previous brief records the same failure at
`62832d3` and a full run of **2125 passed, 1 skipped, 1 failed**. That older count is
historical, not a fresh full-suite result for this planning turn. Do not update the
golden or suppress the test to green this program. Later gates name the same known
failure rather than repeatedly investigate it.

### 3.3 Scope of execution and live work

This planning turn writes documents only. A later dispatched executor implements
the slices. Synthetic tests are the primary gate. Before intentional live Canvas
reads or private-workspace import, state the exact read scopes and local target
roots. Such runs must be read-only against Canvas and produce only private output
or aggregate counters. No live scoring/content push is needed to prove this work.
Do not print students, source filenames, private paths, tokens, or evidence text.

Additive import and reversible read activation are the planned migration actions.
Destructive cleanup, vault reseeding, deleting existing records, public exposure,
external OCR services, or changing Canvas write review requires a separate teacher
decision. Do not repeatedly ask about ordinary authorized implementation choices.

## 4. Engineering design to implement

### 4.1 Three physical storage classes

Use the selected workspace helpers; never a developer-specific path:

```text
<workspace>/CanvasMirror/                       agent-readable, synchronized
  reader.v1.json                               generated schema/reading contract
  sources/<source_key>/courses/<course_id>/
    objects/<prefix>/<sha256>.json              immutable normalized facts/blocks
    commits/<prefix>/<sha256>.json              immutable scope acquisition receipts
    notes/<prefix>/<sha256>.json                immutable derived-note revisions

<workspace>/_System/Archive/CanvasMirror Originals/   PRIVATE, synchronized
  blobs/<prefix>/<original_sha256>.zip          verified original bytes only
  associations/<prefix>/<sha256>.json           private filename/type/source relation

<workspace>/_System/CanvasMirror Control/        PRIVATE coordination, synchronized
  presence/<opaque_machine_key>.json            single-writer presence/claim
  requests/<request_id>.json                    bounded read-only acquisition requests

<LOCALAPPDATA>/CanvasExpert/cache/CanvasMirror/  machine-local, never cloud-synced
  <workspace_key>/<source_key>/query.sqlite3     pseudonymized disposable query index
  <workspace_key>/<source_key>/control.sqlite3   PRIVATE jobs/checkpoints/import status
  <workspace_key>/<source_key>/staging/          PRIVATE temporary acquisition/extraction
```

`workspace_key` prevents one workspace's local index leaking into another. Derive
`source_key` deterministically from the normalized configured Canvas origin using
SHA-256; the raw origin remains in private config. This scopes numeric course IDs
without exposing a URL or requiring a multi-account registry. Derive public machine
keys from the existing stable machine ID; never publish the hostname itself.
Validate path components and keep all paths within their declared root.

No live SQLite/WAL file, mutable shared history manifest, or cross-machine appended
JSONL is the synchronized authority. SQLite is a local read index plus private job
state. Synchronized objects are sufficient to rebuild query state. These classes
have separate privacy tests; `query.sqlite3` must contain no private control tables.
The new mirror sits outside `_Shared/` deliberately: its immutable conflict-copy
handling must not accidentally trigger the vault's broad shared-store conflict
scanner. Do not weaken the existing vault/settings scanner to accommodate it.
The mirror therefore owns its own targeted conflict-copy scan. Provider-created
copies are validated by embedded identity and digest, not a OneDrive-specific
filename pattern alone. Identical duplicates are harmless; contradictory or corrupt
copies mark only the affected object/scope for repair and never replace last-good
evidence. Preserve offending bytes privately for diagnosis; do not delete originals.

The generated reader contract has one repository owner; do not hand-maintain a
second workspace copy. Different runtime versions may publish only the same exact
versioned contract bytes; an incompatible version is reported, not overwritten.

### 4.2 Immutable facts and acquisition receipts

Use canonical UTF-8 JSON, sorted object keys, finite JSON values, explicit versions,
SHA-256 content addressing, strict nested allowlists, and existing fsync/atomic
publication patterns. Validate and scrub in private staging BEFORE writing any
byte into the agent-readable root, including temporary files. Exclusive publication
of an existing digest verifies identical bytes; disagreement is corruption.

Separate semantic facts from acquisition time so unchanged polls reuse fact blobs.
A fact object has `schema_version`, `kind`, `source_key`, `course_id`, `entity_key`,
and a kind-specific `payload`. A commit describes when/where that fact was observed:

```json
{
  "schema_version": 1,
  "source_key": "<origin digest>",
  "course_id": "<course id>",
  "scope": "assignment.submissions",
  "scope_id": "<assignment id>",
  "writer_key": "<opaque machine key>",
  "run_id": "<uuid>",
  "parents": ["<observed prior scope commit digest>"],
  "acquisition_started_at": "<UTC timestamp>",
  "acquisition_finished_at": "<UTC timestamp>",
  "mode": "snapshot",
  "membership_complete": true,
  "record_refs": ["<fact digest>"],
  "member_keys": ["<stable entity key>"],
  "gaps": [],
  "watermarks": {}
}
```

The filename is the hash of canonical content and is not recursively included in
the hashed payload. Publish facts first and the commit last locally. A cloud client
may still deliver the commit first: readers validate every required reference and
stage the commit as `sync_pending` until complete. Last-good published views remain
usable. Missing private originals do not prevent using already published safe text;
safe extraction commits do require every safe block they reference.

Commit scopes are concrete, not a new general event bus: course roster, groups,
assignments, modules, assignment groups, pages, overrides, and assignment submissions,
comments, attachment extraction. Distinguish `snapshot`, `delta`, and `import`.
Only complete paginated snapshots define absence for their exact membership scope.
Deltas, access denial, corrupt input, timeouts, file-sync gaps and deselection never
authorize tombstones. A complete empty snapshot is valid and distinct from failure.
Tombstones remove current membership only; historical facts remain.

### 4.3 Entity identity and current-versus-history reduction

Core identities:

| Entity | Key |
|---|---|
| Course/content | source + course + Canvas content kind/ID |
| Student in course | source + course + stable pseudonym |
| Attempt | source + course + assignment + pseudonym + Canvas attempt number |
| Attachment association | attempt key + opaque attachment key + original-content revision |
| Extraction | original digest + extractor/schema/model version + privacy-policy revision |
| Note revision | note UUID + parent revision + exact evidence references |

Do NOT use mutable `submitted_at`, download URLs, original filenames, or capture
time as attempt identity. Unknown attempt numbers remain explicitly unresolved
observations; never fabricate attempt 1. Preserve each observed submitted timestamp
as evidence. A later discrepant timestamp for the same numbered attempt is an
explicit discrepancy; it cannot erase the earlier timestamp. For causally ordered
observations the first captured timestamp is the established value. Concurrent
disagreeing branches expose both until reconciled; wall-clock order does not prove
which machine observed first. `first_attempt_at` means earliest known submitted
attempt, not earliest local download. Missing history is never represented as full.

History is the union of validated observations by digest. Current state is a
materialized view over complete causal scope commits and their deltas. A new commit
names the scope heads it actually observed. Different cloud-arrival orders must
produce the same history and current result. Do not use cloud mtime, client clock,
lexical UUID, or lease epoch to pick the newest Canvas fact across competing heads.
Ancestry orders sequential observations; applicable Canvas source revisions can
prove additional ordering only when their field semantics are known.

Equivalent concurrent facts may coalesce. Conflicting incomparable current facts
are `ambiguous`; retain the previous unambiguous view plus labeled candidates and
schedule one focused Canvas reconciliation naming all observed heads as parents.
An unresolved old branch arriving later may need another reconciliation. Do not
pause other courses or unrelated students. Avoid repeated full-course refresh loops.
The reducer is a small scope-specific DAG reducer, not a reusable CRDT framework.

Import commits are historical seeds, never proof of current Canvas membership or
freshness. New acquisition must not be displaced by an import arriving later.

### 4.4 Query index and reading contract

Use Python's standard `sqlite3` for the local index. One CE writer, transactional
ingestion, foreign keys, bounded busy handling, and read transactions for stable
pagination. Keep WAL sidecars local. Agents open with `mode=ro` and query-only
connections; do not use `immutable=1` for a database the runtime can update.
No arbitrary SQL execution tool over MCP. A direct reader is bound by the written
contract, not a newly invented access-control system.

Index logical tables/views for: courses; selected/retained status; content;
roster/groups; attempts and observations; current submissions/grades; comments;
effective Canvas due-date facts/overrides; attachment associations; extracted blocks;
scope commits/coverage; gaps; notes; comparison results. All are pseudonymized.
Source timestamps, UTC submission timestamps and acquisition timestamps are separate.
Display conversion uses configured timezone; retain the original instants.

The authoritative named views are `courses`, `assignment_context`,
`current_submissions`, `attempt_history`, `attachment_blocks`, `scope_status`,
`comparison_evidence`, and `agent_notes`. Specify their exact columns/null semantics
in S01/S07 and generate schema documentation from the defining registry. Test a
registry-driven boundary rather than duplicating column lists across tools/tests.

Queries return deterministic order, a dataset revision, bounded pagination,
provenance references and an envelope separating:

- freshness: source and last successful observation time, age and policy metadata;
- membership coverage: complete/incomplete/unknown for the exact scope;
- evidence coverage: available/partial/unavailable with per-file statuses;
- synchronization: locally complete/pending known references/ambiguous;
- acquisition: idle/queued/running/failed and next actionable step.

Do not call "all cloud files synchronized" based on locally visible manifests.
Age alone never makes readable evidence disappear. Pure direct/MCP reads do not
invoke Canvas, extraction, or cloud-provider APIs. Read-driven local index ingestion
may be bounded and report catch-up; heavy work is queued once by the owning runtime.
Reuse the known index instead of rescanning every historical object on every query.
Filesystem watcher hints are optional; correctness includes bounded periodic scans
and later arrival into older directories. Do not assume monotonically arriving dates.

### 4.5 Privacy publication law

Reuse the Identity Vault's stable pseudonyms and existing scrub/replacement map.
Preserve established pseudonyms; no new naming system. Publication refuses records
requiring unresolved/provisional identity, failing the nested allowlist, or failing
post-scrub verification; retain them privately for repair
and continue publishable records. A collection excluding unresolved identities must
not claim complete student membership.

Allowlist every nested text/metadata field. Student IDs can occur in group
membership, overrides, comments, document metadata and score observations, not just
top-level rows. Convert each to a pseudonym or safe role/opaque reference. Retain
useful author consistency as pseudonym/role evidence; never raw Office creator names.
Strip embedded transport URLs and private paths. Generic attachment labels replace
original names. Course/content IDs are permitted navigation identifiers.

No raw original, unreviewed image pixels, EXIF, internal OOXML package, private
score-ledger row, extraction traceback, or identity mapping is copied into the safe
root/index. JPG/scanned-PDF support publishes scrubbed OCR and geometry/status; it
does not imply original images are safe to expose. Existing explicitly SAFE media
flows remain supported; adding new visual derivatives requires the same actual
sanitization boundary, not metadata stripping alone. Visually essential missing
content is reported as a gap rather than called completely extracted.

This is pseudonymization, not a claim of anonymity or perfect PII detection. The
teacher's accepted direct-read contract remains sufficient; no per-read approvals.

### 4.6 Automatic acquisition ownership and synchronization

Implement a narrow advisory owner per workspace/source, separate from work-session
leases. One machine writes only its own presence file; presence contains opaque
writer key, random process-incarnation ID, claim lineage, increasing heartbeat
counter, release marker and last advertised commit references. Use atomic replacement.
No hostname, process path or credential enters synchronized presence.

Initial engineering defaults: heartbeat every 30 seconds; a visible incumbent is
protected while its counter advances. A contender observes unchanged presence for
120 seconds on its own monotonic clock before publishing its own successor claim;
clean release permits earlier acquisition. These are tunable implementation defaults,
not a teacher SLA. Far-future wall clocks cannot hold ownership forever. Sleep/resume
and process restart re-observe the visible incumbent before background acquisition.
Never write, replace or delete another machine's presence file. The election reducer
chooses among independently published claims; replacing an incumbent means changing
the derived owner, not overwriting that machine's file.
Two simultaneous new claims use a deterministic opaque-key tie-break, then keep the
chosen incumbent stable. Claims and delayed release from another incarnation cannot
revoke the current process's claim accidentally.

No eventual folder protocol guarantees one live owner during a partition. All
acquisition commits remain immutable and merges safe when both computers act. Detect
competing branches and reconcile automatically after visibility converges. A
non-owner can index/read all available evidence and request a bounded focused
acquisition via the control requests directory. The owner checks requests with its
heartbeat, not just the 15-minute course tick. If ownership is stale, the requester
may take over automatically under the rule above. If a needed focused read cannot
be fulfilled promptly, a bounded duplicate read-only acquisition is allowed and
labeled; it does not seize healthy background ownership. Coalesce identical work.

Local ownership changes never authorize Canvas writes or steal a scoring/operation
lease. Preserve `shared_work` handoff semantics in this program; the automatic
transition applies to course acquisition and read availability, not concurrent
posting rights. If session continuation separately requires its existing explicit
lease action, surface that existing requirement rather than changing it silently.

### 4.7 Acquisition completeness and incremental work

Use `config.active_courses()` as the initial explicitly selected set. Saved Previous
courses and retained store-only courses remain readable. Moving a course out of
Current or removing its bookmark stops acquisition, including queued jobs at the
next safe boundary, but retains data and discoverability as `retained`.
An explicit future reselection resumes it. Ordinary reads never reselect courses.
Do not discover every accessible Canvas course and silently acquire it.

Reuse the existing two-worker coordinator, complete pagination receipts, transport
allowlist, foreground yielding, cancellation and bounded 429 handling. Keep 15-minute
background change checks and 24-hour complete reconciliation as initial defaults;
these are observable/tunable, not new freshness refusal gates. Full selected-course
initial acquisition includes structure, roster/groups, submissions with observed
history, comments, rubric and due-date/override context. Do not run duplicate
Catalog and Mirror HTTP fetches for the same scope: one receipt feeds normalization.
The current submission fetch often returns only `(rows, error)`, unlike the complete
assignment/roster receipt. Add a completeness-bearing submission acquisition receipt
before using its result to remove membership. Absence of a transport error alone is
not proof that all pages were acquired.

Reuse submitted/graded overlap windows where reliable. Comment-only changes and
other unobservable deltas retain separate completeness/freshness until a full or
focused scope acquisition. File identity/source revision plus digest prevents
re-download and re-extraction. Missing/unreliable source metadata leads to a bounded
recheck; never infer a change from cloud file mtime. Do not claim unmodified work
requires zero Canvas requests, only no duplicate full downloads/extractions.

Keep 100 MiB per-file and 20-download/200-MiB acquisition-chunk limits initially.
These are resumable chunk bounds, not a permanent first-20-file cap. Persist the
remaining queue; continue fairly across assignments/courses on subsequent chunks.
Over-limit files retain explicit status and available evidence, never vanish.
Jobs survive restart in private local control state and can be reconstructed from
synced attachment associations minus completed results on another machine. Never
persist expiring Canvas download URLs as durable authority; reacquire through CE.

Preserve score-ledger observation semantics and idempotency by routing observations
through its current owner. The safe store may contain pseudonymized grade facts,
not raw ledger internals. Keep New Quiz storage/private response paths working
without treating them as supported ordinary-assignment scoring evidence.

## 5. Attachment and extraction contract

### 5.1 Originals and private ZIP archive

The original byte SHA-256 is the content identity. Store one deterministic ZIP per
original digest in the private archive, with one generic `payload` member containing
only the exact original bytes. Original filename, declared media type and source/
attempt association belong in separate immutable PRIVATE association records, not
the content-addressed ZIP. Identical bytes under two names produce one blob and two
preserved private associations. Use ZIP_STORED with canonical fixed member metadata
so zlib/runtime versions cannot produce competing archive bytes. Validate a present
archive by safely recovering the original digest before accepting it as reusable;
same named archive with different original bytes is corruption. Verify
archive integrity AND the recovered original hash before publishing a captured
status. Attachments reference the original digest opaquely in safe records; no path.

Stream through existing coordinated Canvas transport and preserve its origin,
redirect/credential and size safeguards. No raw-extension intermediate enters a
synced folder. Temporary files and extraction work remain machine-local/private.
Do not unpack executable submissions, run macros, follow OOXML external relations,
evaluate spreadsheet formulas, or execute uploaded code. ZIP here is the archival
representation, not permission to support arbitrary submitted ZIP projects.

Original archival success, text extraction success and safe publication success are
separate statuses. A captured original is retained even if extraction fails. A
crash between archive publication and safe commit leaves a reusable blob, not loss.

### 5.2 Required extraction result

Define pure-bytes adapters returning an internal extraction result, then one Sol-
owned privacy/publication boundary converts it to the synchronized result:

- extractor/schema/model versions and input digest;
- detected format, native/OCR/mixed method, available revision/format metadata;
- ordered blocks with stable block IDs, kind, exact extracted text, and locator
  (paragraph/table, PDF page, slide/notes, sheet/cell, image bounding box);
- for DOCX, paragraph/run style labels (scrubbed), list level/numbering identity,
  bold/italic/underline and explicit tab/break spans where present. Hyperlink display
  text is retained; raw hyperlink targets are not exposed. These are observed
  formatting attributes, not an attempt to reproduce Word layout;
- availability, partial reason, warnings, resource-limit status and processed/total
  counts; distinguish empty content, no extractable text, corruption, encryption,
  unsupported type, missing dependency/model, timeout and truncated processing;
- separate native and OCR provenance/confidence where provided; confidence is not
  an integrity verdict;
- source evidence revision and privacy-policy revision for invalidation/reprocessing.

Preserve Unicode punctuation, tabs, whitespace relevant to code, paragraph breaks,
and visible content order. Do not pass canonical evidence through `collapse_ws`.
Search/comparison may use separately labeled normalized derivatives. Query page
limits never truncate the durable extracted evidence silently. Large processing is
chunked; budget exhaustion marks remaining work/gaps explicitly.

| Format | Required behavior and real synthetic acceptance |
|---|---|
| DOCX | Walk body paragraphs/tables in order; preserve tabs/line breaks, quote characters, useful run formatting. Integrate existing tracked insertion/deletion timeline; keep revision text distinct from final visible text. Scrub author/creator metadata; absent revision history is unknown, not invented. |
| PPTX | Follow presentation relationships for slide order, not lexical `slide10` sorting. Preserve slide/shape/table text and speaker notes with locators. Extract relevant embedded-image text through the image adapter or explicitly report unprocessed visual content. |
| XLSX | Follow workbook/sheet relationships; preserve cells, shared/inline strings, formula text and cached values separately, sheet order, number-format identifiers and available comments. No recalculation, formula execution, external-link resolution or flattened cell soup. Mark uncached formula results unavailable. |
| PDF | Native per-page text first; rasterize and OCR textless/image-only pages. Mixed documents retain page-level method and gaps. A failed page cannot silently become an empty successful page. Preserve punctuation observed by extraction; OCR uncertainty is labeled. |
| JPG/JPEG/PNG | Validate format/pixel bounds, orient privately using metadata, CPU OCR into ordered located text blocks, scrub before publication. EXIF/GPS/raw pixels remain private. Diagram-only or unreadable handwriting reports a visual/recognition gap; do not fabricate text. |
| Existing text | Preserve existing accepted extensions, decoding provenance and exact meaningful whitespace. Do not expand executable-file support merely because originals are safely archived. |

### 5.3 Dependencies and resource bounds

Use the existing `python-docx`, Pillow and pypdf where appropriate. Preferred new
stack: maintained `rapidocr` with CPU `onnxruntime`, plus `pypdfium2` for PDF page
rendering. Do not choose legacy `rapidocr-onnxruntime` (its Python compatibility
differs), GPU runtimes, cloud OCR or a required system Tesseract installation.

S00 must prove compatible Windows x64 wheels for the actual launcher Python
(currently 3.13 or newer), first OCR with network denied, all model/dictionary files
available locally, asset hashes/licenses, and synthetic native/scanned examples.
Pin the tested dependency/model combination rather than trusting floating latest.
Record an explicit supported Python-minor/architecture matrix. Test every target
the revised launcher accepts, or constrain selection to the tested matrix with
clear unsupported-runtime guidance. One successful 3.13/x64 install does not prove
support for every newer Python or Windows ARM64. Do not alter the user's global
Python/PATH; use the application's existing private-environment provisioning path.
If models are not in wheels, use an explicit verified asset provisioning step in
the existing setup flow; runtime inference never silently downloads models.
No PATH edits, elevation, standalone engine installer or runtime auto-install.
Missing assets leave file work resumable and actionable, not a broken runtime.
An unproven OCR stub cannot pass the final program gate.

Adapters enforce archive member count/expanded-byte/compression bounds, XML safety,
image pixel bounds, per-chunk page/cell limits and a process timeout. Initial ceilings:
100 MiB input; 512 MiB total OOXML expansion; 20,000 members; 50 million pixels per
image; 25 PDF pages per chunk; 60 seconds per OCR page; 300 seconds per file chunk.
Sol may lower/adjust measured limits with a recorded rationale; never silently
discard evidence above them. Run heavy parsers/OCR in supervised worker processes
that can be stopped; thread cancellation alone does not bound a stuck native parser.
Reuse existing image/XML guards where they satisfy these explicit requirements.

Primary dependency references, not instructions to install during planning:
[SQLite WAL](https://www.sqlite.org/wal.html),
[SQLite URI read modes](https://www.sqlite.org/uri.html),
[RapidOCR](https://github.com/RapidAI/RapidOCR),
[RapidOCR package](https://pypi.org/project/rapidocr/),
[ONNX Runtime package](https://pypi.org/project/onnxruntime/),
[pypdfium2](https://github.com/pypdfium2-team/pypdfium2).

## 6. Agent reads, scoring, and contained derived work

### 6.1 One reading implementation

Create an application query service over named index views. Adapt existing
`get_roster`, `get_submissions`, `get_gradebook_snapshot`, `get_course_content`, and
history reads to it. Runtime-private compatibility adapters may rehydrate identities
in memory for existing internal consumers; never persist those results in the index.

Preserve compact MCP tables and existing history bounds (1-100 observations per
page, at most 20,000 requested chars per observation and 100,000 aggregate chars),
with explicit continuation and provenance. New attachment block reads paginate by
stable block position/revision; no repeatedly extracting entire files on reads.

Add only the concrete missing surface: `get_assignment_evidence(course_id,
assignment_id, view, ...)`, with views `attachments`, `comparisons`,
`notes`. Reuse the same query service for direct access and MCP; validate view-
specific options rather than silently ignoring them. No generic SQL MCP tool.
Existing `get_submissions(history=true)` remains a thin alias to the same history
owner while it is a current user-facing contract, not a parallel implementation.

Expose the reading contract through `get_product_guide(topic="canvasmirror")`.
The guide describes exact safe root/index discovery. Extend the existing guide
response with a local `read_access` descriptor for this topic: only the designated
safe mirror root and safe query index path, schema/revision and readiness. This is
an explicit narrow exception for agent access location, not permission to expose
vault/original/control paths or browse the workspace. Include a direct read-only
SQL example and equivalent MCP example. Direct queries must work without opening
the vault or starting FastAPI. Token-holding CE remains the only Canvas client.

Retained-course read access is deliberately broader than Current-only write scope.
Course discovery labels selected/retained. Pages and historical evidence for retained
courses are readable through the same contract. No read gate quietly changes a
write adapter's Current-course authorization.

### 6.2 Comparison evidence

Reuse `api/powergrader/overlap.py` for assignment-scoped comparison. Add chronological
attempt selection and extracted-file block inputs; retain original safe text and
separate normalized comparison text. Produce deterministic exact-file equality,
text overlap/span references, and successive-attempt additions/removals with source
locators. Include coverage, algorithm version, excluded shared assignment wording
and evidence digests. Compare like evidence and identify OCR-derived uncertainty.

Do not add plagiarism probability, AI-authorship detection, a curly-quote verdict,
cross-year scanning, or penalties. Quotes/revisions/timing are preserved observations
the agent interprets with the teacher. Cache results by input digests/version; new
evidence invalidates affected comparisons only. At this pilot scale an assignment-
bounded deterministic comparison is sufficient; no embeddings/vector service.

### 6.3 Scoring consumption and partial work

Replace the blanket attachment hold in `prepare_assignment_from_mirror` with a
read of ready safe evidence. Feed native/OCR text, block references, attempt timing
and relevant revision evidence through the current SAFE packet construction.
Scoring continues to freeze an assignment-scoped packet and stage exact results.
Agent-read access to the store does not itself authorize a Canvas score/comment.

Read availability and scorable completeness are distinct. If a file is unavailable,
expose the readable parts and an explicit gap. Prepare other students normally.
The affected student's item remains held for final scoring when its required evidence
is incomplete; do not silently score a partial assignment as complete. Existing
teacher resolution/held-work paths remain usable; no new whole-course refusal.
Make this structural: packet items carry evidence completeness and an explicit
hold marker independently of nonempty response text. The stage validator refuses
held items even when readable text exists. Today nonempty text can imply scorable;
the packet and stage boundaries must change together, with a regression test that
readable partial evidence cannot bypass a required-file hold.

Use assignment evidence revision, not a global mirror timestamp, for frozen packet
identity. Unrelated course changes or an unchanged refresh do not invalidate an
open packet. A new attempt never silently replaces reviewed/staged work. Return an
actionable newer-evidence indication and use explicit `refresh_scoring_session` to
incorporate it, preserving completed/reviewed work under existing rules. Keep current
material-change and live-grade preflight checks; tests must prove their preservation.

Preparation should use complete available evidence and attach freshness metadata.
It may coalesce a bounded focused acquisition for genuinely missing required input;
it must not loop over whole-course refreshes or require teacher confirmation merely
because an age window elapsed. The old preparation-only age refusal is intentionally
replaced by this availability/revision rule. Known ambiguous input holds affected
items until reconciliation; history and other complete work remain accessible.

Attempt timestamp evidence feeds existing late-day logic. Teacher-set days/waivers
and conversational extra-time decisions remain explicit, separate from source facts.
Never recalculate the earlier attempt's submission time from the latest row's
`late` flag or erase it when a later attempt arrives. Do not migrate or rewrite
private score events, effort-credit policy, correction packages, or feedback content.

### 6.4 Notes

One `notes/` namespace in the course store, assignment scoped by default. Save
append-only revisions with note ID, category (summary/comparison/feedback draft/
teacher directive), provisional/teacher-confirmed status, superseded reference,
safe text and source evidence revisions. Defaults are provisional, teacher-only,
never part of student feedback or a Canvas payload. Only explicit teacher direction
can mark a note teacher-confirmed. Source changes mark derived notes stale without
erasing them or pretending they are newly reviewed.

Provide `save_mirror_note(course_id, assignment_id, note, expected_revision)` as a
local persistence operation using the normal privacy boundary, plus the `notes`
read view. Direct filesystem agents use this helper for note publication rather
than editing immutable acquired objects or SQLite. This is write validation, not
a restriction on approved direct reads. Keep original `agent_commentary` in its
existing session owner; no automatic migration/posting of commentary.

## 7. Slices and dependency order

Each slice leaves a runnable product boundary and an execution-result entry. Do
not dispatch an entire program as one agent turn. A lead may execute multiple
ready slices sequentially; parallelism is within the declared ownership below.

```text
S00 dependency capability --> S05 PDF/image extraction
S01 text store + direct read --> S02 additive import
S01 --> S03 acquisition + automatic ownership --> S04 originals + resumable files
S02 private archive helper --> S04 transport/jobs integration
S04 + S00 --> S05 complete required extraction
S01 --> S06 unified reads + comparisons + notes
S05 + S06 --> S07 scoring consumption
S02 + S03 + S07 --> S08 activation + recovery + status + retirement
```

S00 and S01 may run concurrently with separate owners. S06's query work may begin
after S01, but shared MCP files remain lead-owned and are integrated sequentially.
S08 cannot cut over until all required formats and privacy/resumption gates pass.

### S00. Deliver a proven local OCR capability and readiness result

**Outcome:** the supported Windows runtime can extract text from a synthetic JPG
and scanned PDF without network access on first inference; missing assets produce
specific readiness status while the rest of CE remains usable.

**Read:** section 5; `api/requirements.txt`; `Open Canvas Expert.bat` dependency
provisioning section; `api/readiness.py`; primary dependency references above.

**Ownership (maximum lead + two workers):**

- Sol worker A: new `api/mirror/extraction/ocr_runtime.py`,
  `api/mirror/extraction/ocr_assets.py`,
  `api/tests/mirror/extraction/test_ocr_runtime.py`,
  `api/tests/mirror/extraction/test_ocr_assets.py`.
- Luna worker B: new synthetic asset builders in
  `api/tests/mirror/extraction/ocr_samples.py` and its own
  `test_ocr_samples.py`; creates no real-student fixtures.
- Lead Sol: `api/requirements.txt`, `Open Canvas Expert.bat`, `api/readiness.py`,
  exact required dependency-asset manifest and related existing setup/readiness
  tests. Lead creates shared extraction package `__init__.py`/`conftest.py`.

**Work:** prove supported Python/wheels, pin tested versions/assets with license
metadata; explicit setup-stage provisioning; no inference-time downloads; bounded
CPU OCR process and dependency readiness. Reuse Pillow and the eventual shared
worker supervisor instead of independent process launch patterns in each adapter.
Do not broaden the launcher to install unrelated system software.

**Acceptance:** a synthetic JPG/raster yields expected OCR key phrases/confidence;
a synthetic scanned PDF page can be rendered to a raster and recognized. This is
the dependency capability proof; native/OCR merging, final locators, orientation
and the format result contract belong to S05. After explicit model provisioning,
run first inference in a fresh subprocess with explicit local paths, outbound
socket calls denied/observed and no attempted model request. Verify asset hashes;
fixture generation never fetches models. Missing/corrupt assets, parser timeout and
missing runtime dependency are typed failures; API imports and ordinary text reads
work without OCR initialized. Prove wheel installation separately in a fresh isolated
setup environment for each accepted Python/architecture target; unavailable wheels
are setup failures, not an OCR runtime test result.
No production test skips missing required OCR and calls the slice GREEN.

**Gate:** `py -m pytest api/tests/mirror/extraction/test_ocr_runtime.py
api/tests/mirror/extraction/test_ocr_assets.py
api/tests/mirror/extraction/test_ocr_samples.py api/tests/test_connections_readiness.py
-p no:randomly -q`, plus the full suite because setup/readiness changed. Record
Python, package/model versions, asset hashes/licenses and fresh-environment result.
If the preferred stack fails, lead supplies a concrete compatible alternative
within local-only/no-admin constraints; ask senior only if those constraints change.

### S01. Persist and query one complete text-assignment evidence slice

**Outcome:** a normalized synthetic course/assignment with two students and multiple
attempts publishes safely to a synced root, rebuilds a local index, and is readable
directly without Canvas, vault access, or FastAPI.

**Read:** sections 4 and 6.1; storage, privacy and course-catalog references in 2.1;
existing `storage_support.py`, `identity_vault_service.py`, mirror privacy tests.

**Ownership:**

- Sol worker A: new `api/mirror/evidence_schema.py`, `evidence_store.py`,
  `api/tests/mirror/test_evidence_schema.py`, `test_evidence_store.py`.
- Sol worker B: new `api/mirror/evidence_index.py`,
  `api/tests/mirror/test_evidence_index.py`, with schema interface frozen by lead
  before writing. No edits to worker A's definitions.
- Lead Sol: new `api/mirror/evidence_publish.py`, `evidence_queries.py`, their
  `test_evidence_publish.py`/`test_evidence_queries.py`; workspace/runtime path
  helpers; `api/tests/mirror/conftest.py`; direct reading contract
  `docs/contracts/canvasmirror-evidence-contract.md` sections Storage, Records,
  Reduction, Privacy and Views. This contract is the implementation authority
  after it matches passing code; do not duplicate it in the direction reference.

**Work:** strict versioned records and allowed reference graph; atomic immutable
publication; causal reducer; local transactional index; named initial views;
publication scrubbing; last-good and pending/ambiguous states; scoped tombstones;
safe root/index discovery; exact read-only SQL examples. Add a minimal existing
acquisition-receipt adapter so the new boundary can consume real normalized rows
later without inventing another Canvas client. Do not yet cut existing reads over.

**Acceptance:** two independently initialized roots receiving the same objects in
different orders converge; duplicate facts deduplicate; commit-before-object stays
pending; failed/partial snapshot cannot delete; complete empty snapshot tombstones
only current membership; history/first submitted timestamps survive; concurrent
conflict is explicit; index deletion/rebuild reproduces semantic results; malformed
or unsafe nested data never appears in safe files, SQLite, WAL, or diagnostics.
Direct reads open no vault/network; read pagination is stable at a given revision.

**Gate:** all five new `test_evidence_*.py` modules named above plus
`api/tests/mirror/test_privacy_inversion.py`, `api/tests/test_workspace_pin.py` and
`api/tests/test_runtime_boundary.py`, then full suite. One invariant test per law,
not one near-identical privacy test per consumer.

### S02. Import existing pilot evidence additively and prove preservation

**Outcome:** a resumable importer copies existing machine-local caches and private
retained history into the new store without changing sources or treating imported
data as fresh Canvas truth. Either machine may contribute uniquely observed history.

**Read:** sections 4.3, 5.1 and 8; history/score-ledger references; actual old store,
Catalog, original manifest and New Quiz schemas. New Quiz private records are
inventory-only unless an existing supported read actually needs a safe projection.

**Ownership:**

- Sol worker A: new `api/mirror/evidence_import.py`,
  `api/tests/mirror/test_evidence_import.py`.
- Luna worker B: synthetic legacy-store builders in
  `api/tests/mirror/legacy_samples.py`, `test_legacy_samples.py`; implements
  representative sparse/rich/timestamp-conflict and corrupt source samples only.
- Lead Sol: new `api/mirror/evidence_migration.py`,
  `api/tests/mirror/test_evidence_migration.py`; the minimal private
  `api/mirror/original_archive.py` and `api/tests/mirror/test_original_archive.py`
  are mandatory in this slice so original-preservation acceptance is executable.
  The lead owns them until explicitly handed to S04's archive worker.

**Work:** dry-run inventory; private report/checkpoints; deterministic import keys;
re-scrub old records under the current vault; reconcile old history keys including
timestamp variations into numbered-attempt observations without dropping conflicts;
preserve original hashes and statuses. Read old private paths only through bounded
workspace helpers. Different machines can import their caches without resetting
the other import. Private score/operation/session stores are verified present but
not rewritten. Importing does not select a course or start a live refresh.

**Acceptance:** rerun after each injected interruption is idempotent; every valid
source observation has a mapped destination digest; every captured original has a
verified archive entry or an explicit unresolved gap; old sources unchanged;
malformed manifest/file doesn't destroy good imports or disappear from the report;
latest acquired state isn't replaced by later import; store-only retained course
is discoverable. No successful import marker while unresolved required records
are silently omitted. Original private bytes never enter the safe root.

**Gate:** new import/migration/sample/archive tests, existing
`api/tests/mirror/test_submission_history.py`, `api/tests/test_shared_vault.py`,
`api/tests/test_workspace_pin.py`, then full suite. Synthetic first; live dry-run
and additive import occur only in S08's announced private acceptance run.

### S03. Acquire selected-course changes with automatic machine ownership

**Outcome:** complete selected-course text/context/history acquisition feeds the
new store; incremental checks reuse unchanged facts; ownership transitions and
accidental overlap require no teacher handoff.

**Read:** sections 4.2-4.7; coordinator contract; current `sync.py`, `service.py`,
`course_catalog.py`, runtime lifecycle; settings course-selection methods.

**Ownership:**

- Sol worker A: new `api/mirror/acquisition_owner.py`,
  `api/tests/mirror/test_acquisition_owner.py`.
- Sol worker B: new `api/mirror/evidence_acquisition.py`,
  `api/tests/mirror/test_evidence_acquisition.py`; adapters use receipt interfaces
  agreed with lead, not direct edits to `sync.py`.
- Lead Sol: existing `api/mirror/sync.py`, `service.py`, `coordinator.py`,
  `course_catalog.py`, `api/runtime.py`, `api/platform_services/config/courses.py`
  and related existing tests; new course-context/override receipt normalization
  belongs to `evidence_acquisition.py` worker B after interface agreement.

**Work:** one acquisition receipt feeds facts and temporary existing read projections;
no duplicate HTTP scope fetch. Automatic ownership/control requests; current owner
priority; deterministic convergence on split claims; release/sleep/restart handling;
scope-specific watermarks; selected-course checks on enqueue and execution; preserve
comment freshness separately; full structure acquisition for selected courses;
fair scheduling and coalesced focused requests. Persist enough progress to resume
metadata acquisition; never reuse a watermark from a partially published pass.

**Acceptance:** desktop release/laptop acquisition; crash without release; advancing
incumbent while contender waits; future/past wall-clock skew; simultaneous claims;
late old-owner receipt; sleep/resume; deferred sync; deselection mid-job. Every
case retains observed history and stable pseudonyms. Only complete enumeration
causes tombstones. One failing assignment doesn't fail all assignments; a roster
identity problem doesn't publish unsafe rows or lie about completeness. All
background requests remain GET-only. Existing New Quiz capability behavior stays.

**Gate:** owner/acquisition tests; existing mirror sync/coordinator/new-quiz/query
tests; `api/tests/test_runtime_startup.py`, `test_local_runtime.py`,
`test_course_catalog.py`, `test_courses_groups_mirror.py`; full suite.

### S04. Capture originals and resume attachment work across interruptions

**Outcome:** all selected-course submission attachments enter a durable bounded
queue; originals are verified private ZIP blobs; completed captures are reused and
failures/gaps do not stop other evidence. No new format extraction is claimed yet.

**Read:** section 5.1; `submission_history.py`; ordinary attachment transport in
`api/powergrader/canvas_fetch.py`; existing credential/origin guards and workspace
evidence helpers. Reuse coordinated streaming rather than adding a downloader.

**Ownership:**

- Sol worker A: new `api/mirror/original_archive.py`,
  `api/tests/mirror/test_original_archive.py` (if introduced in S02, lead hands over
  explicitly and does not edit it simultaneously).
- Sol worker B: new `api/mirror/evidence_jobs.py`,
  `api/tests/mirror/test_evidence_jobs.py`.
- Lead Sol: `evidence_acquisition.py` integration, existing `sync.py`,
  `submission_history.py`, and narrowly necessary `canvas_fetch.py` routing/tests.

**Work:** stable attachment association facts, original-byte/hash validation,
generic ZIP members, no loose blocked extensions in new synced roots, local private
job DB, deterministic job identity, resumable chunk scheduling, transient retry
backoff, terminal actionable statuses, bounded temporary-file cleanup, crash-safe
publication. Reconstruct jobs from facts on the other computer without syncing a
live queue database. Attachment updates create revisions, not filename overwrites.

**Acceptance:** >20 files eventually all processed over chunks; one >100-MiB input
is explicit and doesn't starve siblings; partial stream never captured; same bytes
under two associations deduplicate; same filename/different bytes remains distinct;
signed URL expiry reacquires through CE; blocked original extension stored only
inside permitted ZIP; ZIP output restores exact bytes; restart after each durable
step doesn't redownload a completed validated original; missing cloud ZIP shows
original pending but doesn't erase already published safe text.

**Gate:** archive/jobs tests, existing mirror submission-history and ordinary
attachment workflow tests; relevant Canvas transport tests discovered from the
actual streaming owner (record exact paths), then full suite. No live uploads.

### S05. Extract required Office, PDF and image evidence without losing fidelity

**Outcome:** DOCX/PDF/PPTX/XLSX/JPG produce located pseudonymized evidence and honest
partial statuses; processing is cached and resumable. S00 and S04 must be accepted.

**Read:** all section 5; actual `source_material_extractors.py`,
`student_attachments.py`, `writing_timeline.py`, SAFE artifacts/safety functions.

**Ownership, wave A (maximum three workers with lead):**

- Sol worker A: new `api/mirror/extraction/docx.py`,
  `api/tests/mirror/extraction/test_docx.py`; existing writing-timeline helpers are
  read-only unless lead explicitly transfers those files in a later wave.
- Luna worker B: new `api/mirror/extraction/pptx.py`,
  `api/tests/mirror/extraction/test_pptx.py`.
- Luna worker C: new `api/mirror/extraction/xlsx.py`,
  `api/tests/mirror/extraction/test_xlsx.py`.
- Lead Sol freezes new `extraction/schema.py`, `supervisor.py`, registry and shared
  synthetic fixtures first; owns their tests and package `__init__.py`.

**Wave B, after A file ownership is released:**

- Sol worker A: new `extraction/pdf.py`, `extraction/image.py`, `test_pdf.py`,
  `test_image.py`, integrating S00's proven runtime.
- Luna worker B: new `extraction/text.py`, `test_text.py`, and a registry-driven
  `test_format_contract.py` after the registry is frozen.
- Lead Sol: extraction-job integration, safe publication, cache/version invalidation,
  `student_attachments.py` compatibility routing where necessary, and existing
  `api/tests/powergrader/test_student_attachments.py`. Lead owns final PII tests.

Dependency/model manifest, setup provisioning and readiness remain owned by the
S00 lead role throughout the program. S05 consumes that frozen OCR service and
owns cache/version invalidation, not a second asset installer. If evidence requires
a dependency change, reassign it explicitly to that owner and rerun S00's proof.
S00's `ocr_samples.py` remains the sole OCR sample builder; S05 imports it. Lead
owns shared extraction `conftest.py` and Office fixture builders; no worker edits
them in parallel or creates a duplicate sample system. Freeze schema/registry
before Luna begins `test_format_contract.py`.

**Work:** implement the format table in 5.2 exactly; no naive source-material helper
reuse that collapses code/punctuation. Bound hostile ZIP/XML/image/PDF work; package
assets/version invalidation; block-level text scrub; rich extraction diagnostics
remain private while safe status codes/locators are published. Scan embedded images
when relevant or expose the gap. No raw image release is implied by OCR support.

**Acceptance:** real synthetic documents, not fake extension bytes, cover all five
required formats; document order vs filename order; shared strings/formulas/cached
cells; slide notes; tracked DOCX insertion/deletion/author scrubbing; native/scanned/
mixed PDF; JPG orientation/OCR; punctuation and code whitespace; encrypted/corrupt/
zip-bomb/oversize/timeout cases; stale extractor version reprocesses only affected
files; punctuation normalization only in separate comparison derivatives.
One failed page/file yields explicit partial evidence and sibling progress.
Image acceptance establishes recognized text with coordinates/confidence only.
It does not establish diagram comprehension or reliable handwriting recognition;
publish `visual_content_unprocessed`/recognition gaps where applicable and preserve
the private original. Test the selected DOCX formatting fields explicitly.

**Gate:** `py -m pytest api/tests/mirror/extraction
api/tests/mirror/test_evidence_jobs.py api/tests/mirror/test_evidence_publish.py
api/tests/powergrader/test_student_attachments.py
api/tests/powergrader/test_writing_timeline.py -p no:randomly -q`, full suite,
plus S00's actual network-denied OCR proof with the final packaged dependencies.
All required adapters must execute; skipped OCR tests do not establish acceptance.

### S06. Deliver consistent direct/MCP reads, comparisons and contained notes

**Outcome:** the agent reads one semantic store through direct SQL/files or MCP,
compares assignment evidence, and retains linked notes in one namespace.

**Read:** sections 4.4, 6; MCP current read wrappers/guide registry/schema tests;
`overlap.py`; catalog/history contracts. S01 accepted; use synthetic extracted
blocks while S05 completes, then rerun parity with the actual adapters.

**Ownership:**

- Sol worker A: new `api/mirror/evidence_comparisons.py`,
  `api/tests/mirror/test_evidence_comparisons.py` and necessary narrow `overlap.py`
  changes plus its existing test owner if assigned explicitly.
- Luna worker B: new `api/mirror/evidence_notes.py`,
  `api/tests/mirror/test_evidence_notes.py`, after lead freezes note revision/privacy
  contract; no private original/vault/Canvas access in this module.
- Lead Sol: `evidence_queries.py`, `read_service.py`, `queries.py`, MCP tools/server/
  schema/snapshot/budget/guide integration, existing MCP tests and generated contract.
  Luna may draft `docs/guides/canvasmirror-agent-reading.md` only after lead hands
  over that single document; lead reviews privacy and path-discovery wording.

**Work:** named-view parity, safe direct locations, course selection/retention
labels, historical page reads, strict bounded queries, per-file statuses and
revision-pinned pagination, concrete evidence and note tools, no arbitrary SQL MCP.
Comparison results cite attempts/blocks and shared-prompt exclusions. Notes use
optimistic revision checks and append-only conflicts; no last-writer silent loss.
Existing privacy gate remains on MCP output even though direct reads are allowed.

**Acceptance:** equivalent direct/MCP requests return same semantic facts and
coverage; no Canvas/vault access needed for direct read; no new Canvas calls on
repeated pure MCP reads; stale age labels do not block; retained course can be read
without being selected; write scope remains unchanged; source changes flag derived
notes/comparisons; PII in a proposed note is scrubbed/refused before publication;
teacher-confirmed status cannot be fabricated by default; historical pagination
doesn't silently skip or duplicate records across a revision boundary.

**Gate:** evidence query/comparison/note tests; `api/tests/mcp_server`,
`api/tests/mirror/test_queries.py`, `test_read_service.py`,
`api/tests/test_course_catalog.py`; full suite. Lead takes next free schema number,
regenerates snapshot under pytest, generated inventory and measured listing budgets.
No invented hard-coded new tool count or unmeasured budget bump.

### S07. Use complete safe attachment/history evidence in Scoring Sessions

**Outcome:** agents can prepare an ordinary assignment containing all required file
types, score usable students, compare drafts and see timing/gaps while preserving
frozen review and Canvas-write safeguards. S05 and S06 accepted.

**Read:** canonical Scoring Sessions and AssignmentForge resources; section 6.3;
scoring/feedback/score-ledger reference sections in 2.1. Preserve actual current
stage/apply implementation when older prose disagrees; report the doc repair.

**Ownership:**

- Sol worker A: `api/powergrader/assignment_refresh.py`, `scoring_artifacts.py`,
  `api/feedback_artifacts.py`, `api/tests/powergrader/test_assignment_refresh.py`,
  `api/tests/powergrader/test_scoring_artifacts.py`,
  `api/tests/test_feedback_pipeline.py`, `api/tests/test_feedback_safety.py`.
- Sol worker B: `api/powergrader/scoring_packet.py`,
  `api/tests/powergrader/test_scoring_packet.py`,
  `api/tests/test_scoring_packet_mcp.py`; no edits to shared fixtures.
- Lead Sol: `scoring_preparation.py`, MCP session wrappers, freshness/material-
  revision seams, `api/tests/powergrader/test_scoring_preparation.py`, MCP prepare/
  refresh/apply tests, shared powergrader fixtures and scoring guide changes.

**Work:** remove blanket attachment hold, consume frozen safe blocks and attempt
timing, share useful context once, preserve multi-file evidence locators, report
partial work and held affected rows. Use evidence revisions instead of global
mirror-change invalidation; explicit session refresh incorporates new attempts.
Prepare/read/stage/apply stay distinct. Bind packet/stage digests to the exact
safe evidence used. Preserve teacher-only commentary and existing late override
questions; don't turn capture time or overwritten latest lateness into old timing.

**Acceptance:** mixed assignment with text/DOCX/PDF/PPTX/XLSX/JPG and one bad file
prepares all usable students; available evidence for held row is inspectable;
staging cannot silently treat incomplete required evidence as complete; unchanged
refresh and unrelated course changes leave packet stable; new attempt is visible
without rewriting reviewed work; explicit session refresh/staging follows existing
review rules; grade/comment live drift, privacy refusal, verification mismatch,
unknown transport result and idempotency tests still pass. No attachment bytes are
downloaded during a pure packet page read. Teacher accommodation decisions persist
through stage/retry without changing original attempt timestamps.

**Gate:** `py -m pytest api/tests/powergrader api/tests/mcp_server
api/tests/test_scoring_packet_mcp.py api/tests/test_powergrader_attachment_workflow.py
api/tests/test_powergrader_mirror_session.py -p no:randomly -q`, relevant existing
grade/ledger/feedback tests based on actual changed imports, then full suite.
Use fake Canvas seams; no real grade/comment post is required.

### S08. Activate, recover, expose progress and retire duplicate read authority

**Outcome:** the production runtime uses the durable store; the second computer
continues automatically from synchronized evidence; interrupted acquisition,
extraction and migration recover; the console explains waits and actionable gaps.
S00-S07 accepted before production activation.
This automatic continuation is for evidence acquisition/reads. Existing open-work
session lease requirements remain visible; do not claim seamless transfer of an
in-progress scoring action or silently change the work/posting lease model.

**Read:** section 8; runtime, workspace, multi-computer guide; console route/load
order references. Review all changed contracts against actual final behavior.

**Ownership:**

- Sol worker A: `evidence_migration.py` activation/recovery, runtime integration
  tests, new `api/tests/mirror/test_cross_machine_recovery.py` and
  `test_evidence_end_to_end.py`; new source/test files are lead-assigned explicitly.
- Luna worker B: `docs/mirror.md`, `docs/guides/more-than-one-computer.md`,
  `docs/guides/canvasmirror-agent-reading.md`, `api/README.md`; no code ownership.
- Lead Sol: runtime/service/readiness/log integration; `api/webui/routes/mirror.py`,
  `api/webui/static/canvasagent.js`, `api/webui/templates/canvasagent.html` and route
  tests; settings courses route only if needed for existing selection display;
  production contract/AGENTS/guide and retired-path changes; schema/budget final sync.

**Work:** run resumable import, publish private coverage report, activate new read
owner after verified coverage, remove temporary dual-write/read paths, keep old
pilot files untouched. Existing Catalog API may remain a thin compatibility facade
over named views if callers still need it; no second independently maintained
Catalog authority. Legacy schema importer remains one deliberate migration owner
until both machines' existing evidence has been imported; retire it after field
acceptance rather than carrying speculative version shims forever.

Status exposes phase, completed/total where known, pending file/page counts, owner
state, scope freshness, last-good availability, elapsed phase time, and why work is
waiting. Errors are sanitized codes. Notification is for meaningful actionable
failure/missing evidence, not every ordinary background tick. Distinguish local
publication from cloud delivery and original capture from complete extraction.
Guide agents/users to keep the designated store available on device using their
provider's setting; no provider shell automation or claim CE controls hydration.

**Acceptance:** complete matrix in section 10; importer dry-run/source-preservation
proof; restart during each durable step; delayed/out-of-order two-directory sync;
all required formats ready; no duplicate authority or read-triggered full fetch;
private stores untouched; safe root/index free of seeded sensitive values;
affected console routes render with required globals and zero new console errors.
Report old files retained and any deliberate temporary migration owner explicitly.

**Gate:** new recovery/end-to-end tests, all mirror/MCP/powergrader tests, affected
runtime/readiness/web routes, full suite, rendered routes, and field procedure in
8.3. Automated results and actual two-machine field results are reported separately.
Do not claim cross-machine field acceptance from two directories on one computer.

## 8. Migration, activation, and rollback

### 8.1 Additive migration protocol

1. Resolve configured workspace/source and take an inventory through private CE
   owners. Include both local Mirror/Catalog and synced history/original stores;
   list legacy ordinary attachment sources from the actual evidence manifests.
   Do not recursively ingest arbitrary teacher folders.
2. Read/validate each source, recording a private source digest and mapping to
   destination observation/archive digests. Keep unresolved corruption/conflicts
   explicit. Original timestamp/provenance is imported, never restamped as Canvas
   acquisition. Reruns resume using content identity, not only filesystem mtime.
3. Scrub and publish safe facts; archive originals privately as ZIP; verify source
   counts, observation identities and recoverable byte hashes. Source files remain
   unchanged even when their extension is blocked by cloud sync.
4. Ingest destination into a fresh local index and compare supported semantic reads
   against source facts. Record mismatches and existing unavailable inputs.
5. Mark import complete only for verified scopes. A file missing from this computer
   can be reported as pending on the other computer, not silently discarded.
6. Activate the new read authority per machine after its required coverage is
   verified. Imported read results remain labeled historical/stale until confirmed
   by successful acquisition. Other useful scopes need not wait for one failed file.
7. When the second machine starts the new runtime, import its unique local evidence
   before retiring its acquisition/read path. Union duplicates deterministically.
   An old application still producing legacy artifacts is reported; do not consume
   incompatible new writes through an invisible permanent fallback.

Migration report: private detailed file mapping plus safe aggregates: attempts,
observations, original blobs/bytes verified, pending/missing/corrupt counts,
unresolved timestamp/body conflicts, selected/retained courses, activation state.
Never paste the detailed report or private file paths into public docs/commits.

### 8.2 Reversible activation

Use one machine-local backend activation checkpoint with explicit schema version
and migration coverage, not a scattered feature-flag framework. Until activation,
old production reads remain in place while tests/probes exercise the new path.
After activation, no invisible old-cache fallback may report fresh data. Failed
index can rebuild from immutable evidence; failed new read presents last-good or
typed repair. Preserve a documented rollback to the previous application version
and intact old files for emergency recovery, acknowledging that old code cannot
interpret new-only observations. New store data is never deleted during rollback.
Final code should not retain a routine dual-write backend after acceptance.

### 8.3 Two-computer field acceptance

After automated acceptance, announce the selected private courses and read-only
Canvas activity. On desktop, acquire/import a selected course and verify a real
ordinary assignment's text and supported attachments locally. Let the cloud client
sync; start CE on laptop without manually handing acquisition over. Verify stable
pseudonyms, retained earlier attempt times, usable evidence and honest pending
status until dependencies arrive. Compare aggregate revisions/hashes privately.

Then test desktop/laptop accidental overlap and normal close/sleep/resume using
read-only acquisition. No real Canvas write is part of this protocol. Exercise
safe simulated missing-file/corruption/reordering scenarios in disposable synthetic
roots, not by corrupting the teacher's real cloud folder. Verify that no new loose
blocked extensions are created by the new archival path. Both machines must use
the new runtime before final legacy retirement is considered.

If the second machine is unavailable, automated work can be complete but field
acceptance remains explicitly YELLOW. Do not stop useful coding because a future
field check needs the teacher's laptop. Do not automatically delete the old files
even after acceptance; destruction is a separate request.

## 9. Multi-agent execution rules and delegation packets

The lead Sol owns architecture interpretation, interface freezing, shared fixtures,
integration, schema counters, acceptance, commits if requested, and this brief's
Execution result. Maximum four active agents including the lead. Preserve shared
`dev` work. Do not use user-visible new chats for internal subtasks.

Workers receive **AGENTS + this brief + their slice only + named references**.
Avoid full-history forks and giant copies of this program into every worker prompt.
Sol handles storage reduction, ownership, migration, privacy boundaries, OCR
supervision and scoring integration. Luna receives frozen interfaces and pure
format/fixture/document tasks. Do not give Luna ownership of migration activation,
lease policy, grade writes, schema counters or privacy acceptance merely to save
tokens. Do not delegate test review to the same worker as the only acceptance proof.

Before each parallel wave the lead posts/records a file ownership list. An existing
module shared by slices belongs to the lead until explicitly handed off; workers
return patches in assigned files and never opportunistically fix neighbors. New
test fixtures go in the nearest lead-owned `conftest.py` through a requested change,
not repeated copied preambles. Pure fixture-builder modules are allowed where
named above because several formats genuinely consume them.

Worker task packet must include:

```text
Slice and outcome:
Required brief sections / source reads:
Owned production files:
Owned tests/docs:
Frozen input/output interface and error semantics:
Invariants and non-goals:
Exact focused command:
Dependencies ready / unavailable:
Report: changed files, test command/counts, deviations, unresolved questions.
Do not edit unowned files, touch real stores, call live Canvas, or commit.
```

Worker completion is not acceptance. Lead reviews cross-boundary diffs, integrates
sequentially, runs focused and required full gates, and records results before the
next dependent slice. If one worker discovers an interface problem, stop only the
dependent workstream; continue unrelated accepted work. Ask senior when the
teacher's direction must change, not for ordinary implementation details.

## 10. Required law/contract/example acceptance matrix

Drive permutations from reusable fixtures/registries; avoid duplicating an invariant
through dozens of wrappers. Use wholly synthetic identities and documents.

| Boundary | Required evidence |
|---|---|
| Safe publication | Seed real-shaped names, student/author IDs, URL secrets, filenames, nested Office metadata, overrides/comments. None reach safe JSON, SQLite/WAL, guide descriptor, logs or tool output. Originals remain privately recoverable. |
| Durable attempt law | Attempt 1 then 2/3; latest lateness flips; sparse later row; conflicting same-number timestamp/body; deletion and deselection. Every prior observation/timestamp remains addressable. |
| Atomic publication | Crash before/after fact, ZIP, commit and index transaction; partial files never count as complete; retries preserve/deduplicate evidence. |
| Sync order | Separate A/B synthetic roots; copy files in arbitrary order, including old directories and provider-style conflict names; commit-before-data; duplicate bytes; corrupted different bytes. Convergence or explicit pending/ambiguous state, never silent loss. |
| Ownership | Incumbent priority, clean release, crash expiry, sleep/resume, future clock, simultaneous claims, delayed old release/receipt; no teacher handoff and no dependency on perfectly exclusive leases. |
| Membership | Fully paginated empty set versus failed/partial pagination, focused assignment versus course scope, access loss versus deletion; only valid complete scope proves absence. |
| Jobs and fairness | More than chunk limit, repeated failures, restart, other computer rebuilding queue, duplicate capture, pending original with available safe text; siblings advance and failures are visible. |
| Required formats | Real synthetic DOCX/PDF/PPTX/XLSX/JPG with expected content/locators; scanned/mixed PDF; first-use offline OCR; corrupt/encrypted/expanded ZIP/image/timeout; no silent normalization/loss. |
| Read consistency | Named views vs MCP semantics, stable paging by revision, stale age served, missing/ambiguous labeled, retained course readable, no read network/vault/original access. |
| Comparison/notes | Assignment-only default, prompt exclusion, exact-file equality, successive drafts, source refs, stale derived notes, append-only note conflict; no detector verdict or Canvas payload contamination. |
| Scoring | Mixed usable/held rows; frozen packet; unchanged/unrelated refresh; new attempt; teacher late/extra-time decisions; stage/apply drift/idempotency/verification/privacy retained. |
| Migration | All source identities/digests mapped or explicit gaps; rerun each failure point; two machine imports; unchanged sources; rollback preserves new evidence. |
| Runtime boundary | Headless operation, one CE process per PC, no new FastAPI import outside allowed mount, no public bind, missing OCR doesn't stop unrelated runtime work. |
| Scale/efficiency | Synthetic 3 courses x 30 students x 120 assignments plus repeated drafts; bounded queries, no per-read full reindex or attachment extraction, unchanged acquisition reuses objects. Record timings/counts without invented latency SLA. |
| Console | Load every affected rendered route; route-specific globals/state present; zero new browser console errors; meaningful phase/gap/repair status. |

Every cross-subsystem slice runs:

```powershell
py -m pytest api/tests engine/tests -p no:randomly -q
```

Focused passing gates establish slice behavior. Full-suite reports must distinguish
new failures from section 3.2's exact pre-existing failure; never claim the full
suite passed while it failed. Slice acceptance may be GREEN against its focused
scope with the baseline exception explicitly carried. Overall repository gate
remains YELLOW until the unrelated failure is independently resolved or a senior
explicitly accepts that named exception; do not quietly waive it. Field acceptance
is separately reported and cannot be inferred from automated tests.

## 11. Ask about / stop conditions

Escalate with concrete evidence if: existing live source schema cannot be preserved;
the required local OCR stack cannot run/package on the supported runtime; a tenant
rejects the approved archival representation; a proposed change needs external AI,
new identity semantics, destructive cleanup, a different course selection policy,
new write permissions, or weakened reviewed-action safeguards. Keep independent
work moving. Do not use these as reasons to ask again about already-approved direct
reads, indefinite history, attachment support, or automatic acquisition handoff.

No work is GREEN by relabeling required PDF/JPG extraction as "later", skipping
tests because dependencies are absent, omitting files above one chunk limit,
conflating locally complete with globally synchronized, or leaving a second
independently maintained current-state store after cutover.

## 12. Execution result (lead updates in place)

**Execution result:** S00–S02 are implemented and accepted on synthetic/local
evidence. No teacher workspace migration, live Canvas call, production read
activation, or public push was performed. The isolated OCR environment's full
suite passes; the global `py` environment still lacks the S00 OCR wheels.

| Slice | Status | Commit(s) | Files / focused gate counts | Deviations / remaining evidence |
|---|---|---|---|---|
| S00 local OCR capability | GREEN | `ca29eea` | 3.13 isolated: 28 focused/readiness passed; 3.14 isolated: 28 passed; fresh subprocess JPG/scanned-PDF proof 21/21 on each runtime; isolated full gate 2218 passed, 1 skipped | Default global `py` has no OCR wheels, so its OCR-focused run reports 17 passed/4 typed dependency failures; the launcher provisions the tested private environments. Native stack: RapidOCR 3.9.2, ONNX Runtime 1.29.0, pypdfium2 5.1.0, Windows x64 Python 3.13/3.14. |
| S01 text evidence store | GREEN | `3a5ff41` | 74 focused tests passed; isolated full gate 2218 passed, 1 skipped | Includes immutable facts/commits, causal reducer, conflict diagnostics, rebuildable SQLite index, direct query envelope, generated reader contract, and safe text publisher. No live migration. |
| S02 additive importer | GREEN | `7f9882e` | 81 focused tests passed, 1 skipped; privacy scan passed with staged files; isolated full gate 2271 passed, 2 skipped | Bounded legacy inventory, additive historical import and private mappings, deterministic private ZIP originals, preserved conflicts and two-machine union, fresh migration-private index comparison. One focused skip requires Windows symlink privilege. No live migration or activation. |
| S03 acquisition/ownership | Not started | — | — | — |
| S04 originals/jobs | Not started | — | — | — |
| S05 required extraction | Not started | — | — | — |
| S06 reads/comparisons/notes | Not started | — | — | — |
| S07 scoring consumption | Not started | — | — | — |
| S08 activation/recovery | Not started | — | — | Two-machine field acceptance outstanding |

**Current traffic light:** YELLOW. S00–S02 are accepted against their
synthetic/local gates. S03 acquisition and ownership is the next pointer.
The private pilot import and production activation remain reserved for S08's
announced acceptance run; required attachment formats, MCP integration,
scoring consumption, and two-computer field acceptance remain outstanding.

For each completed slice record actual SHA, owned files, exact commands/counts,
traffic light, deviations, baseline exception and remaining field evidence. Keep a
single next-slice pointer rather than growing an execution diary. After final
acceptance, move durable implemented rules into the named contracts/guides and
retire this brief in the same batch. No future queue of separate slice briefs.
