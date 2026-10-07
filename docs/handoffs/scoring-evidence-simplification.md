# Execution brief: readable scoring work and safe hold recovery

**Status:** YELLOW, awaiting senior acceptance, 2026-10-07. Implemented,
committed to `dev`, and field-verified; see section 10 for open items.
**Target:** `dev`. **Inspected baseline:** `a6be405`; field runtime confirmed
post-fix (lock owner restarted). Preserve unrelated worktree changes.
**Authority:** teacher asked to "simplify as much as possible" after a pilot
scoring session was fully blocked.
**Acceptance:** senior accepts the integrated implementation and field evidence.

Teacher's current request: propose slices/briefs for the monitored scoring
diagnostic. This revision incorporates safe recovery into the existing batch;
it does not create a competing hold-rule brief. See
`../reference/scoring-diagnostic-plan.md` for evidence limits and later slices.

`canvasmirror-performance-discovery.md` (YELLOW) named "using extracted
attachments in a scoring packet" as the next assessment; this is that batch. Both
briefs stay current (teacher, 2026-10-07: more than one brief is fine). This batch
does not wait on that brief's open desktop/S12 items.

## 1. Teacher-visible outcome

A prepared Scoring Session puts every student who submitted readable work in the
packet. A student is held only for a reason that belongs to **that student's own
submission**, stated in plain words the agent can repeat ("essay.docx is still
being read", "media recording", "no text submitted"). Mirror bookkeeping about
whether an assignment's attachment index is "complete" never holds anyone.

When results cannot be staged, the error names the real cause (held, not in
packet, out of range), never a misleading field such as `item_id`.

An explicitly refreshed open session can recover existing held work when its
same-attempt text/extraction becomes usable, even if no new student or attempt
arrived. Other students' drafts, review state, and posted history survive.

### Required reads and preflight

Read `AGENTS.md`, `docs/reference/project-state.md`, this brief, the canonical
`docs/guides/scoring-sessions.md`, and
`api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`.
Also read `docs/contracts/agent-runtime-product-contract.md` sections
"Primary interface", "Canonical cooperation loop", and "Runtime boundaries"
subsections "Agent runtime", "Read spine", and "Action spine";
`docs/contracts/feedback-scoring-contract.md` sections "Direction 1 - SAFE
bundle", "Direction 2 - Results", and "Session consumption and write safety";
and `docs/mcp-server.md` section "Token-lean results". Source routing is section 7.

Record branch, HEAD, status, and current schema version before editing. Inspect
the refresh/stage preservation tests before changing recovery. Do not import
`api.*` through ad hoc Python commands. Check that same-attempt identity and
frozen hold metadata can be established from the existing private session;
missing identity is a reported blocker, never a guessed match. Before any
field run, identify the loaded runtime build separately from checkout HEAD.

## 2. Incident that motivates this (2026-10-07)

CS 8, five text-entry SCR assignments, 48 ungraded submissions. Every packet came
back with `students_total=0` and every student in `held_pseudonyms`; every
`stage_scoring_results` call failed with `invalid_results`, `fields: ["item_id"]`.
The agent misdiagnosed it as failed text extraction and proposed bypasses.

Cause: `api/mirror/evidence_acquisition.py` publishes an `assignment.attachments`
scope only for assignments that observed attachments. `api/mirror/evidence_scoring.py`
read the missing scope as `attachment_scope_incomplete` and set `_held` on every
response of a text-only assignment. `feedback_results.validate_results` then
rejected each result because its key was held, reporting the field as `item_id`.
The unit test passed because its fake index assumed an empty "complete" scope the
real mirror never writes.

Stop-gap already on `dev`: `_no_attachments_observed` treats "submission scope
complete + no attachment scope" as no files, with regression test
`test_text_only_assignment_without_attachment_scope_is_scorable`. This batch
replaces the stop-gap with the simple rule below; do not build on it.

The later monitored diagnostic returned five scorable responses and 54 holds
across five existing packets. All packets had structural health `ok=true`.
Four held pseudonyms matched readable current mirror text, but frozen attempt
metadata was absent and the mirror changed during observation. That comparison
does not establish a same-attempt hydration defect or the cause of every hold.

Confirmed recovery gap in this checkout: `scoring_preparation.refresh_scoring_session`
returns the old bundle when there are no added/replaced rows. Repeating that
call cannot generally repair existing holds. The former instruction to
"re-prepare (`refresh_scoring_session`)" was therefore insufficient.

## 3. Current hold mechanisms (the complexity to remove)

A response can currently be held by four separate layers, each with its own
vocabulary:

1. `api/powergrader/session_builder.py`: `attachment_eligibility`
   (`student_attachments.eligibility_decision`), `has_media_recording`,
   `speedgrader_required`. Read by `tools._held_reason`.
2. `api/powergrader/scoring_artifacts.py::_prepare_attachment_safe_bundle`:
   `media_holds`, attachment exclusions.
3. `api/mirror/evidence_scoring.py`: scope coverage gaps, attempt mismatch,
   `original_pending`, `extraction_missing`, partial extraction. Its caller in
   `scoring_artifacts.py` wraps it in `except Exception: pass`, so an unreadable
   evidence store silently drops all attachment text.
4. `api/powergrader/scoring_packet.py`: `_held` marker or empty text.

The packet reports only a count and pseudonyms (`held`, `held_pseudonyms`),
not reasons; `_scoring_preview_model` reports a reason from layer 1 only.

## 4. Target rule (teacher decision: simplify)

Decide per student, from the student's own current-attempt submission as read at
prepare time (Canvas submission row, already in the private session):

- **Scorable:** the student has any text: body text, plus extracted text for
  each attached file that has a complete extraction.
- **Held, with one reason per student:**
  - `file_not_read`: a file on the current attempt has no complete extraction
    yet (pending, partial, too large, unreadable, store unavailable). Reason
    names the file's extension only, never its name if that could carry identity.
  - `media_recording`: audio/video submission.
  - `needs_speedgrader`: New Quiz upload or ungraded non-essay item.
  - `no_text`: nothing readable at all.
- Mirror scope/coverage state (`membership`, `synchronization`, pending commits)
  is **not** an input. If the evidence store cannot be read, only students who
  have files are held (`file_not_read`); text-only students are unaffected.
- Attempt matching stays: extracted text is used only when its attempt equals the
  session's current attempt for that student; otherwise that file is `file_not_read`.

One function owns this decision and returns `{scorable: bool, reason: str|None,
text: str}` per student; layers 1-4 call it or are deleted. Prefer deleting.

### Existing held-row recovery

Use the existing `refresh_scoring_session` owner and its scope/session locks;
no extra recovery tool. Re-evaluate existing unposted held rows through the
same decision function, even when no submission was added or replaced.

- Match the frozen attempt and submission timestamp before using current
  evidence. A changed attempt remains in `resubmitted_not_replaced` unless the
  teacher requests the existing `replace_resubmitted=true` behavior. Missing
  identity is reported as unverified; do not silently promote newer work.
- Rebuild only eligible held rows into a new SAFE artifact through the existing
  privacy path. Keep earlier artifacts, session identity, teacher guidance,
  unrelated frozen response text, and frozen grade provenance intact.
- Preserve authored scores, feedback, commentary, stored review choices, push
  logs, idempotency records, and posted rows. Never reset the session to recover
  a hold. Retained choices must still pass the current review validation.
- If packet input changes, invalidate the old applicable stage/review digest
  and approval, without deleting authored drafts. Return the new packet digest
  and a bounded list of recovered response keys; require fresh staging, preview,
  and teacher approval before apply. Recovered rows may occur before
  `first_new_offset`; that offset alone is not a recovery cursor.
- If no hold/evidence changed, keep packet/stage identity stable and report no
  recovery. Failed build, privacy check, validation, or save leaves the previous
  actionable state intact. Preserve current finished-session and ambiguous-write
  refusals; background mirror activity never repairs a frozen session implicitly.

## 5. Acceptance criteria

1. A text-only assignment with no attachment scope, an empty complete scope, or
   an unreadable evidence store produces a packet with every text submitter
   scorable. (Law test, at the decision function.)
2. A student with body text plus an unread file is held as `file_not_read`
   (default; see question 1 in section 9); a classmate with only text is scorable
   in the same packet.
3. `get_scoring_packet` returns `held: [{pseudonym, item_id, reason, attempt,
   submitted_at}]` (reasons from the fixed vocabulary above) instead of a bare
   count/pseudonym list; the preview's held rows use the same reasons. Attempt
   and timestamp describe frozen work, are null when unknown, and never contain
   Canvas student IDs or filenames. Aggregate consumers use `held_count`;
   update preparation, discovery/session summaries where affected, and token
   sizing explicitly rather than retaining conflicting meanings for `held`.
4. `stage_scoring_results` for a held or absent row returns a validation entry
   naming the pseudonym and the reason (`held: file_not_read`, `not_in_packet`),
   not `fields: ["item_id"]`.
5. No `except Exception: pass` around evidence merging; a failure becomes
   `file_not_read` holds plus one operational log line without student data.
6. `_no_attachments_observed`, `_scope_complete`, scope-gap code, and the now
   unused coverage plumbing in `evidence_scoring.py` are deleted. Net line count
   across the touched modules goes down.
7. Docs: `docs/guides/scoring-sessions.md` "Failure modes" and
   `docs/contracts/feedback-scoring-contract.md` "Direction 1 - SAFE bundle"
   describe the held reasons; the MCP server instructions do not grow.
8. Same-attempt held work becomes readable after explicit session refresh with
   zero added/replaced submissions. The response names recovered keys and
   remaining holds; an unchanged repeat is idempotent. Different/unknown attempts
   cannot be silently recovered. Test frozen body text and newly completed file
   extraction separately, using real evidence-query result shapes.
9. Mixed held/drafted/reviewed/posted sessions retain unrelated work and private
   history. Old digests cannot stage/apply changed packet input. A failed recovery
   commits no partial state; retry succeeds without duplication. Synthetic apply
   checks prove only freshly reviewed unposted results can reach the write lane.
10. Packet success reports scoring readiness separately from structural
    `packet_health`. Define readiness from whole-packet scorable response counts:
    `ready`, `partially_held`, `held_only`, or `empty`. Next guidance follows that
    state and pagination: continue pages, stage scorable work, report/recover
    holds, or report no work. Held-only/empty packets never instruct staging.
    A no-change refresh explains remaining holds instead of promising a repair.

## 6. Non-goals

- No change to mirror acquisition, extraction workers, OCR, or index
  publication. Discovery is untouched.
- No new MCP tool, no Web UI surface, no bypass path for staging.
- No change to pedagogical choices, grade math, posting policy, or the existing
  apply safeguards. Digest invalidation remains mandatory when packet input changes.
- No general legacy compatibility framework. Existing pilot sessions are real
  state: use their frozen private metadata for bounded recovery; report any
  missing information rather than discarding or guessing it.

## 7. Workstreams and file ownership

- **W1 decision function** (owns `api/mirror/evidence_scoring.py`, its tests in
  `api/tests/mirror/test_evidence_scoring.py`): implement section 4; delete the
  scope-coverage logic. Fake index rows in tests must use the shape the real
  `EvidenceQueryService.read` returns, including `membership.state == "unknown"`
  for an absent scope.
- **W2 bundle assembly** (owns `api/powergrader/scoring_artifacts.py`,
  `api/powergrader/session_builder.py`, `api/powergrader/scoring_packet.py`,
  `api/powergrader/student_attachments.py`, `api/powergrader/scoring_preparation.py`,
  `api/feedback_artifacts.py` (the submitted-empty-row inclusion seam only),
  and their tests): route all holds through W1; remove duplicate hold layers;
  packet emits reasons and frozen metadata; implement safe existing-hold refresh.
- **W3 agent surface** (owns `api/mcp_server/tools.py`, `api/feedback_results.py`,
  `api/mcp_server/server.py`, `api/tests/mcp_server/test_refresh_scoring_session.py`,
  other affected MCP tests, `api/tests/test_feedback_results.py`, and the two
  docs in criterion 7): held reasons in packet and preview; stage validation
  messages; state-dependent next guidance. Update the guide's "Late arrivals
  and resubmissions" and contract's "Session consumption and write safety" too.
- **Lead integration** owns this brief, `api/mcp_server/contract.py`, schema
  snapshot, generated tool inventory and listing-budget changes if required.
  Output shape changes, so take the next `TOOL_SCHEMA_VERSION` and regenerate
  the snapshot under pytest per `AGENTS.md`; do not hard-code version 84.

W2 depends on W1's function signature; agree it first, then workstreams may run
in parallel with the ownership above. W3 consumes W2's agreed hold/readiness and
recovery shape. Keep recovery in the preparation owner, not the MCP wrapper.

## 8. Gate

Focused:

```
py -m pytest api/tests/mirror/test_evidence_scoring.py api/tests/powergrader api/tests/mcp_server api/tests/test_feedback_results.py -p no:randomly -q
```

Full suite before GREEN (`py -m pytest api/tests engine/tests -p no:randomly -q`).
Baseline at the stop-gap commit: 2,796 passed, 1 skipped, 9 failed, with
`api/tests/mirror/test_evidence_extraction.py` ignored because `python-pptx` is
not installed on the laptop. The 9 failures are identical without the stop-gap:
7 in `api/tests/mirror/extraction/` (missing `pptx`/OCR assets locally),
`test_qf_pusher.py::test_new_engine_plan_is_unchanged_for_existing_files`,
`test_readiness_routes.py::test_probe_runs_once_until_forced`. Cite, don't re-explain.

Field after implementation: say what real private state will be touched, confirm
the runtime has loaded the tested build, and inspect existing drafts/reviews
before explicit refresh of the teacher-selected incident sessions. Recover only
verified same-attempt work; report remaining holds. Confirm readable text and
complete `.docx` extraction are scorable and unread files have the right reason.
Keep field evidence private. Use synthetic tests for deliberately invalid stages;
do not inject invented scores into live teacher sessions. Stage actual work only
when directed, preview it, and require a contemporaneous teacher go for apply.
Record what was demonstrated; no claim of live end-to-end success without it.

## 9. Ask the teacher

1. Text plus an unread file: hold the student (default, current behavior) or
   score the text with a warning that a file was not read?

Retain the existing hold default unless the teacher changes it; this question
does not block text-only fixes or same-attempt recovery. Ask before expanding
recovery to unknown-attempt work or replacing/discarding any live draft. If
current code/metadata cannot meet the preservation requirements, report YELLOW
with the concrete conflict before adopting a weaker recovery policy.

Field evidence is laptop-only; the desktop is not available.

## 10. Execution result

**YELLOW** (2026-10-07). Criteria 1-5, 7, 8 and 10 met; 9 mostly met; 6 needs
a waiver. Field run succeeded end to end. Commit: see `git log` for
"fix(scoring): one per-student hold rule and safe held-row recovery" on `dev`.

- **Changed:** `api/mirror/evidence_scoring.py`, `api/powergrader/{scoring_preparation,
  scoring_artifacts,scoring_packet,session_builder}.py`, `api/feedback_{artifacts,results}.py`,
  `api/mcp_server/{tools,server,contract}.py`, schema snapshot v83 -> v84, their
  tests, new `api/tests/powergrader/test_scoring_preparation_recovery.py`, and
  the docs named in criterion 7.
- **Gate:** focused gate 746 passed. Full suite (same command and ignore as the
  baseline): 2,816 passed, 1 skipped, 9 failed; the 9 are exactly the cited
  baseline failures.
- **Field defect found and fixed:** recovery called `attempt_text_digest`
  without importing it. Every real session (whose baseline carries
  `submission_digest`) raised `NameError`, surfaced only as `safe_refresh_failed`.
  Synthetic tests never set that field. Regression test:
  `test_recovery_frozen_no_text_with_baseline_digest`.
- **Field evidence (private details stay local):** five CS 8 text-entry SCR
  sessions frozen this morning with false `no_text` holds. Explicit refresh
  recovered 4, 7, 20, 19 and 4 held rows with no blockers, `held_count` 0,
  `readiness: ready`. On the teacher's direction all 59 rows were staged,
  previewed and applied; every row `finalized` and `verified`. A timed-out apply
  retried once with no duplicate write.
- **Deviation, runtime build:** restarting one host did not load new code. The
  Canvas Expert machine lock was owned by a runtime another host (Codex) had
  started before the edits; later processes only proxied to it. The fix reached
  the field only after that host closed. Preflight "identify the loaded runtime
  build" has no tool to answer it.
- **Open items:**
  1. Criterion 6: net line count rose (~+195 in `scoring_preparation.py` for
     recovery). Senior waiver or a follow-up reduction.
  2. Criterion 9: the posted row in synthetic test 5 is set by editing the
     session dict, not a real apply; the "only freshly reviewed unposted rows
     reach the write lane" synthetic check is not written. The field apply
     exercised it for real.
  3. Follow-up (not this brief): `safe_refresh_failed` swallows the exception
     with no operational log line; `nothing_staged` after an apply timeout cannot
     distinguish "already applied" from "stage lost"; completed sessions are
     hidden from `list_scoring_sessions`; no way to see which runtime build/owner
     is answering.
  4. Teacher question in section 9 (text plus unread file) remains at the default.
