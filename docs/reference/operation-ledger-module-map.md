# Operation Ledger Module Map

Routing scope: open this map only when the active handoff touches a content
operation-ledger adapter, then use the relevant section. It is not global executor context.
These are high-risk Canvas write flows.

The Operation Ledger is a runtime safety boundary used by both the connected agent and
retained control-console paths. This map documents implementation ownership, not a browser
preview or authoring mandate; host agents own their own presentation of semantic review
results.

## Facades

- `api/operation_ledger/adapters/assignment.py` — `AssignmentAdapter` payload build,
  digest, target verification, baseline/drift, review shaping, retry/reversal, and
  compatibility seams for `_autoscore_queue`, `_validate_printable_pdf`,
  `_upload_course_file`, `_allowed_printable_roots`, and
  `requests`.
- Assignment preparation freezes generated printable hashes and teacher attachment
  names, labels, private paths, and hashes. Attachment files are resolved only under
  the configured workspace `To Review/Attachments/` root. The adapter's rendered
  Canvas HTML contains specific attachment/printable link slots; after review, apply
  may fill only those slots from checkpointed Canvas file IDs. The final outbound
  content request is write-ahead recorded after URL binding.
- `api/operation_ledger/adapters/quiz.py` — `QuizAdapter` payload build, digest,
  target verification, baseline/drift, review shaping, and retry/reversal. A plan that
  declares `quiz_engine: "classic"` branches to `quiz_classic.py` at payload, digest,
  baseline, drift, review, execute, and reconcile; every other plan is untouched.
- `api/operation_ledger/adapters/page.py` — page payload, teacher attachment upload
  steps and links, page create/reconcile, and Page-specific module-item behavior
  because Canvas page module attachment semantics differ from Assignment-type items.
- `api/operation_ledger/adapters/quick_assignment.py` — quick-assignment create/reconcile facade.

## Execution Owners

- `api/operation_ledger/adapters/assignment_whole.py` — whole-class assignment create,
  printable and attachment upload steps, module attachment, autoscore scheduling, and
  whole reconcile.
- `api/operation_ledger/adapters/assignment_tiered.py` — differentiated family source
  assignment-draft creation, shared attachment uploads, each tier's printable upload,
  and exact-ID tiered reconcile. It performs no roster/group, override, module, or
  family-tail work.
- `api/operation_ledger/adapters/assignment_hub.py` — sequences the tier pages and
  one whole-class assignment for Differentiated Hub AssignmentForge, over `tier_pages.py`.
- `api/operation_ledger/adapters/tier_pages.py` — the shared Hub page owner (AssignmentForge
  Hub and classic QuizForge Hub): restricted tier-page create, visibility restriction, tag
  assignment, fail-closed publish and compensation, link substitution, exact-step page
  reconciliation, the frozen review `hub` block, and the live tag read with its
  title-collision refusal. It knows tier to tag, never tag membership.
- `api/operation_ledger/adapters/quiz_classic.py` — Classic Quiz delivery for
  `content.quiz` (`quiz_engine: "classic"`): Hub payload rendering, review, and the
  checkpointed steps `create_quiz:0`, `create_question:0:<index>`, `save_quiz:0`,
  `patch_assignment:0`, `attach_module:0` (a `Quiz`-type module item), `publish_quiz:0`, and
  `rollback_quiz:0`, plus reconcile. Existence is proven by the quiz's assignment id, never
  the quiz GET alone (a deleted classic quiz still answers 200). Its one Canvas write
  helper, `_send`, is the sole mutation owner in the transport registry.
- `api/operation_ledger/adapters/quiz_whole.py` — whole-class quiz coordinator and reconcile.
- `api/operation_ledger/adapters/quiz_differentiated.py` — differentiated quiz
  coordinator, extra-time bucket handling, variant failure-state policy, shared
  family-tail dispatch, and reconcile.
- `api/operation_ledger/adapters/quiz_steps.py` — shared quiz write-ahead helpers for
  quiz creation, item creation, assignment restriction, override creation, assignment
  patch verification, and Assignment-type module attachment.
