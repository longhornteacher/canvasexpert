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
authoring guidance, call the relevant product guide or authoring contract:

- Optional teacher workspace references may add local authoring or incident context, but
  they are not required for the supported connected scoring path and are never a
  substitute for the tool contracts below.

- **Local and indirect.** Serves this teacher's own Canvas data from Canvas Expert's
  local copy on their computer. It never holds the Canvas token. Canvas writes use
  bounded preview/apply or operation-ledger paths, except a teacher-requested
  `push_content_live` and the Scoring Session apply. A request to prepare one
  assignment authorizes valid results only for that exact course/assignment; the
  server privately selects the Canvas transport. The stage/apply pair keeps the SAFE
  packet binding, per-student review, idempotency, transport, and receipt safeguards.
  Everything else writes only to local CanvasExpert state.
- **Agent-agnostic, not assistant-specific.** The tools, results, and focused repo guides
  are the operational contract. Optional teacher workspace notes can add local context,
  but a fresh client is not required to read a repository or arbitrary workspace file.
- **Pseudonymized, not anonymous.** Every student-data tool routes its result through the identity vault
  (`api/feedback_vault.py`) before returning it. Students are identified only by a stable
  one-word pseudonym (e.g. "Pikachu") — never a real name, Canvas user ID, or SIS ID. See
  `docs/contracts/pseudonym-contract.md` for the full pseudonym shape contract. The teacher
  may keep private identity records in M365 OneDrive for device sync; the MCP boundary still
  returns only pseudonymized data and never returns the vault or private filesystem paths.
- **Fail-closed.** Every student-data result also passes the existing outbound safety scan
  (`api/feedback_safety.py::scan_payload`) as a final check. If it isn't green, the tool
  withholds the payload and returns only a sanitized violation description.
- **Session-local.** Nothing here logs tool arguments or results. The pseudonym is the
  only student handle that crosses the wire, so it is also the only one an assistant has
  to work with.
- **stdio is the agent transport.** A loopback-only Streamable HTTP endpoint is mounted
  inside the lock-owning local runtime so a second CE entry point can attach to the same
  process. It is not exposed beyond `127.0.0.1` and is not a client configuration surface.
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

Tool schema version 59 (52 tools). Version 59 adds `verify_live`, `resume_operation`, and
`abandon_operation`, and adds a `verify_hint` field to every successful apply
(`apply_content_push`, `push_content_live`, `apply_assignment_update`, `apply_sis_grade_bridge`)
naming the object(s) it created or changed for a follow-up `verify_live` call.

