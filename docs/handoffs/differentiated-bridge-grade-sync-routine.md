# Differentiated bridge grade sync routine

Status: READY FOR EXECUTION
Owner: one implementation executor
Risk: high — scheduled Canvas grade/status writes

## Objective

Make registered differentiated-family bridge reconciliation a built-in Canvas Expert
Routine. A teacher can run it on demand or explicitly enable its local schedule. For every
active student, Canvas Expert derives the bridge value from whichever registered source
assignment carries the final posted grade, without treating the student's tier membership as
grade authority. The routine updates only the registered bridge in Canvas Live; the teacher
reviews there and remains solely responsible for Canvas Grade Sync to the SIS.

This is a narrow extension of the existing `gradebook.sis_bridge` Operation Ledger path. Do
not create another grade-write path, family-discovery mechanism, scheduler, or UI workflow.

## Teacher-visible outcome

- Routines shows one built-in row, **Differentiated bridge grade sync**, marked as writing
  to Canvas, disabled by default, and using the existing interval and Run controls.
- A run processes all registered differentiated families in Current courses. Registrations
  provide exact source and bridge assignment IDs; names and color tags are not rediscovered.
- The run reports one concise line per family and an aggregate last-run summary. Counts
  distinguish copied scores, missing zeroes, cleared prior routine values, already matching
  rows, held rows, and conflicting rows requiring Attention.
- A repeated run against unchanged Canvas state performs no grade/status mutation.
- Canvas Expert never starts, requests, or monitors Canvas Grade Sync or any SIS operation.

## Locked decisions

### Routine registration and triggers

- Add built-in routine id `sis_bridge_sync`, label `Differentiated bridge grade sync`,
  `writes=True`, and default `{enabled: false, every_hours: 24, params: {}}`.
- Reuse the current Routines registries and their three existing triggers: manual Run,
  launch catch-up, and the 30-minute due check. The schedule remains machine-local.
- Enabling the default-off write routine is the teacher's specific authorization for
  scheduled runs. Clicking its Run control authorizes that manual run.
- Process every student-free bridge registration returned for every Current course. Do not
  add course, family, tier, or title selectors in this batch.
- Reuse the generic Routines row and report rendering. No template, CSS, navigation, or new
  confirmation surface is needed.

### Grade resolution

- The exact registered source assignment IDs are the complete candidate set. Support every
  registered family of two or more sources; do not hardcode Blue, Silver, Red, a source
  count, title punctuation, or public tags.
- Resolve a target for every active student by inspecting final Canvas submissions across
  all registered sources. Current override/tier membership may still be verified as family
  structure, but it does not select the grade and overlap does not by itself select or reject
  a value.
- A single posted final numeric score is copied as the same number of points. Multiple posted
  final numeric scores that agree resolve to that same value. Numeric zero is populated.
- A single posted final excused state resolves to excused. Multiple agreeing excused states
  also resolve to excused. Numeric versus excused, or differing numeric finals, is a
  `conflicting_final_values` Attention row: leave that student's bridge unchanged while
  continuing other safe rows.
- A hidden or unposted final source grade is held: do not reveal it, copy it, replace it with
  zero, clear the bridge, or include its value in a teacher-visible result.
- A submitted-but-ungraded source remains blank. Before the verified bridge `due_at`, a
  student with no final source score remains blank. In either case, clear the bridge only
  when its current value still exactly matches a value previously written for that student by
  this routine. Never clear a teacher edit or a value whose provenance is uncertain.
- After the verified bridge `due_at`, a student whose source state is blank, unsubmitted, or
  absent resolves to numeric `0` with Canvas late-policy status `missing`. A
  submitted-but-ungraded or hidden/unposted grade remains held rather than becoming zero.
- Family sources and bridge must retain equal points possible. No scaling, percentages,
  rounding, or score invention beyond the locked past-due missing zero is allowed.

### Operation, idempotency, and privacy

- Extend the existing `gradebook.sis_bridge` adapter and its current
  `preview_sis_grade_bridge` / `apply_sis_grade_bridge` facade. The routine performs one
  frozen preview/apply cycle per registered family; it does not call Canvas submission writes
  directly.
- Baseline capture reads the exact registered assignments, active students, all exact source
  submissions, and current bridge submissions inside the token-holding app. CanvasMirror is
  context only and never authorizes a mutation.
- The frozen plan contains steps only for bridge coordinates whose target differs from
  current Canvas state. Already matching rows are no-ops. A later identical run therefore
  sends no Canvas mutation.
- Before apply, preserve the current full drift re-read. Every changed or cleared coordinate
  keeps the existing before-send checkpoint, request digest, exact postcondition, uncertain
  result handling, retry/reconciliation behavior, targeted mirror notification, and private
  Operation Ledger receipt.
