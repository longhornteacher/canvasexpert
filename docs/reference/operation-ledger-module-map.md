# Operation Ledger Module Map

Routing scope: open this map only when the active handoff touches a content
operation-ledger adapter, then use the relevant section. It is not global executor context.
These are high-risk Canvas write flows.

## Facades

- `api/operation_ledger/adapters/assignment.py` — `AssignmentAdapter` payload build,
  digest, target verification, baseline/drift, review shaping, retry/reversal, and
  compatibility seams for `resolve_assignment_groups`, `_autoscore_queue`,
  `_validate_printable_pdf`, `_upload_course_file`, `_allowed_printable_roots`, and
  `requests`.
- `api/operation_ledger/adapters/quiz.py` — `QuizAdapter` payload build, digest,
  target verification, baseline/drift, review shaping, retry/reversal, and the
  compatibility seam for `resolve_assignment_groups`.
- `api/operation_ledger/adapters/page.py` — page create/reconcile facade; still owns
  Page-specific module-item behavior because Canvas page module attachment semantics
  differ from Assignment-type items.
- `api/operation_ledger/adapters/rubric.py` — rubric create/student-page facade.
- `api/operation_ledger/adapters/quick_assignment.py` — quick-assignment create/reconcile facade.

## Execution Owners

- `api/operation_ledger/adapters/assignment_whole.py` — whole-class assignment create,
  printable upload, module attachment, autoscore scheduling, and whole reconcile.
- `api/operation_ledger/adapters/assignment_tiered.py` — color-suffixed assignment
  create, override creation, autoscore scheduling, shared family-tail dispatch, and
  tiered reconcile.
- `api/operation_ledger/adapters/quiz_whole.py` — whole-class quiz coordinator and reconcile.
- `api/operation_ledger/adapters/quiz_differentiated.py` — differentiated quiz
  coordinator, extra-time bucket handling, variant failure-state policy, shared
  family-tail dispatch, and reconcile.
- `api/operation_ledger/adapters/quiz_steps.py` — shared quiz write-ahead helpers for
  quiz creation, item creation, assignment restriction, override creation, assignment
  patch verification, and Assignment-type module attachment.
- `api/operation_ledger/adapters/sis_grade_bridge.py` — registered-family exact-ID
  verification, all-source posted-final resolution independent of tier membership,
  bridge comparison, provenance-safe clear planning, aggregate review, grade projection,
  and ambiguous grade-write reconciliation for `gradebook.sis_bridge`. It owns no family
  discovery, structure repair, registration, or SIS-sync request.

## Shared Support

- `api/operation_ledger/adapters/adapter_support.py` — shared content-adapter step/result
  primitives: ordered projection for explicit orders, prepend/ensure/replace helpers,
  module-id recovery, outbound-marker detection, scalar-to-list normalization,
  uncertain transport classification, and standard result shaping.
- `api/operation_ledger/adapters/module_placement.py` — shared Canvas Assignment-type
  module find/create/attach behavior for Assignment and Quiz flows only.
- `api/operation_ledger/adapters/assignment_groups.py` — canonical safe group-resolution
  helper used by tiered assignments and differentiated quizzes.
- `api/operation_ledger/adapters/differentiated_bridge.py` — one shared owner for
  public-tag normalization, source/bridge shape verification, end-of-day bridge due
  time, runtime Dashboard instructions, bridge create/activate, bridge-only module
  placement, final family verification, and student-free registration.

## Safety Boundaries

- Do not change step keys, checkpoint timing, `before_send` ordering, request digests,
  failure classification, or returned result keys without updating the durable contract
  and the high-risk adapter tests together.
- Keep Assignment/Page module-item behavior separate. `module_placement.py` is only for
  Canvas Assignment-type module items.
- Differentiated sources never call module placement. The shared family helper is the
  only differentiated owner allowed to attach the bridge assignment.
- Grade projection starts from an exact registered family and may only write eligible
  final submission scores/statuses, past-due missing zeroes, or provenance-proven clears
  to that bridge. Hidden, submitted-ungraded, conflicting, and teacher-changed rows remain
  held. It cannot change family structure or trigger Canvas Grade Sync.
- Preserve facade monkeypatch seams when moving code. Existing tests still patch the
  facade modules rather than every leaf helper.
- No live Canvas verification belongs here. Use mocked adapter tests only.

## Test Routing

- `api/tests/test_assignment_operation.py`
- `api/tests/test_assignment_tier_operation.py`
- `api/tests/test_printable_attach.py`
- `api/tests/test_quiz_operation.py`
- `api/tests/test_quiz_tier_operation.py`
- `api/tests/test_page_operation.py`
- `api/tests/test_quick_assignment_operation.py`
- `api/tests/test_rubric_operation.py`
- `api/tests/test_operation_ledger.py`
- `api/tests/test_sis_grade_bridge_operation.py`
- `api/tests/test_routines_builtin_sis_grade_bridge.py`
- `api/tests/test_operation_routes.py`

## Source-size reports

Use [`tools/size_report.py`](../../tools/size_report.py) for current source-size
reports; this map intentionally does not maintain line-count snapshots.
