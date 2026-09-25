# Engine Architecture

The `engine/` package is the offline rendering library. It has no Canvas token, no
network dependency, and no student data. It turns authored content into validated
domain objects and local output artifacts.

For repo-wide safety rules and subsystem boundaries, read `../../AGENTS.md` first.
For the live Canvas app, read `../../api/README.md`.

## Data Flow

```text
Forge input
  -> parsing/import
  -> validation and optional fixing
  -> domain model
  -> optional PrintDoc adapter for physical output
  -> render target
  -> local package/output
```

The current QuizForge JSON path enters through `engine/spec_engine` and `JsonImporter`.
The legacy text path remains in `engine/parsing/text_parser.py` for backward
compatibility.

AssignmentForge and PageForge use a separate plain-dictionary path. The API parses
and validates their `2.0-json` envelopes, normalizes author text, and passes the
result to `engine/rendering/forge/` for Canvas HTML. AssignmentForge also uses its
`render_assignment_printable` entry point to build a standalone printable from the
same normalized model and palette. The adapter sends that HTML to the existing Edge
PDF emitter; Canvas file upload and review remain in `api/`. Teacher attachments are
resolved and uploaded by the API operation path. After review, the API fills only the
frozen attachment link slots with URLs derived from checkpointed Canvas file IDs.
This package owns the shared palette, author-HTML allowlist and decoration,
assignment/page layouts, and submission wording. Its fixed swatches and canonical
default tier colors live in
`engine/rendering/forge/palette.py`; the teacher's synced color choices are read by
the operation adapters while preparing an assignment or page. It has no `api`
imports, Canvas token, network access, or student data. The adapters freeze the
rendered HTML into the reviewed operation payload, so its source digest covers the
actual Canvas content.

## Main Layers

| Layer | Path | Role |
|---|---|---|
| Domain model | `engine/core/` | Quiz, question, and answer objects consumed by renderers. |
| Import/parsing | `engine/importers.py`, `engine/spec_engine/`, `engine/parsing/` | Convert Forge specs or legacy text into domain objects. |
| Validation | `engine/validation/` | Structural/fairness/rationale checks plus focused fixers. |
| Orchestration | `engine/orchestrator.py` | Runs parse, validate, render, package, and feedback steps. |
| Canvas/QTI render | `engine/rendering/canvas/` | Offline Canvas/QTI package rendering. |
| Forge Canvas HTML | `engine/rendering/forge/` | Shared palette, author HTML checks and decoration, AssignmentForge and PageForge HTML, and submission wording. |
| Physical render | `engine/rendering/physical/` | Quiz `PrintDoc` output and shared HTML/CSS to PDF/DOCX through installed Edge and bundled Pandoc. |
| Correction docs | `engine/rendering/correction_doc/` | Marked-up correction document rendering. |
| Packaging | `engine/packaging/`, `engine/packagers/` | Output folder/package handlers for Canvas packages and physical quizzes. |
| Feedback | `engine/feedback/` | Local success/failure prompts and logs. |

## Physical Output

QuizForge adapts trusted content into `PrintDoc`, then renders the same HTML substrate
to DOCX and PDF. AssignmentForge printables use the Forge plain-dictionary model and
`engine/rendering/forge/` template renderer; they do not extend `PrintDoc` or change
the quiz/key `render_html` entry point. The API adapter generates the PDF with the
installed Edge emitter during prepare, then uploads it through the reviewed operation
path during apply.

Key files:

- `engine/rendering/physical/printdoc.py`
- `engine/rendering/physical/quiz_adapter.py`
- `engine/rendering/physical/redact.py`
- `engine/packagers/physical_handler.py`

## Boundaries

- `engine/` does not call Canvas APIs. Live Canvas behavior belongs in `api/`.
- Authoring contracts remain canonical in `api/default_docs/AI Authoring/Author a *.txt`.
- API code validates Forge envelopes before rendering. The Forge package also
  checks its author-HTML allowlist and decorates permitted fragments; it does not
  parse envelope schemas or own business validation.
- Test fixtures must stay fictional and must not include student data.

For file-by-file navigation, see `AGENT_MAP.md`.
