# Mutation Reconciliation Map

Routing scope: read this only when the active handoff is Batch 6/7 work on
Canvas mutation ownership or targeted reconciliation. It groups
[`docs/contracts/canvas-transport-owners.json`](../contracts/canvas-transport-owners.json)
(the machine authority — every call-level fact, classification, scope, and
reconciliation state lives there, keyed by path + qualified symbol + detector
+ call_index) into the next exact vertical families. This document does not
duplicate those facts; it names which family is already covered and which
gaps are next. **No gap named here is GREEN.**

`api/tests/test_canvas_mutation_ownership.py` enforces the JSON against the
live `api/` tree: any unlisted mutation-shaped call, any stale listed owner,
any duplicate key, or any out-of-vocabulary classification/scope/state fails
the suite.

## Current totals (from the JSON, 2026-09-07)

- **53 owners total.**
- By classification: `canvas_mutation` 41, `canvas_read_acquisition` 5,
  `canvas_upload` 2, `generic_transport_internal` 2, `canvas_mutation_native` 1,
  `diagnostic_probe` 1, `external` 1.
- By reconciliation state: `none` 6, `n/a` 20, `targeted` 12, `invalidate` 15.
- By scope (an owner may touch more than one): `private.submissions` 9,
  `catalog.assignments` 9, `none` 7, `catalog.modules` 6,
  `focused_evidence` 6, `new_quiz.metadata` 5, `private.assignments` 5,
  `private.groups` 5, `catalog.pages` 2, `private.submission_comments` 2,
  `new_quiz.responses` 1. `catalog.assignment_groups`, `private.roster`, and
  `unknown` are currently unused (no live mutation touches them).

## Vertical families

### 1. Grades, comments, and curves (`private.submissions`, `private.submission_comments`)

**Covered (targeted):** PowerGrader's two grade-push owners —
`api/powergrader/session_actions.py push_grades` and
`api/powergrader/autopush_executor.py run_autopush_for_session` — converge
through `_notify_write_through` in `api/webui/routes/powergrader.py` (and, for
the scheduled-autoscore path, an inline `notify_course_changed` in
`routines_powergrader.py`), which calls `mirror_service.notify_course_changed`
(a narrow per-course submissions delta refresh).

**Covered (targeted):** existing-grade adjustments are owned by
`api/operation_ledger/adapters/grade_adjustment.py` (`execute`). It writes only
`posted_grade`, verifies each readback, and calls
`mirror_service.notify_course_changed` after successful writes. The built-in
curve routine is a caller of this reviewed operation and does not own a Canvas
mutation.

The former console curve routes, routine writer, curve-event store, and dead
`gradebook.curve` adapter were retired together. No legacy curve-event data is
read or migrated.

**Covered (targeted) — SIS bridge:**
`operation_ledger/adapters/sis_grade_bridge.py` reads the current local
assignment/submission sync for discovery, preview, and drift checks. After an
approved apply it copies present numeric source grades/statuses to the
whole-course bridge, verifies each live write, then invokes
`mirror_service.notify_course_changed` once for the course. Missing local data
refuses without a live fallback; due dates, student coverage, overrides, and
module placement are not preview invariants. An ambiguous grade write is
reconciled only from its exact bridge-submission postcondition and is never
resent by guess.

### 2. Assignment/Quiz/Module/Page structure (`catalog.assignments`, `catalog.modules`, `catalog.pages`, `new_quiz.metadata`)

**Covered (invalidate):** after each successfully-applied ledger operation,
the central `operation_ledger.catalog_reconcile` hook marks the affected
whole catalog scope stale through `course_catalog.invalidate_scope`. The
kind-to-scope mapping is deliberately conservative: assignments and quizzes
invalidate `catalog.assignments` plus `catalog.modules`, quick assignments
invalidate assignments, and a page invalidates `catalog.pages` always plus
`catalog.modules` when it has a module placement. This whole-scope
stale-mark is intentional; Canvas remains truth and the next catalog refresh
refetches the collection. The ownership contract carries fourteen call owners
at `invalidate` on a `catalog.*` scope, two of them added on 2026-09-07 for
`catalog.pages`. It deliberately leaves `new_quiz.metadata` (including
`ensure_item`) at `none`; that has no invalidate in this unit.

