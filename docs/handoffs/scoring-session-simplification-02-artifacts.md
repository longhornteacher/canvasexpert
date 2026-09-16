# Direct brief: Scoring Session simplification 02 — canonical artifacts

Status: current / ready for one implementation executor

Program: `docs/reference/scoring-session-simplification-program.md`

Accepted prerequisite: batch 01 implementation commit `6ca30aa`; its GREEN
execution record is preserved in Git commit `64c5cde`.

## Objective

Replace the legacy general SAFE/private file writer used by assignment-scoped
Scoring Sessions with one scoring-specific artifact builder. A prepared run must
persist exactly the two artifacts consumed by the live workflow: one private
assignment-scoped session record and one scrubbed SAFE bundle.

Preserve every accepted packet, privacy, held-work, and Canvas-write behavior from
batch 01. This is storage and ownership simplification, not a scoring-contract
redesign.

## Teacher-visible outcome

`prepare_scoring_session`, `list_scoring_sessions`, `get_scoring_packet`, and
`submit_scoring_results` retain their accepted batch-01 shapes and behavior.
Ordinary text work still becomes a pseudonymized packet; unsafe or unreadable work
is still held; Canvas Live remains the only review surface. The teacher sees no new
tool and no storage terminology.

Internally, a newly prepared run no longer creates per-student SAFE text files, a
duplicate PRIVATE bundle, `who-is-who.csv`, a separate HOW-TO-SCORE file, or a
standalone privacy-audit file.

## Acceptance criteria

1. One successful ordinary preparation creates exactly one private session JSON
   and one SAFE bundle JSON for that run. No other per-run file is created.
2. The global identity vault remains the only identity mapping source. The SAFE
   bundle contains no real name, Canvas/SIS id, credential, signed URL, or private
   path.
3. The builder retains the vault transaction, pseudonym assignment, deep scrub,
   hard structural scan, per-student survivor scan, shared-context scan, held-work
   accounting, and final outbound pseudonym gate.
4. A hard structural identity violation blocks the entire SAFE bundle. A survivor
   found by the per-student verification gate removes only that student's response
   from SAFE while retaining private review state in the session.
5. Attachment-bearing, media-only, empty, and unreadable mirror rows remain held.
   Preparation downloads no evidence bytes.
6. Packet page zero, paging, full-text segmentation, context/guidance compaction,
   digest binding, response/item counts, held counts, and result validation remain
   behaviorally unchanged from accepted batch 01.
7. Preparation failures remain typed and identity-safe. No raw exception, student
   identity, response text, or private path crosses MCP.
8. Ordinary-assignment review questions, drift, idempotency, PUT-then-GET
   verification, `sent_unknown`/Attention, receipts, and public aggregate outcomes
   remain unchanged.
9. `api/powergrader/ai_workflow.py`,
   `api/powergrader/ai_workflow_support.py`, and
   `api/powergrader/helpers.py` are deleted only after `rg` proves no live caller.
   Dead general-writer functions in `api/feedback_artifacts.py` are deleted or
   narrowed by the same rule; shared helpers with real consumers remain.
10. Tests for current privacy, packet, and write laws are preserved and adapted,
    not deleted to make the gate pass. Only tests whose sole subject is a deleted
    artifact format may be removed.
11. Current contracts, route cards, guides, and source comments describe the
    canonical two-artifact implementation and contain no live claim that the
    redundant files are part of Scoring Sessions.
12. The focused gate and the declared full API integration checkpoint pass with no
    undeclared deviation.

## Explicit non-goals

- Do not change the MCP registry, schema v49, tool parameters, response shapes, or
  assignment-scoped session lifecycle.
- Do not change candidate discovery, mirror refresh policy, New Quiz rejection,
  scoring norms, guidance compaction limits, packet token ceilings, or scoring
  judgments.
- Do not change Canvas grade/comment write algorithms or add any write surface.
- Do not add a database, artifact registry, migration, compatibility reader,
  cleanup routine, UI, hosted model, or background process.
- Do not delete old workspace sessions or artifacts. Historical private files stay
  untouched unless the user separately requests cleanup.
- Do not change bridge/SIS behavior.
- Do not put private data, developer-specific paths, or generated teacher artifacts
  in the repository, fixtures, command output, or execution report.