- `api/operation_ledger/adapters/sis_grade_bridge.py` — linked-family exact-ID
  verification, all-source posted-final resolution independent of tier membership,
  bridge comparison, provenance-safe clear planning, aggregate review, grade projection,
  and ambiguous grade-write reconciliation for `gradebook.sis_bridge`. It owns no family
  discovery, structure repair, family-link repair, or SIS-sync request.
- `api/operation_ledger/adapters/grade_adjustment.py` — mirror-backed existing-grade
  baseline, assignment drift check, per-student prior-score guard, posted-grade
  write/readback, uncertainty reconciliation, and private receipt entries for
  `gradebook.grade_adjustment`.
- `api/operation_ledger/adapters/attempts_grant.py` — `gradebook.attempts_grant`: the
  per-kind transport table (`KIND_TABLE`: regular, Classic Quiz, New Quiz), the live
  assignment classification read with base dates, per-step `observe` re-reads shared by
  execute and reconcile, the single `_send` write owner, override adoption on resume, and
  pseudonym-only receipt rows. Step keys: `patch_dates:0`, `patch_attempts:0`, `reopen:0`,
  `grant:<index>`.

## Shared Support

- `api/operation_ledger/adapters/adapter_support.py` — shared content-adapter step/result
  primitives: ordered projection for explicit orders, prepend/ensure/replace helpers,
  module-id recovery, outbound-marker detection, scalar-to-list normalization,
  uncertain transport classification, and standard result shaping. File upload steps
  use these ordered write-ahead/checkpoint primitives; confirmed exact file IDs are
  checkpointed before the next Canvas call.
- `api/operation_ledger/adapters/module_placement.py` — shared Canvas Assignment-type
  module find/create/attach behavior for Assignment and Quiz flows only.
- `api/operation_ledger/adapters/differentiated_bridge.py` — one shared owner for
  public-tag normalization, source/bridge shape verification, end-of-day bridge due
  time, runtime Dashboard instructions, bridge create/activate, source-only module
  placement, final family verification, and student-free family-link save.

## Safety Boundaries

- Do not change step keys, checkpoint timing, `before_send` ordering, request digests,
  failure classification, or returned result keys without updating the durable contract
  and the high-risk adapter tests together.
- Attachment and printable upload steps stop in Attention on uncertain outcomes or
  missing exact file IDs. Resume may reuse an applied upload only after exact-ID Canvas
  verification; filename or approximate match never proves completion, and uploads are
  not automatically deleted.
- Keep Assignment/Page module-item behavior separate. `module_placement.py` is for
  Assignment-type module items and, through its `item_type` keyword, `Quiz`-type items
  for classic quizzes; Page items stay in `page.py`.
- The shared differentiated-family helper is the only differentiated owner allowed to
  attach exact source assignments (the bridge remains gradebook-only).
- Grade projection starts from an exact linked family and may only write eligible
  final submission scores/statuses, past-due missing zeroes, or provenance-proven clears
  to that bridge. Hidden, submitted-ungraded, conflicting, and teacher-changed rows remain
  held. It cannot change family structure or trigger Canvas Grade Sync.
- Preserve facade monkeypatch seams when moving code. Existing tests still patch the
  facade modules rather than every leaf helper.
- No live Canvas verification belongs here. Use mocked adapter tests only.
- After CE's own verified write, teacher edits in Canvas Live to titles, dates, and
  overrides are authoritative; recovery (resume, family tail, family link) re-checks only
  CE-owned invariants (exact id, published, `grading_type`, `omit_from_final_grade`,
  `post_to_sis`, and points/assignment-group consistency across sources) for an
  unrestricted differentiated family.

## Test Routing