**Pages (corrected 2026-09-07):** the v3 catalog carries a real `pages`
scope, projected to teachers through the `get_course_pages` MCP tool and the
web UI, and `course_catalog.INVALIDATABLE_SCOPES` accepts it. Earlier
revisions of this document recorded page bodies as having *no* catalog scope,
which was true of the pre-v3 vocabulary and stale afterwards. A page created
in Canvas by `PageAdapter.execute` now marks the local pages scope stale, so
`get_course_pages` does not keep reporting a pre-push record set until a
teacher refreshes the catalog by hand. `content.page` invalidates
`catalog.pages` unconditionally and reaches this through the same post-apply
hook; nothing in the push path itself changed.

**Differentiated Hub AssignmentForge:** one `content.assignment` operation creates
`catalog.assignments` plus one restricted `catalog.pages` object per supplied tier.
Its payload-sensitive scope includes assignments, modules (the hub may use ordinary
module placement), and pages; each page is reported from its checkpointed
`create_tier_page:<index>` step. Tag reads never fetch membership and do not invalidate
private group scopes.

The `gradebook.sis_bridge` adapter is also covered: approved bridge creation or
registration and grade writes map conservatively to `catalog.assignments`
through the central post-apply invalidation hook. Module placement is outside
the bridge-only path.

### 3. Per-student assignment facts (`private.assignments`)

**Deferred (bounded staleness, accepted 2026-07-19) — not an open gap:** tier
overrides (`assignment_tiered.py`), quiz overrides (`quiz_steps.py
create_override`), and extension/override adapters (`extension.py`) all create/update Canvas
assignment overrides with no mirror invalidate call. A senior audit
(2026-07-19) traced the actual staleness this causes and ruled it a deliberately
deferred, bounded limitation rather than a reconciliation task, on this evidence:

- **The mirror stores no assignment-override projection.** `normalize_assignment`
  (`api/mirror/store.py`) persists only base fields (single class-wide `due_at`,
  points, name, published, etc.) — no override objects, no `all_dates`, no
  per-student dates. So the `private.assignments` scope itself has nothing to
  reconcile. (The scope tag stays `private.assignments` because overrides are
  inherently per-student, not catalog data — confirmed against the JSON, not
  `unknown`.)
- **The only mirror footprint is `cached_due_date` on submission rows**
  (`normalize_submission`, `store.py`) — Canvas's per-student effective due date.
  One mirror-backed teacher surface reads it: the Student Report "Due date
  extended to X" line (`api/student_packet.py` `_info_blocks`). Every other
  override reader (late-catchup) reads **live**, so it is always correct.
- **The write-through delta hook cannot repair it, and this is why it is not
  simply wired like curves/grades.** `refresh_submissions_course_delta`
  (`api/mirror/sync.py`) refetches only rows `submitted_since`/`graded_since` the
  watermark; an override changes `cached_due_date` but neither timestamp, so the
  affected rows are never refetched. Only the periodic `full_pass` picks it up.
- **Severity is low and the window is bounded.** The report local path is gated on
  the freshness window (`mirror_serve_max_age_hours`, default 6h) advanced only by
  `full_pass`; outside it the report falls back to **live** Canvas and is correct.
  Worst case: a teacher grants an extension and immediately generates a Student
  Report within the window, which then shows the pre-override due date — a display
  blemish on one line, not a grade error.

Closing it would need new machinery not reused from any existing hook (a targeted
per-assignment submissions force-refetch, or a new whole-scope submissions
stale-mark). Given the low, bounded severity and the live fallback, that machinery
is deferred for 1.0beta. If reopened, verify these facts against code first.

### 4. Groups and membership (`private.groups`)

Course Info and the agent-facing `list_groups` tool retain read-only group-set and
name display from the private mirror. Canvas Expert no longer creates group sets or
edits memberships through Roster; the former `roster_canvas.py` and `roster_groups.py`
write routes were retired in Forge Batch 1 (three tiers, no student-to-tier knowledge).

### 5. Gradebook configuration (`gradebook.late_policy`)

The former `api/webui/routes/gradebook_policy.py apply_late_policy` owner and its
`gradebook.late_policy` scope were retired with the unlinked Gradebook console page.
Canvas owns late-policy settings directly; no Canvas Expert mutation owner or
reconciliation entry remains for this scope.

