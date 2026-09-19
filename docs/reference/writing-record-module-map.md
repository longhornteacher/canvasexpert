# Writing Record Route Card

This card maps the private runtime service and its retained control-console trigger. The
primary agent-facing read path is MCP `get_writing_history`; the browser/CLI entries below
are local acquisition and maintenance surfaces, not a separate scoring workflow.

## What this subsystem is

Writing Record privately preserves scrubbed writing evidence for later
teacher/AI evaluation. It is not a grading authority and does not score,
coach, infer trends, recommend tiers, or assemble feedback. PowerGrader owns
grading and review-first Canvas posting; Writing Timeline reads revision facts
for one DOCX and is separate.

## Entry points

- Evidence types and scrub/segmentation: `api/dailywriting/core/`
- Private store: `api/dailywriting/store/` (see `store/SCHEMA.md`)
- Explicit outbound projection: `api/dailywriting/projection.py`
- Assignment mapping and teacher-triggered Canvas ingest:
  `canvas_source.py`, `canvas_ingest.py`, `canvas_attachments.py`
- Local CLI: `api/dailywriting/cli/ingest.py` and `ingest_canvas.py`
- Teacher web trigger: `api/webui/routes/dailywriting.py`
- Assistant read: `get_writing_history` in `api/mcp_server/tools.py`

## Ownership routes

`core/models.py` owns evidence shape; `core/scrub.py` removes vault-known
names before storage; `core/segmentation.py` attributes spans; `store/` keeps
Canvas ID private and resolves pseudonyms; `projection.py` is the only
assistant-facing allowlist. `canvas_source.py` maps only reliable catalog
facts into assignment context.

## Where the record lives

`<workspace>/_System/WritingReps/` is private machine state. Its only
artifacts are `submissions/YYYY-MM.json` and `reps.json`. Canvas IDs stay on
disk; pseudonyms are resolved before projection. Storage rechecks scrubbed
text, and the outbound safety gate scans every exposed text field.

## Two rules that are easy to break

Scrub before segmentation and storage: spans must never be derived from
unscrubbed text. Build outbound payloads field by field: the safety scanner is
keyed by exposed text-field names, and stored records must never be serialized
wholesale.

## The one live Canvas path

Typed submissions come only from the current local mirror and make zero live
Canvas calls. A DOCX upload has no mirrored bytes, so teacher-triggered ingest
may perform one focused fetch/download through `canvas_attachments.py`. It is
never an MCP tool. Typed body wins over an attachment; latest DOCX wins among
uploads; only DOCX is supported; per-student failures are reported and do not
stop other acquired work.

## Open decisions

Selecting an assignment scopes a teacher-triggered acquisition; it creates no
persistent tracking or eligibility flag, and every acquired response is kept.
No genre, purpose, stem, or missing prompt text is inferred. Rubric capture,
delivered-feedback history, and automatic acquisition remain unimplemented.

## Verification discipline

For evidence changes, preserve pinned fixture submission IDs, rep IDs,
timestamps, scrubbed-text digest, word count, ordered segment origins/spans,
flags, and scrub findings. Verify typed ingestion makes zero live calls,
re-ingest reads as one current assignment/submission, and the assistant
projection withholds text unless requested. Run the named Writing Record and
MCP gates; only add browser verification when executable UI code changes.