| Tool | Purpose | Student data? |
|---|---|---|
| `list_courses` | First call for every saved course (Current + Previous) and the `course_id` used by course-scoped tools | No |
| `list_sis_grade_bridges(course_id)` | Configured whole-course SIS bridges for a Current `course_id` returned by `list_courses` | No |
| `reconcile_sis_grade_bridges(course_id)` | Discovers differentiated families from the current local sync/mirror and returns a student-free bridge matrix | No |
| `preview_sis_grade_bridge(course_id, family_title)` | Persists a mirror-backed, digest-protected score projection review for one exact linked differentiated family | No |
| `preview_sis_grade_bridge_reconciliation(course_id, family_title, source_assignment_ids?, bridge_assignment_id?)` | Persists a reviewed bridge-only repair; optionally proposes an exact grouping when title discovery did not find the family | No |
| `apply_sis_grade_bridge(operation_id, batch_id, review_digest)` | After approval, pushes the unchanged reviewed scores to the exact linked bridge through the Operation Ledger | No |
| `reset_scoring_review(scoring_session_id)` | Reopens the current local scoring review without changing its packet or history | No |
| `list_sections(course_id)` | Saved section values from the local mirror | No |
| `get_course_assignments(course_id, full_descriptions=false)` | Disk-only catalog assignments; descriptions are previews unless `full_descriptions=true`; reports aged unconfirmed CE writes | No |
| `get_modules(course_id, include_items=false)` | Disk-only catalog modules; set `include_items=true` to include their items; reports aged unconfirmed CE writes | No |
| `refresh_course_structure(course_id)` | Coordinator-backed refresh of all four student-free catalog sections; reports each section's state and the oldest successful read, with no Canvas rows | No |
| `get_course_pages(course_id, full_text=false, include_unpublished=true)` | Normalized pages from the Current course's local v3 catalog, including unpublished pages by default; set `full_text=true` for complete bodies or `include_unpublished=false` to filter; reports aged unconfirmed CE writes | No |
| `list_learning_objectives(course_id)` | Current reviewed learning objectives as a compact table; Current-course and local-document gated | No |
| `preview_learning_objective(course_id, objective, effective_start, effective_end, source_refs, replaces?)` | Exact reviewed create or replacement preview grounded in current local module, assignment, or page records | No |
| `apply_learning_objective(course_id, preview, preview_digest, expected_revision)` | Applies only the exact reviewed create or replacement preview after catalog/source/revision checks; replacement identity comes from the digest-protected preview | No |
| `delete_learning_objective(course_id, entry_id, expected_revision)` | Directly deletes one selected reviewed objective with revision protection | No |
| `get_authoring_contract(kind)` | Canonical authoring contract for Forge (`quiz`, `assignment`, `page`) from `api/default_docs/AI Authoring/` | No |
| `get_product_guide(topic="")` | CanvasExpert product knowledge; omit `topic` for the overview, use the annotated topic map to choose detail, or select `tools` for the complete generated inventory | No |
| `stage_content(kind, label, content)` | Writes one authored draft and its `.done` marker into the per-kind review Inbox; refuses an existing label rather than overwriting | No |
| `list_staged_content(kind="")` | Drafts in the local review Inbox; pass `kind` to filter or omit it for all drafts | No |
| `preview_content_push(course_id, kind, label, published=None, module_name="", module_id="", create_module=false, assignment_group_name="", due_at="", unlock_at="", lock_at="", post_to_sis=None)` | Persists a local frozen review of one staged draft for one Current course; differentiated AssignmentForge creates unrestricted tier sources and the shared bridge; existing module IDs are exact, while module creation is explicit | No |
| `list_groups(course_id)` | Current-course group-set and group names from the fresh local mirror, including the group set selected in Roster; no memberships or Canvas IDs | No |
| `preview_differentiated_quiz_push(course_id, variants, published=false, module_name="", module_id="", create_module=false, assignment_group_name="", due_at="", unlock_at="", lock_at="", post_to_sis=false)` | Persists a frozen review for one staged QuizForge family; variants are tier files, legacy pod/group fields are ignored, dates are optional, and sources attach unrestricted to the selected module while the gradebook-only bridge does not | No |
| `apply_content_push(operation_id, batch_id, review_digest)` | Creates the exact frozen draft in Canvas through the Operation Ledger; same claims, drift check, and receipt as the push tab | No |
| `push_content_live(course_id, kind, label, content, published=None, module_name="", module_id="", create_module=false, assignment_group_name="", post_to_sis=None)` | The route for a teacher who asked for content in Canvas; stages the draft, freezes and drift-checks it internally, then creates it. Tiered delivery uses the reviewed family path; carries no dates: use the preview pair for those | No |
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
| `list_feedback_contracts()` | List teacher-authored judgment and feedback-shape contracts available in the private workspace; returns ids, summaries, and projected sizes only | No |
| `discover_scoring_work()` | Read every Current course locally and return student-free assignment, freshness, and attention tables; no refresh, preparation, or Canvas write | No |
| `preview_workspace_reset()` | Dry-runs the explicitly authorized local cleanup and reports classified paths, counts, and refusals | No |
| `apply_workspace_reset(preview_digest)` | Applies only an unchanged, non-refused workspace cleanup preview and returns a local receipt | No |
| `verify_live(course_id, kind, id="", title="")` | The one Live Canvas read an agent makes after a push: confirms one `assignment`/`page`/`quiz` by exact id or exact title. One Canvas call, or two only when `module_ids` isn't already on the object; writes nothing | No |
| `resume_operation(operation_id)` | Continues one existing, teacher-approved operation from its last recorded step through the same executor retry path; refuses an operation that already applied, was abandoned, or is held by another attempt | No |
| `abandon_operation(operation_id)` | Marks one existing, teacher-approved operation abandoned with no Canvas call; blocks later `resume_operation`/apply and returns a `repair_plan` of what was already created from recorded steps | No |
| `prepare_scoring_session(course_id, assignment_id, scoring_guidance="", use_existing_mirror=false, scoring_guidance_provenance="", feedback_contract_id="")` | Prepare one exact assignment from current local mirror projections; snapshots beyond the local-time threshold require explicit acknowledgement; missing norms return bounded teacher input; a selected contract travels in page zero | No |
| `list_scoring_sessions()` | Identity-free assignment-scoped summaries for current courses | No |
| `list_work_items()` | Shared work-item holders, sync progress, and orphan counts without private session contents | No |
| `get_work_item(work_id)` | One shared work item's holder and sync status | No |
| `handoff_work_item(work_id)` | Release this device's lease so another device can resume after sync | No |
| `take_over_work_item(work_id, confirm_stale=false)` | Acquire a released item after sync, or explicitly confirm takeover after a stale lease | No |
| `get_scoring_packet(scoring_session_id, offset=0, limit=10, include_context=true)` | SAFE scoring packet with an authoritative contract and untrusted response text; `next` explains row/person counts and paging | Yes, pseudonymized |
| `stage_scoring_results(scoring_session_id, results, expected_packet_digest, review_digest="", answers=None)` | Validate and freeze valid SAFE-packet results locally; returns pseudonym-only questions when teacher input is needed and never calls Canvas | Yes, pseudonymized |
| `apply_staged_scoring_results(scoring_session_id, expected_stage_digest, idempotency_key="")` | Post only the unchanged private stage after a direct teacher instruction; preserves narrow transport and idempotency safeguards | Yes, pseudonymized |

