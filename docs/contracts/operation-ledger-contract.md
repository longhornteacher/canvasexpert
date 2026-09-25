# Operation Ledger Contract

Status: implemented durable contract. Design rationale is recorded in
`docs/reference/operation-ledger-design.md`.

## Boundary and storage

The ledger separates prepare, review, and apply. It is a server-owned command system,
not a browser cart and not a claim of atomic Canvas writes.

```text
Working -> Prepared -> Reviewed -> Applying -> Applied / Partial / Failed / Attention
```

Operations, batches, receipts, claims, and migration backups are PRIVATE and
machine-affine. Store them under `%LOCALAPPDATA%\CanvasExpert\workbench\private\`,
never the synced workspace, repository, browser storage, SAFE artifacts, fixtures,
console, or general activity log. The work registry remains in the configured
workspace; only its PII-minimized projections may link to opaque ledger IDs.

All writes use temp-file + flush/fsync + atomic replace. Corrupt records are
quarantined. Apply is guarded by both a process lock and an atomic machine-local claim
with owner, acquired time, and lease expiry. A record created on another machine is
not portable and cannot be applied there.

## Operation and targets

Every registered kind has a typed builder, validator, reviewer, drift checker,
executor, target reconciler, retry selector, and reversal descriptor. The browser may
never choose a Canvas method, endpoint, or arbitrary payload.

Each operation records an opaque ID, kind, source job ID, normalized private payload,
source digest, review digest, status, timestamps, and ordered targets. Each target has:

- deterministic `target_key` and `idempotency_key`;
- Canvas scope/object identifiers, kept PRIVATE;
- `pending | claimed | sent_unknown | applied | failed | blocked | skipped` state;
- attempt ID, outbound payload digest, claim owner/lease, and timestamps;
- returned Canvas object/correlation ID when known;
- redacted error code plus PRIVATE diagnostic.

Quiz item rejections may also carry a bounded `failed_items` projection with each
source item’s id (when present), source type, 1-based plan index, stable field hint,
and parsed Canvas status. It contains no request payload or private diagnostic.

Idempotency is target-level. Operation-level keys do not prove that a partially applied
multi-target operation is safe to repeat.

## Review, write-ahead apply, and ambiguous outcomes

Review accepts operation IDs only. The server reloads and revalidates them, captures
current Canvas/local baselines, freezes normalized summaries, and returns an opaque
batch ID plus a digest over the ordered frozen reviews. Apply requires both values.
Any payload, source, setting, target, or baseline change invalidates review.

### Reviewed file slots and ordered uploads

For AssignmentForge and PageForge operations, review freezes attachment names, labels,
private source paths, SHA-256 hashes, printable availability, tier mapping, and the
specific rendered HTML link slots. Local paths and raw Canvas upload responses stay
private. A source or attachment hash change invalidates review; apply rechecks the
frozen attachment hashes before upload.

Canvas file IDs do not exist at review. During apply, Canvas Expert may fill only a
frozen attachment or printable link slot with a URL derived from that slot's exact,
checkpointed Canvas file ID. It cannot change the authored wording, labels, ordering,
or layout. The final content-create request, including derived URLs, has its own
write-ahead payload digest before that request is sent. This rule is limited to these
file link slots and does not authorize other post-review payload edits.

Each attachment upload is an ordered step before the first content create; a teacher
file is uploaded once per course target and its checkpointed ID is shared by the
assignment's tiers. Each printable upload is an ordered step immediately before its
corresponding assignment create. Before every upload mutation, call `before_send` and
flush its outbound marker. After confirmed completion, checkpoint the exact Canvas file
ID before making another Canvas call.

Canvas's file-upload completion may return a redirect or `201 Location`; the completion
GET yields the exact file ID. Follow only a validated Canvas-origin completion URL, and
send the bearer token only to that validated Canvas origin. A completion with no exact
file ID is not a successful checkpoint.

An uncertain initiation or upload result, or a completion without an exact file ID,
is checkpointed as `sent_unknown` and stops the operation in Attention. Never resend a
step with an outbound marker but no proved ID. On resume, reuse an `applied` upload only
after exact-ID Canvas verification. A filename or approximate match cannot resolve an
ambiguous upload; when exact proof is unavailable, keep it in Attention for teacher
recovery. Uploaded files are not automatically deleted.

For every target, apply must:

1. acquire the claim and persist `claimed`, attempt ID, payload digest, and baseline;
2. flush that write-ahead record before making the Canvas call;
3. on confirmed success, persist returned IDs/postcondition and `applied` before moving
   to the next target;
4. on an unambiguous rejection, persist `failed` or `blocked`;
5. on timeout, disconnect, crash window, or any uncertain response, persist
   `sent_unknown`, surface Attention, and do not resend.

On restart, stale `applying`/`claimed` records are reconciled target by target. An
adapter may mark an ambiguous target applied only from a unique Canvas-side identifier
or exact, kind-specific postcondition. It may return it to pending only after proving
the effect is absent. Same-title or approximate-content matching never proves either.
If neither conclusion is provable, the target stays blocked in Attention.

Retries create a new attempt for unresolved targets only. Applied targets are never
resent. Sequential batches may be partial; no automatic rollback crosses unrelated
Canvas calls.

## Receipts

One immutable receipt is written per invoked attempt. Its common envelope contains:

- `version`, opaque `receipt_id`, `subject_type: operation | routine`, and `subject_id`;
- optional `operation_id`, `batch_id`, or routine run ID;
- kind, attempt and completion timestamps;
- `applied | partial | failed | blocked | no_effect` status;
- source/review digests where applicable;
- ordered target results with target key, object type, returned object ID/URL, state,
  redacted error code, and PRIVATE detail;
- reversal descriptor and state.

`no_effect` means an invoked routine completed and proved there was nothing to change.
A disabled or not-due routine was not invoked and creates no receipt. General activity
gets only a content-free projection (kind, course count, status, receipt ID, local
detail URL); it is never authoritative.

List endpoints use generic labels or a deterministic protected-name sanitizer with a
generic fallback. Raw course, assignment, student, grade, comment, or submission data
may hydrate only after opening a PRIVATE detail view.

## Local mutation protection and API

CanvasExpert has no user-authentication layer; documentation must not call it an
“authenticated UI.” New ledger mutation routes require a process-random CSRF token,
emitted in the Workbench base markup and sent in a header, plus strict same-origin and
loopback Host/Origin checks. The token is not persisted. Missing or invalid protection
returns 403 before reading a private record or performing work.

- `GET /api/operations` — PII-minimized summaries.
- `POST /api/operations/{kind}/prepare` — typed preparation.
- `POST /api/operation-batches/review` — frozen batch review.
- `POST /api/operation-batches/{batch_id}/apply` — digest-gated apply.
- `POST /api/operations/{operation_id}/retry` — proven unresolved targets only.
- `GET /api/receipts` — PII-minimized summaries.
- `GET /api/receipts/{receipt_id}` — PRIVATE detail from the local UI.

Ignore/snooze/cancel routes introduced by the registry or Desk use the same mutation
protection. Route additions deliberately update `api/tests/test_route_contract.py`.

## Invariants and forbidden behavior

- Cancel receives initial focus in every write review.
- Server state, never browser preview rows, is authoritative.
- AI suggestions remain drafts until teacher approval except the existing narrow,
  per-job scheduled-auto-push policy.
- Reversal is offered only when the adapter proves it and preserves an exact snapshot.
- SAFE means pseudonymized, not anonymous.
- No generic executor, activity-log receipt, silent ambiguous retry, multi-call atomicity
  claim, or kind migration without drift/idempotency/reconciliation/retry/reversal tests.
