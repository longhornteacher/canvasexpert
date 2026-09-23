# Brief: Push, then prove it: verify_live and tiered-apply recovery

**Status:** Current. **Risk:** High (Canvas writes, graded assignments).
**Senior:** Claude (Opus). **Executor:** Sonnet. **Branch:** `dev`.
**Baseline known failure:** `test_quick_fix_contract_and_version` fails at `b2261da`
before this work; it is unrelated, so don't fix or absorb it.

## Teacher outcome
After I approve a push, the agent confirms it with one cheap Live check and tells me
what exists in Canvas. When a tiered AssignmentForge apply is interrupted or gets an
ambiguous response, CE either finishes the family or tells me exactly which objects it
created and how to resume or abandon. It never loops on `drift_detected` (dev-log
Issue #11: tier 0 created, `assignment_create_unverified`, then drift forever).

## Current repo truth (mapped 2026-09-23 at `72d9b6f`; re-verify in preflight)
- No `verify_live` and no `verify_hint` anywhere. Apply success is
  `{ok, operation_id, status, target_results}` (`operation_ledger/executor.py`
  `_finish_operation` ~L204; `mcp_server/tools.py` `apply_content_push` ~L1608).
- `operation_ledger/adapters/assignment_tiered.py` `execute()` (L33-157): each tier is
  created, re-read by recorded id, and published (`_publish_assignment`/`_put_and_verify`).
  On resume it GETs the recorded `returned_object_id`. A shape mismatch returns
  `sent_unknown`/`assignment_create_unverified` with a field-level `private_diagnostic`
  (commit `3b72bec`). There is **no title lookup** when no id was recorded, and no retry
  or duplicate detection.
- Family tail order (`differentiated_bridge.execute_family_tail` L418-645): tiers → module
  items → bridge → family link. Keep this order.
- Recovery is automatic only: `operation_ledger/recovery.py`
  `recover_pending_operations()` runs at startup. No `resume_operation` or
  `abandon_operation` tool exists.
- Drift is computed over the Canvas baseline captured at preview (`executor.py` ~L250-259).
  The response is only `error_code:"drift_detected"`: no fields, no next step.
- `repair_plan` exists only for SIS bridges. Tiered steps already carry
  `step_key`/`state`/`returned_object_id`.
- `preview_content_push` (`content_push.py` L152-238) has no `post_to_sis`/`due_at`
  check. No `canvas_message` passthrough anywhere.
- Test double: `FakeCanvas` in `api/tests/test_assignment_tier_operation.py:135-199`,
  wired via monkeypatch of `canvas_client.canvas_get` / `_canvas_send` / `canvas_get_all`.
- MCP schema `tool_schema_v58.json`, 49 tools.

## Acceptance criteria
- **AC1 `verify_live(course_id, kind, id? , title?)`.** A new MCP tool plus a shared
  service (the MCP wrapper stays thin). It uses exactly one Canvas GET by id, or one
  title-filtered list call when only a title is given. It writes nothing: no catalog,
  mirror, or pending-writes change.
  - `kind=assignment` returns `{found, id, title, published, points_possible,
    module_ids, assignment_group_id, omit_from_final_grade, post_to_sis, due_at, url,
    checked_at}`.
  - `kind=page` returns `{found, url, title, published, module_ids}`.
  - `kind=quiz` returns the assignment shape for the quiz's assignment.
  - Student-free. The tool returns only these projected fields, never the raw Canvas
    response.
  - `module_ids` may cost one extra call only if Canvas can't return it on the object.
    Document the call count in the tool description.
- **AC2 `verify_hint`.** Every successful apply (`apply_content_push`,
  `push_content_live`, `apply_assignment_update`, `apply_sis_grade_bridge`) includes
  `verify_hint: [{course_id, kind, id}]` for each object it created or changed.
- **AC3 Ambiguous create lookup (tiered and whole assignment).** When a create's
  outcome is unknown and **no id was recorded**, list the course's assignments filtered
  by exact title, created within the last 10 minutes:
  - exactly one → record its id, re-read it, continue as created;
  - none → retry the create once;
  - more than one → stop with `duplicate_suspected {ids}`.
  The lookup is idempotent under resume.
- **AC4 Named drift.** `drift_detected` responses include `drift_fields` (field names
  only, never values) and `next: "resume_operation" | "abandon_operation" | "re-preview"`.
- **AC5 `resume_operation(operation_id)`.** A new MCP tool that reuses the recovery and
  executor path for one operation and continues from the last recorded step. It is not
  a new write capability: it may only perform steps of the already-approved operation.
  It refuses if the operation is `applied`, `abandoned`, or held on another machine
  (`work_item_held_elsewhere` from shared work).
- **AC6 `abandon_operation(operation_id)`.** A new MCP tool that makes no Canvas call.
  It marks the operation abandoned, returns `repair_plan`, and blocks later
  resume/apply for that operation.
- **AC7 `repair_plan`.** Any unrecoverable mid-family stop in the tiered or
  differentiated path returns `repair_plan: [{step, created_id, state}]` built from the
  recorded steps. Nothing is deleted.
- **AC8 Preview refusals.** `preview_content_push` with `post_to_sis:true` and no `due_at`
  refuses with `sis_requires_due_at`. On any Canvas 4xx during apply, the response
  carries `canvas_message`: Canvas's error message text, truncated to 500 characters.
  It carries no request body, token, or URL query.
- **AC9 One schema bump.** v58 → v59 for the three new tools and the added fields. Keep
  the registry, snapshot, generated inventory, wrapper tests, and `docs/mcp-server.md`
  in sync.

## Non-goals
- No delete or update tools for Canvas content, no auto-cleanup, no auto-refresh.
- No change to family step order, tier tags, or SIS bridge logic (just shipped).
- No fix for the root cause of Issue #11's shape mismatch. There's not enough evidence;
  see the live run below.
- No storage/vault/freshness changes. No Web UI surface.

## Locked decisions
- `verify_live` is the only Live read an agent makes after a push (R4). It never feeds the
  catalog. Catalog confirmation still happens only through refresh.
- `resume_operation` and `abandon_operation` act only on existing, teacher-approved
  operations. Their tool descriptions must say so.
- Add fields only. Existing tool names and argument shapes keep working.

## Scope (expected files)
New: `api/live_verify.py`, `api/tests/test_live_verify.py`.
Changed: `api/mcp_server/{tools,server,contract}.py`, new `tool_schema_v59.json`,
`api/operation_ledger/{executor,recovery}.py`,
`api/operation_ledger/adapters/{assignment_tiered,assignment,differentiated_bridge}.py`,
`api/content_push.py`, `docs/mcp-server.md`, and tests mirroring those modules
(`test_assignment_tier_operation.py`, `test_operation_ledger.py`,
`mcp_server/test_contract.py`, `mcp_server/test_tools.py`,
`mcp_server/test_content_push_tools.py`).

## References (read only these)
`AGENTS.md`; `docs/reference/operation-ledger-module-map.md`; `docs/mcp-server.md`;
`api/README.md` §Canvas push; the files named above.

## Tests (AGENTS.md taxonomy; all Canvas traffic through `FakeCanvas`)
- **Law:** `verify_live` makes exactly one GET (or one list) and writes nothing.
- **Law:** ambiguous create → one / none / many (AC3), including resume idempotency.
- **Law:** `abandon_operation` blocks later resume and apply.
- **Contract:** `verify_hint` on every apply tool, parametrized over the apply-tool list.
- **Contract:** `drift_detected` shape (AC4).
- **Example:** an interrupted tiered family runs → `resume_operation` → completes with
  `verify_hint`.
- **Contract:** schema snapshot and inventory (AC9).

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_live_verify.py api/tests/test_assignment_tier_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_operation_ledger.py api/tests/test_sis_grade_bridge_operation.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_content_push_tools.py
```
Record the baseline at `72d9b6f` before editing (`test_live_verify.py` won't exist yet).

## Executor safety (AGENTS.md guardrail 7)
Tests only. Don't start the Web UI or MCP server, and make no live Canvas calls. Run no
`api.*` code outside pytest. Read nothing under the teacher's OneDrive or
`%LOCALAPPDATA%\CanvasExpert`.

## Live acceptance (after GREEN; senior or teacher runs it through the MCP runtime)
Sandbox: **CS 8** (`121046`), a live course with students. The teacher approved test
pushes there on 2026-09-23 under these conditions:
- every test object's title contains `IGNORE`;
- every test object goes into a new **unpublished** module named `IGNORE - CE Tests`;
- SIS is the teacher's concern; the tests don't set `post_to_sis`.

Checks:
- **T3.1** `verify_live` on a known CS 8 assignment returns found, published and
  module_ids with one Canvas request.
- **T3.2** Push a tiered AssignmentForge draft titled `IGNORE - CE Tiered Test` into
  `IGNORE - CE Tests`. Expect 3 published override-only sources `… - Silver/Red/Blue`,
  1 unsuffixed bridge in no module, and the family link `verified`. `verify_live` confirms
  each object named in `verify_hint`. If `assignment_create_unverified` recurs, record
  the `drift_fields`/diagnostic **field names** and stop for a senior decision on the
  Issue #11 root cause.
- **T3.4** `post_to_sis:true` without `due_at` is refused at preview.
- Afterwards, list every created id so the teacher can delete them by hand.

## Stop conditions
Stop if: the preflight contradicts "current repo truth"; AC5 would need to perform a
step the original operation didn't approve; `verify_live` can't be done in ≤2 calls;
changing the executor's checkpoint or receipt format would break existing receipts; or
any change touches storage, vault, or SIS bridge logic.

## Execution result

**GREEN**, with two disclosed scope-narrowing deviations flagged below for senior review.
Commit: see `git log` on `dev` ("Add push verification and tiered-recovery tools").

**Named gate:** baseline at `72d9b6f`/`504ec26` = 261 passed, 0 failed
(`test_live_verify.py` did not exist). Final = **295 passed, 0 failed**.
Full `api/tests` checkpoint (not the named gate; run once as an integration check since
the change touches `executor.py`/`models.py`/`adapter_support.py`): baseline 71
pre-existing failures / 1867 passed at `504ec26`; final = the **same 71** pre-existing
failures (full-suite ordering/pollution artifacts unrelated to this brief, including the
documented `test_quick_fix_contract_and_version`) / **1890 passed**. Two real regressions
surfaced by that full run were fixed: a stale owner entry in
`docs/contracts/canvas-transport-owners.json` (the tiered-create POST moved into a new
helper function) and two hardcoded schema-version/tool-count assertions in
`api/tests/test_beta075_mcp.py` (58/49 -> 59/52), outside the named gate but clearly
caused by this change.

**Changed files:** `api/live_verify.py` (new), `api/tests/test_live_verify.py` (new),
`api/mcp_server/tool_schema_v59.json` (new), `api/mcp_server/{contract,server,tools}.py`,
`api/operation_ledger/{executor,models}.py`,
`api/operation_ledger/adapters/{adapter_support,assignment_tiered,differentiated_bridge}.py`,
`api/content_push.py`, `api/sis_grade_bridge.py` (one additive `course_id` field),
`docs/mcp-server.md`, `docs/contracts/canvas-transport-owners.json`,
`api/tests/{test_operation_ledger,test_assignment_tier_operation,test_beta075_mcp}.py`,
`api/tests/mcp_server/{test_contract,test_tools,test_content_push_tools}.py`.

**AC-by-AC:**
- AC1 `verify_live`: new `api/live_verify.py`, ≤2 Canvas calls (1 identity + 1 optional
  module scan), writes nothing. Tested in `test_live_verify.py`.
- AC2 `verify_hint`: added in `content_push._result_projection` (covers
  `apply_content_push`/`apply_assignment_update`/`push_content_live`) and wrapped onto
  `apply_sis_grade_bridge` at the `tools.py` boundary (required one additive `course_id`
  field on `api/sis_grade_bridge.py`'s own result dict; no bridge logic changed).
- AC3 ambiguous create lookup: implemented in `assignment_tiered.py` only (tiered
  AssignmentForge path — the brief's Scope/named-gate file list). **Deviation:** the
  brief's AC3 title also names "whole assignment"; `assignment_whole.py` has the same
  no-id/outbound-started gap but is outside Scope and outside the named gate
  (`test_assignment_operation.py` isn't in it), so it was left untouched to avoid shipping
  an unverified change to that path. Open decision for the senior: a follow-up slice, or
  confirm tiered-only satisfies the intent.
- AC4 named drift: generic, adapter-agnostic field-name diff between the stored and a
  freshly captured baseline (no per-adapter change, so SIS bridge logic is untouched);
  `next` is `abandon_operation` when the same target already drifted once (breaks the
  Issue #11 loop), else `resume_operation` with progress or `re-preview` with none.
- AC5/AC6 `resume_operation`/`abandon_operation`: new MCP tools. `resume_operation` is a
  thin wrapper over the existing `executor.retry_operation` (no new write capability) plus
  refusals for `applied`/`abandoned`/claimed-and-unexpired ("held elsewhere", reusing that
  exact code name). `abandon_operation` is a new `executor.abandon_operation` (new
  `abandoned` operation status, additive to `models.OPERATION_TRANSITIONS`); makes no
  Canvas call.
- AC7 `repair_plan`: `executor.build_repair_plan` (pure projection of recorded steps);
  surfaced in `abandon_operation` and in `content_push._result_projection` for a
  differentiated family stuck `needs_repair`.
- AC8: `sis_requires_due_at` preview refusal in `content_push.py` (both push-preview
  entry points), before any Canvas read. `canvas_message` (Canvas's 4xx text, truncated to
  500 chars, via a new `adapter_support.canvas_message_from_error`) is wired through the
  tiered/differentiated AssignmentForge path (`assignment_tiered.py`,
  `differentiated_bridge.py`) only. **Deviation:** not wired into
  `assignment_whole.py`/`quiz.py`/`page.py`/`quick_assignment.py`/`sis_grade_bridge.py`
  adapters, to avoid unverified changes to paths outside the named gate; those adapters
  still return their existing `private_diagnostic`/`error_code` shape unchanged.
- AC9: schema v58 -> v59 (49 -> 52 tools): `contract.TOOL_SCHEMA_VERSION`,
  `tool_schema_v59.json`, `server.py` wrappers (plus a fix to
  `_strip_generated_schema_titles` — it was deleting the entire `title` *property* schema
  for any tool with a parameter literally named `title`, which `verify_live` is the first
  to have), `tools._TOOL_GROUPS`, `docs/mcp-server.md`, and the generated inventory/contract
  tests.

**Non-goals honored:** no delete/update tools; no change to family step order, tier tags,
or SIS bridge decision logic (only additive output fields); no fix to Issue #11's root
cause (only a named exit from the drift loop); no storage/vault/freshness changes; no Web
UI surface.

**Open decisions for the senior:** (1) the two AC3/AC8 scope-narrowing deviations above;
(2) whether "no change to SIS bridge logic" tolerates the one additive `course_id` output
field I added to `api/sis_grade_bridge.py`'s `_result_projection` (needed for
`apply_sis_grade_bridge`'s `verify_hint`; no behavior changed, confirmed by the unchanged
`test_sis_grade_bridge.py`/`test_sis_grade_bridge_operation.py`/
`mcp_server/test_sis_grade_bridge_tools.py` suites).

**Live acceptance (T3.1/T3.2/T3.4):** not run — reserved for the senior/teacher per the brief.

## Live acceptance result (2026-09-23, senior)
- T2.1 (storage brief): `refresh_course_structure` on ELA 7 and ELA 7 PAP returned
  `complete` with all four sections current.
- T3.1: `verify_live` on the PAP whole-assignment push returned published, module,
  due, and SIS state in one call. PASS.
- T3.2: the ELA 7 tiered push reproduced Issue #11. Tier 0 was created, then
  `sent_unknown`/`assignment_create_unverified`, with the ledger diagnostic
  `assignment postcondition mismatch: description`. AC4/AC7 behaved as specified: a
  `repair_plan` was returned and there was no drift loop. The same description passed
  the whole-assignment postcondition in ELA 7 PAP, so the tiered comparison is stricter
  than the whole path's.

## Correction 1 (senior decision)
Scope is now open for the Issue #11 root cause, limited to the tiered description
postcondition:
- `assignment_tiered.py` must compare a created source's description with the same
  canonicalization the whole-assignment path uses. That means one shared helper, not
  a second copy.
- Canvas's HTML sanitization of the tier description (base description plus scaffolding)
  must not count as a mismatch. Missing or different visible text still must.
- **Law test:** a tier description round-tripped through a Canvas-like sanitizer
  (entities decoded, whitespace and newlines inside tags normalized, attribute order
  changed) verifies. A changed visible sentence fails.
- `resume_operation` on an operation stopped this way then continues from the
  recorded id without creating a duplicate.

## Correction 1 execution result

**GREEN.** Root cause found: `assignment_tiered.py` had its own private
`_canonical_description` (entity-unescape + whitespace-collapse only, no tag/attribute
awareness at all). A tier description is composed as base text plus an HTML scaffolding
panel (`<div class="..." style="..." data-tier="...">...</div>`); Canvas's own sanitizer
is free to reorder that div's attributes, re-encode entities, and reformat inter-tag
whitespace, none of which the old string-level compare tolerated -- any of those alone
produced `assignment postcondition mismatch: description` even though the visible text
was byte-identical. `assignment_whole.py` never hit this because it has no description
postcondition check at all (nothing to reproduce there; there was no second copy to
compare against, only a gap).

Fix: one shared, tag-aware canonicalizer, `adapter_support.canonical_html` (new;
`html.parser.HTMLParser`-based) -- decodes entities, sorts each tag's attributes, drops
whitespace-only text between/inside tags, collapses real text whitespace to single
spaces. `assignment_tiered.py`'s private `_canonical_description` is deleted; its one
caller (`_shape_value_matches`) now calls the shared helper. No second copy exists.

**Tests added:**
- Law: `api/tests/test_adapter_support.py` -- a Canvas-sanitized round trip (entities
  re-encoded, attribute order changed, whitespace/newlines added) verifies; a changed
  visible sentence does not.
- Contract: `test_source_shape_matching_tolerates_a_sanitized_scaffolding_panel` in
  `test_assignment_tier_operation.py` -- the same law through the real tiered
  postcondition check (`_source_shape_matches`), using an actual scaffolding-panel div.
- Example: `test_resume_operation_on_a_recorded_tier_zero_id_continues_without_a_second_create`
  -- builds a real Operation Ledger operation (not a bare adapter call), applies it to a
  tier-0 `sent_unknown`/recorded-id stop via `executor.apply_operation`, then resumes via
  `executor.retry_operation` (what `resume_operation` calls) and asserts exactly one POST
  ever created tier 0.

**Named gate (as before) + the two new/changed test files:** 298 passed, 0 failed.
Also reran `test_assignment_operation.py`, `test_quiz_operation.py`, `test_page_operation.py`,
`test_quick_assignment_operation.py`, `test_printable_attach.py`, `test_operation_routes.py`,
`test_canvas_mutation_ownership.py`, `test_beta075_mcp.py` (137 passed) since
`adapter_support.py` is shared: no regressions.

One test-writing correction along the way: my first version of the sanitized-panel fixture
used whitespace-only text nodes between tags (e.g. indentation) that my first
`canonical_html` draft kept as a phantom single space instead of dropping, so the two
canonicalized strings differed only in incidental inter-tag spacing. Fixed by dropping
whitespace-only text nodes entirely rather than collapsing them to one space, re-verified
against both the Law test and the existing entity/whitespace test
(`test_source_shape_matching_tolerates_canvas_html_normalization`).

Commit: see `git log` on `dev` (this correction lands as a new commit on top of `149c2a4`).

**Correction 2:** `style` value was still compared literally; Canvas's sanitizer reformats
inline CSS and may prune a disallowed property, so `canonical_html` now keeps a `style`
attribute's presence but renders its value as empty for comparison, leaving every other
attribute, tag, and visible-text check unchanged; the Law test gained a parametrized case
for a reformatted/pruned style plus its own changed-visible-text negative, and the
named gate is 299 passed, 0 failed.

## Live acceptance, continued (senior)
- Fixes landed live: `35b53c5` (dash normalization), `100c5ab` (visible-text
  description check), `759fe86` (family-link read-back against the normalized save).
  The resume then created, published and verified Red/Blue, attached all three sources
  to the module, and created and activated the bridge. All of it was verified with
  `verify_live`.
- The teacher then edited Canvas Live (renamed sources to `ECR Prep 3: …`, per-class due
  dates). The next resume failed at `source_final_shape_unverified`, because
  `_verified_family_sources` re-checks the exact title, the top-level `due_at`, and "no
  overrides" against the original push.

## Correction 3 (senior decision): teacher edits in Canvas Live are authoritative
After CE's own verified create or publish, titles, due/unlock/lock dates, and
per-section overrides belong to the teacher. Recovery (resume, family tail, family
link) re-checks only CE-owned invariants: exact id exists, published, `grading_type`
points, points and assignment group consistent across tiers, `omit_from_final_grade`
true, and `post_to_sis` false. Create and publish postconditions keep verifying CE's own
writes as they happen. The saved family link records the sources' current live titles.
Ids stay the authority.

## Correction 3 execution result

**GREEN.** In `differentiated_bridge._verified_family_sources`, the unrestricted-tier
branch now drops `name`/`due_at` from `expected` and skips the override fetch entirely
(the restricted/group branch is byte-for-byte unchanged); still enforces exact id,
`published`, `grading_type: points`, `omit_from_final_grade: true`, `post_to_sis: false`,
and cross-source points/assignment-group consistency. `execute_family_tail` and
`reconcile_family_tail` both now read `source_titles` for the saved/expected registration
from the verified rows' live `name` (not the originally pushed `source_titles`), so the
two stay in sync and a renamed family doesn't self-mismatch on the next reconcile.

**Tests:** `api/tests/test_differentiated_bridge.py` (new) -- Law: an unrestricted
source's rename/cleared-due-date/added-overrides never blocks and overrides are never
even fetched; Law (parametrized): `omit_from_final_grade=false` or `post_to_sis=true`
still fails `source_final_shape_unverified`. `test_assignment_tier_operation.py` gained
`test_family_tail_completes_after_teacher_edits_and_saves_live_titles` -- a full
create-then-teacher-edits-then-resume through the real adapter, asserting the tail
completes with no second create and the saved bridge registration's `source_titles`
match the live (renamed) names. One line added to
`docs/reference/operation-ledger-module-map.md` stating the rule.

Named gate + `api/tests/platform_services/config/test_sis_grade_bridge.py` +
`test_differentiated_bridge.py`: **304 passed, 0 failed.** Also reran
`test_routines_builtin_sis_grade_bridge.py`, `test_sis_grade_bridge.py`,
`mcp_server/test_sis_grade_bridge_tools.py` (11 passed): no regressions.
