# Course Catalog v3 contract

Course Catalog is Canvas Expert's durable, student-free projection of navigation, search,
and reviewed classroom objective evidence for one configured course. Canvas remains
authoritative for focused state, submissions, grades, comments, and every write.

## Location and identity

Each course owns this canonical/previous pair in the machine-local `%LOCALAPPDATA%\CanvasExpert\cache\Canvas Catalog\` directory:

```text
%LOCALAPPDATA%\CanvasExpert\cache\Canvas Catalog\<canvas-course-id>\catalog.v3.json
%LOCALAPPDATA%\CanvasExpert\cache\Canvas Catalog\<canvas-course-id>\catalog.v3.previous.json
```

The Canvas course ID, not a display name, is directory identity. A disk read is local-only;
the Current-course refresh route is the only Catalog acquisition path.

## Root and scope schema

Every persisted document has exactly these root keys:

```json
{
  "version": 3,
  "course_id": "course-id",
  "course_name": "Teacher-facing course label",
  "updated_at": "ISO-8601 timestamp",
  "assignments": {"state": "current", "last_success_at": "", "last_attempt_at": "", "error_code": "", "records": {}},
  "modules": {"state": "current", "last_success_at": "", "last_attempt_at": "", "error_code": "", "records": []},
  "assignment_groups": {"state": "current", "last_success_at": "", "last_attempt_at": "", "error_code": "", "records": []},
  "pages": {"state": "current", "last_success_at": "", "last_attempt_at": "", "error_code": "", "records": []}
}
```

Every scope uses exactly `state`, `last_success_at`, `last_attempt_at`, `error_code`, and
`records`. States are `current`, `stale`, `incomplete`, and `unavailable`. A failed or
incomplete acquisition retains validated last-good records; a fully paginated empty
collection is current and authoritative.

Assignments, modules, and assignment groups retain their existing exact v3 allowlists.
Raw assignment HTML is normalized to plain text.

## Published page scope

Refresh requests Canvas's paginated `/api/v1/courses/{course_id}/pages` collection with
`include[]=body`. Each persisted page has exactly:

```json
{"id":"page-id","title":"Unit 1","body_text":"Plain text.","published":true,"front_page":false,"updated_at":"2026-08-30T12:00:00+00:00"}
```

Title and body are normalized plain text. Control characters and content that cannot be
normalized are rejected, making the page scope incomplete; raw HTML is never retained.
Canvas URLs/slugs, editor identity, roles, lock explanations, block-editor JSON, and every
other response field are forbidden. The page scope is student-free and is the only Catalog
source for the local MCP page read and Learning Objective source references.

## Reads and refresh

`GET /api/course-catalog?course_id=...` is disk-only. `POST /api/course-catalog/refresh`
performs the read-only paginated acquisition for all four scopes. Both routes enforce the
Current-course boundary and return sanitized state/warnings without paths or raw errors.

The typed read service exposes `catalog_assignments`, `catalog_modules`,
`catalog_assignment_groups`, and `catalog_pages`. Consumers never use a Canvas fallback or
private mirror file for these scopes. MCP page/objective reads are Current-course gated;
all MCP reads label source, state, and freshness.

## Durability and forbidden material

The complete document is validated before every write. Writes use a same-directory
temporary file, flush, `fsync`, and `os.replace`. Before replacement, the validated canonical
snapshot becomes `catalog.v3.previous.json`. Corrupt snapshots are quarantined and are never
treated as valid state.

The Catalog must never contain raw Canvas responses, users, enrollments, submissions,
attempts, grades, comments, attachments, student identifiers, authorization material,
signed URLs, Canvas transport URLs, private local paths, or arbitrary unapproved fields.
Validation rejects unknown root, scope, assignment, rubric, module, module-item, group, and
page keys before storage.
