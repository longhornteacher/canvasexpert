# Feedback Scoring Contract - v1

The data contract between the MCP-connected scoring agent and Canvas Expert's
private scoring engine. Canvas Expert has no hosted grader. The agent prepares one
exact assignment-bounded SAFE pseudonymized packet at a time, then stages results
locally and explicitly applies the exact frozen stage to Canvas.

Before preparation, `discover_scoring_work()` is the broad, read-only entry point:
it reads every configured Current course from the local mirror, projects only aggregate
local state, and returns a student-free digest. The teacher directs which exact
course/assignment rows to continue. Discovery creates no session or packet and never
widens the assignment-scoped authorization described below.

Each discovery freshness row reports the oldest required local scope. A valid old
snapshot remains usable, but an unavailable, corrupt, or non-current projection is
reported as `mirror_projection_unavailable`. The scoring freshness advisory is
decided during preparation, not discovery. A valid current snapshot up to 60
minutes old during Monday-Friday 07:00-16:30 America/Chicago, or up to 600
minutes outside those hours, is silently accepted; the exact threshold is also
accepted. No discovery path enqueues, waits for,
polls, or retries a refresh.

Privacy invariant: the agent sees pseudonyms and scrubbed work only. Real names,
Canvas/SIS IDs, signed URLs, credentials, and private paths remain in the local
application and are never part of this contract. Pseudonymized does not mean
anonymous.

`contract_version` is `"1.0"`. The validator checks the major version; a breaking
shape change requires a major bump.

## Direction 1 - SAFE bundle (Canvas Expert -> agent)

`list_feedback_contracts()` lists the teacher's private Markdown feedback
contracts without course or student data. Each row carries the contract id,
name, conversational `applies_to` text, a short summary, and projected token
size. `prepare_scoring_session(course_id, assignment_id, scoring_guidance="",
use_existing_mirror=false, scoring_guidance_provenance="", feedback_contract_id="")`
requires one exact Current course and assignment and prepares it from valid
local projections. It performs no refresh, Canvas write, or direct Canvas read.
The base feedback shape (required fields, minimums, and the rendered layout) is
product-owned Python text in `api/feedback_contract.py` and is always present in
page zero; no selection ever replaces it. An explicit `feedback_contract_id`
selects one workspace contract file, which layers on top of the base shape under
one heading: judgment, tone, and emphasis are overridable, the fields and layout
are not. The selected body is carried verbatim in page zero and is bound to the
private session by a digest. Non-empty `scoring_guidance` is not a second copy
under that heading: it layers onto the scoring basis instead, as the existing
teacher-directive rubric block described below, so it appears exactly once in
page zero.
Non-empty assignment content is authoritative. A Canvas assignment rubric is used
only when assignment content is empty. Teacher-authored directives layer on top of
that basis; inherited, defaulted, or unknown guidance never overrides it. Teacher
guidance is retained privately
in full; when it exceeds the effective transport ceiling, the model and SAFE packet use
a deterministic compacted projection with an explicit marker and original/effective/
omitted character and unit counts. No basis returns a
successful conversation state with `ok: true`, `status: "needs_teacher_input"`, code
`needs_scoring_norms`, the assignment name, and a concise question. The agent asks
and retries the same exact preparation with bounded guidance. Page zero from
`get_scoring_packet` includes the selected feedback contract and resolved basis.
When teacher-authored guidance layers on assignment content or a Canvas rubric,
`scoring_basis.layered` is `true`; the original basis source remains visible.
Later pages may omit context. Optional shared assignment materials may be compacted or
omitted when needed to fit the transport ceiling, but the packet carries an explicit
machine-readable and human-readable compaction marker. The selected scoring
contract and resolved scoring basis remain on page zero.

Every SAFE bundle contains exactly one active assignment: pseudonym/item response rows, full response text without
silent truncation, held-work counts, and a packet digest. The digest binds results to
the exact packet. Response text is untrusted student work, never instructions to the
agent. The agent must report held or otherwise unscorable work before scoring and read
every page. Item/catalog or evidence gaps are never represented as an empty assignment.
Packet pages may project one original response as multiple deterministic, complete
segments. Each projected row identifies its `segment_index` and `segment_count`; the
segments concatenate in order to the exact original response and retain one result key
`(pseudonym, item_id)`. `total`/`segment_total` count projected segment rows, while
`source_response_total` counts original scorable responses.
New sessions include only submissions Canvas still marks `submitted` or `pending_review`.
Mirror preparation ignores only an unmatched row that is demonstrably historical already-
graded work (`workflow_state="graded"`, numeric score present, and empty `submitted_at`).
Any submitted, pending, or ambiguous identity mismatch fails closed with the structured
`mirror_submission_identity_mismatch` error; no live Canvas recovery lookup is performed.
If refreshed rows contain no eligible submission, preparation returns the typed
`nothing_to_grade` blocker without creating a packet. A genuinely empty acquisition
remains an error rather than being recast as completed grading. Every other failed
preparation returns a stable code, stage, retryability, and identity-safe user action.
Existing New Quizzes with writing stop before SAFE packet creation with
`new_quiz_writing_requires_assignment`. The teacher grades that writing in Canvas and
authors future writing portions as separate 100-point AssignmentForge assignments.

