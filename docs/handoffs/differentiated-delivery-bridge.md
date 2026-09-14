# Direct brief: differentiated delivery owns the SIS bridge

Status: ready for execution
Risk: high (Canvas content, grade projection, SIS configuration, private roster evidence)
Branch: `dev`

## Objective

Make every differentiated AssignmentForge or QuizForge delivery one reviewed,
crash-safe family operation. The operation creates color-suffixed real variants and one
unsuffixed no-submission bridge, attaches only the bridge to the selected module, and
registers the exact family for later grade projection. CanvasExpert may copy verified
Canvas scores into the bridge, but it never asks Canvas to sync grades to an SIS.

Whole-class delivery remains a single ordinary Canvas object and never creates a bridge.

## Teacher-visible outcome

For a base title such as `Outsiders Ch 5 - Nothing Gold Can Stay`, a differentiated
delivery using the configured public tags Silver, Red, and Blue creates:

- `Outsiders Ch 5 - Nothing Gold Can Stay` — the published bridge in the selected module;
- `Outsiders Ch 5 - Nothing Gold Can Stay - Silver`;
- `Outsiders Ch 5 - Nothing Gold Can Stay - Red`; and
- `Outsiders Ch 5 - Nothing Gold Can Stay - Blue`.

Students use the color-suffixed object assigned to their Canvas group. The bridge tells
students and parents why it exists and links to the configured Canvas Dashboard. The
teacher reviews content and grades in Canvas Live and initiates Canvas Grade Sync there.

## Locked decisions

1. `Support`, `Core`, `Accelerate`, and `Extend` remain the pedagogical tier labels.
   `config.get_tier_tags()` supplies the public Canvas title suffix for each used label.
   Every used tag is required, trimmed, nonempty, and unique after case-folding. Missing
   or duplicate tags block preparation and direct the teacher to Settings.
2. AssignmentForge uses each authored `tier.label` to resolve its tag. A differentiated
   QuizForge file declares its pedagogical tier in `metadata.variant` (or the already
   supported `metadata.variant_label` alias); preparation requires one canonical tier per
   file. Selected Canvas `group_name` remains separate group authority.
3. All files in a differentiated QuizForge family carry the same exact trimmed,
   unsuffixed base title. Server normalization appends ` - <tag>`; separately authored
   suffixes and differing base titles are rejected.
4. A family contains any two or more used tiers. It need not contain all four tiers.
5. Differentiated preparation requires a timezone-aware `due_at` and a `module_name`.
   Variants retain the requested due timestamp. The bridge due time is 23:59:00 on the
   same calendar date using the UTC offset carried by `due_at`. No due date or a naive
   timestamp blocks preparation; creation/publish time is never substituted.
6. All variants in one family have the same points possible and assignment group. A
   differentiated New Quiz family with unequal totals blocks before any write. Scores are
   copied as points; percentage scaling is not introduced.
7. Real variants finish `published=true`, `only_visible_to_overrides=true`,
   `omit_from_final_grade=true`, and `post_to_sis=false`. No variant is attached to a
   module. The server owns these values regardless of generic caller flags.
8. The bridge finishes with the exact unsuffixed title, common points and assignment
   group, `submission_types=["none"]`, point grading, no overrides,
   `only_visible_to_overrides=false`, `published=true`,
   `omit_from_final_grade=false`, and `post_to_sis=true`.
9. The bridge description is assignment/quiz-neutral, explains that no submission is made
   there, directs the student to the color-suffixed work assigned to them, and contains an
   escaped link to the runtime-configured Canvas base URL's Dashboard. No district URL is
   hardcoded in source.
10. Exactly one Assignment-type module item points to the exact bridge assignment ID in
    the selected module. Existing module create/reuse and exact-ID reconciliation laws
    remain in force.
11. The differentiated content operation saves the source IDs/titles, bridge ID/title,
    and verified bridge structural digest only after all required postconditions pass.
    Raw student IDs remain transient and never enter the operation, registration, review,
    receipt, tool result, log, fixture, or repository.
12. `gradebook.sis_bridge` becomes a registered-family grade-projection operation only.
    It never discovers/adopts an unregistered family, creates a bridge, repairs structure,
    or patches source/bridge SIS settings. It verifies the registered family and copies
    only eligible final numeric/excused states into the bridge with existing drift,
    idempotency, checkpoint, verification, receipt, and targeted-refresh safeguards.
13. Remove the `/post_grades` send, `post_grades` ledger step, passback status/result,
    teacher-observed passback confirmation API/tool, and related recovery behavior. No
    CanvasExpert production path triggers SIS/Skyward sync. `post_to_sis=true` remains the
    bridge assignment's Canvas SIS Sync setting.