## Locked implementation decisions

Use `docs/reference/scoring-session-simplification-program.md` → **Locked
decisions** and the complete **Handoff 02 — canonical scoring artifacts and legacy
preparation deletion** section as authority.

Additionally:

- Create `api/powergrader/scoring_artifacts.py` as the scoring-specific builder and
  writer. It receives mirror-prepared submissions plus assignment context, uses the
  canonical vault/scrub/safety helpers, and returns the SAFE bundle path plus
  identity-free aggregate counts required by the session.
- `api/powergrader/scoring_preparation.py` calls this owner directly. It must not
  route through `ai_workflow`, `feedback_pipeline.write_safe_and_private`, or a
  compatibility adapter.
- The private session record continues to own real submission baselines, teacher
  settings needed by the write owner, scoring basis/guidance, held state, and
  privacy-step receipts. Do not create a second private copy.
- The SAFE bundle is one JSON file containing only the scrubbed material required
  by `scoring_packet` and result validation. Contract and rubric text remain
  server-authored at packet read time, not in a separate file.
- Keep `api/feedback_vault.py` as the canonical global identity owner. Do not write
  a per-run decoder or export.
- `privacy_artifacts` may retain the SAFE bundle location and identity-free counts;
  it must not claim or reference removed private/per-student/how-to/audit files.
- Existing artifact directories may remain as storage containers. The acceptance
  count concerns files created for the new run, not directory entries inherited
  from prior sessions.
- Preserve function-style tests; do not introduce test classes. Run every pytest
  command with `-p no:randomly`.

## Authorized scope

Primary owners:

- new `api/powergrader/scoring_artifacts.py`
- `api/powergrader/scoring_preparation.py`
- `api/feedback_artifacts.py`
- `api/feedback_pipeline.py` only to remove dead re-exports after live callers are
  gone
- `api/powergrader/privacy.py` only if its standalone audit writer becomes dead
- `api/powergrader/session_builder.py` only for canonical session fields
- delete `api/powergrader/ai_workflow.py`
- delete `api/powergrader/ai_workflow_support.py`
- delete `api/powergrader/helpers.py` only after live-call proof

Tests:

- new `api/tests/powergrader/test_scoring_artifacts.py`
- `api/tests/powergrader/test_scoring_preparation.py`
- `api/tests/test_feedback_pipeline.py`
- `api/tests/test_feedback_safety.py`
- `api/tests/test_scoring_packet_mcp.py`
- `api/tests/mcp_server/test_prepare_scoring_session.py`
- `api/tests/mcp_server/test_scoring_apply_tools.py`
- `api/tests/powergrader/test_scoring_apply.py`
- other tests only when an import from a deleted owner must be updated; report each
  such file as a bounded deviation in the execution result

Documents only where their current text describes scoring artifact ownership:

- `docs/contracts/feedback-scoring-contract.md`
- `docs/guides/scoring-sessions.md`
- `docs/mcp-server.md`
- `docs/reference/powergrader-scoring-map.md`
- `docs/reference/powergrader-module-map.md`
- `docs/reference/workbench-canonical-flow-map.md`
- `api/README.md`

Do not broaden work merely because `feedback_artifacts.py` contains shared packet
or oral-reading helpers.

## Required reading

Read only:

1. `AGENTS.md` in full.
2. This brief in full.
3. `docs/reference/project-state.md` in full.
4. `docs/reference/scoring-session-simplification-program.md` sections **Locked
   decisions** and the complete **Handoff 02 — canonical scoring artifacts and
   legacy preparation deletion** section.
5. `docs/reference/powergrader-scoring-map.md` in full.
6. `docs/contracts/feedback-scoring-contract.md` sections **Direction 1 — SAFE
   bundle**, **Direction 2 — Results**, and **Session consumption and write
   safety**.
7. `docs/mirror.md` sections **Design laws** and **MCP reads and the refresh tool
   (`api/mcp_server/tools.py`, `server.py`)**.
8. The primary owners and tests named in **Authorized scope**.

Do not read the retired batch-01 brief from Git history, archived handoffs,
CanvasMirror vision material, bridge contracts, or unrelated module maps.