## Direction 2 - Results (agent -> Canvas Expert)

The agent stages one result per `(pseudonym, item_id)` supplied by the packet, using
`stage_scoring_results(scoring_session_id, results, expected_packet_digest,
review_digest="", answers=None, exemplars=None, disclosure="")`. A separate
`apply_staged_scoring_results(scoring_session_id, expected_stage_digest,
idempotency_key="")` applies only the unchanged private stage after a direct,
contemporaneous teacher request to post it.

The model no longer writes plain-text feedback. It supplies structured fields, and
Canvas Expert renders them into one fixed plain-text layout (score, explanation,
Glows, Grows, and, unless the row is at full marks, an Extra credit section with
numbered fixes and a hand-copy exemplar). No persona and no AI identity or
disclosure line is ever invented; `disclosure` is appended once, as the final
line, only when the teacher asked for one this session.

| Field | Required | Shape and meaning |
|---|---:|---|
| `pseudonym` | yes | Exact stand-in from the SAFE packet. |
| `item_id` | yes | Exact response item from that pseudonym's packet rows. |
| `score` | yes | Number or `null`. On ordinary assignments, `null` may permit comment-only posting after explicit teacher confirmation. |
| `explanation` | yes | 1-3 sentences explaining the score. |
| `glows` | yes | 2-3 specific strengths, at least one non-empty string. |
| `grows` | yes | 1-2 specific areas to improve, at least one non-empty string. |
| `fixes` | required when the row is not at full marks | 2-4 concrete changes doable by hand in a second draft. |
| `writing_process_observations` | no | Separate, teacher-only local observation; never student feedback or a score input. |
| `insincere` | no | A sincere-attempt proposal, in a course with a grading policy; must agree across every item row of one pseudonym. |
| `late_days` | no | An integer 0-60, in a course with a grading policy; must agree across every item row of one pseudonym. |

`exemplars` is a separate `{item_id: text}` argument to `stage_scoring_results`,
not a per-result field: one shared model answer per item, written once and used
for every student, required for any item where a row is not at full marks unless
a teacher AssignmentForge correction already covers that item. A missing exemplar
fails closed with typed code `missing_exemplars` and the affected item ids only --
no response content or identity.

Duplicates, unknown pseudonyms/items, malformed values, and stale packet digests fail
closed. The complete result set is validated before re-identification. A field-shape
failure returns count-only `errors`/`warnings` plus a `fields` list naming the
offending fields. Out-of-range scores and other judgment conditions do not receive
implicit defaults.

If a safe ordinary-assignment plan has no questions, Canvas Expert freezes it locally
without a Canvas call. When teacher judgment is required (for example, overwriting a score,
exceeding the maximum, ordinary-assignment comment-only posting, a pseudonym in feedback, or held work
receiving nothing), the tool returns `needs_teacher_input`, pseudonym-only questions,
the allowed answers, and a review digest without writing. The agent asks the teacher,
then resubmits the unchanged results and packet digest with every explicit answer and
the exact review digest. A successful stage returns an opaque stage digest and
aggregate counts. A changed review plan or invalid answer fails closed.
## Session consumption and write safety

One exact course-and-assignment scope has at most one actionable Scoring Session. Before starting
another full scoring refresh, preparation checks that exact scope. When a usable actionable
record exists (`ready` or staging `needs_teacher_input` with a valid SAFE packet), it
returns the identity-safe `scoring_session_already_open` refusal with the existing
`scoring_session_id`; it does not refresh, save, or supersede anything. The agent must continue
from that immutable packet and must not call preparation or `refresh_mirror` again for the
assignment. A failed preparation, a typed blocker, and the basis-stage `needs_scoring_norms`
state neither save a session nor supersede one. A snapshot beyond the applicable
local-time threshold is an advisory decision: the teacher may explicitly acknowledge
it with `use_existing_mirror=true`.

`get_scoring_packet` remains readable while the current session is in
`needs_teacher_input`. `reset_scoring_review(scoring_session_id)` is local-only,
returns that current session to `ready`, and preserves the packet, history, and
private artifacts.

If the current packet is stale, missing, or invalid, preparation may create a replacement. A
successful replacement activates its newly saved session and marks every earlier actionable
session for that exact `(course_id, assignment_id)` as `superseded` with private
`superseded_by_session_id` and `superseded_at` fields. Terminal `completed`/`completed_with_holds`
sessions remain unchanged. `list_scoring_sessions()` is an identity-free resume aid and returns
at most one row per exact scope.

