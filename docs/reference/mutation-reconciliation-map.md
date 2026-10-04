# Mutation Reconciliation Map

Routing scope: read this only when the active handoff is Batch 6/7 work on
Canvas mutation ownership or targeted reconciliation. It groups
[`docs/contracts/canvas-transport-owners.json`](../contracts/canvas-transport-owners.json)
(the machine authority: every call-level fact, classification, scope, and
reconciliation state lives there, keyed by path + qualified symbol + detector
+ call_index) into the next exact vertical families. This document does not
duplicate those facts; it names which family is already covered and which
gaps are next. **No gap named here is GREEN.**

`api/tests/test_canvas_mutation_ownership.py` enforces the JSON against the
live `api/` tree: any unlisted mutation-shaped call, any stale listed owner,
any duplicate key, or any out-of-vocabulary classification/scope/state fails
the suite.

## Ownership inventory

`docs/contracts/canvas-transport-owners.json` is the current inventory. Derive
counts and classifications from it rather than maintaining a second numeric snapshot.

## Vertical families

### 1. Grades, comments, and curves (`private.submissions`, `private.submission_comments`)

Scoring writes use `api/powergrader/session_actions.py` (`push_grades`), called
by `api/powergrader/scoring_apply.py`. The transport registry and current scoring
contract own verification and reconciliation behavior; no route callback is a
scoring authority. Feedback revision (`api/powergrader/feedback_revision.py`)
writes comments and comment attachments only, at reconciliation `none`. Score curve
rules (`set_score_curve_rule`) are local; they reach Canvas through a scoring stage or
a reviewed grade adjustment and have no Canvas writer of their own. Canvas owns
late-policy settings, and Canvas Expert has no mutation owner for them.

**Covered (targeted):** existing-grade adjustments are owned by
`api/operation_ledger/adapters/grade_adjustment.py` (`execute`). It writes only
`posted_grade`, verifies each readback, and calls `notify_course_changed` in
`api/mirror/service.py` after successful writes.

**Covered (targeted), SIS bridge:**
`operation_ledger/adapters/sis_grade_bridge.py` reads the current local
assignment/submission sync for discovery, preview, and drift checks. After an
approved apply it copies present numeric source grades/statuses to the
whole-course bridge, verifies each live write, then invokes
`notify_course_changed` once for the course. Missing local data
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
refetches the collection. `quiz_steps.ensure_item` deliberately stays at
`none`: `new_quiz.metadata` has no catalog reconciliation in this unit.

**Pages:** the catalog carries a real `pages` scope, projected through the
`get_course_content(kind="pages", ...)` MCP tool, and
`course_catalog.INVALIDATABLE_SCOPES` accepts it. A page created in Canvas by
`PageAdapter.execute` marks the local pages scope stale, so
`get_course_content(kind="pages", ...)` does not keep reporting a pre-push record
set. `content.page` invalidates `catalog.pages` unconditionally through the same
post-apply hook.

**Classic QuizForge (2026-09-29):** a `content.quiz` operation whose plan declares
`quiz_engine: "classic"` writes only through `quiz_classic.py _send` (create the
unpublished quiz, add questions, save settings, publish, and delete only a quiz the same
run created after a definitive question rejection), plus the existing
`quiz_steps.patch_assignment` and `module_placement` owners. A classic Hub also uses the
shared `tier_pages.py _send`. The kind maps to `catalog.assignments` and `catalog.modules`,
and a classic Hub adds `catalog.pages`, exactly like the AssignmentForge Hub. A classic quiz
is not `new_quiz.metadata`. Reconcile proves existence through the assignment id.

**Differentiated Hub AssignmentForge:** one `content.assignment` operation creates
`catalog.assignments` plus one restricted `catalog.pages` object per supplied tier.
Its payload-sensitive scope includes assignments, modules (the hub may use ordinary
module placement), and pages; each page is reported from its checkpointed
`create_tier_page:<index>` step. Tag reads never fetch membership and do not invalidate
private group scopes. The page and tag helpers live in `tier_pages.py`, shared with the
classic QuizForge Hub.

The `gradebook.sis_bridge` adapter is also covered: approved bridge creation or
registration and grade writes map conservatively to `catalog.assignments`
through the central post-apply invalidation hook. Module placement is outside
the bridge-only path.

### 3. Per-student assignment facts (`private.assignments`)

**Accepted limitation.** The mirror has no assignment-override projection. Its
submission `cached_due_date` can lag an extension because delta refresh selects
submission/grade timestamp changes; an override-only change does not advance those
timestamps. A full pass refreshes it.

`gradebook.attempts_grant` writes student overrides and per-student extensions with
live verification. Its whole-class attempts/date patches and overrides mark
`catalog.assignments` stale through the central post-apply hook; it makes no
submission refresh call. Any future consumer of cached effective due dates must
reassess freshness against current code before relying on them.

### 4. Groups and membership (`private.groups`)

The agent-facing `get_roster(include=["groups"], ...)` tool reads group-set and
group names from the private mirror. Canvas Expert creates no group sets and edits no
memberships, so this scope has no mutation owner.

### 5. New Quiz responses (`new_quiz.responses`, `focused_evidence`)

Canvas Expert has no New Quiz item score or per-item feedback mutation. Existing
New Quizzes that need writing scores stop before packet creation, and future writing
portions use separate 100-point assignments.

**Read-acquisition, not mutation (by design):** the Student Analysis report-create
call in `new_quiz_fetch.py _create_report` and the native-file-resolution JWT/launch
exchange in `new_quiz_fetch.py _native_file_transport` issue HTTP POST but change no
Canvas content. They remain classified `canvas_read_acquisition`, reconciliation `n/a`.

### 6. Uploads and generic transport (expected `none`/`n/a`)

- **Uploads** (`assignment_whole.py`: the `upload_course_file` `/files` init POST
  and the `upload_initialized_file` upload-URL POST): scope `none` is the
  documented correct state per spine 14.2 ("never trigger course-wide binary
  refresh"), not a gap.
- **Generic transport internals** (`api/platform_services/canvas_client.py _canvas_send`):
  the shared low-level HTTP call each owner above
  ultimately runs through. Classified `generic_transport_internal` rather
  than silently excluded, per the brief's requirement.

## Remaining gaps

1. **`new_quiz.metadata`** (family 2): item creates stay at `none`. A per-record
   catalog merge is a later refinement.
2. **Per-student assignment facts** (family 3): a bounded, documented limitation.
   Overrides and extensions have no mirror override projection; their only mirror
   footprint is `submissions.cached_due_date`, and the delta watermark misses
   override-only changes.

No entry in this document is `unknown`-scoped; grep the JSON contract directly to
confirm that if the contract changes.
