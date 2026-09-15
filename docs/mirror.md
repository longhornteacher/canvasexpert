# CanvasMirror

This file documents current implemented behavior. The 1.0-beta target architecture and
migration program live in
`docs/reference/canvasmirror-1.0beta-information-spine.md`; that vision does not supersede
the current contracts until its individual implementation briefs are completed.

A disposable local mirror of Canvas course facts, kept fresh by deterministic
background sync, living in the synced workspace at `_System/Canvas Mirror/`.
Reads that used to cost live Canvas round trips are served from disk in
milliseconds when the mirror is fresh. Two different rules apply to what
happens when it isn't fresh, by design:

- **The web UI's own gradebook snapshot route** (`gradebook_snapshot.load_snapshot`,
  used by CanvasExpert's own grading screens) falls back to a live Canvas
  fetch, visibly labeled `source: "canvas"` — the teacher is in the loop and
  reads there can feed a write decision, so staleness should never block them.
- **The AI-facing MCP tools** (`get_roster`, `get_submissions`,
  `get_gradebook_snapshot`) never fall back to live Canvas. This is the strict
  mirror-only law: the AI's whole path to Canvas must stay indirect — through
  Canvas Expert's own sync engine, never a direct relay of a live fetch. A
  stale or missing mirror makes these tools refuse with a clear error instead
  of serving live data; the assistant calls `refresh_mirror` (also an MCP
  tool) to trigger a sync and re-checks freshness, then re-reads. See "MCP
  reads and the refresh tool" below.

## Design laws

1. **Canvas is truth; the mirror is disposable.** Every file under
   `Canvas Mirror/` can be deleted and rebuilt by re-sync. Corrupt or invalid
   files are treated as absent, never repaired in place.
2. **Sync is deterministic.** No LLM anywhere in the data path. CanvasExpert's
   heartbeat moves data; assistants only consume the result.
3. **Real data at rest under teacher custody** — the same boundary as Canvas
   itself. Pseudonymization stays exactly where it was: at the outbound
   MCP/LLM gate. The mirror changes where reads come from, never what leaves
   the machine.
4. **Freshness is always visible.** Every collection carries an envelope
   (`state`, `last_success_at`, `last_attempt_at`, `error_code`); every
   mirror-served read is labeled `source: "mirror"` + `synced_at`; the web
   UI's own live fallbacks are labeled `source: "canvas"`. Staleness is never
   silent — and for the MCP tools, staleness is never quietly papered over
   with a live fetch either (see law 6).
5. **Foreground wins.** Sync runs on a background heartbeat and yields to
   whatever the teacher is doing.
6. **The AI's path to Canvas always stays indirect.** `get_roster`,
   `get_submissions`, and `get_gradebook_snapshot` serve ONLY from the mirror
   and refuse rather than falling back to a live Canvas fetch. The only way
   forward from a refusal is `refresh_mirror`, which triggers Canvas Expert's
   own sync engine (the same coordinator behind "Sync now") and reports a
   freshness status — never Canvas data itself. This is an absolute, not a
   default: there is no config flag or fallback path that lets an MCP tool
   relay a live Canvas response to the AI.

## On-disk layout

