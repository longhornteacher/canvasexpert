# Brief: A lone assignment is not a bridge family

**Status:** Current. **Risk:** Medium (read-only discovery; it steers agent proposals).
**Senior:** Claude (Opus). **Executor:** Sonnet. **Branch:** `dev`.
**Baseline known failure:** `test_quick_fix_contract_and_version` (unrelated; leave it).

## Teacher outcome
When I ask the agent about SIS bridges, `reconcile_sis_grade_bridges` lists only real
differentiated families. Ordinary single assignments ("My Poem", "Quill Diagnostic",
"ECR Prep 1: Labeling the Parts") no longer appear as `incomplete` families with
`action:"create"`, which invites an agent to offer to build bridges for them.

## Evidence (live ELA 7 at 2026-09-23 16:53Z, catalog-backed)
About 12 matrix rows had `source_tiers:["unsuffixed"]`, `source_count:1`,
`bridge_count:0`, `identity_source:"title_fallback"`,
`reasons:["bridge_missing","family_link_missing","two_source_threshold_not_met"]`, and
`action:"create"`. Real families (linked, or with ≥1 tag-suffixed source) were
classified correctly.

## Acceptance criteria
- **AC1.** `reconcile_sis_grade_bridges` omits a discovered row when **all** of these hold:
  identity is `title_fallback` (no saved registration); every member is unsuffixed (no
  configured tier tag); there is no bridge candidate; and the source count is under 2.
- **AC2.** These are still reported exactly as today:
  - any row with a saved registration (`family_link`);
  - any row with ≥1 tag-suffixed source, including a lone `X - Silver` (a
    suspicious partial family);
  - any row with a bridge candidate;
  - `title_mismatch_suspected` and `bridge_candidates_ambiguous` rows.
- **AC3.** The result carries a count, `omitted_single_assignments: <int>`, so the agent
  knows rows were dropped. No ids, no titles.
- **AC4.** `preview_sis_grade_bridge_reconciliation` for an omitted title still refuses
  with `differentiated family was not discovered` (behavior unchanged).

## Non-goals
No change to family matching, normalization, repair plans, apply, or tier-tag logic.
No Canvas calls. No Web UI.

## Locked decisions
- Filter in `api/sis_grade_bridge.py` `reconcile_sis_grade_bridges`, after
  classification. Don't change `differentiated_bridge.discover_families` (the push path
  uses it too).
- Adding the `omitted_single_assignments` field is additive. If the MCP output schema
  snapshot covers this result shape, bump it once and keep the registry, snapshot,
  inventory, and `docs/mcp-server.md` in sync. If it doesn't, no bump.

## Scope
`api/sis_grade_bridge.py`, `api/tests/test_sis_grade_bridge_reconciliation.py`,
`docs/guides/sis-grade-bridges.md` (one sentence), and, only if needed, the MCP
schema files.

## Tests (taxonomy)
- **Law:** one parametrized test. A lone unsuffixed assignment → omitted and counted. A
  lone `X - Silver` → kept. A linked single → kept. An unsuffixed single with a bridge
  candidate → kept.
- Keep the existing reconciliation tests green.

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_sis_grade_bridge_operation.py api/tests/test_routines_builtin_sis_grade_bridge.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py
```

## Safety
AGENTS.md guardrail 7: tests only, run no `api.*` code outside pytest, start no servers,
and don't touch OneDrive or `%LOCALAPPDATA%`. Use synthetic fixtures only.

## Stop conditions
Stop if the filter would need to change `discover_families`, if a routine or other
caller depends on the incomplete single rows, or if the schema question is ambiguous.

## Execution result
_(Executor fills in.)_