14. A teacher request to land a differentiated family in Canvas authorizes the internal
    prepare/apply sequence for that exact course and family. Agent guidance must not add a
    second chat approval step; it reports the created Canvas objects and sends the teacher
    to Canvas Live for review. Internal digest review and Operation Ledger safeguards stay.
15. Retire the unsafe direct differentiated live CLI path rather than reproducing the old
    no-bridge behavior. There is no migration, compatibility shim, or legacy adoption path.

## Ordered operation behavior

Preserve the existing per-variant exact-ID checkpoints for create, visibility restriction,
overrides, quiz items, assignment patching, and retry. Replace per-variant module steps with
shared family steps that have stable keys and deterministic order:

1. prepare and revalidate group membership, tags, family title, points/category, due date,
   module, and same-title collisions;
2. create and verify every color-suffixed real variant in its safe final source shape;
3. create the bridge in an unpublished, omitted, SIS-disabled safe state and checkpoint its
   exact ID before downstream work;
4. resolve/create the selected module and attach only the exact bridge ID;
5. activate and verify the bridge's published, counted, SIS-enabled final state; and
6. persist and re-read the student-free family registration.

An ambiguous create/attach/patch remains `sent_unknown` and is never resent by guess. A
definitive downstream failure after any successful write is partial and resumes only after
exact-ID reconciliation. Registration is not success proof for an unverified Canvas effect.

Use one shared bridge-shape/helper owner consumed by differentiated Assignment, differentiated
Quiz, and grade projection; do not copy request/verification rules across three adapters.

## Acceptance criteria

1. A whole-class AssignmentForge or QuizForge operation has no bridge step, creates one
   ordinary object, and preserves its existing module/SIS/publish choices.
2. Differentiated AssignmentForge and QuizForge preparation blocks before Canvas mutation
   when a used public tag is missing/duplicated, the base titles differ, `due_at` or
   `module_name` is absent/invalid, or points/category differ.
3. A successful differentiated family creates exact `Base - <tag>` sources and one exact
   `Base` bridge. The server-generated suffix is shown in frozen review and result URLs.
4. Every source has an exact group override, is published and override-only, is omitted
   from the final grade and SIS-disabled, and is absent from all module-item POSTs.
5. The bridge has the locked shape and end-of-day due timestamp, and its description contains
   the runtime Dashboard link and neutral directions to the real color-suffixed work.
6. Exactly one module item points to the exact bridge ID. Retry/recovery does not duplicate
   sources, bridge, overrides, module, or module item.
7. The registration is written only after exact source, bridge, and module postconditions;
   later grade projection addresses only those registered exact IDs.
8. Grade projection copies eligible final numeric/excused states and verifies them, while
   blank, non-final, overlapping, stale, or drifted state still fails closed.
9. No production call path ends in `/post_grades`; no current MCP schema, tool instruction,
   guide, result, ledger step, or API names passback confirmation.
10. Reviews, operations, receipts, logs, tool results, tests, and tracked files contain no
    raw student identity or real course data.
11. Agent and authoring guidance describes public color tags, automatic bridge delivery,
    Canvas Live review, and teacher-owned SIS sync without claiming CanvasExpert performs it.
12. The named focused gate passes with `pytest-randomly` disabled.

## Explicit non-goals

- No change to the four pedagogical tier meanings or assessment-grouping cutoffs.
- No percentage scaling, score invention, rubric/comment copying, or New Quiz item-score
  copying into the bridge.
- No direct SIS/Skyward API, credentials, browser automation, sync confirmation, or polling.
- No student-facing variant module items.
- No whole-class bridge.
- No migration/adoption/repair path for pre-existing unregistered families.
- No New Quiz writing-item retirement; that is the next separately briefed batch.
- No CanvasMirror/scoring acquisition or general length-handling work in this batch.

## Authorized scope and insertion points

Durable behavior and guidance:

- `docs/contracts/sis-grade-bridge-contract.md`
- `docs/reference/assignment-differentiation-design.md`
- `docs/reference/quiz-operation-design.md`
- `docs/reference/operation-ledger-module-map.md`
- `docs/reference/course-expert-module-map.md`
- `docs/guides/sis-grade-bridges.md`
- `docs/mcp-server.md`
- `api/README.md`
- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`
- `api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt`
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt`

Runtime owners:

- `api/operation_ledger/adapters/assignment.py`
- `api/operation_ledger/adapters/assignment_tiered.py`
- `api/operation_ledger/adapters/quiz.py`
- `api/operation_ledger/adapters/quiz_differentiated.py`
- `api/operation_ledger/adapters/module_placement.py` only if its generic exact-ID primitive
  needs a narrow extension
- one new shared helper under `api/operation_ledger/adapters/` for the locked bridge shape,
  create/verify/registration steps, and end-of-day/tag normalization