`get_course_assignments` and `get_modules` only read the local course catalog written by
the CanvasExpert runtime/control console — neither ever falls back to a live Canvas call.
If the catalog hasn't been refreshed yet, call `refresh_course_structure` first, then retry. Unlike the mirror
tools below, `get_modules` returns whatever module records
the catalog holds, labeled with `source`, `synced_at`, and `state`; stale scope is not write-authoritative.

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
tiers to the configured public suffixes. Apply creates the exact configured-tag-suffixed sources and one
unsuffixed no-submission bridge, attaches only the sources to the selected module, and
links the verified family. The result directs the teacher to Canvas Live for review;
the teacher owns Canvas Grade Sync.

The reviewed-preview machinery runs on every route. Whole-class drafts may remain
unpublished. Differentiated sources use the reviewed family operation, including source-only
module placement, bridge verification, and family-link save. QuizForge retains group
restriction; AssignmentForge sources are unrestricted and tier placement is teacher-owned.

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
refused rather than dropped. Whole-class drafts stay unpublished unless `published=true`;
differentiated family delivery uses its explicit reviewed publication and verification path.

`preview_assignment_update`/`apply_assignment_update` is a separate, narrower write pair
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
`operation_id`, `batch_id`, and `review_digest`. A stale or missing mirror refuses with no
live fallback. Preview does not inspect due dates, student coverage, overrides, or module
placement. After teacher approval, apply pushes only the unchanged reviewed scores to the
exact linked bridge, performs live postconditions for those writes, and requests a targeted
mirror refresh. Bridge operations do not repair or rearrange modules.

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
Canvas drift: `reconcile_sis_grade_bridges`, `preview_sis_grade_bridge_reconciliation`, and
apply (when the catalog goes stale between preview and apply) all return
`{"code": "catalog_not_current", "blocking": true, "sections": {<scope>: <state>}, "error":
"The local course catalog is not current.", "next": "Ask the teacher whether to refresh this
course's structure (refresh_course_structure). Do not refresh automatically."}`. This shape is
plain text any MCP host can act on directly; do not refresh automatically on its own
authority. A link-only repair (no live Canvas write) also never marks any local catalog scope
stale.

See the [SIS Grade Bridges guide](guides/sis-grade-bridges.md) for the complete three-tool
workflow, automatic family creation, recurring updates, privacy boundaries, and exact-ID
Attention recovery. The linked contract,
not the guide, remains the normative behavior authority.

`get_authoring_contract(kind)` takes no `course_id` and carries no student data, so it
needs no course gate, no identity vault, and no safety scan. Forge kinds (`quiz`, `assignment`, `page`) read the same
`api/default_docs/AI Authoring/` file the control console's `/api/download-contract` route serves,
then receive the Forge-only staging appendix.

`get_product_guide(topic="")` closes the gap between what the tool list implies and what
the app actually does. Every successful response returns an ordered object that annotates
all ten topics with one-line summaries. `overview` serves Appendix B; the other named
CanvasAgent sections serve their exact Appendix A-F slices; `full` serves the entire file;
and the two writing topics serve their own canonical files. `tools` is generated from the
frozen schema-v53 contract and groups all 42 tools exactly once by teacher-facing job.
Topic matching trims surrounding whitespace and ignores case. The download route's
CanvasAgent bytes equal `topic="full"`; section topics are extracted from those same bytes.
Results are text-only MCP content: the server returns one minified JSON text block and
advertises no structured output schema or structured result. Same gate posture as
`get_authoring_contract`: no `course_id`, no vault, no safety scan. The always-on server
instructions point here rather than restating any of it.

