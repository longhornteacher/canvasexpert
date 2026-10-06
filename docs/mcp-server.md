# CanvasExpert MCP server

A local, stdio-first [Model Context Protocol](https://modelcontextprotocol.io) server that
lets any MCP-capable assistant help plan lessons and manage rosters conversationally,
while CanvasExpert keeps sole custody of the Canvas PAT and almost every write path.

This is the primary agent-facing boundary for the Canvas Expert product. The durable
product definition, runtime/control-console split, host-neutral result expectation, and
canonical cooperation loop live in
[`docs/contracts/agent-runtime-product-contract.md`](contracts/agent-runtime-product-contract.md).

## Agent-agnostic workspace resources

The connected tools and their results carry the operational contract; a fresh client
does not need repository or workspace files before using the supported path. For deeper
authoring guidance, call `get_product_guide` with the relevant topic:

- Optional teacher workspace references may add local authoring or incident context, but
  they are not required for the supported connected scoring path and are never a
  substitute for the tool contracts below.

- **Local and indirect.** Serves this teacher's own Canvas data from Canvas Expert's
  local copy on their computer. The agent never holds the Canvas token. Canvas writes use
  bounded preview/apply or operation-ledger paths, except a teacher-requested
  `push_content_live` and the Scoring Session apply. A direction to score and post one
  assignment authorizes valid results only for that exact course/assignment, after the
  agent shows the preview; the server privately selects the Canvas transport. The
  stage/preview/apply sequence keeps the SAFE packet binding, per-student review,
  idempotency, transport, and receipt safeguards.
  Everything else writes only to local CanvasExpert state.
- **Agent-agnostic, not assistant-specific.** The tools, results, and focused repo guides
  are the operational contract. Optional teacher workspace notes can add local context,
  but a fresh client is not required to read a repository or arbitrary workspace file.
- **Pseudonymized, not anonymous.** Every student-data tool routes its result through the identity vault
  (`api/feedback_vault.py`) before returning it. Students are identified only by a stable
  one-word pseudonym (e.g. "Pikachu"), never a real name, Canvas user ID, or SIS ID. See
  `docs/contracts/pseudonym-contract.md` for the full pseudonym shape contract. The teacher
  may keep private identity records in M365 OneDrive for device sync; the MCP boundary still
  returns only pseudonymized data and never returns the vault or private filesystem paths.
- **Fail-closed.** Every student-data result also passes the existing outbound safety scan
  (`api/feedback_safety.py::scan_payload`) as a final check. If it isn't green, the tool
  withholds the payload and returns only a sanitized violation description.
- **Session-local.** Nothing here logs tool arguments or results; the local usage log
  records tool names only (see the end of this page). The pseudonym is the
  only student handle that crosses the wire, so it is also the only one an assistant has
  to work with.
- **stdio is the agent transport.** A loopback-only Streamable HTTP endpoint is mounted
  inside the lock-owning local runtime so a second CE entry point can attach to the same
  process. It is not exposed beyond `127.0.0.1` and is not a client configuration surface.
- **Evidence-bounded, never a live relay.** `get_roster`, `get_submissions`,
  `get_gradebook_snapshot`, and `get_assignment_evidence` serve exclusively from
  the local pseudonymized evidence index (`docs/mirror.md`). They serve available
  evidence with `coverage` and `warnings`, instead of fetching live from Canvas.
  A scope with no evidence refuses with `evidence_refresh_required` (call
  `refresh_mirror`); an index still being built refuses with
  `evidence_index_pending` (retry the read, not acquisition). The agent can ask
  Canvas Expert to sync and then read what Canvas Expert wrote to disk, but it can
  never receive a live Canvas response directly. The assistant runs
  `refresh_mirror` itself when needed and skips it when the data is available.

## Tools

Tool schema version 83 (38 tools). v83 changes `discover_scoring_work` results only (evidence-index source, coverage columns, opaque `mirror_revision`); inputs are unchanged.

| Tool | Purpose | Student data? |
|---|---|---|
| `list_courses` | Call list_courses first to get the course_id used by course-scoped tools. | No |
| `reconcile_sis_grade_bridges` | Discover differentiated bridge families and return a student-free status matrix. | No |
| `preview_sis_grade_bridge` | Persist a local SIS bridge projection or reconciliation review. | No |
| `preview_grade_adjustment` | Prepare a pseudonymized existing-grade adjustment for teacher review. | Yes, pseudonymized where applicable |
| `preview_attempts_grant` | Prepare a pseudonymized extra-attempts or reopen grant for review; no Canvas write. | Yes, pseudonymized where applicable |
| `get_roster` | Read stand-ins, selected sections/groups, or one pseudonym's local settings. | Yes, pseudonymized where applicable |
| `preview_roster_student_change` | Preview a validated pseudonym-first roster settings change without writing. | Yes, pseudonymized where applicable |
| `apply_roster_student_change` | Apply an unchanged preview of a local roster settings change. | Yes, pseudonymized where applicable |
| `get_submissions` | Read pseudonymized submissions or paginated retained history from local stores. | Yes, pseudonymized where applicable |
| `get_assignment_evidence` | Read one assignment's durable attachments, comparisons, or notes from the local evidence store. | Yes, pseudonymized where applicable |
| `get_score_ledger` | Read bounded pseudonymized score evidence from the private local archive. | Yes, pseudonymized where applicable |
| `get_gradebook_snapshot` | Read Current-course assignment grading counts and pseudonymized students. | Yes, pseudonymized where applicable |
| `get_product_guide` | Read CanvasExpert's product guide; omit topic for the overview. | No |
| `list_staged_content` | List drafts currently staged in the teacher's local review Inbox. | No |
| `preview_content_push` | Persist a local review of one staged draft or differentiated quiz variants; pass teacher quiz options in `quiz_settings`. | No |
| `preview_assignment_update` | Freeze a publish/date patch for one existing Canvas assignment by id, refused with no Canvas call if no field is supplied. | No |
| `stage_content` | Stage one completed Forge envelope in the teacher's review Inbox; no Canvas write. | No |
| `stage_attachment` | Stage a local Forge attachment. | No |
| `push_content_live` | Stage and create one authored draft in Canvas; dates use the preview pair and quiz options use `quiz_settings`. | No |
| `verify_live` | The one cheap Live check an agent makes after a push, by exact id or exact title. | No |
| `resume_operation` | Resume a teacher-approved operation from its last recorded step; not a new write. | No |
| `abandon_operation` | Mark one existing, teacher-approved operation abandoned; makes no Canvas call. | No |
| `refresh_mirror` | Refresh a saved course mirror, or its catalog with structure_only. | No |
| `list_feedback_contracts` | List teacher feedback contracts. | No |
| `discover_scoring_work` | Discover grading work without preparing or writing. | No |
| `prepare_scoring_session` | Prepare a local scoring or feedback_revision session for one exact assignment. | No |
| `refresh_scoring_session` | Add late or resubmitted mirror work to an open Scoring Session; tell the teacher what it added. | Yes, pseudonymized where applicable |
| `list_scoring_sessions` | List identity-free Scoring Session summaries. | No |
| `list_work_items` | Read shared work holders and sync status, or one work_id detail. | No |
| `get_scoring_packet` | Read one SAFE packet page for scoring or existing-comment revision. | Yes, pseudonymized where applicable |
| `stage_scoring_results` | Freeze scoring results or comment revisions locally; no Canvas write. | Yes, pseudonymized where applicable |
| `get_scoring_preview` | Read a page of the staged review exactly as Canvas will receive it, with warnings. | Yes, pseudonymized where applicable |
| `apply_staged_scoring_results` | Post the unchanged private stage to Canvas after direct teacher instruction; refuses the whole stage if live grades changed. | Yes, pseudonymized where applicable |
| `reset_scoring_review` | Reopen the current local scoring review. | No |
| `apply_operation` | Write the exact frozen operation to Canvas through its existing owner. | Yes, pseudonymized where applicable |
| `get_course_content` | Read local catalog assignments, pages or modules with kind-specific options. | No |
| `transfer_work_item` | Take over or hand off one shared work lease after sync. | No |
| `set_score_curve_rule` | Create or deactivate a local score curve rule without changing Canvas grades. | No |

`get_course_content` only reads the local Course Catalog and never falls back to a live
Canvas call. If the catalog hasn't been refreshed yet, or a result is outside policy, call
`refresh_mirror(structure_only=true)` first, then retry. Unlike the mirror tools below,
`get_course_content(kind="modules")` returns whatever module records the catalog holds,
labeled with `source`, `synced_at`, and `state`; stale scope is not write-authoritative.

The staged-content push tools land authored content in Canvas. Authoring still stages
first, always: the envelope and its `.done` marker go into the per-kind To Review Inbox,
exactly as `get_product_guide` describes. `stage_content` lets the assistant stage the
draft itself, so a client with no file access can reach the Inbox.

From there the route is chosen by what the teacher asked for, not by a default that
outranks them. A teacher who asked for content in their course gets `push_content_live`:
it stages the draft and applies it in one call, keeping the freeze internally so the
baseline capture, persisted review, and drift check all still run. Their ask picks the
route, so the assistant does not stage the draft and ask again, and does not put a review
in front of them that they never asked to see. Before the call it says what will land and
any warnings, then waits for their go. A teacher who asked for a draft
prepared for their review gets `stage_content` and stops there, with the draft waiting in
the Inbox for the preview pair. A draft that stages but fails to push is left staged on
purpose, so the teacher can read what was authored.

Group discovery is mirror-only: `get_roster(include=["groups"])` returns only group-set and
group names with freshness labeled; missing or malformed data refuses with `refresh_mirror`.
Differentiated quiz delivery is `preview_content_push(kind="quiz", variants=[...])`:
it resolves staged labels, captures a fresh private Canvas baseline through the
Operation Ledger quiz adapter, and exposes only the safe frozen review projection.
Every file declares one canonical pedagogical tier in `metadata.variant` (or
`metadata.variant_label`) and carries the same unsuffixed base title. Settings maps those
tiers to the configured public suffixes. Apply creates the exact configured-tag-suffixed
sources and one `<base title> - Bridge` no-submission bridge, attaches only the sources to
the selected module, and links the verified family. The result directs the teacher to
Canvas Live for review; the teacher owns Canvas Grade Sync.

The reviewed-preview machinery runs on every route. Whole-class drafts may remain
unpublished. Differentiated sources use the reviewed family operation, including source-only
module placement, bridge verification, and family-link save. Bridge sources from
AssignmentForge and QuizForge are unrestricted and tier placement is teacher-owned; Hub tier
pages are restricted and assigned to live differentiation tags.

The live push carries no due, unlock, or lock dates. Scheduling stays on
`preview_content_push`, because dated work is the case that most wants a look before it
lands, and every parameter is paid for in the tool listing of every session. Dated
content goes `stage_content`, then the preview pair. `preview_content_push` names the draft by the label
`list_staged_content` returns, builds the adapter payload, captures the Canvas baseline,
and persists one frozen operation; `apply_operation` takes only the three coordinates
that preview returned and runs the Operation Ledger apply, with its claim, drift check,
per-step checkpoints, and receipt. One draft, one
course, one call: the assistant cannot reach a second course or a draft the teacher did
not name, and a course that changed under the frozen review is refused as drift rather
than overwritten. Delivery options are per kind, and naming one a kind cannot carry is
refused rather than dropped. Whole-class drafts stay unpublished unless `published=true`;
differentiated family delivery uses its explicit reviewed publication and verification path.

`preview_assignment_update`/`apply_operation` is a separate, narrower write pair
for an assignment that already exists: there is no draft and no label, only a Canvas
`assignment_id` the caller supplies. Only `published` and the three schedule dates can
change; `description`, points, and assignment group are never read or resent, so nothing
on this path can flatten them. The preview reads the assignment live from Canvas -- never
the mirror or Course Catalog -- freezes its `updated_at` as the drift anchor, and shows a
`{field, from, to}` row per changed field. Supplying no field at all is refused before
Canvas is ever called, and apply is blocked as `drift_detected`, not overwritten, if the
assignment changed in Canvas since the preview.

The SIS grade-bridge pair is a bounded, linked-family grade-projection surface.
Discovery, reconciliation, and preview read the current local sync/mirror only.
Preview persists a local frozen operation and returns all three coordinates apply needs:
`operation_id`, `batch_id`, and `review_digest`. Missing or malformed mirror data refuses
with no live fallback. Preview does not inspect due dates, student coverage, overrides, or
module placement. After teacher approval, apply pushes only the unchanged reviewed scores to
the exact linked bridge, performs live postconditions for those writes, and requests a
targeted mirror refresh. Bridge operations do not repair or rearrange modules.

The grade-adjustment pair is the only existing-score write lane. Preview reads the
typed private mirror and returns pseudonym-only review rows; apply uses the Operation
Ledger assignment drift check and live prior-score check before each `posted_grade` PUT.
Undo is another preview with `adjustment.kind = "revert"`, and completed, non-reverted
receipts supply the private grade-adjustment projection and the reviewed curve operation's
already-adjusted guard.

Rule previews include every eligible numeric entered score, including zero, Canvas-missing,
and teacher-confirmed insincere rows. The teacher may pass eligible pseudonyms in
`exclude_pseudonyms`; excluded rows leave the rule math and appear in preview counts. Rule
models require their explicit input (`bump`, `target_avg_pct`, or `floor`); no target or
floor is inferred. Explicit and revert behavior is unchanged.

Reconciliation is the separate reviewed path for missing bridge links: it may create or
register a bridge from exact local IDs, but never guesses from title alone or starts Canvas
Grade Sync. Any invariant failure still stops the write.

The highest score is canon (a teacher may add extra credit on either the bridge or a tier
source): each preview compares every tier source's final Canvas score with the bridge's
current score and takes the highest, writing it only when it is higher than the bridge's
current score or the bridge is blank. It never lowers a bridge score and never writes to a
tier source; no late status is copied since the penalty is already in the copied score. The
preview reports `raises`, `already_canon`, `held`, and `held_students` (pseudonyms only,
resolved through the existing identity/pseudonym service) per family; any mix of excused and
scored states across the sources and the bridge is held for the teacher, never guessed.

A non-current local catalog is its own plain-text answer, not a failure and not reported
Canvas drift: `reconcile_sis_grade_bridges`, `preview_sis_grade_bridge(reconcile=true)`, and
apply (when the catalog goes stale between preview and apply) all return
`{"code": "catalog_not_current", "blocking": true, "sections": {<scope>: <state>}, "error":
"The local course catalog is not current.", "next": <refresh instruction>}`. This shape is
plain text any MCP host can act on directly: the agent runs `refresh_mirror(structure_only=true)`
itself and retries. A link-only repair (no live Canvas write) also never marks any local
catalog scope stale.

See the [SIS Grade Bridges guide](guides/sis-grade-bridges.md) for the complete three-tool
workflow, automatic family creation, recurring updates, privacy boundaries, and exact-ID
Attention recovery. The linked contract,
not the guide, remains the normative behavior authority.

`get_product_guide(topic=kind)` takes no `course_id` and carries no student data, so it
needs no course gate, no identity vault, and no safety scan. Forge kinds (`quiz`, `assignment`, `page`) read the same
`api/default_docs/AI Authoring/` file the control console's `/api/download-contract` route serves,
then receive the Forge-only staging appendix.

`get_product_guide(topic="")` closes the gap between what the tool list implies and what
the app actually does. Every successful response returns an ordered object that annotates
every guide topic with a one-line summary. `overview` serves Appendix B; the other named
CanvasAgent sections serve their exact Appendix A-F slices; `full` serves the entire file;
a standalone guide topic serves its own canonical file. `tools` is generated from the
current schema contract and groups every tool exactly once by teacher-facing job.
Topic matching trims surrounding whitespace and ignores case. The download route's
CanvasAgent bytes equal `topic="full"`; section topics are extracted from those same bytes.
Results are text-only MCP content: the server returns one minified JSON text block and
advertises no structured output schema or structured result. Same gate posture as
`get_product_guide`: no `course_id`, no vault, no safety scan. The always-on server
instructions point here rather than restating any of it.

`list_staged_content(kind="")` also takes no `course_id` and carries no student data, so
it likewise needs no course gate, no identity vault, and no safety scan. It reuses
`api.staged_content.list_inbox_files` (the marker-gated To Review listing) and
returns only each draft's label, never its absolute path. Pass `kind` to narrow to one of
`quiz`, `assignment`, or `page`; omit it to see everything staged across all three.

**Scoring Session workflow.** For a broad request such as “what needs grading,” the
assistant calls `discover_scoring_work()` with no arguments. Canvas Expert reads every
Current course from the local CanvasMirror evidence index in one consistent read and
returns assignment, freshness, and attention tables. No discovery call enqueues,
waits for, polls, or retries a refresh, and a background refresh never blocks it.
Counts are observed counts: `coverage` (`complete`, `incomplete`, `unknown`) and
`counts_complete` say whether an assignment's count is the whole workload, and an
unknown count is `null`, never zero. A course whose evidence is missing, partial,
or needs a newer Canvas Expert gets its own attention row (`evidence_not_acquired`,
`evidence_membership_incomplete`, `evidence_update_required`,
`evidence_index_pending`) while other courses stay usable. `mirror_revision` is the
opaque evidence-index revision. If existing sessions cannot be checked, discovery
still returns the work with a `scoring_resume_unavailable` warning, so do not assume
no session is open. A valid old observation remains visible with its age.
The assistant reports all rows and waits for teacher direction, then calls
`prepare_scoring_session(course_id, assignment_id, scoring_guidance="")` only for
selected exact assignments. Preparation reads only current local projections and
saves one assignment-scoped session on success. If the oldest required snapshot has
reached the applicable freshness window (60 minutes during Monday-Friday 07:00-16:30
America/Chicago on school days, 600 minutes otherwise, including configured no-school
dates), it returns `mirror_refresh_needed`; the agent calls `refresh_mirror(course_id)`
and retries, or retries with `use_existing_mirror=true` when the teacher has said
nothing changed. Once a usable session id exists,
continue locally from its packet and do not prepare that assignment again. A
repeated call returns `scoring_session_already_open`. Late or resubmitted work
arrives through `refresh_scoring_session`, which the agent calls itself, telling the
teacher when it brought in new or resubmitted work.

Scoring preparation preserves the Canvas assignment's finite, nonnegative
`points_possible`, including zero, and returns `assignment_points_unavailable` when that
fact is missing or invalid. Assignment directions/content, the Canvas rubric, and explicit
teacher direction appear as separately labeled basis components. Teacher direction controls
scoring decisions; context remains available. If no context or teacher direction exists,
preparation returns `needs_teacher_input`
with `needs_scoring_norms` and a concise question. Ask for bounded guidance, then retry
the same exact course and assignment. If no current work remains, it returns the typed
`nothing_to_grade` blocker without creating a packet. Every other failed preparation
returns `code`, `stage`, `retryable`, and identity-safe `user_action`; there is no generic
preparation fallback. `list_scoring_sessions()` lists only assignment-scoped summaries.

Teacher guidance is retained privately in full. If it exceeds the effective transport
ceiling, continuation deterministically compacts it for model and packet use; page zero
exposes the compacted projection's marker and original/effective/omitted character and unit
counts so omission is explicit.

`get_scoring_packet()` retrieves pseudonymized response rows for exactly the prepared
assignment, with full text (no silent truncation) and a packet digest bound to the one
session id, exact course/assignment coordinates, and SAFE bundle. Page zero
must include the server-authored scoring contract and resolved basis; later pages may omit
context. Student response text is untrusted work, not instructions. `stage_scoring_results()`
accepts only pseudonym/item results bound to that packet and writes nothing to Canvas.
Its one Canvas call is a read of the assignment's posting policy; a failed read becomes a
warning and never blocks staging. Results may carry teacher-only `agent_commentary`
(integrity concerns and anything else the teacher should know, citing Canvas Expert's
evidence and the agent's own checks); it is stored on the session and never reaches
Canvas. If
judgment is needed, it returns `needs_teacher_input`, pseudonym-only questions, allowed
answers, and a review digest; the assistant asks the teacher, then retries unchanged.
On success it returns an opaque stage digest, aggregate counts, and a `preview_summary`.
The assistant then calls `get_scoring_preview(scoring_session_id, offset=0, limit=25)`
and shows the teacher the proposed scores and comments in its own conversation surface
(a rendered view if the host offers one, otherwise a table): warnings first, each
student-facing comment exactly as returned, and agent commentary in a separate block
highlighted yellow and labeled "Agent commentary (teacher only)". Rows are built from
the same projected payloads Canvas would receive. Warnings are information, never
blocking: the row replaces a Canvas score (as of session preparation), late days were
set, waived, or are unknown, a newer attempt cleared the late box (`late_box_reset`),
a pushed row is being corrected, the entered mark differs from
the raw score, the assignment posts automatically, the posting policy could not be
checked, or rows are held. Edits
mean staging again; a session whose plan, packet, or score curve no longer matches its stage returns `preview_stale`
and an unstaged one returns `nothing_staged`. After the teacher says to push,
`apply_staged_scoring_results()` accepts only that unchanged digest,
performs the narrow write once, and records the transport receipt. Before any `apply_*`
or `push_content_live` call the assistant says what will change and any warnings, then
waits for the teacher's go; one go can cover several selected rows or assignments. After a terminal apply,
that assignment-scoped session is complete; continue through
any remaining rows in the teacher-selected set without a new blanket confirmation per
assignment. A newly discovered assignment requires new teacher direction. Existing New Quizzes with writing stop before a
packet with `new_quiz_writing_requires_assignment`; the teacher grades them in Canvas and
uses separate AssignmentForge assignments with teacher-chosen points for future writing portions. No transport type,
operation token, or private local id crosses the MCP boundary. A stale packet,
changed review plan, invalid answer, or ambiguous write fails closed. The preview is the
first review; Canvas Live is the record. Previously verified numeric-score pushes in the
current assignment session can use the reviewed correction path; comment-only and
feedback-only edits use feedback revision mode. Later edits happen in Canvas Live. The teacher's go
authorizes only the exact selected assignment set, not later discovered work.

Preparation uses fresh local CanvasMirror roster, assignment, and submission
projections. It makes no live Canvas call and downloads no attachments while preparing
the SAFE packet. Text
responses continue through the existing SAFE flow; attachment-bearing, media-only,
empty, and unreadable work stays held for review.
The mirror assignment projection carries only student-free quiz classification fields, so a
true New Quiz returns `new_quiz_writing_requires_assignment` before scoring norms or packet
creation. If any required mirror scope is missing, stale, malformed, incomplete, or
ambiguous, preparation returns a typed mirror blocker naming `refresh_mirror(course_id)` for repair.

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

**Scoring Session writes.** `apply_staged_scoring_results()` sends ordinary assignment scores and
comments through the frozen, verified assignment write lane, then reads back every posted
numeric score once (`score_mismatch`, `late_not_honored` with `score_readback_mismatch`, or
`score_readback_unavailable`). Previously verified numeric-score pushes in the current
session can be corrected through a newly reviewed stage; identical restages do not send,
and `sent_unknown` remains blocked. Comment-only and feedback-only edits use feedback
revision mode.
Canvas Expert does not write New Quiz item scores, per-item feedback, assignment totals, or
fallback comments. Results return only aggregate counts and pseudonym-keyed outcomes. A teacher who directs
the agent to score and post the selected assignments has authorized each exact stage to post,
after the agent shows the preview and its warnings; later edits happen in Canvas Live.
Authorization never carries to later assignments, another session, SIS action, or
arbitrary grade edit.
Ordinary assignments may offer comment-only posting after the teacher answers its question.

`get_roster`, `get_submissions`, `get_gradebook_snapshot`, and
`get_assignment_evidence` only read the local pseudonymized evidence index. None
fall back to a live Canvas call. Available evidence serves with `coverage`
(`complete`/`incomplete`/`unknown`) and `warnings`, even when partial or old. A
scope with no evidence refuses with `evidence_refresh_required`; the agent calls
`refresh_mirror(course_id)` itself and retries once it reports `"synced"`. An
index still being built refuses with `evidence_index_pending`; the agent retries
the read, not acquisition.

Stale `get_course_content(kind="modules")` and `get_course_content(kind="pages")` results
name `refresh_mirror(structure_only=true)` as their repair, which refreshes the whole
Course Catalog (assignments, assignment groups, modules, and pages).
Plain `refresh_mirror` refreshes the private mirror's roster, groups, assignments, and
submissions; it does not refresh catalog modules or pages.

The section, mirror, and Course Catalog reads named here reject an ID absent from
`list_courses` before recommending a mirror or Course Catalog refresh. Student-data tools
(`get_roster`, `get_submissions`, and `get_gradebook_snapshot`), group discovery,
and `get_course_content(kind="pages")` are scoped to Current courses (`config.active_courses()`).
`get_roster(include=["sections"])`, `get_course_content(kind="assignments")`,
`get_course_content(kind="modules")`, `refresh_mirror`, and `refresh_mirror(structure_only=true)`
accept any saved course, including Previous courses. Pseudonymized artifacts are
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
  ungraded-work count: `total_ungraded` sums submitted or pending-review work
  without a grade across the returned roster rows.
- The outbound safety scan always runs on the full row payload **before** tabulation and
  truncation happens **before** the scan, so the gate inspects exactly the bytes that leave
  the machine.

## Running it

Canvas Expert runs as one local process per PC, guarded by an OS lock. The first entry
point to start (a desktop agent through `api/mcp_server/__main__.py`, or
`Open Canvas Expert.bat` through `api/qf_ui.py`) owns the runtime; another agent on that
PC attaches to the same process through its local MCP endpoint. The browser console is
optional: it can be opened while the runtime is
running, and an unavailable console does not prevent the agent runtime from working.
Closing an attached agent does not stop the owner process; the owner shuts down when
its own entry point exits. `api/runtime.py` owns shared startup and shutdown work, while
the runtime host serves MCP and mounts the console when available.
If port 8765 is unavailable, the owning agent keeps its stdio connection; the console
and second-agent attachment are unavailable until the runtime can bind that port.

The CanvasAgent page in the local control console is the source for current local stdio setup.
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

On the CanvasAgent page, open Advanced setup and instructions → Download Claude package, then in the already installed Claude Desktop open
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

After registering, try `list_courses` first (no Canvas call, no student data; a quick
sanity check that the process starts and the interpreter resolves correctly), then
`get_gradebook_snapshot` on a Current course. Every student name in the output should be a
pseudonym you don't recognize from the real roster; that's the privacy boundary working as
intended, not a bug. If the evidence index has no data for this course yet,
`get_gradebook_snapshot` (or `get_roster`/`get_submissions`) refuses with
`evidence_refresh_required`; call `refresh_mirror` for that course and retry. If the
index is still being built, it refuses with `evidence_index_pending`; retry the read.

Merged options are mode-specific. Inapplicable options return `inapplicable_option` before
side effects. `get_course_content` accepts `full_descriptions` for assignments,
`full_text`/`include_unpublished` for pages, and `include_items` for modules.
`get_submissions(history=true)` reads retained observations with offset/limit and a default
12,000-character text bound; ordinary reads default to 2,000 and refuse history paging.
`get_roster(include=["sections","groups"])` selects safe structural projections; with
`pseudonym` it returns private settings plus `expected_settings_digest`. Null values in a
roster preview patch clear supported fields. Curve rules use `set_score_curve_rule` to create,
or `rule_id` plus `active=false` to deactivate. Work leases use
`transfer_work_item(action="take_over"|"hand_off")`.

Feedback revision is `prepare_scoring_session(mode="feedback_revision")` followed by the
same packet/stage/preview/apply tools. Revision ids use a distinct `feedback-` namespace.
Rows are `{pseudonym, comment_key, feedback}`; `attachment_file` is revision-only. The preview
shows `current_comment`, `new_comment`, and the frozen attachment name. Existing comments
are edited without score fields, preserving the existing receipts and attachment flow.
Scoring guidance, feedback contracts, late policy, review answers, grade mode and apply
idempotency keys do not apply to revision sessions; refresh/reset review refuse them.

Every registered tool call emits one local operations-log event: its tool name, timestamp,
outcome (`ok`, `refused`, or `error`) and duration; errors may add only the class name. The
existing log envelope includes app version. Arguments, result content, identifiers, names and
pseudonyms are never logged. No refusal-code field is added to the log allowlist.

Schema v78 wire budgets: 37 tools, at most 14,658 characters for tools/list and 2,303
characters for server instructions. The warn-before-push rule ends within the first 2,048
characters. These are wire measurements, not token promises.