- `api/tests/test_assignment_operation.py`
- `api/tests/test_assignment_tier_operation.py`
- `api/tests/test_printable_attach.py`
- `api/tests/test_quiz_operation.py`
- `api/tests/test_quiz_tier_operation.py`
- `api/tests/operation_ledger/adapters/test_quiz_classic.py`
- `api/tests/test_assignment_hub_operation.py` (tier-page visibility and no-membership laws)
- `api/tests/test_qf_pusher.py`, `api/tests/test_transform_classic.py`
- `api/tests/test_page_operation.py`
- `api/tests/test_quick_assignment_operation.py`
- `api/tests/test_operation_ledger.py`
- `api/tests/test_sis_grade_bridge_operation.py`
- `api/tests/test_grade_adjustment_operation.py`
- `api/tests/test_grade_adjustment.py`
- `api/tests/test_attempts_grant.py`
- `api/tests/operation_ledger/adapters/test_attempts_grant.py`
- `api/tests/test_operation_routes.py`

## Source-size reports

Use [`tools/size_report.py`](../../tools/size_report.py) for current source-size
reports; this map intentionally does not maintain line-count snapshots.


## Accepted design reference

This historical implementation design supplements the current ownership map. The
contract in `docs/contracts/operation-ledger-contract.md` remains canonical.

Status: implemented historical design reference. The contract in
`docs/contracts/operation-ledger-contract.md` remains canonical; this document records the
storage schema, state machine, adapter interface, and claim/lease/recovery rules used by the
implementation. New changes require a current execution brief under `AGENTS.md`, not authority
inferred from this document.

## 1. Storage layout

All operation-ledger records are PRIVATE and machine-affine, stored under
`%LOCALAPPDATA%\CanvasExpert\workbench\private\` (the existing `paths.private_root()`).

### Files

| File | Contents | Format |
|---|---|---|
| `operations.v1.json` | All operations and their targets | Single JSON document, atomic-replaced |
| `claims.v1.json` | Active and expired claim records | Single JSON document, atomic-replaced |
| `receipts.v1.json` | Immutable receipts (existing, unchanged) | Single JSON document, atomic-replaced |

Batches are embedded inside the operation record (see §2.3). There is no separate
batches file — a batch is a frozen review snapshot stored on the operation.

### paths.py additions

```python
def operations_file() -> Path:
    return private_root() / "operations.v1.json"

def claims_file() -> Path:
    return private_root() / "claims.v1.json"