`list_staged_content(kind="")` also takes no `course_id` and carries no student data, so
it likewise needs no course gate, no identity vault, and no safety scan. It reuses
`webui.deps.list_inbox_files` (the same marker-gated To Review listing the push tabs use) and
returns only each draft's label, never its absolute path. Pass `kind` to narrow to one of
`quiz`, `assignment`, or `page`; omit it to see everything staged across all three.

**Scoring Session workflow.** For a broad request such as “what needs grading,” the
assistant calls `discover_scoring_work()` with no arguments. Canvas Expert reads every
Current course from its local mirror and returns complete assignment, freshness, and
attention tables. No discovery call enqueues, waits for, polls, or retries a refresh.
An unavailable, corrupt, or non-current projection is reported as
`mirror_projection_unavailable`; a valid old snapshot remains visible as usable.
The assistant reports all rows and waits for teacher direction, then calls
`prepare_scoring_session(course_id, assignment_id, scoring_guidance="")` only for
selected exact assignments. Preparation reads only current local projections and
saves one assignment-scoped session on success. If the oldest required snapshot is
older than the applicable local-time threshold (60 minutes during Monday-Friday
07:00-16:30 America/Chicago, 600 minutes otherwise), it returns
`mirror_freshness_confirmation_required`; the agent asks whether relevant Canvas
work changed and either waits for an explicit refresh request or retries with
`use_existing_mirror=true`. At exactly the threshold it does not prompt. Once a usable session id exists,
continue locally from its immutable packet and do not prepare or refresh that
assignment again. A repeated call returns `scoring_session_already_open`.

AssignmentForge auto-scoring gates: An assignment qualifies for AI auto-scoring in a
Scoring Session only when all four conditions are met: (1) the assignment text explicitly
tells students HOW to submit (paper, text box, file, etc.); (2) the assignment text
explicitly states the point value of each work piece; (3) the total is 0-100 points
unless the teacher explicitly approved a different scale; (4) writing pieces (SCR/ECR)
are weighted higher than shorter pieces. If any condition is unmet, the assignment
is eligible for teacher review only - do not auto-score it.

Non-empty assignment content is the scoring basis. A Canvas rubric is used only when
assignment content is empty; teacher-authored directives layer on top. If neither
exists, preparation returns `needs_teacher_input`
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
accepts only pseudonym/item results bound to that packet and never calls Canvas. If
judgment is needed, it returns `needs_teacher_input`, pseudonym-only questions, allowed
answers, and a review digest; the assistant asks the teacher, then retries unchanged.
On success it returns an opaque stage digest and aggregate counts. After a direct
teacher request, `apply_staged_scoring_results()` accepts only that unchanged digest,
performs the narrow write once, and records the transport receipt. After a terminal apply,
that assignment-scoped session is complete; continue through
any remaining rows in the teacher-selected set without a new blanket confirmation per
assignment. A newly discovered assignment requires new teacher direction. Existing New Quizzes with writing stop before a
packet with `new_quiz_writing_requires_assignment`; the teacher grades them in Canvas and
uses separate 100-point assignments for future writing portions. No transport type,
operation token, or private local id crosses the MCP boundary. A stale packet,
changed review plan, invalid answer, or ambiguous write fails closed. Review and editing
happen in Canvas Live; the teacher request authorizes only the exact selected assignment
set, not later discovered work.

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
comments through the frozen, verified assignment write lane. Canvas Expert
does not write New Quiz item scores, per-item feedback, assignment totals, or fallback
comments. Results return only aggregate counts and pseudonym-keyed outcomes. A teacher who asked to prepare this
assignment has authorized the exact named stage to post; the assistant directs review or
edits to Canvas Live. Authorization never carries to later assignments,
another session,
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
  refuses rather than truncating when the compact result exceeds 20,000 serialized
  characters. It reports the method, cutoffs, group-set label, No Data group, coverage,
  group counts/membership, and proposal digest. Use the Students UI for review and apply.
- The outbound safety scan always runs on the full row payload **before** tabulation and
  truncation happens **before** the scan — the gate inspects exactly the bytes that leave
  the machine.

## Running it

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