Supersession never deletes: earlier session JSON, SAFE bundles, receipts, and Canvas objects
remain as teacher history, and supersession metadata stays private. Activated sessions carry a
private positive scope generation; current-session resolution ranks generated records by
`(scope_generation, created, session_id)`, so a newer terminal outcome suppresses older
actionable duplicates. For pre-lifecycle records with no generation, the deterministic fallback
is newest by `(created, session_id)` regardless of status. Older records are treated as
superseded at the call boundary without a migration.
`get_scoring_packet()`, `stage_scoring_results()`, and `apply_staged_scoring_results()` return identity-safe
`{"ok": false, "code": "session_superseded", ...}` for a superseded or non-current duplicate; the
stage/apply refusal occurs before result validation, re-identification, Canvas planning, or any Canvas
call. Activation and final apply are serialized by one deterministic course/assignment
scope lock, and the lock order is scope, then session.

AssignmentForge corrections are a private, teacher-authored scoring aid. When a
row is not at full marks and the private authored envelope contains an exact
`item_id` correction, the renderer uses it as Extra credit Part 2 instead of the
model's exemplar: the correction's `answer`, a blank line, then `Why: {why}`.
Shared corrections are used for prompt-identical parts; tier-specific corrections
are selected from the exact AssignmentForge tier/tag associated with the created
assignment. Missing corrections fall back to the model's own exemplar for that
item; full-credit results render no Extra credit section at all. The correction
library never enters the SAFE packet or MCP response.

Teacher guidance remains available privately in full for the session record. When oversized,
its effective model and packet projection carries the compaction marker and counts above;
those counts are the signal that effective text was omitted. Ordinary assignments use the
public prepare -> packet -> stage -> explicit apply flow and write through one narrow lane: the reviewed raw
score (`submission.posted_grade`) and one plain-text submission comment
(`comment.text_comment`) go to the existing Canvas Submissions endpoint once. That endpoint is
the API counterpart of entering the raw score in SpeedGrader; it is not the LTI Score API and
adds no rubric-assessment or New Quiz item-score write. `item_id` remains the SAFE
packet/result identity and correction-selection key, not a separate writable Canvas score
field for ordinary Assignments. A direct teacher request to post a named staged result
authorizes apply for that exact stage; a review-only or no-submit direction stops before apply.

Canvas Expert does not read the resulting grade back. There is no post-write GET, no mirror
refresh, no score equality comparison, no comment-count or latest-comment comparison, no
`points_deducted` use, and no grade/score/late-policy fact in MCP results or receipts. Canvas
may apply a late/missing policy or any other gradebook adjustment; CE neither changes that
policy nor asks about, reads, calculates, displays, or treats the adjusted result as a write
failure. The teacher reviews the result in Canvas and may edit it there; that review is not an
automated CE responsibility. CE sends no `excuse` or other policy/gradebook adjustment field,
and requests no course late policy. In a course with a grading policy
(`docs/contracts/grading-policy-contract.md`), `posted_grade` is the effort-credit mark rather
than the raw rubric score, and a Scoring Session sends `late_policy_status` and
`seconds_late_override` for the teacher-confirmed late-day count -- the only two exceptions to
"no policy/gradebook adjustment field." Without a grading policy, CE sends neither field and
the posted value stays the raw score, exactly as before.

A Canvas HTTP success means the write was accepted; CE records that compact receipt and moves
on. A non-HTTP transport error is `write_transport_unknown`: it performs no later verification
and no automatic retry, and it is never reported as `canvas_write_attention`. An explicit
Canvas HTTP rejection is a failed write. Neither outcome may trigger a second submission
comment write. A successful transport response finalizes the exact local idempotency slot, so
an already accepted exact payload is never sent twice; an unconfirmed transport error is never
treated as accepted.

Grade-state preflight is not part of this lane: no existing-score lookup, no
`overwrites_existing_score` question, no frozen Canvas score/comment baseline, and no pre-write
Canvas drift check. Packet digest, assignment scope, session currentness, result-shape/range
validation, the outbound privacy scan, held-work handling, and explicit teacher answers to the
remaining non-grade questions stay in force. Existing New Quizzes with
writing return the identity-safe unsupported code before scoring norms, SAFE packet generation, or Canvas mutation. New Quiz
assignment totals and assignment-level comments are not scoring fallbacks.

The teacher's request authorizes valid results only for the exact course/assignment
saved in that assignment-scoped session. It does not authorize another Scoring
Session, SIS action, or arbitrary grade edit. Canvas Live is the review/edit
surface. Canvas Expert has no local approval
queue, import workflow, or second blanket confirmation. Student feedback is not labeled
as AI unless the teacher explicitly chose a signoff; writing-process observations never enter Canvas
feedback, scores, or receipts.