## Preflight before writing

1. Confirm branch `dev`, record `git status --short --branch`, preserve unrelated
   work (including `docs/guides/canvasexpert-agent-capabilities.md` if still
   untracked), and do not stage it.
2. Fetch and compare `origin/dev` and `origin/main` before claiming current state.
   Confirm `6ca30aa` is an ancestor of HEAD.
3. Confirm this is the sole file in `docs/handoffs/`.
4. Use `rg` to enumerate every live caller of `ai_workflow`,
   `ai_workflow_support`, `helpers`, `write_safe_and_private`,
   `pseudonymize_submissions`, and every key returned in `privacy_artifacts`.
   Stop if a proposed deletion has a live consumer outside authorized scope.
5. Run the accepted batch-01 focused gate as the baseline and record exact counts:

   ```powershell
   py -m pytest -p no:randomly api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_server_instructions.py api/tests/mcp_server/test_tools.py api/tests/powergrader/test_scoring_preparation.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py api/tests/test_beta075_mcp.py api/tests/test_canvasagent_instructions.py
   ```

Stop on an unexplained baseline failure.

## Named focused gate

```powershell
py -m pytest -p no:randomly api/tests/powergrader/test_scoring_artifacts.py api/tests/powergrader/test_scoring_preparation.py api/tests/test_feedback_pipeline.py api/tests/test_feedback_safety.py api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/powergrader/test_scoring_apply.py
```

Then run the explicitly declared cross-cutting integration checkpoint:

```powershell
py -m pytest -p no:randomly api/tests
```

Also run:

```powershell
rg -n "ai_workflow|ai_workflow_support|powergrader\.helpers|write_safe_and_private|who-is-who|HOW-TO-SCORE|privacy_audit" api docs
git diff --check
py -m compileall -q api/powergrader api/mcp_server
```

Every remaining match must be a proven live shared owner or an explicitly
historical frozen fixture. Current scoring code/docs must have no dependency on a
deleted artifact.

## Proportional real-data verification

This may refresh the local mirror and create private local artifacts but must not
write a Canvas score/comment or emit student data/private paths.

1. Restart/use a persistent current local process; the scoring full refresh may
   require bounded retries across the 25-second MCP wait.
2. Prepare PAP assignment `3682268` with bounded verification-only guidance. It
   must reach `ready` or return a specific typed blocker.
3. For the successful new run, verify without printing paths or contents that the
   run created one session JSON and one SAFE bundle JSON, and none of the retired
   per-student/private/CSV/how-to/audit files.
4. Retrieve page zero and verify context, basis, digest, counts, and expected table
   shape.
5. Prepare New Quiz assignment `3678471` and confirm it returns
   `new_quiz_writing_requires_assignment` without creating either per-run artifact.
6. Do not call `submit_scoring_results` against live Canvas.

If the private environment is unavailable after the named gates pass, return
YELLOW. Do not fabricate evidence or weaken the artifact count.

## Stop conditions

Stop RED if:

- removing an artifact weakens or bypasses an identity/safety scan;
- another live product surface consumes a proposed deleted file or owner;
- the batch requires changing schema v49 or any accepted public tool shape;
- the session record can no longer support the unchanged write baseline, drift,
  idempotency, verification, Attention, or receipt laws;
- a private artifact would enter repository history, fixtures, logs, or the report;
- an unrelated regression appears.

Stop YELLOW if the focused gate passes but the full API checkpoint or proportional
private verification is unavailable. Return to the same executor for routine test
repair; do not replace safety coverage with smaller examples.

## Self-review checklist

- Exactly two new files per successful run: session JSON and SAFE bundle JSON.
- No duplicate identity export or private bundle.
- All scrub/safety/held gates still execute once in the same order.
- Public MCP contract and Canvas writes unchanged.
- Packet and apply law tests retained.
- Dead owners proven unused before deletion.
- No migration, compatibility layer, cleanup, or unrelated edit.

## Execution result

Not yet executed.

The executor replaces this section with: traffic light; commit hash if any;
changed/deleted files; baseline, focused, and full-suite commands with exact
counts; call-graph/source-check evidence; identity-safe real-data artifact counts
and packet outcome; deviations; unresolved senior decisions.
