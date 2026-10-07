# Execution brief: one simple hold rule for scoring packets

**Status:** READY FOR EXECUTION; senior planning only, 2026-10-07.
**Target:** `dev`. **Inspected baseline:** the commit that adds this brief (it
also carries the stop-gap fix described in section 2).
**Authority:** teacher asked to "simplify as much as possible" after a pilot
scoring session was fully blocked.
**Acceptance:** senior accepts the integrated implementation and field evidence.

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

## 5. Acceptance criteria

1. A text-only assignment with no attachment scope, an empty complete scope, or
   an unreadable evidence store produces a packet with every text submitter
   scorable. (Law test, at the decision function.)
2. A student with body text plus an unread file is held as `file_not_read`
   (default; see question 1 in section 9); a classmate with only text is scorable
   in the same packet.
3. `get_scoring_packet` returns `held: [{pseudonym, reason}]` (reasons from the
   fixed vocabulary above) instead of a bare count/pseudonym list; the preview's
   held rows use the same reasons.
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

## 6. Non-goals

- No change to mirror acquisition, extraction workers, OCR, or index
  publication. Discovery is untouched.
- No new MCP tool, no Web UI surface, no bypass path for staging.
- No change to scoring, feedback contracts, posting, or apply behavior.
- No compatibility shim for sessions prepared before this batch: pre-launch,
  the teacher re-prepares (`refresh_scoring_session`).

## 7. Workstreams and file ownership

- **W1 decision function** (owns `api/mirror/evidence_scoring.py`, its tests in
  `api/tests/mirror/test_evidence_scoring.py`): implement section 4; delete the
  scope-coverage logic. Fake index rows in tests must use the shape the real
  `EvidenceQueryService.read` returns, including `membership.state == "unknown"`
  for an absent scope.
- **W2 bundle assembly** (owns `api/powergrader/scoring_artifacts.py`,
  `api/powergrader/session_builder.py`, `api/powergrader/scoring_packet.py`,
  `api/powergrader/student_attachments.py` and their tests): route all holds
  through W1; remove duplicate hold layers; packet emits reasons.
- **W3 agent surface** (owns `api/mcp_server/tools.py`, `api/feedback_results.py`,
  the two docs in criterion 7, and MCP snapshot/version files): held reasons in
  packet and preview; stage validation messages. Output shape changes, so take
  the next `TOOL_SCHEMA_VERSION` and regenerate the snapshot per `AGENTS.md`.

W2 depends on W1's function signature; agree it first, then run in parallel.

## 8. Gate

Focused:

```
py -m pytest api/tests/mirror/test_evidence_scoring.py api/tests/powergrader api/tests/mcp_server -p no:randomly -q
```

Full suite before GREEN (`py -m pytest api/tests engine/tests -p no:randomly -q`).
Baseline at the stop-gap commit: 2,796 passed, 1 skipped, 9 failed, with
`api/tests/mirror/test_evidence_extraction.py` ignored because `python-pptx` is
not installed on the laptop. The 9 failures are identical without the stop-gap:
7 in `api/tests/mirror/extraction/` (missing `pptx`/OCR assets locally),
`test_qf_pusher.py::test_new_engine_plan_is_unchanged_for_existing_files`,
`test_readiness_routes.py::test_probe_runs_once_until_forced`. Cite, don't re-explain.

Field (real Canvas, nothing posted without the teacher's go): re-prepare the
CS 8 SCR 2-6 sessions and confirm every text submitter is scorable; prepare one
assignment with `.docx` submissions and confirm read files are scorable and an
unread one is held as `file_not_read`. Stage one result to a held row and confirm
the error text.

## 9. Ask the teacher

1. Text plus an unread file: hold the student (default, current behavior) or
   score the text with a warning that a file was not read?

Field evidence is laptop-only; the desktop is not available.

## 10. Execution result

_Not started._
