# Brief: Remove the pointless freshness blockers (MCP reads)

**Target branch:** `dev` · **Status:** approved, in execution (2026-10-04)

## Objective

Four age/scope blockers on the MCP read surface protect nothing and cost the model a
refresh round-trip or a dead-end repair. Remove them so local data **serves with its age
labeled**, repairs are always **executable**, and the teacher is interrupted **only when
the agent is actually stuck**.

## Teacher decisions (recorded here)

1. Group names are not PII - freshness must not block group discovery.
2. The Previous-course catalog dead end must be fixed, not reworded away.
3. Teacher notification only when the agent is **blocked** (refresh failed) or
   **looping** (multiple refreshes in a short window). Age alone never escalates.
4. Age is metadata, not a gate: a projection that exists on disk serves regardless of
   `within_policy`.

## The four fixes

### F1 - Groups serve regardless of age
`api/mcp_server/tools.py::_roster_groups`

- **Now:** outside-policy freshness converts to `ok:false` ("A fresh local Canvas group
  mirror is required"); stale-without-timestamp also refuses.
- **Change:** serve whenever `state in {current, stale}` and `records` is a list -
  project names, attach the freshness envelope, attach `attention` as a **non-blocking
  hint** exactly as `_roster_sections` already does on the same data. This makes groups
  consistent with sections.
- **Still refuses:** `missing`/`unavailable` state, `records` not a list, malformed rows
  - data genuinely absent or broken, repair = `refresh_mirror`.
- **Edge (decided):** `state=stale`, `records=[]`, no timestamp → serve the empty
  labeled list, do not refuse.

### F2 - Make the catalog repair executable for Previous courses (Option A, decided)
`api/mirror/service.py::refresh_course_structure` and `::_run_structure_refresh`

- **Now:** both gate on `config.active_courses()`. But
  `_catalog_assignments`/`_catalog_modules` accept any **saved** course and their
  missing-catalog error instructs `refresh_mirror(structure_only=true)` → for a Previous
  course the instructed repair raises `"Course is not in Current courses."`
- **Change (Option A):** widen both to `config.saved_courses()`, following the existing
  `_run_course_refresh` pattern (active first, saved fallback). The catalog is
  student-free and read-only per `docs/contracts/course-catalog-contract.md`, and plain
  `refresh_mirror` already accepts saved courses - this aligns structure refresh with it.
- `get_course_content(kind="pages")` stays Current-scoped (it has its own
  `_course_gate_check` and never reaches the broken path).

### F3 - Attention action matches reality; teacher only on stuck/loop (3 refreshes / 120 s, decided)
`api/mcp_server/tools.py::_freshness_attention`, `::refresh_mirror`

- **Now:** action `ask_teacher_confirmation` while the reason says "refresh it yourself"
  - two contradictory instructions on every stale read.
- **Change:**
  - Rename the read-surface action to `refresh_mirror`; reason keeps the
    `structure_only=true` variant for catalog sources. After F1/F4 this is only ever
    attached to `ok:true` results - a hint, never an interruption.
  - **Blocked (the one `ok:false` exception to "hint only"):** the `failed` branch of `refresh_mirror` gains
    `attention: {action: "ask_teacher_confirmation", ...}` - the agent can't
    self-repair, so the teacher hears about it (point them at *Refresh course data* on
    the CanvasAgent page).
  - **Precedence:** if one result is both blocked and the 3rd call in the window, the
    loop attention wins (it is the stronger "stop retrying"); both tell the teacher.
  - **Looping:** per-course call log (module-level dict + injectable monotonic seam,
    matching the existing `_enqueue_sync`/`_wait_for_plan` seams). On the **3rd
    `refresh_mirror` for the same course within 120 s**, the response still performs the
    refresh but carries `ask_teacher_confirmation`: *"refreshed N times in ~2 minutes
    without settling - stop retrying and tell the teacher."* Sliding window; `syncing`
    timeouts count, since retry-loops are the failure mode.
- **Scope fence:** `api/operation_ledger/adapters/adapter_support.py::mirror_freshness_attention`
  and the grade-adjustment/attempts-grant `ask_teacher_confirmation` are
  **write-preflight** gates with deliberate "do not refresh automatically" wording -
  untouched.

### F4 - Stop age-gating reads that already loaded the data
`api/mcp_server/tools.py`: `get_roster`, `get_submissions`, `get_gradebook_snapshot`

- **Now:** `_mirror_roster_doc`/`_mirror_submission_bundle` load with
  `max_age_hours=None`, prove the projection is structurally sound, then
  `_freshness_attention` refuses it purely on age.
- **Change:** drop the refusal branch; serve with the full freshness envelope
  (`state`, `age_minutes`, `within_policy`, `policy_window_minutes`) plus the
  non-blocking `attention` hint. The loaders themselves need no change.
- **Keep** the existing default in `get_gradebook_snapshot`: a snapshot with no
  `_freshness` falls back to a `current` envelope built from `synced_at`, then the
  attention hint is computed from it.
- **Unchanged:** missing/malformed mirror still refuses via `_MIRROR_UNAVAILABLE_*`
  with the `refresh_mirror` repair; **no read ever falls back to live Canvas** (the
  `_forbid_live_reads` tests stay green); pseudonymization and the outbound scan run
  exactly as before.

## Non-goals

- Scoring preparation freshness (`mirror_refresh_needed`,
  `requires_teacher_confirmation`) - write-adjacent, has its own
  `use_existing_mirror` escape. (Confirmed out of scope.)
- Write-preflight freshness gates (operation-ledger adapters). (Confirmed out of scope.)
- Heartbeat/background sync behavior; pages Current-course scope; any live-fetch-on-read.

## Consequences to carry

| Item | Detail |
|---|---|
| **R3 law flips** | `api/tests/mirror/test_queries.py::test_mcp_r3_past_policy_window_asks_teacher_without_student_rows` pins "outside window → no student rows." This brief reverses that law **deliberately** (decision #4 above); rewrite to assert serve + labeled freshness. |
| **Tool docstrings** | `get_roster`/`get_submissions`/`get_gradebook_snapshot` say "a stale or missing mirror is refused" - becomes half-false. Reword to "stale serves labeled; missing refuses." Then per AGENTS.md: `TOOL_SCHEMA_VERSION` is already 79 at HEAD, so take **80**, regenerate the snapshot as `tool_schema_v80.json` under pytest (only the current snapshot stays on disk), re-measure `LISTING_BUDGET` (14,868) and `DESCRIPTION_BUDGET` (343). |
| **Server instructions** | Likely unchanged - "outside policy refresh_mirror yourself" stays true; avoids touching `INSTRUCTION_BUDGET` (2,303). Verify, don't force it. |
| **Docs** | `docs/mcp-server.md` L384–393: structure refresh scope now includes Previous courses; stale-read wording. Check `course-catalog-contract.md` and `canvasmirror-coordinator-contract.md` for Current-only phrasing. |

## Workstreams (file ownership)

- **W1 tests:** `api/tests/mcp_server/test_tools.py`, `api/tests/mirror/` only.
- **W2 schema/budgets:** `api/mcp_server/contract.py`, the schema snapshot,
  `api/tests/mcp_server/test_server_instructions.py` only.
- **W3 docs:** `docs/mcp-server.md`, `docs/contracts/course-catalog-contract.md`,
  `docs/contracts/canvasmirror-coordinator-contract.md` only.
- Lead owns `api/mcp_server/tools.py`, `api/mirror/service.py`, and the full-suite gate.

**Ask about:** any assumption in this brief that the code contradicts.

## Test gate

0. Before anything else: `py -m py_compile api/mcp_server/tools.py api/mirror/service.py`.
1. Focused: `py -m pytest api/tests/mcp_server api/tests/mirror api/tests/test_freshness_policy.py -p no:randomly -q`
2. Full before GREEN (crosses MCP + mirror + docs): `py -m pytest api/tests engine/tests -p no:randomly -q`
3. New tests: groups serve when stale/old; structure refresh succeeds on a saved
   Previous course; loop escalation at 3/120 s (fake clock); failed sync carries teacher
   attention; reads serve at `age_minutes=601` with `within_policy=false` labeled.

**Risk:** Medium - reverses a pinned freshness law but loosens no privacy boundary
(pseudonyms, scan, no-live-fetch all intact).

## Execution result

**YELLOW** (one decision for the teacher). Uncommitted on `dev`; no commit hash yet.

- **Changed:** `api/mcp_server/tools.py`, `api/mirror/service.py`, `api/mcp_server/contract.py`
  (schema 79 to 80), `tool_schema_v80.json` (v79 deleted), `test_contract.py`,
  `test_tools.py`, `api/tests/mirror/test_queries.py`, `docs/mcp-server.md`,
  `docs/contracts/course-catalog-contract.md`.
- **Gate:** `py -m pytest api/tests engine/tests -p no:randomly -q` gave 2125 passed, 1
  skipped, 1 failed. Budgets (`LISTING_BUDGET`, `DESCRIPTION_BUDGET`) unchanged.
- **Baseline failure (not from this work):**
  `api/tests/test_qf_pusher.py::test_new_engine_plan_is_unchanged_for_existing_files`
  fails identically on clean HEAD 62832d3 (golden plan mismatch).
- **Deviations:** a bad text edit left `get_gradebook_snapshot` with a syntax error
  (fixed, compiled, tests green). `test_contract.py` edited outside W2's file list for the
  version assertion. `canvasmirror-coordinator-contract.md` needed no change.
- **Decision (teacher, 2026-10-04):** committed as is on `dev`, not pushed. The qf_pusher golden
  failure is left for a separate fix. Next: investigate that failure, then accept and retire this brief.