```
<workspace>/_System/Canvas Mirror/<course_id>/
  _sync.v1.json                    pass envelopes + delta watermarks
  roster.v1.json                   students + sections (consumer fields only —
                                   no emails, no avatars)
  assignments.v1.json              slim Canvas-shaped assignment index
                                   (the authoring catalog stays the rich source)
  submissions/<assignment_id>.v1.json
                                   per-student current row + append-only attempts
  submission_comments_state.v1.json
                                   private, course-level freshness sidecar for
                                   comment-bearing submission acquisition only
                                   (schema, course id, state, last success/attempt
                                   timestamps, sanitized error code — no comment
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

## Sync passes (`api/mirror/sync.py`)

- **full** — backfill and nightly reconcile are the *same code path*: fetch
  everything (with `submission_history` and, only on this pass,
  `submission_comments`), rewrite collections with attempt-preserving replace
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
- **delta** — two course-level questions since the last watermark:
  `submitted_since` (with history — catches resubmissions as new attempts)
  and `graded_since`. Near-empty for stagnant courses; a stagnant assignment
  costs zero requests forever. Delta never requests `submission_comments` and
  never reads or writes the comment sidecar, so a newer comment-free delta can
  never be mistaken for comment freshness.
- **roster** — students + sections; rosters rarely change, so daily. Roster
  also never touches the comment sidecar.

Every other submission-refresh path — the focused single-assignment refresh, the
`submissions.course_delta` write-through refresh, and group/roster
reconciliation — is narrower than a full pass and likewise never advances or
claims comment freshness; only a comment-inclusive full pass may do so.

Roster uses the private roster document only when its state is exactly `current` for
student and section reads. Roster also uses its separate private `groups.v1.json` only
when it is exactly `current` and under 24 hours old; it stores category/group IDs and
names plus membership `{id,user_id}` pairs, never student names or raw Canvas fields.
Missing, corrupt, stale, or unavailable group snapshots follow the existing live group
loader and a successful normalized live read replaces the snapshot. Roster membership and
group/group-set writes always validate and execute live, then invalidate the snapshot.
Create's differentiated-group endpoint and Home roster warnings reuse that same fresh
snapshot; either retains its existing live fallback when it is unavailable. Local group
data remains display/derived-warning input only and never authorizes a mutation.

Watermarks advance only on success, to pass-start minus a 10-minute overlap;
store merges are idempotent so overlap duplicates are harmless. Failures
degrade the pass envelope (`stale` after a prior success, `unavailable`
before one) and never touch collection files.

The Current-course Course Catalog refresh can acquire one complete assignment
collection and forward its in-memory receipt to the Catalog and this mirror's
assignment-membership commit. The mirror applies the existing complete-receipt
validation and membership writer only: it does not update a pass envelope or
watermark, reconcile submissions, or perform New Quiz work. The two local
projection commits are independent rather than transactional.

Every `full_pass`/`delta_pass` invocation — the 15-minute heartbeat, the nightly
reconcile, and manual "Sync now" alike — forwards the same already-acquired
assignment receipt to Course Catalog's assignment scope only, via
`course_catalog.refresh_catalog_assignments_only`. This happens before either
pass's own `assignment_error` early-return, so Catalog receives and applies the
receipt (good or bad) independently of whether the mirror pass itself continues.
Catalog's modules and assignment-groups scopes are never touched by this
path — they stay exactly as last committed. This is the same non-transactional,
independently-durable coordination the manual Catalog-refresh route already
uses, extended to the passes that previously re-fetched assignments without
ever updating Catalog.

Create's module picker reads the Course Catalog only when its modules scope is exactly
`current`; missing or non-current catalog state falls back to the existing live Canvas
lookup. This display-only read never authorizes module placement or another write.

Create's assignment-group picker likewise reads only an exactly-current Catalog v2
assignment-group scope (`id`, name, position, and weight are the stored allowlist) and
otherwise uses its existing live Canvas lookup. Catalog v1 remains a read-only
assignments/modules compatibility fallback and deliberately reports assignment groups
unavailable. Local picker data never authorizes a group mutation: operation preparation and
execution resolve the selected group live and retain their existing drift/preflight checks.

Course Info's assignments, students, and group sets each read their local projection
(Course Catalog, `roster.v1.json`, `groups.v1.json`) only when that scope is exactly
current/fresh, independently falling back to its own existing live Canvas call otherwise —
email stays a removed, live-only-if-ever-added field; modules stay live.

Gradebook's late-policy panel reads a small student-free `late_policy.v1.json` projection
(1.0beta-04a; seven allowlisted Canvas `late_policy` fields only, via
`api/mirror/store.py`'s `read_late_policy`/`write_late_policy`/`late_policy_is_current`,
its own file rather than widening the Course Catalog contract) with acquire-on-read
semantics (mirroring `list_groups`): serve it when its own `state` is `current`, otherwise
live-fetch and seed it. Applying a late policy stays fully live and, after a verified
success, invalidates only that course's projection (never blind-refreshes from the
submitted payload) so a failed reconcile leaves the scope stale rather than falsely
current. The extra-time student list (`GET /api/students/list`) reads the same roster
mirror as Course Info's student list, live only when the roster is not `current`.

**Attempt history is append-only** within a living submission: students who
resubmit accumulate `attempts` keyed by attempt number, which survive full-
pass rewrites. This is the substrate for regrade queues, revision chains, and
growth-over-time views. (Deleted submissions take their attempts with them —
the mirror mirrors truth.) New Quiz response snapshots follow the same law:
attempts captured earlier but absent from a later report are carried forward,
while `current`/`latest_attempt` always reflect the newest fetch alone.

## Scheduling (`api/webui/mirror_service.py`)

A daemon heartbeat (started in the server lifespan, alongside the routines
heartbeat) ticks every 15 minutes for Current courses only:

- first tick 2 minutes after launch (catch-up)
- **full** when none has succeeded in 24 h (first-run backfill, then nightly)
- otherwise **delta** every tick, plus **roster** daily
- Every successful **full** or **roster** maintenance pass also makes one
  best-effort private group-context read through Roster's existing normalized
  loader. Its result is nested evidence on that pass, not a new cadence or
  pass envelope: success replaces `groups.v1.json`; failure retains its
  last-good categories and marks that snapshot stale without failing the core
  maintenance pass. Ordinary deltas do not refresh groups.
- `notify_course_changed(course_id)` — write-through hook: after CanvasExpert
  itself pushes grades (PowerGrader push, curve apply/revert), a short-delay
  `submissions.course_delta` refresh issues only the overlapping submitted and
  graded collection questions. It does not fetch assignment structure or New
  Quiz metadata, and it deliberately leaves the general delta pass and
  watermarks unchanged; the next ordinary delta remains the course-wide
  freshness authority.

Config (machine-local): `mirror_enabled` (default true),
`mirror_serve_max_age_hours` (default 6 — older than this, the web UI's own
readers fall back to live Canvas; the MCP tools refuse instead, per law 6).

Routes: `GET /api/mirror/status` (per-course pass envelopes + watermarks, and
sanitized plan progress when passed `plan_id`), `POST /api/mirror/sync-now`
(asynchronous manual read-only sync; returns an opaque plan ID with `202`). The Home
surface polls the plan before rescanning its local Work findings. The legacy internal
`sync_now()` compatibility function remains direct for existing callers/tests; the HTTP
route uses the two-worker coordinator. Heartbeat and post-write refreshes also submit
read-only coordinator plans, so background GETs yield to foreground local requests.
See
`docs/contracts/canvasmirror-coordinator-contract.md`.

For release measurement, `tools/canvasmirror_release_benchmark.py --live-readonly`
uses the configured 1-current/2-concluded profile with disposable roots and core GET
owners only. It refuses any other profile and writes aggregate-only metrics outside the
workspace; it is never a Canvas content/grade write tool.

New Quiz metadata follows the same full/delta cadence without generating
Student Analysis reports. Per-quiz metadata fetches are skipped while the
stored doc is current (unchanged assignment `updated_at`, under a 24 h
true-up age) — a stagnant quiz costs zero requests per tick; the daily
true-up bounds staleness from item edits that don't bump `updated_at`. PowerGrader's focused New Quiz acquisition writes
the response snapshot on success. A fresh response snapshot can satisfy a
later PowerGrader read without another ordinary submission/report read; native
file evidence still uses the focused live transport.

### New Quiz capability gate (1.0beta slices 01a / 02b)

New Quiz endpoints are gated on active enrollment (`api/README.md` ~205-213): the
same token returns 200 in an actively-enrolled course and 403 in a
concluded/past-enrollment course, deterministically, for the metadata scope. Design:
**lifecycle predicts, probe confirms, circuit backstops**
(`docs/reference/canvasmirror-1.0beta-information-spine.md` Sec 9.4). Slice 01a
implemented the probe/circuit half; slice 01d now supplies the student-free lifecycle
predictor and suppresses normal concluded-course New Quiz metadata work.

`sync_metadata` (`api/mirror/new_quizzes.py`) keeps a small, student-free
capability record per course (`new_quiz_capability.v1.json`, via
`api/mirror/store.py`'s course_dir/course_lock/atomic-write conventions —
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
skipped-restricted. Once the cooldown expires, and for a manual
`sync_now(course_id)` with at least one New Quiz assignment, it makes one
collection probe. A successful probe can clear the restriction even when every
local quiz document is fresh, without inventing metadata. An empty New Quiz
assignment set remains zero-call. Skipped-restricted runs and circuit opens/clears are counted
in `sync_metadata`'s existing return summary (`capability`,
`skipped_restricted`, `circuit_opened`, `circuit_cleared`) so the effect is
observable without exposing course names.

## Mirror-first reads (`api/mirror/queries.py`)

Implements the `gradebook_queries` interface (`course_students`,
`course_assignments`, `course_submissions`, `assignment`,
`assignment_submissions` — each returning `(data, error)`) from the store.
Consumers flipped in v1:

- `gradebook_snapshot.load_snapshot` — mirror-first when fresh, live
  fallback; snapshot carries `source` + `synced_at` either way. This is the
  web UI gradebook route's own loader.
- MCP `get_roster` / `get_submissions` / `get_gradebook_snapshot` — served
  from the mirror when fresh (zero Canvas calls, works offline),
  pseudonymized and gated exactly as before; payloads carry `source` +
  `synced_at`. Unlike the web UI route above, these three tools call
  `mirror_queries` directly and refuse (a structured `{"ok": false, "error":
  ...}`) rather than falling through to a live fetch when the mirror can't
  serve — see "MCP reads and the refresh tool" below.

Explicit `queries=` overrides and monkeypatched test seams always bypass the
mirror in the web UI's loader, so offline tests exercise the live path
unchanged; the MCP tools' seam guard (`api/mcp_server/tools.py::_cache_safe`)
instead makes a monkeypatched fetch seam a reason to *refuse*, since there is
no live path left for it to fall into.

## MCP reads and the refresh tool (`api/mcp_server/tools.py`, `server.py`)

`get_roster`, `get_submissions`, and `get_gradebook_snapshot` are strict
mirror-only (design law 6): a stale or missing mirror returns
`{"ok": false, "error": "..."}` naming the problem, never a live Canvas
payload. `refresh_mirror(course_id)` is the assistant's only lever to move
past that: it calls `mirror_service.enqueue_sync` (the same manual-priority
coordinator plan behind the web UI's "Sync now") and waits up to
`tools._REFRESH_TIMEOUT_SECONDS` (25s) via `mirror_service.wait_for_plan`,
then reports `{"ok": true, "status": "synced"}`, `{"ok": true, "status":
"syncing"}` (still running past the timeout — safe to retry shortly), or
`{"ok": false, "status": "failed"}`. It never returns course, roster, or
submission data itself, so it opens no identity vault and runs no outbound
safety scan — the response is a sync status, full stop. This keeps the AI's
entire path to Canvas indirect: it can only ask Canvas Expert to sync, then
read whatever Canvas Expert wrote to disk.

## v1 non-goals (deliberate)

- Submission **comments** are captured only by the nightly full pass (author
  id, author role, comment text, created_at only — no names/avatars/
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
  last-good submission files. This slice is read-service-only: no Home/Work
  consumer, report fallback, or write-triggered invalidation is wired to it yet.
- **Attachment downloads** (names only, in attempt records).
- New Quiz item-level grading or feedback writes. Mirror snapshots are read-only,
  and Canvas Expert does not write New Quiz item scores or per-item feedback.
- Multi-machine conflict smarts beyond disposability. (`vault.json` — not a
  mirror file — remains the one cross-machine-conflict-sensitive artifact.)
- Startup-item registration (separate slice; per-user Startup folder,
  no admin).
