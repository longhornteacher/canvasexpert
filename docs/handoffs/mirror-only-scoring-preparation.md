# Direct brief: mirror-only Scoring Session preparation

Status: current

## Objective

Make agent-facing Scoring Session continuation prepare packets solely from fresh
CanvasMirror assignment and submission projections. The agent must never acquire
Canvas Live data while deciding whether an assignment can be scored or while
building its SAFE packet.

## Teacher-visible outcome

After `start_scoring_session` finds work, `continue_scoring_session` either
prepares the next assignment from the fresh local mirror or gives an honest,
identity-safe action: call `refresh_mirror(course_id)` and retry. Text responses
can proceed to the existing SAFE packet flow. Attachment-bearing or otherwise
unreadable work is held for teacher review. A true New Quiz is stopped with the
existing `new_quiz_writing_requires_assignment` guidance before norms or packet
creation.

## Locked decisions

1. Add only student-free New Quiz classification fields to the existing,
   versioned mirror assignment projection, populated by the existing shared
   assignment collection during normal full/delta refresh. Add no new Canvas
   fetch owner.
2. Scoring Session continuation and preparation read only fresh mirror
   assignment/submission projections. They make zero Canvas calls and download
   no attachments.
3. Submission text may enter the existing SAFE processing. Attachment-bearing,
   media-only, empty, or otherwise unreadable work is held and remains visible in
   the existing aggregate accounting.
4. Missing, stale, malformed, incomplete, or ambiguous mirror state returns an
   identity-safe result naming `refresh_mirror(course_id)`; it never falls through
   to Canvas Live.
5. A reliably classified New Quiz returns the existing
   `new_quiz_writing_requires_assignment` result before scoring norms or packet
   preparation.
6. Do not duplicate rubric data into the private mirror in this slice. Scoring
   may use an existing local Canvas Expert rubric or bounded teacher guidance.
   Agent-facing preparation must never fetch a Canvas rubric live.
7. Submit/write drift checks, execution, verification, and targeted private
   reconciliation remain allowed to use their existing live Canvas calls.
8. Do not change Canvas write behavior, New Quiz read acquisition, length limits,
   or the UI.

## Authorized scope

Change only the existing assignment projection’s student-free classification
allowlist and the PowerGrader/MCP preparation seam needed to enforce the locked
read boundary. Update the directly affected mirror/MCP documentation and focused
tests. Preserve unrelated worktree changes.

Expected implementation owners are:

- `api/mirror/store.py` and the existing assignment receipt path in
  `api/mirror/sync.py`;
- `api/powergrader/start_workflow.py`, `api/powergrader/assignment_refresh.py`,
  and/or a narrowly scoped mirror preparation helper;
- `api/mcp_server/tools.py` Scoring Session continuation;
- the existing mirror and Scoring Session test modules.

Do not broaden the assignment projection with rubric criteria, student fields,
binary evidence, signed URLs, or raw Canvas envelopes.

## Routed references

Read before implementation:

- `AGENTS.md` and `docs/reference/project-state.md`;
- `docs/mirror.md` current behavior, especially the assignment projection,
  mirror-first reads, MCP reads/refresh, and New Quiz capability sections;
- `docs/mcp-server.md`, Scoring Session workflow, packet, and write paragraphs;
- `docs/contracts/canvas-read-spine-contract.md`;
- `docs/reference/canvasmirror-1.0beta-information-spine.md`, §§5.5–5.7, 8, 9,
  11, 12, 14.1, and 21 item 7 only.

Inspect only the exact symbols named by the implementation owners and the tests
that exercise them. Do not preload the whole architecture or archived handoffs.

## Preflight

Before writing, confirm on `dev` that:

- the assignment collection remains the shared acquisition used by both Catalog
  and mirror;
- the assignment projection is still versioned and its normal full/delta writer
  is available;
- `continue_scoring_session` still reaches packet preparation through the
  PowerGrader start workflow;
- fresh mirror submissions contain response text and the fields needed for the
  existing held/unreadable accounting;
- write drift/verification remains in the submit path and is not being moved.

If any assumption is false, stop and return RED with the observed repository
truth. Do not invent a compatibility path or live fallback.

## Acceptance criteria

- Fresh assignment, roster, and submission mirror state prepares an ordinary text
  assignment with zero Canvas transport calls.
- The persisted assignment record exposes only the approved student-free New
  Quiz classification fields, and refresh writes them through the existing
  assignment acquisition.
- True New Quiz classification produces
  `new_quiz_writing_requires_assignment` before norms, SAFE packet creation, or
  attachment/evidence acquisition.
- Missing, stale, malformed, incomplete, or ambiguous mirror state produces an
  identity-safe refresh instruction naming `refresh_mirror(course_id)` and makes
  zero Canvas calls.
- Attachment-bearing, media-only, empty, and unreadable responses are held;
  no attachment is downloaded during agent-facing preparation.
- A missing Canvas rubric does not trigger a live rubric fetch; local rubric or
  bounded teacher guidance remains the only preparation basis.
- Existing submit/write drift, execution, verification, and reconciliation
  behavior remains unchanged and continues to use private live reads where
  required.
- Focused tests pass with all Canvas transport seams set to fail loudly, and
  `git diff --check` is clean.

## Explicit non-goals

- No rubric projection or duplicate rubric source.
- No change to New Quiz metadata/response acquisition or native evidence paths.
- No attachment, media, or binary download in MCP scoring preparation.
- No change to SAFE length limits, pseudonymization, safety scanning, packet
  shape, scoring result schema, or queue semantics.
- No change to Canvas write, drift, idempotency, verification, or reconciliation
  behavior.
- No UI, route, scheduler, migration, compatibility shim, or broad transport
  ownership rewrite.

## Named verification gate

Run the focused gate:

```powershell
py -m pytest -p no:randomly api/tests/mcp_server/test_start_scoring_session.py api/tests/powergrader/test_start_workflow.py
```

Add or update only the meaningful law/contract/example coverage needed for the
acceptance criteria. Also run `git diff --check`. Do not run the full suite
unless the focused gate reveals unexpected cross-cutting coupling.

## Stop conditions

Stop and return YELLOW or RED if classification cannot be obtained reliably from
the shared assignment receipt, if ordinary text cannot be read from the existing
mirror submission projection, if a rubric requires a new persisted source, if a
live call is needed to produce an agent-visible preparation result, or if the
change would alter write behavior, New Quiz acquisition, length limits, or UI.

## Execution result

Executor must append a compact report here: traffic light; commit hash if any;
changed files; exact test command and count; `git diff --check` result;
deviations; and unresolved decisions. A GREEN result must leave this brief
retired in the same closing batch according to `AGENTS.md`.
