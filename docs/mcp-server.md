# CanvasExpert MCP server

A local, stdio-only [Model Context Protocol](https://modelcontextprotocol.io) server that
lets any MCP-capable assistant help plan lessons and manage rosters conversationally,
while CanvasExpert keeps sole custody of the Canvas PAT and almost every write path.

- **Local and indirect.** Serves this teacher's own Canvas data from Canvas Expert's
  local copy on their computer. It never holds the Canvas token. Canvas writes use
  bounded preview/apply or operation-ledger paths, except a teacher-requested
  `push_content_live` and the Scoring Session submit. A request to start one
  Scoring Session authorizes valid results only for its frozen course/assignment
  queue; the server privately selects the Canvas transport. `submit_scoring_results` keeps
  the SAFE packet binding, per-student review, drift, idempotency, verification, and
  receipt safeguards. Everything else writes only to local CanvasExpert state.
- **Pseudonymized, not anonymous.** Every student-data tool routes its result through the identity vault
  (`api/feedback_vault.py`) before returning it. Students are identified only by a stable
  one-word pseudonym (e.g. "Pikachu") — never a real name, Canvas user ID, or SIS ID. See
  `docs/contracts/pseudonym-contract.md` for the full pseudonym shape contract.
- **Fail-closed.** Every student-data result also passes the existing outbound safety scan
  (`api/feedback_safety.py::scan_payload`) as a final check. If it isn't green, the tool
  withholds the payload and returns only a sanitized violation description.
- **Session-local.** Nothing here logs tool arguments or results. The pseudonym is the
  only student handle that crosses the wire, so it is also the only one an assistant has
  to work with.
- **stdio transport only.** No network port is ever bound.
- **Mirror-bounded, never a live relay.** `get_roster`, `get_submissions`, and
  `get_gradebook_snapshot` serve exclusively from the local CanvasMirror
  (`docs/mirror.md`). All three refuse
  with a clear error when the required mirror data is stale or missing, instead
  of fetching live from Canvas. The assistant's only way past a refusal is
  `refresh_mirror`, which triggers Canvas Expert's own sync and reports
  freshness — never Canvas data. This keeps the AI's whole path to Canvas
  indirect: it can ask Canvas Expert to sync, then read what Canvas Expert
  wrote to disk, but it can never receive a live Canvas response directly.

## Tools

Tool schema version 46 (44 tools).

| Tool | Purpose | Student data? |
|---|---|---|
| `list_courses` | First call for every saved course (Current + Previous) and the `course_id` used by course-scoped tools | No |
| `list_sis_grade_bridges(course_id)` | Configured whole-course SIS bridges for a Current `course_id` returned by `list_courses` | No |
| `preview_sis_grade_bridge(course_id, family_title)` | Persists a local aggregate, digest-protected grade-projection review for one exact registered differentiated family | No |
| `apply_sis_grade_bridge(operation_id, batch_id, review_digest)` | Copies eligible final Canvas scores to the exact registered bridge through the Operation Ledger | No |
| `list_sections(course_id)` | Saved section values from the local mirror | No |
| `get_course_assignments(course_id, full_descriptions=false)` | Disk-only catalog assignments; descriptions are previews unless `full_descriptions=true` | No |
| `get_modules(course_id, include_items=false)` | Disk-only catalog modules; set `include_items=true` to include their items | No |
| `get_course_pages(course_id, full_text=false)` | Published normalized pages from the Current course's local v3 catalog; set `full_text=true` for complete bodies | No |
| `list_learning_objectives(course_id)` | Current reviewed learning objectives as a compact table; Current-course and local-document gated | No |
| `preview_learning_objective(course_id, objective, effective_start, effective_end, source_refs, replaces?)` | Exact reviewed create or replacement preview grounded in current local module, assignment, or page records | No |
| `apply_learning_objective(course_id, preview, preview_digest, expected_revision)` | Applies only the exact reviewed create or replacement preview after catalog/source/revision checks; replacement identity comes from the digest-protected preview | No |
| `delete_learning_objective(course_id, entry_id, expected_revision)` | Directly deletes one selected reviewed objective with revision protection | No |
| `get_authoring_contract(kind)` | Canonical authoring contract for Forge (`quiz`, `assignment`, `page`, `rubric`) from `api/default_docs/AI Authoring/` | No |
| `get_product_guide(topic="")` | CanvasExpert product knowledge; omit `topic` for the overview, use the annotated topic map to choose detail, or select `tools` for the complete generated inventory | No |
| `get_standards_profile()` | Published offline DataForge standards profile; no `course_id` or Canvas call, with Identity Vault access required | Yes, pseudonymized |
| `get_assessment_context(course_id, pseudonyms="")` | Bounded local assessment evidence for exact Current-roster pseudonyms; observational only | Yes, pseudonymized |
| `get_assessment_grouping_proposal(course_id, snapshot_id, method="overall_pct", cutoffs="", no_data_group="", group_set_label="")` | Read-only grouping proposal using an exact teacher-safe group-set label; no Canvas apply path | Yes, pseudonymized |
| `stage_content(kind, label, content)` | Writes one authored draft and its `.done` marker into the per-kind review Inbox; refuses an existing label rather than overwriting | No |
| `list_staged_content(kind="")` | Drafts in the local review Inbox; pass `kind` to filter or omit it for all drafts | No |
| `preview_content_push(course_id, kind, label, published=false, module_name="", assignment_group_name="", due_at="", unlock_at="", lock_at="", post_to_sis=false)` | Persists a local frozen review of one staged draft for one Current course; `next` carries the confirm-then-apply handoff | No |
| `list_groups(course_id)` | Current-course group-set and group names from the fresh local mirror, including the group set selected in Roster; no memberships or Canvas IDs | No |
| `preview_differentiated_quiz_push(course_id, variants, published=false, module_name="", assignment_group_name="", due_at="", unlock_at="", lock_at="", post_to_sis=false)` | Persists a frozen review for one staged QuizForge family; preparation requires canonical metadata tiers, configured public tags, a due timestamp, and a module, then `apply_content_push` creates color-suffixed sources plus the unsuffixed bridge | No |
| `apply_content_push(operation_id, batch_id, review_digest)` | Creates the exact frozen draft in Canvas through the Operation Ledger; same claims, drift check, and receipt as the push tab | No |
| `push_content_live(course_id, kind, label, content, published=false, module_name="", assignment_group_name="", post_to_sis=false)` | The route for a teacher who asked for content in Canvas; stages the draft, freezes and drift-checks it internally, then creates it. Unpublished unless `published=true`. Carries no dates: use the preview pair for those | No |
| `preview_assignment_update(course_id, assignment_id, published=None, due_at="", unlock_at="", lock_at="")` | Persists a local frozen field-diff review against one existing Canvas assignment named by id, read live from Canvas; refuses with no Canvas call when no field is supplied | No |
| `apply_assignment_update(operation_id, batch_id, review_digest)` | Writes only the frozen published/due_at/unlock_at/lock_at patch to Canvas through the Operation Ledger; blocked as `drift_detected` rather than overwritten if the assignment changed since preview | No |
| `get_roster(course_id)` | Current mirror roster as stable one-word stand-ins and section names | Yes, pseudonymized |
| `get_roster_student_settings(course_id, pseudonym)` | Safe local settings projection; stored nicknames are omitted | Yes, pseudonymized |
| `preview_roster_student_change(course_id, pseudonym, patch)` | Digest-protected pseudonym-first settings preview; `next` carries the confirm-then-apply handoff | Yes, pseudonymized |
| `apply_roster_student_change(course_id, preview, preview_digest, expected_settings_digest)` | Applies the unchanged preview; only a `canvas_group` patch reaches Canvas | Yes, pseudonymized |
| `clear_roster_student_field(course_id, pseudonym, field, expected_settings_digest)` | Direct digest-protected clear for supported local settings; nickname fields are rejected | Yes, pseudonymized |
| `get_submissions(course_id, assignment_id, include_text=true, pseudonyms="", max_text_chars=2000)` | Mirror submissions including historical rows; current_enrollment marks same-mirror roster membership; optional pseudonym narrowing and bounded text | Yes, pseudonymized |
| `get_writing_history(pseudonym, since="", until="", include_text=false, max_text_chars=2000)` | Private longitudinal Writing Record evidence; date-bounded, optional prose, and never a score, coaching, or judgment | Yes, pseudonymized |
| `get_gradebook_snapshot(course_id)` | Current-course pseudonymized gradebook snapshot from the local mirror, including assignment-level `ungraded` and `partially_scored` counts from Canvas workflow state | Yes, pseudonymized |
| `refresh_mirror(course_id)` | Sync a saved course's mirror after a stale refusal, report status, then retry the read | No, returns a sync status, never course data |
| `get_bell_schedule(schedule_id="")` | Workspace Bell Schedules; an empty id returns all variants | No |
| `get_day_schedule(date)` | Calendar state and schedule blocks for one YYYY-MM-DD date; repeated blocks yield consecutive meeting runs | No |
| `get_teacher_schedule()` | The teacher's local versioned schedule blocks | No |
| `get_school_calendar(date_from="", date_to="")` | Canonical School Calendar readiness, or a bounded range when both dates are given | No |
| `start_scoring_session(course_id="", assignment_id="")` | Freeze a mirror-backed queue for every Current course, one Current course, or one exact assignment in a Current course | No |
| `continue_scoring_session(scoring_session_id, rubric_name="", scoring_guidance="")` | Prepare or resume the active queue item; missing norms pause the same root session for teacher input | No |
| `list_scoring_sessions()` | One identity-free row per root Scoring Session with aggregate queue progress | No |
| `get_scoring_packet(scoring_session_id, offset=0, limit=10, include_context=true)` | SAFE scoring packet with an authoritative contract and untrusted response text; `next` explains row/person counts and paging | Yes, pseudonymized |
| `submit_scoring_results(scoring_session_id, results, expected_packet_digest, review_digest="", answers=None)` | Post valid SAFE-packet results to Canvas, or return pseudonym-only questions for an explicit conversational answer and retry | Yes, pseudonymized |

`get_course_assignments` and `get_modules` only read the local course catalog written by
the CanvasExpert web UI — neither ever falls back to a live Canvas call. If the catalog
hasn't been refreshed yet, refresh it from the web UI first, then retry. Unlike the mirror
tools below, `get_modules` never refuses on staleness: it returns whatever module records
the catalog holds, labeled with `source`, `synced_at`, and `state`, since module structure
is far lower-risk than student data.

The staged-content push tools land authored content in Canvas. Authoring still stages
first, always: the envelope and its `.done` marker go into the per-kind To Review Inbox,
exactly as `get_authoring_contract` describes, and the draft appears in the matching push
tab. What changed is who performs that step. `stage_content` lets the assistant stage the
draft itself, so a client with no file access can reach the Inbox, and the teacher no
longer hand-drops a file in the middle of a request they already made.

From there the route is chosen by what the teacher asked for, not by a default that
outranks them. A teacher who asked for content in their course gets `push_content_live`:
it stages the draft and applies it in one call, keeping the freeze internally so the
baseline capture, persisted review, and drift check all still run. Their ask is the
authorization, so the assistant does not stage the draft and ask again, and does not put
a review in front of them that they never asked to see. A teacher who asked for a draft
prepared for their review gets `stage_content` and stops there, with the draft waiting in
the push tab. A draft that stages but fails to push is left staged on purpose, so the
teacher can read what was authored.

Group discovery is mirror-only: `list_groups` returns only group-set and group names
and the Roster-selected set, and refuses with `refresh_mirror` when the private group
snapshot is stale or missing. Differentiated quiz preview is the separate write path:
it resolves staged labels, captures a fresh private Canvas baseline through the
Operation Ledger quiz adapter, and exposes only the safe frozen review projection.
Every file declares one canonical pedagogical tier in `metadata.variant` (or
`metadata.variant_label`) and carries the same unsuffixed base title. Settings maps those
tiers to the public color suffixes. Apply creates the exact color-suffixed sources and one
unsuffixed no-submission bridge, attaches only the bridge to the required module, and
registers the verified family. The result directs the teacher to Canvas Live for review;
the teacher owns Canvas Grade Sync.

The reviewed-preview machinery is not what makes the write safe to skip asking about --
it runs on every route. What the pair adds over the live push is a chance to look and a
place to put dates, and a teacher who wants neither should not have to walk it. Drafts
land unpublished unless `published=true`, which is the property that makes this
recoverable: the teacher can edit or delete the object by hand in Canvas before any
student sees it.

The live push carries no due, unlock, or lock dates. Scheduling stays on
`preview_content_push`, because dated work is the case that most wants a look before it
lands, and every parameter is paid for in the tool listing of every session. Dated
content goes `stage_content`, then the preview pair. `preview_content_push` names the draft by the label
`list_staged_content` returns, builds the same adapter payload the push tab builds,
captures the Canvas baseline, and persists one frozen operation; `apply_content_push`
takes only the three coordinates that preview returned and runs the same Operation Ledger
apply, so a draft landed from chat and a draft landed from the web UI are the same write
with the same claim, drift check, per-step checkpoints, and receipt. One draft, one
course, one call: the assistant cannot reach a second course or a draft the teacher did
not name, and a course that changed under the frozen review is refused as drift rather
than overwritten. Delivery options are per kind, and naming one a kind cannot carry is
refused rather than dropped. Drafts stay unpublished unless `published=true`.

`preview_assignment_update`/`apply_assignment_update` is a separate, narrower write pair
for an assignment that already exists: there is no draft and no label, only a Canvas
`assignment_id` the caller supplies. Only `published` and the three schedule dates can
change; `description`, points, and assignment group are never read or resent, so nothing
on this path can flatten them. The preview reads the assignment live from Canvas -- never
the mirror or Course Catalog -- freezes its `updated_at` as the drift anchor, and shows a
`{field, from, to}` row per changed field. Supplying no field at all is refused before
Canvas is ever called, and apply is blocked as `drift_detected`, not overwritten, if the
assignment changed in Canvas since the preview.

The SIS grade-bridge pair is a bounded, registered-family grade-projection surface.
Preview persists a local frozen operation and returns all three coordinates apply needs:
`operation_id`, `batch_id`, and `review_digest`. Apply copies only eligible final Canvas
scores to the exact registered bridge. It never discovers a family, creates or repairs a
bridge, changes family SIS settings, or starts Canvas Grade Sync.
A teacher who asks for the write has authorized it: the assistant runs the
preview/apply cycle and reports what landed, rather than asking a second time
for what was just requested. The authorization covers the course and family
they named, or all already-registered bridges when they say so explicitly, and
does not extend to another family or to an unbounded Canvas write. An assistant
choosing the target itself should summarize the preview first. Any invariant
failure still stops the write.

See the [SIS Grade Bridges guide](guides/sis-grade-bridges.md) for the complete three-tool
workflow, automatic family creation, recurring updates, privacy boundaries, and exact-ID
Attention recovery. The linked contract,
not the guide, remains the normative behavior authority.

`get_authoring_contract(kind)` takes no `course_id` and carries no student data, so it
needs no course gate, no identity vault, and no safety scan. Forge kinds (`quiz`, `assignment`, `page`, `rubric`) read the same
`api/default_docs/AI Authoring/` file the web UI's `/api/download-contract` route serves,
then receive the Forge-only staging appendix.

The Calendar, Bell Schedule, and teacher-schedule tools are reads only
(`get_school_calendar`, `get_bell_schedule`, `get_day_schedule`,
`get_teacher_schedule`). They share the same exemption: no `course_id`, no student data,
no course gate, no safety scan. Their write pairs were retired in schema v39 along with
`get_seating_context` and the whole Seating feature: all of them were built to feed the
classroom display, which has since been removed, and a calendar edit wants a calendar in
front of you, so those edits live in the web UI. What follows describes the retired shape and is kept only as
background for the stored contracts. The event pair used `action="upsert"` with one complete
structured event or `action="delete"` with its stable `event_id`; it mutates only the
canonical `events` array and supports the same revision/digest/atomic-write boundary.
The game-score pair is intentionally narrower: it requires an existing stable event ID whose
kind is `game`, changes only its `result`, and preserves the event's label, date, shape, and
other fields. It is the preferred tool for recording a result on a game that is already on the
calendar; it does not create or retarget events.

`get_product_guide(topic="")` closes the gap between what the tool list implies and what
the app actually does. Every successful response returns an ordered object that annotates
all eleven topics with one-line summaries. `overview` serves Appendix B; the other named
CanvasAgent sections serve their exact Appendix A-G slices; `full` serves the entire file;
and the two writing topics serve their own canonical files. `tools` is generated from the
frozen schema-v45 contract and groups all 45 tools exactly once by teacher-facing job.
Topic matching trims surrounding whitespace and ignores case. The download route's
CanvasAgent bytes equal `topic="full"`; section topics are extracted from those same bytes.
Results are text-only MCP content: the server returns one minified JSON text block and
advertises no structured output schema or structured result. Same gate posture as
`get_authoring_contract`: no `course_id`, no vault, no safety scan. The always-on server
instructions point here rather than restating any of it.

`get_standards_profile()` reads the one published local
`For AI/DataForge/standards-profile.json` artifact. It is offline and has no
`course_id`, but it is still student data: the Identity Vault and the same
outbound safety scan are required before the pseudonymized profile leaves the
process. A missing, malformed, unsupported, or unsafe artifact is withheld with
a structured error. It does not generate a profile, call Canvas, or apply a
grouping; the teacher reviews the profile and uses the Assessments coverage and
Students grouping surfaces for any later local review/apply step.

`get_assessment_context(course_id, pseudonyms="")` first requires a current local
mirror roster, then joins only its canonical pseudonyms to the published profile.
The roster source is labeled `local_mirror`; the assessment source is labeled
`local_longitudinal_history` with the profile's `generated`, `grain`, and
`snapshots_used` metadata. The result reports four coverage counts: current-roster
students with and without history, requested pseudonyms outside the current roster,
and published-profile students outside the current roster. It returns at most 25
students, at most 32 standards per student, and at most 8 `assessed_in` labels per
standard; a limit refusal never truncates evidence. Missing, stale, malformed, or
inconsistent roster state returns no rows with `action: "refresh_mirror"`. The tool
reports percentages and standards as source facts; it does not encode placement,
capability, integrity, remediation, or other judgment labels.

`get_assessment_grouping_proposal(course_id, snapshot_id, method="overall_pct", cutoffs="", no_data_group="", group_set_label="")`
requires a Current course, a current local roster mirror, and a current local group mirror.
The snapshot identifier is the exact trimmed local history ID. `group_set_label` is matched
after trim and case-folding against the teacher-facing Canvas group-category label; missing
or duplicate labels are blocking errors, and raw category/group IDs are never accepted from
or returned to the assistant. The proposal reuses the Students page's
`api.dataforge.canvas_join.build_coverage_report` and
`api.dataforge.grouping.build_grouping_proposal` seams, so method, cutoffs, No Data placement,
counts, tier membership, and the existing `proposal_digest` remain the UI proposal's facts.
The returned groups and placements contain pseudonyms only. Missing, stale, malformed, or
ambiguous local sources return no proposal rows and no live Canvas fallback. The tool is
read-only: the teacher reviews and applies a digest-protected change in Students; the
assistant must never imply that a Canvas group change was applied.

`list_staged_content(kind="")` also takes no `course_id` and carries no student data, so
it likewise needs no course gate, no identity vault, and no safety scan. It reuses
`webui.deps.list_inbox_files` (the same marker-gated To Review listing the push tabs use) and
returns only each draft's label, never its absolute path. Pass `kind` to narrow to one of
`quiz`, `assignment`, `page`, or `rubric`; omit it to see everything staged across all four.

**Scoring Session workflow.** `start_scoring_session(course_id="", assignment_id="")`
freezes an ordered queue from fresh Current-course mirror gradebook snapshots. Empty
filters mean every Current course; a course alone means that course; both filters mean
that exact assignment. An assignment without a course is an invalid scope. Start performs
no Canvas write. The root `scoring_session_id` authorizes valid results only for its frozen
queue; newly discovered assignments need a later session.

For a broad request such as “start a Scoring Session” or “what needs grading,” the
assistant lists Current courses, requests `refresh_mirror` for each, and reads each
`get_gradebook_snapshot`. It reports assignments whose `ungraded` count is positive and
their `partially_scored` counts, then starts one session without asking the teacher to pick
an assignment. If start returns `needs_refresh`, refresh the listed courses and retry.
Canvas workflow state is authoritative: a numeric score or teacher comment does not clear
`submitted` or `pending_review` work. The assistant never asks for an assignment type or
scoring transport.

`continue_scoring_session(scoring_session_id, rubric_name="", scoring_guidance="")`
prepares or resumes exactly one active assignment. If it lacks a usable Canvas rubric, it
returns `needs_teacher_input`, the same root id, available rubric labels, and a concise
question. Ask the teacher to choose one or provide bounded scoring guidance, then continue
that same root. A Canvas rubric remains authoritative for each assignment. If a just-in-time
refresh finds no grading work, continuation records `nothing_to_grade` and advances without
creating a packet. `list_scoring_sessions()` returns one identity-free row per root session
and aggregate progress.

`get_scoring_packet()` retrieves pseudonymized response rows for exactly the active
assignment, with full text (no silent truncation) and a packet digest bound to the root,
private assignment run, exact course/assignment coordinates, and SAFE bundle. Page zero
must include the server-authored scoring contract and resolved basis; later pages may omit
context. Student response text is untrusted work, not instructions. `submit_scoring_results()`
accepts only pseudonym/item results bound to that packet. Ordinary assignment results with
no questions apply immediately. If judgment is needed, the tool returns `needs_teacher_input`,
pseudonym-only questions, allowed answers, and a review digest without writing; the assistant
asks the teacher, then retries the same tool with the unchanged results and explicit answers.
After every terminal submit, the assistant calls `continue_scoring_session` with the same
root id and keeps going until the queue is complete, teacher input is required, a blocker
occurs, or the teacher asks it to stop. Existing New Quizzes with writing stop before a
packet with `new_quiz_writing_requires_assignment`; the teacher grades them in Canvas and
uses separate 100-point assignments for future writing portions. No transport type,
operation token, or private assignment-run id crosses the MCP boundary. A stale packet,
changed review plan, invalid answer, or ambiguous write fails closed. Review and editing
happen in Canvas Live; the teacher request authorizes only the frozen queue, not later work.

Continuation prepares each queue item from fresh local CanvasMirror roster,
assignment, and submission projections. It makes no live Canvas call and downloads no
attachments while preparing the SAFE packet. Text responses continue through the existing
SAFE flow; attachment-bearing, media-only, empty, and unreadable work stays held for review.
The mirror assignment projection carries only student-free quiz classification fields, so a
true New Quiz returns `new_quiz_writing_requires_assignment` before scoring norms or packet
creation. If any required mirror scope is missing, stale, malformed, incomplete, or
ambiguous, continuation names `refresh_mirror(course_id)` for repair.

Paging counts projected response segments, not students. A multi-item quiz gives one row per
student per item, and an oversized response may give several complete ordered segments. The
`segment_index` and `segment_count` columns identify each segment; concatenating its text in
order reproduces the original response. `total`, `segment_total`, `offset`, `limit`, and
`next_offset` count projected segment rows, while `source_response_total` counts original
scorable responses and `students_total` carries the distinct-student count separately. One
original response still has exactly one `(pseudonym, item_id)` result key for submission.
Walk pages by following `next_offset` until it is absent rather than comparing an offset
against `total`. The 25,000-token ceiling remains unchanged: pages pack complete segments to
fit it, and optional shared assignment context is deterministically compacted or omitted with
an explicit `shared_context_compaction` marker. The server-authored scoring contract and
resolved basis remain on page zero.

Packet membership counts are separate: `session_student_count` counts distinct private
session students, `bundle_student_count` counts distinct SAFE pseudonyms, and
`excluded_student_count` is the nonnegative session-minus-bundle count gap. This aggregate
does not join private identities to pseudonyms or infer why a student was excluded.
`students_without_responses` counts bundle students with no response rows; `held` counts
responses without scorable text. A 19-student session with 16 held bundle students therefore
reports 3 excluded students, 16 held responses, and 0 scorable rows.
The assistant reports held or otherwise unscorable work before scoring. Item/catalog or
evidence gaps are not described as an empty assignment and are never silently discarded.

The safety scan walks dict keys, so it cannot see into `{columns, rows}` tables. Every tool
that returns student text therefore gates the dict-row payload first and tabulates only after
the gate has passed it, `get_scoring_packet` included.

**Scoring Session writes.** `submit_scoring_results()` sends ordinary assignment scores and
comments through the frozen, drift-checked, verified assignment write lane. Canvas Expert
does not write New Quiz item scores, per-item feedback, assignment totals, or fallback
comments. Results return only aggregate counts and pseudonym-keyed outcomes. A teacher who asked to start this
session has authorized valid results for its frozen queue to post; the assistant reports
what landed, calls `continue_scoring_session` after terminal outcomes, and directs review or
edits to Canvas Live. Authorization never carries to later assignments, another session,
SIS action, or arbitrary grade edit.
Ordinary assignments may offer comment-only posting after the teacher answers its question.

`get_roster`, `get_submissions`, and `get_gradebook_snapshot` only read the local
CanvasMirror. None fall back to
a live Canvas call. If the required mirror data is stale or missing, they return
`{"ok": false, "error": "..."}` naming the problem; call `refresh_mirror(course_id)` and
retry the same read once it reports `"synced"`.

Stale `get_modules` and `get_course_pages` results name the Course Catalog refresh surface
as their repair. `refresh_mirror` reports only its actual roster, assignments, and
submissions scope; it does not refresh catalog modules or pages.

The section, mirror, and Course Catalog reads named here reject an ID absent from
`list_courses` before recommending a mirror or Course Catalog refresh. Student-data tools (`get_roster`, `get_submissions`, and
`get_gradebook_snapshot`) are scoped to Current courses (`config.active_courses()`). The
catalog reads (`list_sections`, `get_course_assignments`, and `get_modules`) and
`refresh_mirror` accept any saved course, including Previous courses. `get_course_pages` and
the Learning Objective preview/apply pair require a Current course
sections (the latter names the candidate ids to retry with). Pseudonymized artifacts are
scrubbed, not anonymous: the pseudonym is stable, and student text still comes through as
the student wrote it.

### Token-lean results

Tool results are carried in protocol responses, so the wire format is deliberately
compact. Client and model token treatment varies:

- Every tool opts into text-only result transport. The returned content is one text block
  containing the server's minified JSON; no structured output schema or structured result
  accompanies it. This keeps
  wire content small without changing tool names, inputs, or operations. Character counts
  describe wire size only; they are not a per-turn token promise.

- A refusal is `{"ok":false,"error":"..."}` inside that normal text result, not an MCP
  transport error. Clients must inspect `ok`; the MCP envelope itself remains successful.
- Results are minified JSON (the server serializes itself rather than letting FastMCP
  pretty-print).
- Differentiated push results include only exact source and bridge references; grade-projection
  results add aggregate counts and content-free step states, never per-student score rows.
- Tabular result sections use `{"columns": [...], "rows": [[...]]}` instead of repeated
  per-row JSON keys; some list tools return arrays instead.
- `get_submissions` supports narrowing: `include_text=false` returns status/scores only;
  `pseudonyms="Name A,Name B"` (comma-separated, case-insensitive) returns specific
  students; `max_text_chars` (default 2000, `0` = full) trims each submission's text with
  an explicit `…[truncated N more chars]` marker. The cheap pattern is status first, then
  full text for only the students that matter.
- Submission rows retain historical records and append `current_enrollment`, determined
  from membership in the same mirror roster. It does not describe a historical enrollment date.
- Gradebook assignment columns `has_submission` and `has_grade` count roster records with
  a submission timestamp and graded records with a score, respectively. Manual grades
  without a submission count toward `has_grade` and averages. Their difference is not an
  ungraded-work count: `total_ungraded` sums the returned students' submitted or pending-review
  work awaiting grading. Excused and unpublished work is omitted from these counts.
- `get_assessment_context` accepts the same comma-separated, trimmed, case-insensitive
  pseudonym filter and returns compact student rows with bounded standards evidence;
  omit the filter for the current roster (up to 25 students), or name only the students
  needed for the question. It refuses rather than silently truncating students or
  standards, and a missing/stale mirror requires `refresh_mirror` before retrying.
- `get_assessment_grouping_proposal` returns at most 25 current-roster placements and
  refuses rather than truncating when the compact result exceeds 20,000 serialized
  characters. It reports the method, cutoffs, group-set label, No Data group, coverage,
  group counts/membership, and proposal digest. Use the Students UI for review and apply.
- The outbound safety scan always runs on the full row payload **before** tabulation and
  truncation happens **before** the scan — the gate inspects exactly the bytes that leave
  the machine.

## Running it

The CanvasAgent page in the local web UI is the source for current local stdio setup.
It resolves the exact Python interpreter and absolute entry point from the unzipped
folder at the moment you copy them. Client-specific connect actions write only the
selected user's config file, keep a backup, preserve other servers, and do not require
administrator access.

For another MCP client that supports local stdio, copy this shape and replace the values
with the current page values:

```json
{
  "mcpServers": {
    "canvas-expert": {
      "command": "C:\\path\\to\\your\\current\\python.exe",
      "args": ["C:\\path\\to\\CanvasExpert\\api\\mcp_server\\__main__.py"]
    }
  }
}
```

## Claude Desktop

Use CanvasAgent → Advanced setup → Download Claude package, then in the already installed Claude Desktop open
Settings → Extensions → Advanced settings → Install Extension and select the package.
The package is folder-linked: it contains only a launcher and a manifest, while Canvas
Expert and its dependencies remain in the unzipped folder. The package embeds the current
folder path, so regenerate it after moving Canvas Expert. This is a user-profile import,
not a Windows application installer; it should not show UAC or request administrator
credentials. If the client reports `Blocked by client policy`, stop and follow district
policy rather than attempting a bypass.

Official references: [Claude local MCP servers](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
and [MCPB manifest](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md).

## ChatGPT desktop

The supported desktop connection uses the ChatGPT app's local stdio configuration,
managed from CanvasAgent. There is no hosted or tunnel path in Canvas Expert. If the
installed client cannot use local MCP tools, follow district policy and use another
supported local client; do not expose the MCP server publicly.

## Verifying it works

After registering, try `list_courses` first (no Canvas call, no student data — a quick
sanity check that the process starts and the interpreter resolves correctly), then
`get_gradebook_snapshot` on a Current course. Every student name in the output should be a
pseudonym you don't recognize from the real roster — that's the privacy boundary working as
intended, not a bug. If the mirror hasn't synced this course yet, `get_gradebook_snapshot`
(or `get_roster`/`get_submissions`) refuses instead — call `refresh_mirror` for that course
and retry.