- Use existing private Operation Ledger facts as routine-write provenance. Do not add student
  IDs, scores, memberships, or per-student provenance to synced settings, routine config,
  logs, assistant-facing reviews, or fixtures.
- The public preview and routine result expose only aggregate counts, warning codes, opaque
  operation coordinates, receipt/reference fields already allowed by the contract, and the
  registered bridge reference. Conflicts identify no student and reveal no score.
- One family's blocking drift or Attention result does not retarget, repair, or discover a
  replacement family. Report it and continue with other registered families when safe.

## Acceptance criteria

1. `sis_bridge_sync` appears through the existing Routines API as a built-in write routine,
   disabled at a 24-hour default, and both manual and scheduled dispatch use its one runner.
2. The runner visits only registered families in Current courses, performs one existing
   preview/apply cycle per family, and returns concise aggregate/per-family results without
   student data.
3. Across all registered sources and regardless of tier membership, one numeric final or
   multiple agreeing numeric finals copies exact points; one or multiple agreeing excused
   finals excuses the bridge.
4. Differing numeric finals or numeric-versus-excused finals leave that bridge coordinate
   unchanged, produce aggregate `conflicting_final_values` Attention, and do not prevent
   other unambiguous coordinates from reconciling.
5. Hidden/unposted grades are never copied or exposed. Submitted-but-ungraded work remains
   blank and is never converted to a missing zero.
6. Before bridge `due_at`, absent/blank/unsubmitted work remains blank. After bridge
   `due_at`, it becomes exactly `0` with `late_policy_status=missing`.
7. Clearing removes only a current bridge value proven to be an unchanged older write by
   this routine. A teacher-changed or provenance-unknown bridge value is left unchanged and
   reported as held/Attention.
8. Current bridge submissions are part of the frozen baseline and drift digest. Plans create
   write steps only for changed coordinates; an identical later run sends zero Canvas
   mutation calls and reports already-matching/no-effect counts.
9. Score, missing-status, excuse, and clear mutations retain write-ahead checkpoints, exact
   postconditions, uncertain-send non-replay, targeted mirror notification, and a private
   Operation Ledger receipt.
10. Registration, source/bridge shape, equal-points, exact-ID, Current-course, and no-SIS
    boundaries remain enforced. The routine does not create, adopt, repair, rename, or move
    any family object.
11. The durable contract and guide describe the routine, grade resolution, past-due zero,
    held/conflicting behavior, privacy, review, recovery, and teacher-owned Canvas Grade
    Sync. Current API/Routines documentation no longer describes bridge projection as only a
    conversational assistant action.
12. The named gate, compile check, and `git diff --check` pass with no live Canvas call and
    no unrelated change.

## Explicit non-goals

- No Canvas Grade Sync/Skyward call, credential, polling, browser automation, or SIS status.
- No family discovery by title, unregistered-family adoption, registration migration,
  structure repair, source reassignment, or differentiation-tier management.
- No hardcoded three-color family, public-tag change, authoring-contract change, or change to
  family creation/module placement.
- No score scaling, rubric/comment/feedback/attempt copy, New Quiz item write, or ordinary
  PowerGrader write change.
- No new scheduler, queue, daemon, routine coordinator rollout, persistence store, registry,
  or generic grade-sync framework.
- No custom-routine SDK behavior change and no new Routines page layout or controls.
- No full API/engine suite and no live Canvas test.

## Authorized scope and insertion points

- `api/operation_ledger/adapters/sis_grade_bridge.py` — grade resolution, current bridge
  comparison, provenance-aware clear planning, aggregate review, step requests and exact
  postconditions only.
- `api/sis_grade_bridge.py` — only the smallest aggregate/facade additions needed for the
  built-in runner to use the existing preview/apply cycle and report safe counts.
- `api/webui/routes/routines_builtin.py` — one tight runner over Current-course
  registrations and existing bridge facade.
- `api/webui/routes/routines.py` — register the built-in definition and runner only.
- `api/README.md` — the SIS grade bridge paragraph only.
- `api/webui/README.md` — the Routines section and built-in table only.
- `api/custom_routines/AUTHORING.md` and `api/custom_routines/README.md` — reserved built-in
  id list only if needed to keep the collision guidance accurate.
- `docs/contracts/sis-grade-bridge-contract.md` — sections 6 through 8 only.
- `docs/guides/sis-grade-bridges.md` — sections 5 through 10 only.
- `docs/reference/operation-ledger-module-map.md` — SIS bridge execution owner, safety
  boundary, and directly named test routing only.
- `api/tests/test_sis_grade_bridge_operation.py`
- `api/tests/test_sis_grade_bridge.py`
- `api/tests/mcp_server/test_sis_grade_bridge_tools.py` only if the existing safe aggregate
  facade shape changes.
- `api/tests/test_routines_scheduler.py` for registration/scheduled dispatch coverage.
- `api/tests/test_routines_builtin_sis_grade_bridge.py` may be added for the single built-in
  routine example and its safe result contract.