- `api/operation_ledger/adapters/sis_grade_bridge.py`
- `api/sis_grade_bridge.py`
- `api/content_push.py`
- `api/mcp_server/tools.py`, `server.py`, `contract.py`, and one new current schema snapshot
- `api/platform_services/config/gradebook.py` only for tag validation/read helpers
- `api/webui/templates/settings.html` only for accurate tier-tag guidance
- `api/push_tiers.py` and its direct references for retirement
- `docs/contracts/canvas-transport-owners.json` and mutation ownership declarations if the
  changed send owners require it

Tests may change only where they directly specify these behaviors. Prefer updating the
existing law/contract/example tests rather than adding parallel coverage.

## Required references

The executor reads this brief, then only:

- `docs/reference/project-state.md` — full file;
- `api/README.md` — **What each push does automatically** and **Confirmed Canvas API facts / limits**;
- `docs/contracts/sis-grade-bridge-contract.md` — sections 1–8;
- `docs/reference/assignment-differentiation-design.md` — **Existing path and product constraint**, all **Locked decisions**, and **Explicit exclusions**;
- `docs/reference/quiz-operation-design.md` — **Confirmed Canvas objects** and all **Locked decisions**;
- `docs/reference/operation-ledger-module-map.md` — **Facades**, **Execution Owners**, **Shared Support**, **Safety Boundaries**, and **Test Routing**;
- `docs/reference/course-expert-module-map.md` — **Backend Routing**, **First Places To Look By Symptom**, and **Guardrails**;
- `docs/mcp-server.md` — **Tools** paragraphs covering staged content and SIS grade bridges, plus **Token-lean results**;
- `docs/guides/sis-grade-bridges.md` — sections 1–8;
- the three canonical authoring files named in scope, limited to CORE/connected guidance
  and the AssignmentForge/QuizForge tier, title, delivery, and validation sections;
- the runtime owners and direct tests named in this brief as needed for implementation.

Do not read the CanvasMirror vision, archived handoffs, broad architecture documents, or
unrelated module maps for this batch.

## Preflight and stop conditions

Before writing, confirm:

- the worktree contains only this committed brief;
- `dev` is checked out;
- the current bridge still sends `/post_grades` and variants still own module attachment;
- both content adapters still use the Operation Ledger and the shared module primitive;
- `get_tier_tags()` remains the current teacher-controlled tag source; and
- the named tests exist.

Stop RED rather than guess if Canvas cannot set `omit_from_final_grade`/`post_to_sis` on
New Quiz assignment shells, the runtime base URL cannot safely produce a Dashboard link,
the operation model cannot checkpoint a shared bridge without a public contract expansion,
or another subsystem must change beyond the authorized scope. Stop YELLOW for one bounded
unavailable check or a single senior decision. Preserve unrelated worktree changes.

## Named verification gate

```powershell
py -m pytest -p no:randomly `
  api/tests/test_assignment_tier_operation.py `
  api/tests/test_quiz_tier_operation.py `
  api/tests/test_sis_grade_bridge_operation.py `
  api/tests/test_sis_grade_bridge.py `
  api/tests/mcp_server/test_sis_grade_bridge_tools.py `
  api/tests/mcp_server/test_content_push_tools.py `
  api/tests/test_operation_routes.py `
  api/tests/test_transport_ownership.py `
  api/tests/test_canvas_mutation_ownership.py `
  api/tests/test_canvasagent_instructions.py `
  api/tests/test_settings_rail.py
```

No full API or engine suite is required unless a focused failure proves unexpected coupling.
For the Settings copy touched here, render `/settings` and confirm the tier-tag controls load
with zero new browser console errors. No live Canvas test is authorized or required.

## Execution result

YELLOW (baseline-only gate defect; implementation otherwise complete). The differentiated
family publication path now creates color-suffixed source artifacts and one published,
unsuffixed No Submission bridge in the selected module; sources are excluded from final
grade, SIS-disabled, and omitted from module placement. The SIS bridge operation is now a
registered-family grade projection and has no `/post_grades` or passback-confirmation path.

Changed runtime, contract, documentation, UI, schema, and focused test files are present in
the working tree. `py_compile`/compile checks pass. The named focused matrix passes 159 tests
with one unchanged baseline failure in `api/tests/test_transport_ownership.py`:
`api/mcp_server/tools.py` contains the pre-existing `requests.Session` New Quiz writer that
is absent from the historical allowlist. The same failure reproduces at committed baseline
559b3b9 in a clean detached worktree (`1 failed, 1 passed` for that test file), so no new
ownership failure was introduced and no allowlist entry was added. The MCP v46 schema
contract passes 6/6. The `/settings` browser check returned HTTP 200, rendered all four
tier-tag controls and guidance, and produced zero console warnings or errors.

No live Canvas test was run. No unresolved product decisions remain for this batch. The
current worktree has not yet been committed by the executor; the parent agent will commit
and push it after this report is recorded.
