# CanvasMirror

This file documents the private projection and its refresh behavior. The durable,
pseudonymized evidence store is the read authority for the MCP student-data
tools; its named views and direct-reading rules are specified in
`docs/contracts/canvasmirror-evidence-contract.md` and
`docs/guides/canvasmirror-agent-reading.md`. There is no activation checkpoint or
historical import: the MCP roster, submission, and gradebook reads serve directly
from the local evidence index, and background maintenance keeps it current. The
1.0-beta target architecture lives in
`docs/reference/canvasmirror-1.0beta-information-spine.md`; that vision does not supersede
the current contracts until its individual implementation briefs are completed.

A disposable local mirror of Canvas course facts, kept fresh by deterministic
background sync, living on this computer at `%LOCALAPPDATA%\CanvasExpert\cache\Canvas Mirror\`.
Reads that used to cost live Canvas round trips are served from disk in
milliseconds when the mirror is within its freshness policy. When it isn't,
the agent tools and the control console handle it differently, by design.

This document covers the runtime's read boundary. The connected agent/MCP path is the
primary agent-facing read surface and is served from the mirror. The browser is a
small control console; its private Names page may use the documented live roster
fallback below. That fallback stays in the console and doesn't turn into a generic
browser-first architecture.

For the agent:

- **The AI-facing MCP tools** (`get_roster`, `get_submissions`,
  `get_gradebook_snapshot`, `get_assignment_evidence`) read from the local
  pseudonymized evidence index rather than from live Canvas. This is a
  design choice, with reasons: index reads are fast, consistent from one call to the
  next, and go through the pseudonym gate on the way out; and Canvas Expert stays the
  only thing that holds the token and talks to Canvas. A read that has no evidence yet
  refuses with `evidence_refresh_required`, and the agent refreshes it itself
  (`refresh_mirror`, `refresh_mirror(course_id, structure_only=true)`, or
  `refresh_scoring_session`) and reads again, without asking the teacher first. A read
  whose index is still being built refuses with `evidence_index_pending` and asks the
  agent to retry the read, not acquisition. Available evidence serves with honest
  `coverage` and `warnings` even when partial or old. See "MCP reads and the refresh
  tool" below.

## Design laws

1. **Canvas is truth; the mirror is disposable.** Every file under
   `Canvas Mirror/` can be deleted and rebuilt by re-sync. Corrupt or invalid
   files are treated as absent, never repaired in place.
2. **Sync is deterministic.** No LLM anywhere in the data path. CanvasExpert's
   heartbeat moves data; assistants only consume the result.
3. **Real data at rest under teacher custody**, the same boundary as Canvas
   itself. Pseudonymization stays exactly where it was: at the outbound
   MCP/LLM gate. The mirror changes where reads come from, never what leaves
   the machine.
4. **Freshness is always visible.** Every collection carries an envelope
   (`state`, `last_success_at`, `last_attempt_at`, `error_code`); every
   mirror-served read is labeled `source: "mirror"` + `synced_at`. Staleness is reported
   honestly in every read, so the agent can tell when a refresh is worth its
   time (see law 6).
5. **Foreground wins.** Sync runs on a background heartbeat and yields to
   whatever the teacher is doing.
6. **Agent reads come from the evidence index, and the agent refreshes it itself.**
   `get_roster`, `get_submissions`, `get_gradebook_snapshot`, and
   `get_assignment_evidence` serve from the local pseudonymized evidence index.
   That is a design choice: index reads are fast, consistent, and go
   through the pseudonym gate, and Canvas Expert stays the only thing that
   talks to Canvas. When a read has no evidence yet, the agent
   refreshes it without asking the teacher: `refresh_mirror` triggers Canvas
   Expert's own sync engine (the same coordinator behind the console's **Refresh
   course data**) and reports a freshness status rather than Canvas data;
   `refresh_mirror(course_id, structure_only=true)` refreshes a Current course's
   student-free Course Catalog (assignments, assignment groups, modules, and pages); and
   `refresh_scoring_session` brings late or resubmitted work from the mirror
   into an open Scoring Session. The agent skips a refresh when the data is
   already available, because a refresh costs time.
   Canvas Expert does not relay live Canvas responses to the agent; newer data
   arrives by refreshing the mirror and reading what Canvas Expert wrote.

Grade-adjustment previews read the local mirror; the apply-time live prior-score
check protects each reviewed write from overwriting a newer Canvas change.

## On-disk layout

```
%LOCALAPPDATA%\CanvasExpert\cache\Canvas Mirror\<course_id>\
  _sync.v1.json                    pass envelopes + delta watermarks
  roster.v1.json                   students + sections (consumer fields only;
                                   no emails, no avatars)
  assignments.v1.json              slim Canvas-shaped assignment index
                                   (the authoring catalog stays the rich source;
                                   includes only student-free quiz_id, is_quiz,
                                   quiz_kind, and is_quiz_lti_assignment
                                   classification)
  submissions/<assignment_id>.v1.json
                                   per-student current row + append-only attempts
  submission_comments_state.v1.json
                                   private, course-level freshness sidecar for
                                   comment-bearing submission acquisition only
                                   (schema, course id, state, last success/attempt
                                   timestamps, sanitized error code; no comment
                                   content; see the submission-comments paragraph below)
  new_quiz_capability.v1.json      New Quiz metadata-scope capability record
                                   (student-free; see "New Quiz capability gate" below)
  new_quizzes/_sync.v2.json        New Quiz metadata/response freshness envelopes
  new_quizzes/<assignment_id>/quiz.v2.json
                                   assignment, quiz, and item catalog metadata
  new_quizzes/<assignment_id>/students/<user_id>.v2.json
                                   on-demand report attempts and URL-free evidence
