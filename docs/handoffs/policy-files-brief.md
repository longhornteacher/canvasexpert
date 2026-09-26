# Brief: grading policy and holidays as two plain workspace files

Status: ready for execution. Risk: medium (settings source for grade-affecting math; no new
Canvas write). Follow-up to `docs/contracts/grading-policy-contract.md` (built 2026-09-26).

## Objective

The teacher controls the grading policy and school holidays by editing two plain files in the
synced workspace. The Grading policy console panel and its settings keys are gone.

## Why

The `/gradebook` page that holds the panel is not linked from anywhere in the console, and the
teacher wants the simplest possible control: files in the sync folder.

## Read

- `AGENTS.md`
- `docs/contracts/grading-policy-contract.md` sections 3, 4, 5, 6
- `api/platform_services/workspace.py` (`library_root`, `library_folder`, `LIBRARY_SUBFOLDERS`)

## Locked decisions

1. **Files.** Both optional; neither is seeded or created by Canvas Expert.
   - `Library/Grading Policy.txt`: `key: value` lines, `#` starts a comment, blank lines
     ignored, keys case-insensitive. Keys `floor_percent`, `missing_percent`,
     `sweep_after_school_days`, all integers. One policy for every course.
   - `Library/Calendars/Holidays.csv`: each row is `start` or `start,end` or
     `start,end,name` (ISO dates; `end` blank or absent means one day; `name` ignored). A
     header row or any row whose first cell is not an ISO date is skipped. Ranges expand to
     every date from start to end inclusive.
2. **Loader.** In `api/grading_policy.py` (update its docstring: pure math plus these two file
   readers):
   - `load_policy(root=None) -> dict | None`: None when the file is absent. Present but
     invalid (a missing key, a non-integer, or out of range: `0 <= missing_percent <=
     floor_percent <= 100`, `1 <= sweep_after_school_days <= 60`) raises
     `GradingPolicyFileError` with a plain one-sentence message naming the problem.
   - `load_no_school_dates(root=None) -> list[str]`: sorted, deduplicated ISO dates; absent
     file means `[]`.
   - Paths via `workspace.library_root` / `library_folder`. Reads the file each call; no cache.
3. **Consumers switch to the loaders.**
   - `stage_scoring_results` (`api/mcp_server/tools.py`): policy from `load_policy`; a
     `GradingPolicyFileError` returns `{"ok": False, "code": "grading_policy_file_invalid",
     "error": <message>}` before staging.
   - The missing sweep discovery (`api/operation_ledger/adapters/missing_fill.py`): same
     loader; invalid file blocks with `grading_policy_file_invalid` and the message; absent
     file keeps `no_grading_policy`.
   - `api/freshness_policy.py` reads holidays from `load_no_school_dates`. Avoid the import
     cycle (`grading_policy` imports `freshness_policy.LOCAL_TIMEZONE`) with a function-local
     import in `freshness_policy`.
4. **Remove, no migration:**
   - `config.get_grading_policy` / `set_grading_policy`, `config.get_no_school_dates` /
     `set_no_school_dates`, and the `grading_policy` and `no_school_dates` entries in
     `SYNCED_KEYS`.
   - The Grading policy panel in `api/webui/templates/gradebook.html`,
     `api/webui/static/gradebook/grading_policy.js`, its load line, the `/api/grading-policy`
     and `/api/no-school-dates` routes, their tests, their route-contract entries, and the
     `api/webui/README.md` mention.
   - Any other code whose only purpose was the panel (for example the cached-late-policy
     warning helper), if nothing else uses it.
5. **Docs in the same commit.** `grading-policy-contract.md` section 4 rewritten for the two
   files (format, location, on-when-present, invalid-file behavior), with matching wording in
   sections 3, 5, 6; `docs/mcp-server.md` or `feedback-scoring-contract.md` only if they name
   the panel or the old settings keys. Keep the Canvas-settings advice (missing-submission
   policy off; lowest possible grade at or above the missing value) in section 4 as plain
   guidance.

## Non-goals

Retiring the rest of the unlinked `/gradebook` page; seeding either file; per-course policies;
a file watcher or cache; any MCP schema change; any change to the mark, late-day, or sweep
math.

## Preflight (stop if false)

- `git status` shows only `stubbed-workspace/` untracked; HEAD is the commit that added this
  brief.
- `config.get_grading_policy` and `config.get_no_school_dates` have only the consumers listed
  in decision 3 plus the panel routes and tests.
- The test conftest isolates the workspace root, so tests can write both files under a temp
  workspace.

## Acceptance criteria

1. With no `Grading Policy.txt`, Scoring Sessions post raw scores and the sweep refuses with
   `no_grading_policy`.
2. With the file containing the three keys (30, 20, 15), behavior matches the built contract.
3. A file with a missing key or `floor_percent: 10` and `missing_percent: 20` makes staging
   and the sweep preview refuse with `grading_policy_file_invalid` and a readable message.
4. `Holidays.csv` rows `2026-11-23,2026-11-27,Thanksgiving` and `2026-12-21` plus a header row
   yield those six dates; `freshness_policy`, suggested late days, and the sweep window all
   use them.
5. The panel, its script, routes, and the two settings keys no longer exist; `/gradebook`
   still renders.
6. The contract describes the files.

## Tests (per `AGENTS.md` taxonomy)

- Law, once: `load_policy` (absent is None; valid parses; each invalid case raises).
- Law, once: `load_no_school_dates` (criterion 4 rows, header, blank and junk rows).
- Replace the existing tests that set policy or dates through config with a shared conftest
  fixture that writes the files into the isolated workspace.
- Delete the panel route tests.

## Gate

```powershell
py -m pytest api/tests/test_grading_policy.py api/tests/test_missing_sweep.py api/tests/test_missing_sweep_operation.py api/tests/mcp_server api/tests/test_freshness_policy.py api/tests/test_gradebook_policy_routes.py api/tests/test_route_contract.py -p no:randomly
```

Then the full API suite once (2093 passed at the start of this brief's parent commit).

## Stop conditions

- The workspace root cannot be resolved from the named consumers without new plumbing.
- Removing the settings keys breaks a consumer not listed here.
- Any change would alter the mark, late-day, or sweep math, or the MCP schema.

## Execution result

(Executor fills in.)
