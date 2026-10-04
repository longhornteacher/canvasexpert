# Work Registry Contract

Status: implemented durable contract, accepted 2026-07-11. Operation-ledger integration
is governed by `docs/contracts/operation-ledger-contract.md`.

This contract governs resumable runtime work and its privacy-minimized indexes. It does not make the browser the primary teacher-agent surface. Connected
agents own conversation, host-rendered previews, and Scoring Session interaction; the
control console owns setup, readiness, recovery, receipts, diagnostics, and
genuinely local-only operations.

## Purpose

The Work Registry gives CanvasExpert one durable, cross-course index of work without
making that index the source of truth for Canvas objects, student records, or authored
Forge files. Scoring Sessions are not registry jobs; the agent lists and transfers
them with `list_work_items` and `transfer_work_item`.

The console lists operations needing attention and recent receipts directly from
their owning services. No console page or MCP tool reads the registry today.

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
    "type": "workspace_relative | canvas_finding | operation_receipt",
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

## Authority and adapters

The registry never copies authoritative subsystem payloads:

- Scoring Session truth remains private in the session store and is not projected into Work.
- Forge source truth remains in the workspace library or staged temp file.
- Canvas assignment/submission truth remains in Canvas and bounded discovery caches.
- Prepared operations and receipts use the Operation Ledger Contract.

Adapters project summaries into jobs. Private details remain with their owning
runtime service. Teachers resume scoring by invoking the Scoring Session flow in
their connected agent; Canvas Live remains the review/edit surface.

## Detected findings

Discovery runs in the background after each mirror heartbeat tick and reads the mirror
first; no page request scans Canvas. A scan is bounded to Current courses, cached with
per-course freshness/error metadata, and runs at most three courses in parallel within a
30-second deadline.

Runtime discovery providers reduce grading debt, roster warnings and
student-response follow-up to aggregates. Operation recovery and recent receipts
are listed directly by their owning services on CanvasAgent.

Discovery persists counts and stable object IDs only. A discovery scan may inspect
private metadata transiently for an approved aggregate reduction, but must discard it
before the scan returns.

The comment follow-up provider reads submission comments only during a discovery
scan. It reduces them immediately to assignment-scoped counts; it never returns,
logs, hashes, caches, or persists comment text, author identity, or per-student state.
Its definite follow-up signal requires an ordered latest student-authored response. A
later non-student response with unprovable staff origin is a separate aggregate
staff-response check, not a claim that a human response is awaiting.

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

A job's `focused_course_id` is an index hint only and never a write target. The
agent's explicit operation scope and the runtime's frozen review define write targets,
and context never broadens a single-course action silently.

## Forbidden behavior

- Do not serialize arbitrary form DOM state as a job.
- Do not store secrets, student identity, grades, comments, or submission content in
  registry/discovery/suppression documents.
- Do not call activity events jobs or receipts.
- Do not infer Canvas connectivity solely from saved credentials.
