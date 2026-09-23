# Brief: Bridge scores use the highest score as canon

**Status:** Current. **Risk:** High (grade writes). The teacher reviews the diff
before push. **Senior:** Claude (Opus). **Executor:** Sonnet. **Branch:** `dev`.
**Baseline known failures:** `test_quick_fix_contract_and_version`; 4 in
`api/tests/mcp_server/test_server_instructions.py` (pre-existing at `b2261da`).

## Teacher outcome
I can add extra credit on either the bridge or a tier source. When CE syncs a
bridge, it keeps the highest score, never lowers a bridge score, never touches my
tier sources or my blanks, and holds excused/scored conflicts for me. Before any
write, I see a per-family summary.

## Authority
`docs/contracts/sis-grade-bridge-contract.md` §6, "Score reconciliation rule"
(written 2026-09-23, this batch). That rule is the spec. Don't reinterpret it.

## Current repo truth (verify in preflight)
- The per-student projection lives in `api/operation_ledger/adapters/sis_grade_bridge.py`:
  - source state: ~L1010–1047 (`score` = Canvas final score);
  - bridge state: `_bridge_submission_state` ~L1053;
  - `_resolve_final_target` ~L1063 holds differing source scores as a conflict;
  - entry actions `score` / `excuse` / `missing` / `clear` (`_grade_request` ~L1100,
    and the builder that emits `missing` and `clear`).
- Result counts: `api/sis_grade_bridge.py` `_result_projection` ~L893
  (`copied_scores`, `copied_excused`, `missing_zeroes`, `cleared_prior_values`).
- The scheduled routine (`api/tests/test_routines_builtin_sis_grade_bridge.py`) uses
  the same projection. The rule applies there too.

## Acceptance criteria
- **AC1 Highest wins.** target = max(numeric source `score`s, bridge `score`). Emit a
  `score` entry only if target > bridge score or the bridge is blank. Differing source
  scores are no longer a conflict.
- **AC2 Never lower, never touch sources.** No entry ever sets a bridge below its
  current score. No request targets a source assignment.
- **AC3 Blanks alone.** Remove the `missing` and `clear` actions from the projection
  entirely (a clean break, no setting). A blank source produces no entry.
- **AC4 Excused.** When every present source is excused and the bridge is blank, emit
  `excuse`. Any mix of excused and scored across sources and bridge is held, never
  written.
- **AC5 No late status copy.** A `score` entry writes `posted_grade` only. It doesn't
  set `late_policy_status` (the penalty is already in the score).
- **AC6 Preview summary.** The preview (`preview_sis_grade_bridge` and the
  reconciliation preview when it freezes grade entries) returns per family:
  `raises`, `already_canon`, `held`, and `held_students`, a list of **pseudonyms
  only**, resolved through CE's existing vault/pseudonym service. Never names or
  Canvas user ids. Apply/receipt counts are renamed to match: `raised_scores`,
  `copied_excused`, `held`. Remove `missing_zeroes` and `cleared_prior_values`.
- **AC7 Docs.** Update `docs/guides/sis-grade-bridges.md` (one short paragraph) and
  `docs/mcp-server.md` wherever the old counts are named. Plain-text `next` stays
  host-neutral.

## Non-goals
No change to family discovery, links, drift, freshness, push adapters, or tier
sources. No new MCP tools. No auto-refresh. No Web UI.

## Locked decisions
- The contract rule above is canon. Never write to sources.
- Pseudonyms come from the existing identity/pseudonym service. Don't add a new
  mapping.
- If any input schema changes (it shouldn't), bump once. Output-only field changes
  don't bump.

## Tests (taxonomy)
- **Law (parametrized, one test):** bridge 95 / source 90 → no write. Source 95 /
  bridge 90 → raise to 95. Two sources 80 and 92, bridge blank → 92. Source blank with
  bridge 70 → no entry. All sources blank → no entry. Source excused with bridge blank
  → excuse. Source excused with bridge 80 → held. One source excused and one scored →
  held. A late-penalized source `score` of 85 (entered 100) → writes 85, with no
  `late_policy_status`.
- **Law:** no generated request targets a source id, and no entry lowers a bridge.
- **Contract:** the preview summary shape (`raises`, `already_canon`, `held`,
  `held_students` pseudonym-only).
- Update the existing projection tests to the new rule. Don't keep the old
  missing/clear expectations.

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_operation.py api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_routines_builtin_sis_grade_bridge.py api/tests/test_operation_ledger.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py
```

## Safety
AGENTS.md guardrail 7: tests only, run no `api.*` code outside pytest, start no servers,
and don't touch OneDrive or `%LOCALAPPDATA%`. Synthetic students and scores only.
This is high risk: don't push. The teacher reviews the diff.

## Stop conditions
Stop if pseudonym resolution for `held_students` would need a new store or would expose
Canvas ids, if the routine path diverges from the adapter projection, or if a setting
or another caller depends on `missing` or `clear`.

## Execution result
_(Executor fills in.)_