```

### Atomicity

All writes use the existing `storage.atomic_write_json()` (temp-file + fsync + atomic
replace) under `storage.storage_lock()` (process-wide RLock). Corrupt documents are
quarantined via `storage._quarantine()`.

## 2. Data models

### 2.1 Operation

```json
{
  "version": 1,
  "operation_id": "op-<token>",
  "kind": "content.page",
  "source_job_id": "job-<hash>" | null,
  "status": "working",
  "source_ref": { "type": "workspace_relative", "value": "<path>" },
  "source_digest": "<sha256>",
  "normalized_payload": { ... kind-specific private payload ... },
  "review": null,
  "targets": [ { ... target records ... } ],
  "created_at": "<iso>",
  "updated_at": "<iso>"
}
```

**Operation status enum** (ordered lifecycle):

```
working → prepared → reviewed → applying → applied | partial | failed | attention
```

- `working` — created, not yet validated/frozen
- `prepared` — server-validated, source digest stored, targets verified
- `reviewed` — batch frozen, review digest stored, ready to apply
- `applying` — at least one target is claimed or in-flight
- `applied` — all targets applied
- `partial` — some targets applied, some failed/blocked/sent_unknown
- `failed` — all targets failed
- `attention` — at least one target is `sent_unknown` or `blocked` and needs human review

Transitions are forward-only except `attention → applying` (retry) and
`applying → attention` (uncertain outcome). No status may skip `prepared` or `reviewed`.

### 2.2 Target

```json
{
  "target_key": "<deterministic>",
  "idempotency_key": "<deterministic>",
  "course_id": "<private>",
  "state": "pending",
  "attempt_id": null,
  "payload_digest": null,
  "claim_owner": null,
  "claim_acquired_at": null,
  "claim_lease_expires_at": null,
  "baseline": null,
  "returned_object_id": null,
  "returned_object_url": null,
  "steps": [ { ... step records ... } ],
  "error_code": null,
  "private_diagnostic": null,
  "updated_at": "<iso>"
}
```

**Target state enum**:

```
pending → claimed → sent_unknown → applied | failed | blocked | skipped
```

- `pending` — not yet attempted
- `claimed` — write-ahead persisted, Canvas call in flight or about to be
- `sent_unknown` — Canvas call returned uncertain (timeout, disconnect, crash window,
  unparseable response); needs recovery; automatic resend is forbidden
- `applied` — Canvas confirmed success; returned IDs persisted
- `failed` — Canvas returned an unambiguous rejection (4xx with clear error)
- `blocked` — adapter detected drift or ambiguous foreign content; needs human review
- `skipped` — idempotency check proved the effect already exists (by exact ID, never
  by title); no Canvas call made

### 2.3 Batch (embedded review snapshot)

```json
{
  "batch_id": "batch-<token>",
  "review_digest": "<sha256 over frozen reviews>",
  "frozen_reviews": [ { ... per-target frozen summary ... } ],
  "created_at": "<iso>"
}
```

Stored as `operation.review` (null until reviewed). The `review_digest` covers the
ordered list of frozen reviews; apply requires both `batch_id` and `review_digest`
to match the stored values.

### 2.4 Step (for multi-step targets)

```json
{
  "step_key": "create_page" | "attach_module",
  "state": "pending" | "claimed" | "sent_unknown" | "applied" | "failed" | "blocked" | "skipped",
  "attempt_id": null,
  "returned_object_id": null,
  "error_code": null,
  "private_diagnostic": null,
  "updated_at": "<iso>"
}
```

Steps execute sequentially within a target. A target is `applied` only when all
required steps are `applied` or `skipped`. If an earlier step is `sent_unknown` or
`blocked`, later steps stay `pending` and the target is not `applied`.

### 2.5 Claim record (claims.v1.json)

```json
{
  "version": 1,
  "claims": [
    {
      "claim_id": "<target_key>:<attempt_id>",
      "target_key": "<deterministic>",
      "operation_id": "op-<token>",
      "attempt_id": "<uuid>",
      "owner_pid": <int>,
      "owner_started_at": "<iso>",
      "acquired_at": "<iso>",
      "lease_expires_at": "<iso>",
      "payload_digest": "<sha256>",
      "state": "claimed" | "released" | "expired"
    }
  ]
}
```

## 3. Adapter interface

Every registered kind implements this Protocol. The registry maps kind strings to
adapter instances.

```python
class OperationAdapter(Protocol):
    kind: str                          # e.g. "content.page"

    def build_payload(self, prepare_request: dict) -> dict:
        """Parse/validate the browser-submitted prepare request.
        Returns the normalized private payload. Raises ValueError on invalid input.
        Never trusts browser-supplied Canvas paths, endpoints, or method names."""

    def source_digest(self, payload: dict) -> str:
        """Deterministic SHA-256 over the normalized payload (not including targets)."""

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        """Verify each target against active/available courses.
        Returns the verified target list with target_key and idempotency_key set.
        Raises ValueError if any target is invalid."""

    def target_key(self, payload: dict, course_id: str) -> str:
        """Deterministic target key for a course (e.g. sha256 of kind+digest+course_id)."""

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        """Deterministic idempotency key (e.g. sha256 of source_digest+course_id+normalized_title)."""

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        """Capture the frozen review summary for a target.
        Includes per-course effects, module-item implications, and baseline snapshot.
        Must be JSON-serializable and deterministic for digest computation."""

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        """Return True if Canvas/local state has drifted since review.
        Drift invalidates review and blocks apply."""

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        """Read current Canvas/local state for drift detection.
        Called at review time and again at apply time."""

    def execute(self, payload: dict, target: dict, baseline: dict, claim: dict,
                context) -> dict:
        """Perform the Canvas call(s) for one target.
        Returns {state, returned_object_id, returned_object_url, error_code, private_diagnostic, steps}.
        May perform multiple sequential steps (e.g. create_page then attach_module).
        Must be idempotent: if the effect already exists (proven by exact ID), return skipped."""

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        """On restart, prove whether a sent_unknown target was applied or not.
        Returns {state, returned_object_id, ...} or {state: "sent_unknown"} if unprovable.
        Same-title matching is never proof. Only exact ID or kind-specific postcondition."""

    def retry_selector(self, operation: dict) -> list[dict]:
        """Return only the targets that are unresolved (sent_unknown, failed, blocked).
        Applied/skipped targets are never retried."""

    def reversal_descriptor(self, payload: dict, target: dict) -> dict:
        """Describe whether/how this target can be reversed.
        {supported: bool, method: str | null, snapshot: dict | null}.
        If no separately validated API path exists, supported is false."""