### 6. New Quiz responses (`new_quiz.responses`, `focused_evidence`)

Canvas Expert has no New Quiz item score or per-item feedback mutation. Existing
New Quizzes that need writing scores stop before packet creation, and future writing
portions use separate 100-point assignments.

**Read-acquisition, not mutation (by design):** the Student Analysis report-create
call in `new_quiz_fetch.py _create_report` and the native-file-resolution JWT/launch
exchange in `new_quiz_fetch.py _native_file_transport` issue HTTP POST but change no
Canvas content. They remain classified `canvas_read_acquisition`, reconciliation `n/a`.

### 7. Uploads, diagnostics, external, and generic transport (expected `none`/`n/a`)

- **Uploads** (`assignment_whole.py upload_course_file`, two calls: the
  `/files` init POST and the follow-up upload-URL POST): scope `none` is the
  documented correct state per spine 14.2 ("never trigger course-wide binary
  refresh"), not a gap.
- **External** (`openrouter_client.py score`): classified `external`, not a
  Canvas write. Listed explicitly (not excluded) because it aliases
  `requests.post` onto a local name (`http_post = requests.post`) rather than
  calling it directly — the scanner has a dedicated alias-assignment detector
  for exactly this shape.
- **Generic transport internals** (`webui/canvas_client.py _canvas_send`,
  `api/canvas.py request`): the shared low-level HTTP call each owner above
  ultimately runs through. Classified `generic_transport_internal` rather
  than silently excluded, per the brief's requirement.
- **Sandbox/demo CLI script** (`api/qf_pusher.py`, routed through `api/canvas.py`):
  this is a standalone experimental tool
  with zero CanvasMirror or operation-ledger integration — reconciliation
  `n/a` because no reconciliation concept applies to it, not because
  reconciliation was skipped.

## Batch 7 seeds (named gaps, in priority order)

1. **Submissions/comments** (family 1): reviewed existing-grade writes use
   `operation_ledger/adapters/grade_adjustment.py::execute`, which performs the
   targeted per-course refresh after verified writes. The routine caller uses
   this operation and therefore introduces no separate mutation owner.
2. **Catalog structure** (family 2): **covered 2026-07-19** for
   `catalog.assignments` and `catalog.modules` by the central ledger post-apply
   stale-mark hook (ten `none` → `invalidate` contract transitions), and
   extended to `catalog.pages` on 2026-09-07 (two more). `new_quiz.metadata`
   reconciliation remains outside this unit; a per-record
   merge remains a later refinement, not Batch 7 work.
3. **Per-student assignment facts** (family 3): **decided 2026-07-19 — deferred
   as a bounded, documented limitation, not an open gap.** Overrides/extensions
   have no mirror override projection; their only mirror footprint is
   `submissions.cached_due_date`, read by the Student Report extension line, with
   live fallback outside the freshness window. Low severity, and no existing hook
   repairs it (the delta watermark misses override-only changes). See family 3.
4. **Retired 2026-07-19 — Batch 8 done.** The duplicate ledger adapters
   (`roster_membership.py`, `roster_group_set.py`, `late_policy.py`, and
   `curve.py`) were confirmed dead: no non-test producer emitted their KINDs, and
   their live direct-route/routine siblings already existed (groups and late policy
   already reconcile). These were **Batch 8** retirements (Former Program 10), now
   complete.

## Batch 8 hygiene note (from the 2026-07-19 dead-path trace)

- **Done 2026-07-19:** the four dead ledger adapters
  (`curve.py`, `late_policy.py`, `roster_membership.py`, `roster_group_set.py`)
  have been deleted, their dedicated test files removed, their imports and
  registrations stripped from both `__init__.py` files, and their six contract
  owner entries pruned. The owner count went 56 → 50, and the full ownership
  test cross-checks pass.
- The generic `/api/operations/{kind}/prepare` endpoint accepts any registered
  KIND (gated only by `require_local_mutation`, not a kind allowlist). After the
  dead adapters are removed, consider allowlisting the KINDs the UI actually
  submits (`content.*`) so a stale KIND cannot be hand-invoked.

No entry in this document is `unknown`-scoped; the JSON contract carries zero
`unknown`-scope owners today (grep it directly rather than trusting this
count if the contract changes).