```

Per-assignment submission files keep OneDrive syncs small and localize any
cross-machine conflict to a single disposable file.

Ordinary assignment attempt evidence has a separate durable home under the
selected workspace's `_System/Archive/Submission History/<course>/<assignment>/`.
Its URL-free manifest keeps pseudonymized observations and references immutable
original-file blobs; it is private teacher evidence, not a current Canvas
projection.

### Evidence store and local index

The pseudonymized evidence store lives under the selected workspace's
`CanvasMirror/sources/<source-key>/courses/<course-id>/` (immutable facts and
scope commits). Its disposable read index is machine-local:

```
%LOCALAPPDATA%\CanvasExpert\cache\CanvasMirror\<workspace-key>\<source-key>\
  query.sqlite3                    disposable SQLite read projection
  reader.json                      local registry-derived descriptor (revision-bound)
  maintenance.v1.json              per-source index maintenance status
  control.sqlite3                  private attachment job queue
```

`reader.json` is generated beside the index after a successful rebuild and
carries the index schema version and revision; the old shared `reader.v1.json`
is ignored. `maintenance.v1.json` records the last attempt, last successful
publication, index readiness, and sanitized failure stage/code. The attachment
job queue has no extraction cache: each job's `extraction_state` is the single
record of "done for this association".

## Sync passes (`api/mirror/sync.py`)

- **full**: backfill and nightly reconcile are the *same code path*: fetch
  everything (with `submission_history` and, only on this pass,
  `submission_comments`; scoring refreshes run a full pass without comments),
  rewrite collections with attempt-preserving replace
  merges, prune assignments/students that no longer exist, reset watermarks.
  The full pass is also the only thing that can fix `missing`-flag drift:
  Canvas flips `missing` when a due date passes with no student action, which
  no delta can ever observe. Because this is the only pass that ever requests
  comments, it is also the only writer of `submission_comments_state.v1.json`:
  a successful comment-inclusive fetch that merges cleanly marks the sidecar
  `current`; an attempted comment fetch that fails degrades it (`stale` after a
  prior success, `unavailable` before one) without touching any submission
  file. A failure earlier in the pass (assignments, students) never attempts
  the comment fetch and so never touches this sidecar at all.
- **delta**: two course-level questions since the last watermark:
  `submitted_since` (with history, which catches resubmissions as new attempts)
  and `graded_since`. Near-empty for stagnant courses; a stagnant assignment
  costs zero requests forever. Delta never requests `submission_comments` and
  never reads or writes the comment sidecar, so a newer comment-free delta can
  never be mistaken for comment freshness.
- **roster**: students + sections, refreshed before the roster can age past
  the serve window (see Scheduling). Roster also never touches the comment sidecar.

Every other submission-refresh path (the focused single-assignment refresh, the
`submissions.course_delta` write-through refresh, and group/roster
reconciliation) is narrower than a full pass and likewise never advances or
claims comment freshness; only a comment-inclusive full pass may do so.

Groups live in a separate private `groups.v1.json`; it stores category/group IDs and
names plus membership `{id,user_id}` pairs, never student names or raw Canvas fields.
Group discovery reads it mirror-only and refuses with a `refresh_mirror` repair when the
snapshot is missing, malformed, or outside the freshness policy. Local group data remains
display context only and never authorizes a mutation.

Watermarks advance only on success, to pass-start minus a 10-minute overlap;
store merges are idempotent so overlap duplicates are harmless. Failures
degrade the pass envelope (`stale` after a prior success, `unavailable`
before one) and never touch collection files.

Every `full_pass`/`delta_pass` invocation (the 15-minute heartbeat, the nightly
reconcile, and manual refreshes alike) forwards its already-acquired
assignment receipt to Course Catalog's assignment scope only, via
`course_catalog.refresh_catalog_assignments_only`. This happens before either
pass's own `assignment_error` early-return, so Catalog receives and applies the
receipt (good or bad) independently of whether the mirror pass itself continues.
Catalog's modules, assignment-groups, and pages scopes are never touched by this
path; they stay exactly as last committed until a structure refresh. The two local
projection commits are independent rather than transactional.

Names reads the course's current private roster projection and otherwise falls back
to a bounded live roster read. Runtime operation preparation and execution keep
live drift/preflight checks; local projection data never authorizes a write.

**Attempt history is append-only** within a living submission: students who
resubmit accumulate `attempts` keyed by attempt number, which survive full-
pass rewrites. The disposable projection still prunes removed
students and assignments, while observed ordinary assignment attempts and
captured originals remain in the private retained-history archive for draft
comparison. This archive reports observed history only; it does not establish
current membership or freshness. New Quiz response snapshots follow the same law:
attempts captured earlier but absent from a later report are carried forward,
while `current`/`latest_attempt` always reflect the newest fetch alone.

## Scheduling (`api/mirror/service.py`)

A daemon heartbeat (started by `api/runtime.py` when the runtime starts, and stopped
with it) ticks every 15 minutes for Current courses only:

- first tick 2 minutes after launch (catch-up)
- **full** when none has succeeded in 24 h (first-run backfill, then nightly)
- otherwise **delta** every tick, plus **roster** once it reaches the serve window
  (`mirror_serve_max_age_hours`, at most 24 h)
- Every successful **full** or **roster** maintenance pass also makes one
  best-effort private group-context read through Roster's existing normalized
  loader. Its result is nested evidence on that pass, not a new cadence or
  pass envelope: success replaces `groups.v1.json`; failure retains its
  last-good categories and marks that snapshot stale without failing the core
  maintenance pass. Ordinary deltas do not refresh groups.
- `notify_course_changed(course_id)` is a write-through hook: after CanvasExpert
  itself pushes grades (grade adjustments, including curves and reverts, and SIS
  bridge writes), a short-delay
  `submissions.course_delta` refresh issues only the overlapping submitted and
  graded collection questions. It does not fetch assignment structure or New
  Quiz metadata, and it deliberately leaves the general delta pass and
  watermarks unchanged; the next ordinary delta remains the course-wide
  freshness authority.

Two evidence workers run alongside the heartbeat, started by `api/runtime.py`
after operation recovery and stopped with the runtime:

- **Index maintenance** (`ce-evidence-index`): rebuilds the disposable local
  index from safe files on a 30-second cadence, coalescing requests. It never
  calls Canvas and never holds the Identity Vault lock while scanning (it
  verifies against a short-lived vault snapshot). A publication or a read miss
  requests maintenance; a request arriving during a rebuild runs on the next
  cycle. Every course scan in the process (maintenance, publication, extraction,
  notes) shares an in-memory memo: an unchanged file (same size and mtime, older
  than 2 s) keeps its validation result, while a new or changed file, or any
  change to the identities the privacy check uses, is read and checked again.
  An idle tick therefore costs a directory listing, and the index is rewritten
  only when its inputs change. The memo is never written to disk.
- **Attachment work** (`ce-evidence-work`): continues capture and extraction in
  bounded chunks. Capture runs only when this computer owns acquisition; local
  extraction runs regardless. Downloads and adapters run outside vault
  transactions. Newly created jobs run newest-first ahead of older backlog. A
  runtime start retries only what can change: it reopens an extraction whose
  extractor or privacy-policy version changed, or whose gap is transient
  (`timeout`, `missing_dependency`, `resource_limit`, `original_missing`), and it
  leaves deterministic gaps and exhausted captures as they are. A successful
  explicit course refresh reopens that course's gaps and exhausted captures once.

Config (machine-local): `mirror_enabled` (default true),
`mirror_serve_max_age_hours` (default 6; older than this, the private roster projection
is not served, internal readers with a live fallback read Canvas instead, and the MCP
tools report it so the agent refreshes, per law 6).

Routes: `GET /api/mirror/status` (per-course pass envelopes + watermarks, and
sanitized plan progress when passed `plan_id`), `POST /api/mirror/sync-now`
(asynchronous manual read-only sync; returns an opaque plan ID with `202`). The
CanvasAgent page's **Refresh course data** button calls it and polls the plan. The route
uses the two-worker coordinator. Heartbeat and post-write refreshes also submit
read-only coordinator plans, so background GETs yield to foreground local requests.
Each heartbeat tick also rescans local work findings after its plans finish.
See
`docs/contracts/canvasmirror-coordinator-contract.md`.

For release measurement, `tools/canvasmirror_release_benchmark.py --live-readonly`
uses the configured 1-current/2-concluded profile with disposable roots and core GET
owners only. It refuses any other profile and writes aggregate-only metrics outside the
workspace; it is never a Canvas content/grade write tool.

New Quiz metadata follows the same full/delta cadence without generating
Student Analysis reports. Per-quiz metadata fetches are skipped while the
stored doc is current (unchanged assignment `updated_at`, under a 24 h
true-up age), so a stagnant quiz costs zero requests per tick; the daily
true-up bounds staleness from item edits that don't bump `updated_at`. PowerGrader's focused New Quiz acquisition writes
the response snapshot on success. A fresh response snapshot can satisfy a
later PowerGrader read without another ordinary submission/report read; native
file evidence still uses the focused live transport.

### New Quiz capability gate (1.0beta slices 01a / 02b)

New Quiz endpoints are gated on active enrollment (see the New Quizzes notes in
`api/README.md`): the
same token returns 200 in an actively-enrolled course and 403 in a
concluded/past-enrollment course, deterministically, for the metadata scope. Design:
**lifecycle predicts, probe confirms, circuit backstops**
(`docs/reference/canvasmirror-1.0beta-information-spine.md` Sec 9.4). Slice 01a
implemented the probe/circuit half; slice 01d now supplies the student-free lifecycle
predictor and suppresses normal concluded-course New Quiz metadata work.

`sync_metadata` (`api/mirror/new_quizzes.py`) keeps a small, student-free
capability record per course (`new_quiz_capability.v1.json`, via
`api/mirror/store.py`'s course_dir/course_lock/atomic-write conventions,
its own file rather than widening `_sync.v1.json`'s schema): `capability`
(`supported` / `restricted` / `unknown`), `last_probe_at`, `retry_after`, and
a sanitized `evidence` (`forbidden` / `unauthorized` category + consecutive
failure count). No status text, response bodies, URLs, or quiz titles are
stored.

Slice 02b uses `GET /api/quiz/v1/courses/:id/quizzes` as the metadata scope
probe and source for collection-matched records. Freshness is evaluated before
that request, so an ordinary under-24-hour unchanged tick stays zero-call.
When refresh is due, one successful collection proves the scope `supported`,
clears any prior restriction, and each uniquely ID-matched stale assignment
fetches only `/items`. A missing or duplicate collection match may make the
existing narrow per-quiz metadata request as a record-level compatibility
fallback.

One collection `HTTP 403`/`HTTP 401` is sufficient active-enrollment scope
evidence: it sets or renews `restricted` for 24 hours with only the sanitized
category and count, and makes no item or per-quiz fan-out calls. Other
collection failures are transient/invalid and neither open nor renew the
circuit. A successful collection plus an item failure remains `supported` and
reports incomplete metadata without replacing last-good quiz data.

Gate: while restricted and the cooldown has not expired, `sync_metadata`
skips the entire metadata pass (zero Canvas calls) and records the run as
skipped-restricted. Once the cooldown expires, and for a manual-priority
refresh of a course with at least one New Quiz assignment, it makes one
collection probe. A successful probe can clear the restriction even when every
local quiz document is fresh, without inventing metadata. An empty New Quiz
assignment set remains zero-call. Skipped-restricted runs and circuit opens/clears are counted
in `sync_metadata`'s existing return summary (`capability`,
`skipped_restricted`, `circuit_opened`, `circuit_cleared`) so the effect is
observable without exposing course names.

## Mirror-first reads (`api/mirror/queries.py`)

Implements the `gradebook_queries` interface (`course_students`,
`course_assignments`, `course_submissions`, `assignment`,
`assignment_submissions`, each returning `(data, error)`) from the typed
projection. This provider remains for internal consumers (Names, scoring
preparation, operation adapters); it is **not** the authority for the MCP
student-data reads, which serve from the pseudonymized evidence index instead
(see "MCP reads and the refresh tool" below).

Explicit `queries=` overrides and monkeypatched test seams always bypass the
mirror in the control-console loader, so offline tests exercise the live path
unchanged.

## MCP reads and the refresh tool (`api/mcp_server/tools.py`, `server.py`)

The durable evidence store (`<workspace>/CanvasMirror/`) is the teacher's
cloud-synchronized, pseudonymized store for agent-led scoring. It holds
immutable facts and scope commits, plus opaque attachment associations and
scrubbed extraction blocks. Private originals live in
`_System/Archive/CanvasMirror Originals/` as verified ZIP blobs; the live
attachment queue and query index are machine-local under
`%LOCALAPPDATA%\CanvasExpert\cache\CanvasMirror\`. Agents read the safe store
directly (see `docs/guides/canvasmirror-agent-reading.md`) or through the
assignment-evidence reader; both use the same named views.

`get_roster`, `get_submissions`, `get_gradebook_snapshot`, and
`get_assignment_evidence` are served from the local pseudonymized evidence index
(design law 6). One resolver (`tools._evidence_reader`) returns the reader or a
typed refusal: `workspace_unconfigured`, `canvas_origin_unconfigured`,
`evidence_update_required`, `evidence_index_pending` (retry the read, not
acquisition), or `evidence_refresh_required` (call `refresh_mirror`). Reads
return `coverage` and `warnings` and serve available evidence even when partial
or old; only a scope with no evidence at all refuses. `refresh_mirror(course_id)`
is how the agent moves past `evidence_refresh_required`, and the agent calls it
on its own, without asking the teacher. The tool calls
`api/mirror/service.py::enqueue_sync` (the same manual-priority
coordinator plan behind the control console's **Refresh course data**) and waits up to
`tools._REFRESH_TIMEOUT_SECONDS` (25s) via `wait_for_plan`,
then reports `{"ok": true, "status": "synced"}`, `{"ok": true, "status":
"syncing"}` (still running past the timeout, so retry shortly), or
`{"ok": false, "status": "failed"}`. A non-empty `operation_id` observes an
existing plan without dispatching, so a slow refresh can be polled without
enqueuing another acquisition. It never returns course, roster, or
submission data itself, so it opens no identity vault and runs no outbound
safety scan; the response is a sync status. This keeps Canvas Expert the only
thing that talks to Canvas: the agent asks it to sync, then reads whatever
Canvas Expert wrote to disk. When the data is already available, the agent
skips the refresh, because it only costs time.

`get_submissions(history=true, ...)` reads the indexed pseudonymized attempt
history (`attempt_history` view) with its captured attachment metadata. It
returns bounded, scrubbed observations with `coverage`; it never refreshes
Canvas or returns raw files. Attachment text is read through
`get_assignment_evidence`; unsupported or unreadable files are explicit gaps.

Scoring Session continuation follows the same boundary. Once the teacher has
selected an assignment, preparation reads fresh roster, assignment, and submission
projections only. Ordinary submission text enters the existing SAFE pipeline;
ready safe evidence from the durable store (native/OCR text and block
references) is merged in, so a student with readable attachment text is
scorable. A required file that is not yet captured or fully extracted carries an
explicit hold marker: the packet holds that item and the stage validator refuses
it even when readable partial text exists. The assignment projection's
student-free quiz classification stops a true New Quiz with
`new_quiz_writing_requires_assignment` before norms or packet preparation.

## v1 non-goals (deliberate)

- Submission **comments** are captured only by comment-inclusive full passes (the
  nightly full pass and the opt-in `refresh_mirror(course_id, include_comments=true)`;
  author id, author role, comment text, created_at only; no names/avatars/
  attachments). Delta stays lean: a comment-only change between full passes is
  still a blind spot until the next full pass, but that staleness is no longer
  silently inferred from the full/delta pass envelopes. A dedicated, private
  `submission_comments_state.v1.json` sidecar (`store.read_submission_comments_state`
  / `store.record_submission_comments_state`) tracks only the comment-inclusive
  fetch's own state/last-success/last-attempt/error, so `current`, `stale`, and
  `unavailable` describe comment freshness honestly instead of borrowing a
  newer comment-free delta's freshness. `read_service.private_submission_comments`
  reuses the normal submission records but reports this sidecar's envelope, and
  a missing or corrupt sidecar reads as `unavailable` without ever touching the
  last-good submission files. Feedback revision preparation and local work
  findings read it; no write-triggered invalidation is wired to it.
- Arbitrary attachment handling, media derivatives, OCR, and PDF extraction.
  Ordinary assignment originals are retained privately as evidence; only the
  existing approved text/DOCX routing may contribute scrubbed text to the new
  history read.
- New Quiz item-level grading or feedback writes. Mirror snapshots are read-only,
  and Canvas Expert does not write New Quiz item scores or per-item feedback.
- Multi-machine conflict smarts beyond disposability. Shared stores under `_Shared/`,
  not mirror files, carry the cross-machine conflict handling.
- Startup-item registration (separate slice; per-user Startup folder,
  no admin).
