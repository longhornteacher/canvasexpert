# Reading CanvasMirror evidence directly

CanvasMirror is the teacher's durable, cloud-synchronized evidence store for
agent-led ELA/CS scoring. It holds pseudonymized, scrubbed facts and commits.
The private originals, identity mappings, and control state live elsewhere and
are never part of this read surface.

The local index is rebuilt from validated safe course files at runtime startup,
after local publication, and as synchronized files arrive. A missing or partial
index is a repair or synchronization state, not proof that course evidence is
absent. Check `scope_status` and each query's coverage and revision before using
the rows. Read activation for the teacher's private pilot has not yet occurred.

## Safe read locations

- Safe evidence root: `<workspace>/CanvasMirror/` (synchronized, agent-readable).
- Reader contract: `<workspace>/CanvasMirror/reader.v1.json`.
- Safe query index:
  `<LOCALAPPDATA>/CanvasExpert/cache/CanvasMirror/<workspace_key>/<source_key>/query.sqlite3`.

Open the index read-only with SQLite URI `mode=ro` and `PRAGMA query_only=ON`.
Do not use `immutable=1` for a database the runtime can update. Direct reads
need no identity store, no Canvas, and no FastAPI.

## Named views

`courses`, `assignment_context`, `group_context`, `module_context`,
`page_context`, `assignment_group_context`, `current_submissions`,
`attempt_history`, `attachment_associations`, `attachment_extractions`,
`attachment_blocks`, `scope_status`, `comparison_evidence`, `agent_notes`.

## Example

```sql
SELECT assignment_id, pseudonym, attempt, submitted_at, payload
FROM attempt_history WHERE source_key = ? AND course_id = ?
AND assignment_id = ? ORDER BY pseudonym, attempt, fact_ref LIMIT 50
```

The equivalent MCP read is `get_assignment_evidence(course_id, assignment_id,
view)` with view `attachments`, `comparisons`, or `notes`.

## What is and is not here

- Pseudonymized and scrubbed, not anonymous. Originals and identity mappings are
  outside this root.
- Attachment associations carry an opaque key, media type, size, and status —
  never a filename or download URL.
- Extraction facts carry scrubbed block text and locators, never raw pixels,
  EXIF, or internal OOXML packages.
- Comparisons identify exact captured files, wording overlap, and changes
  between attempts within one assignment. Their coverage and input revision
  travel with the rows; they are evidence for teacher review, not a verdict.
- Notes are provisional and teacher-only unless an explicit teacher direction
  marks them confirmed; they are never posted to Canvas automatically.