No other files are authorized. Do not add a test class; extend the existing bridge harness
or use functions/fixtures.

## Required references

The executor reads this brief, then only:

- `docs/reference/project-state.md` — full short file;
- `api/custom_routines/AUTHORING.md` — runner contract, decorator/default metadata, write
  marker, and trigger behavior only;
- `docs/contracts/sis-grade-bridge-contract.md` — sections 1, 2, 4, 6, 7, and 8;
- `docs/guides/sis-grade-bridges.md` — sections 1 and 4 through 10;
- `docs/reference/operation-ledger-module-map.md` — Execution Owners, Shared Support, Safety
  Boundaries, and Test Routing;
- `api/webui/README.md` — Routines and Custom routines sections only;
- the runtime owners and direct tests listed in Authorized scope.

Do not read archived handoffs, other module maps, CanvasMirror vision documents, DataForge,
or unrelated routine, gradebook, PowerGrader, New Quiz, or UI owners.

## Test taxonomy

- **Law:** source resolution ignores tier membership; agreeing finals converge; conflicts,
  hidden/unposted grades, submitted-ungraded work, due-time zeroes, and provenance-safe clears
  obey the locked rules; uncertain mutations are never replayed.
- **Contract:** the aggregate preview/result remains student-free, changed-only steps cover
  score/excuse/missing/clear actions, and the built-in registry exposes the locked default-off
  write metadata.
- **Example:** one routine run across a registered family copies one score, adds one due
  missing zero, skips matching work, and reports aggregate results through the existing
  Routines runner shape.

Do not duplicate the same invariant through wrapper tests.

## Preflight and stop conditions

Before writing, confirm:

- branch `dev` is based on `f79365a`, the worktree contains only this committed brief, and it
  is the sole file in `docs/handoffs/`;
- differentiated delivery still saves exact source IDs, source titles, bridge ID, and bridge
  structural digest without student data;
- the existing adapter remains the sole `gradebook.sis_bridge` grade-write owner and the
  current Routines registries drive all three triggers;
- Canvas submission responses at the existing live seam provide enough posted/hidden state,
  bridge values/status, and timestamps to implement the locked distinctions;
- prior routine-written bridge values can be proven from existing private Operation Ledger
  state without adding another durable store; and
- all named existing tests are present.

Stop RED before implementation if posted-versus-hidden state or prior-routine provenance
cannot be determined reliably, if a safe clear cannot be expressed and postcondition-checked
through the existing Canvas submission endpoint, if the routine would need to bypass the
Operation Ledger, if the exact registered family cannot cover the active-student resolution,
or if another public architecture/contract must expand. Stop YELLOW for one unavailable
required check or one senior decision. Preserve unrelated worktree changes.

## Named focused gate

```powershell
py -m pytest -p no:randomly `
  api/tests/test_sis_grade_bridge_operation.py `
  api/tests/test_sis_grade_bridge.py `
  api/tests/mcp_server/test_sis_grade_bridge_tools.py `
  api/tests/test_routines_scheduler.py `
  api/tests/test_routines_builtin_sis_grade_bridge.py
```

Run `py -m compileall -q api/operation_ledger/adapters/sis_grade_bridge.py
api/sis_grade_bridge.py api/webui/routes/routines_builtin.py api/webui/routes/routines.py`
and `git diff --check`. No browser render is required unless a teacher-facing HTML/CSS/JS
file changes. No live Canvas test is authorized. Do not run the full suites unless the
focused gate reveals unexpected coupling.

## Execution result

**GREEN**

- Commit: none (implementation left uncommitted for senior acceptance).
- Changed files: `api/operation_ledger/adapters/sis_grade_bridge.py`,
  `api/sis_grade_bridge.py`, `api/webui/routes/routines_builtin.py`,
  `api/webui/routes/routines.py`, the routed API/custom-routine/bridge contract-guide/module-map
  documentation, `api/tests/test_sis_grade_bridge_operation.py`, and the new
  `api/tests/test_routines_builtin_sis_grade_bridge.py`.
- Named gate: 31 passed in 10.28s with `-p no:randomly`; no live Canvas call.
- Compile gate: passed for all four named runtime owners.
- `git diff --check`: passed (Git emitted only the repository's CRLF conversion notices).
- Acceptance: the default-off write Routine uses the existing registry and one frozen
  `gradebook.sis_bridge` preview/apply cycle per registered Current-course family. Resolution
  scans every registered source independent of tier membership; agreeing posted finals,
  conflicts, hidden grades, submitted-ungraded holds, due-time missing zeroes, changed-only
  plans, private-ledger provenance clears, drift, exact postconditions, uncertain-send
  non-replay, safe aggregate reporting, and blocked-family continuation are covered.
- Deviations: none.
- Unresolved decisions: none.