```

## 4. Claim / lease / recovery rules

### 4.1 Claim acquisition

Before any Canvas call, the executor:

1. Generates a new `attempt_id` (UUID4).
2. Computes `payload_digest` = SHA-256 over the normalized outbound payload.
3. Acquires the claim under the claims interprocess lock: writes to
   `claims.v1.json` with `state: "claimed"`,
   `owner_pid`, `owner_started_at` (process start time), `acquired_at`, and
   `lease_expires_at` = `acquired_at + 5 minutes`.
4. Persists the target as `claimed` with `attempt_id`, `payload_digest`, and
   additive `apply_baseline` to `operations.v1.json`. The review-time `baseline`
   remains immutable.
5. **Flushes both writes (fsync + atomic replace) before making the Canvas call.**

Before each individual Canvas mutation, the adapter calls the executor-owned execution
context to persist the named step as `claimed` with `outbound_started_at`. A successful
response and every returned object ID are checkpointed and flushed before the next Canvas
call. The context verifies the current claim before every checkpoint so a superseded
worker cannot overwrite recovered state.

### 4.2 Lease expiry

- Lease duration: **5 minutes** (sufficient for a single Canvas POST + module item POST).
- On restart, any claim with `lease_expires_at < now` is marked `expired` and the
  target is reconciled via `adapter.reconcile()`.
- A claim with `lease_expires_at >= now` remains active regardless of PID. PID inequality
  is not evidence that a process died.
- Expiry permits reconciliation, never blind takeover or resend. An expired but
  unreconciled claim still blocks a new claim.
- Claims and operations read-modify-write transactions use one shared adjacent OS-locked
  ledger lock file in addition to the in-process lock. The shared lock makes claim
  validation plus target checkpointing one fenced transaction; separate per-document
  locks are forbidden. Atomic JSON replacement alone is not a cross-process mutex.

### 4.3 Recovery on restart

For every target in `claimed` or `sent_unknown` state:

1. Skip an unexpired active claim. If its lease expired, atomically mark it `expired`.
2. Call `adapter.reconcile(payload, target, baseline)`.
3. If reconcile proves `applied`: persist returned IDs, set target `applied`.
4. Reset to `pending` only when no mutation step has an outbound marker. Absence after a
   possibly-sent create is not proof of absence and remains `sent_unknown`.
5. If reconcile cannot prove either: target stays `sent_unknown` (Attention).

### 4.4 Retry

Retry creates a **new attempt** for unresolved targets only. The executor:

1. Calls `adapter.retry_selector(operation)` to get unresolved targets.
2. For each, runs the normal claim → write-ahead → execute → persist loop.
3. Applied targets from the original attempt are never resent.

## Shipped adapters

The single `content.page` pilot that this design was first written against has been
superseded by the full shipped adapter set under `api/operation_ledger/adapters/`
(page, quiz, assignment, and module-placement families).
For per-kind behavior, route contracts, and the current invariants, treat
`docs/contracts/operation-ledger-contract.md` and the adapter modules as authoritative;
this document remains the durable explainer for the storage schema, state machine,
adapter Protocol, and claim/lease/recovery rules above.
