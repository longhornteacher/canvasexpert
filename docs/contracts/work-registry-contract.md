# Work Registry Contract

Status: implemented durable contract, accepted 2026-07-11. Operation-ledger integration
is governed by `docs/contracts/operation-ledger-contract.md`.

## Purpose

The Work Registry gives CanvasExpert one durable, cross-course index of work without
making that index the source of truth for Canvas objects, student records, or authored
Forge files. Scoring Sessions are not Home/Work jobs.

The UI has three operating states:

- **Desk** answers: Start, Continue, Attention, Prepared, and Receipts.
- **Workbench** opens one resumable job with a persistent Work rail and explicit scope.
- **Instrument** expands the same job for high-density grading, comparison, preview, or
  delivery work. Instrument never clones or creates a second job/form.

## Persistence boundary

The registry is stored under the configured workspace, never in the repository and
never inside `settings.json`:

```text
<workspace>/_System/workbench/
├── registry.v1.json
├── suppressions.v1.json
├── discovery-cache.v1.json
└── quarantine/
```

`registry.v1.json`, suppressions, and discovery cache are PII-minimized indexes.
Student names, Canvas user IDs, submission content, grades, comments, monitored notes,
and per-student prepared changes are forbidden there. Those details remain in their
authoritative PRIVATE subsystem or the machine-local operation ledger.

Writes use a process-local `threading.RLock`, a same-directory temporary file,
`flush`/`fsync`, and `os.replace`. A corrupt file is moved to `quarantine/` with a
timestamp and the app starts from an empty versioned document; it must not overwrite
the only corrupt copy silently.

## Registry document

```json
{
  "version": 1,
  "updated_at": "ISO-8601",
  "jobs": []
}
```

Each job has exactly this public index shape:

```json
{
  "job_id": "stable opaque id",
  "fingerprint": "kind/course/assignment/condition stable key",
  "material_version": "changes when underlying facts materially change",
  "origin": "intentional | detected | system",
  "kind": "namespaced kind such as create.assignment or grade.debt",
  "status": "draft | ready | in_progress | attention | ignored | completed | failed",
  "title": "PII-free display title",
  "description": "PII-free short display phrase for the kind, or empty",
  "course_ids": ["string Canvas course id"],
  "focused_course_id": "string or empty",
  "assignment_id": "string or empty",
  "resumable_url": "local relative URL beginning with /",
  "source_ref": {
    "type": "workspace_relative | canvas_finding | routine_state | operation_receipt",
    "value": "non-secret reference"
  },
  "counts": {
    "total": 0,
    "pending": 0,
    "affected": 0
  },
  "attention_reason": "short PII-free text or empty",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "completed_at": "ISO-8601 or empty"
}
```

Unknown versions, origins, statuses, source types, absolute URLs, absolute filesystem
paths, and non-string Canvas IDs are rejected. `focused_course_id` is empty or a member
of `course_ids`.

## Transient Desk presentation sidecar

The exact job shape above remains generic and is the only job shape written to registry,
discovery, suppression, session, or queue storage. For the loopback-only Desk UI,
`GET /api/work` may return a separate top-level `presentations` mapping keyed by opaque
`job_id`. Dashboard initial data receives the same mapping. Each value has exactly four
string fields:

```json
{
  "course_label": "configured Current-course nickname or empty",
  "title": "local semantic title",
  "summary": "aggregate-only progress sentence",
  "action_label": "specific local action label"
}
```

This sidecar is computed on demand from Current-course configuration and the already-public
job counts. It is never
merged into a job or persisted. It may include a teacher-authored assignment title and
aggregate student/submission counts, but never student names or IDs, grades, comments,
submission content, feedback, roster notes, or per-student state. Missing local authorities
fail closed to generic presentation text without opening a full private session or reading
Canvas.

## Authority and adapters

The registry never copies authoritative subsystem payloads:

- Scoring Session truth remains private in the session store and is not projected into Work.
- Routine enablement and due state remain machine-local in `config/routines.py`.
- Forge source truth remains in the workspace library or staged temp file.
- Canvas assignment/submission truth remains in Canvas and bounded discovery caches.
- Prepared operations and receipts use the Operation Ledger Contract.

Adapters project summaries into jobs and hydrate details only after the job opens. The
transient Desk presentation sidecar is a display projection, not hydration and not an
authority.

Home and Work contain no scoring-session resume cards or scheduled scoring jobs. Teachers
resume by invoking the Scoring Session flow in their connected agent; Canvas Live remains
the review/edit surface.

## Detected findings

Discovery is an explicit asynchronous read-only request; `GET /` must not synchronously
scan Canvas. A scan is bounded to active bookmarked courses, cached with per-course
freshness/error metadata, and may run in parallel with a maximum documented concurrency.

Initial providers, in order:

1. Enabled/due routines and failed/partial routine receipts.
2. Cross-course grading debt: assignments with submitted work lacking teacher score,
   comment, or CanvasExpert session evidence.
3. Aggregate late-work findings using school-day and extra-time math.
4. Aggregate roster warning categories.
5. Aggregate Home attention: evidence-backed student-response follow-up and uncertain staff
   response checks.

Discovery persists counts and stable object IDs only. Student identities and submission
text are retained only when a job opens in a PRIVATE workspace. An explicit discovery
scan may inspect private metadata transiently for an approved aggregate reduction, but
must discard it before the scan returns.

The Home-attention provider reads submission comments only during the explicit discovery
request. It reduces them immediately to assignment-scoped counts; it never returns,
logs, hashes, caches, or persists comment text, author identity, or per-student state.
Its definite follow-up signal requires an ordered latest student-authored response. A
trailing Canvas Expert Teaching Assistant marker does not resolve that signal. A later
non-student response with unprovable staff origin is a separate aggregate staff-response
check, not a claim that a human response is awaiting.

## Ignore and snooze

Suppressions are keyed by `fingerprint` plus `material_version`:

```json
{
  "version": 1,
  "items": [
    {
      "fingerprint": "...",
      "material_version": "...",
      "mode": "ignored | snoozed",
      "until": "ISO-8601 or empty",
      "updated_at": "ISO-8601"
    }
  ]
}
```

A changed `material_version` resurfaces the finding. A snooze resurfaces at `until`.
Ignoring a job never mutates Canvas or its authoritative subsystem.

## Course context

Focused course and write targets are distinct:

- The focused course feeds course-specific reads and selectors.
- Target courses are the explicit live-write scope.
- Focus is independent of targets. `setFocus` never mutates write targets.
- Deep links override only fields they explicitly supply.
- Context never broadens a previously single-course action silently.

CourseExpert's existing picker may explicitly add a newly focused course to its checked
target set before publishing both changes; that is picker behavior, not a global context
invariant. Read-only/single-course pages may change focus without authorizing a write.

The browser contract is `window.CE_CONTEXT` with `snapshot`, `setFocus`, `setTargets`,
`reconcile(availableCourses, {source, authoritative})`, and `subscribe`, plus the
`ce:contextchange` event. Reconciliation prunes stale focus/targets only after a successful
authoritative all-course response. Bookmark-only hydration and failed Canvas fetches are
non-destructive. The existing
`canvasExpert.push.coursePicker.v1` state is migrated once and read as a compatibility
fallback for one release.

## Forbidden behavior

- Do not serialize arbitrary form DOM state as a job.
- Do not store secrets, student identity, grades, comments, or submission content in
  registry/discovery/suppression documents.
- Do not call activity events jobs or receipts.
- Do not infer Canvas connectivity solely from saved credentials.
- Do not let Instrument create a cloned form or duplicate DOM IDs.
