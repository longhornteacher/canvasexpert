# Brief: Forge Batch 3 — generated printables and teacher attachments

Status: **current**, ready for execution. Branch: `dev`; base `719ca0b` (Batch 2b accepted).

## Required reading

1. `AGENTS.md`, this brief, `docs/reference/project-state.md` “What this means for scope”, `docs/guides/scoring-sessions.md`, and the canonical AssignmentForge authoring file.
2. `docs/reference/forge-presentation-plan.md` §0, §1.4, §3 **Printable bullet only**, and §6. `docs/contracts/forge-presentation-contract.md` §4 item 9, §5, §6, §6.1, and §7 laws 1, 2, 4.
3. `docs/reference/operation-ledger-module-map.md` Shared Support and Safety Boundaries; `docs/contracts/operation-ledger-contract.md` “Operation and targets” and “Review, write-ahead apply, and ambiguous outcomes”; `docs/contracts/agent-runtime-product-contract.md` Primary interface and Runtime boundaries. Read `api/README.md` AssignmentForge/PageForge sections before push changes.
4. Only the named §6 source/test seams and the two canonical Forge authoring files. Do not load older handoffs or unrelated maps.

## Objective and acceptance

Implement all of plan §6: a standalone paper PDF for each eligible assignment/tier at prepare; checkpointed Canvas uploads and links at apply; teacher-supplied attachments on assignments/pages, with content hashes, path confinement, one upload per file per course target, tier sharing, receipts and resume. Both MCP and Web UI use the existing reviewed content operation path. All nine §6 acceptance criteria and its non-goals are in scope.

## Senior-locked boundaries

- Use the normalized, validated author model and the prepared Batch 2b palette key. `engine/rendering/forge/` owns printable HTML; add `engine/rendering/physical/templates/assignment.html.j2` and call existing `emit_pdf.html_to_pdf`. Keep `PrintDoc` and quiz/key `render_html` unchanged. Use the installed Edge only. Tracked writing is `online_upload` with only `docx`; `external_tool` gets no PDF.
- Generate into `printables_dir()/<sanitized title>/` with the §6 filenames. Use the shared `safe_filename_component`; ensure resolved output stays below the printables root. Freeze each successful PDF's path, hash, tier/tag/label and palette in the payload. A generation failure yields `printable_unavailable` in the frozen review and no printable upload/link; it does not block content push. Never adopt an old file left at the same path after a failed generation.
- Validate `attachments` in both 2.0 authoring envelopes as a list of `{file,label}`. File names are bare, non-empty, unique case-insensitively and use only §6.1 extensions. Resolve exact names below configured workspace `To Review/Attachments/`; reject separators, absolute paths, `..`, symlink escapes, missing files and disallowed types. Freeze label, file name, resolved private path and SHA-256 in the payload; do not put local paths into host-visible review. Missing/invalid attachments block prepare. Recheck hashes before upload.
- The frozen review owns authored HTML, file labels/names/hashes, printable availability, tier mapping, and link slots. Canvas file IDs do not yet exist at review. Apply may substitute **only** a URL derived from a checkpointed Canvas file ID into each frozen link slot; it cannot rewrite semantic text or layout. The final outbound create request gets its own write-ahead digest. Document this narrow derived-field rule and the upload-step policy in `docs/contracts/operation-ledger-contract.md` before relying on it; update the module map and high-risk tests together. This is a senior-approved clarification of the existing step/checkpoint model, not a general review bypass.
- Upload each unique attachment once per course target before the first content create (`upload_attachment:<index>`), shared by all tiers. Upload each available printable immediately before its assignment create (`upload_printable:<tier-index>` or `upload_printable` untiered). A single target may contain multiple ordered steps. Do not add a cross-operation file registry or dedupe by filename; “once per course” here means once in this reviewed course target. Upload to `Canvas Expert Attachments` or `Canvas Expert Printables` respectively.
- Call `context.before_send` before the first upload mutation and `checkpoint_step` with returned file ID before the next Canvas call. On uncertain initiation/upload result or missing file ID, checkpoint `sent_unknown` and stop in Attention. Never resend a step with an outbound marker and no proved ID. On resume, reuse an `applied` upload only after exact-ID Canvas verification; filename or approximate match never proves an ambiguous upload. If exact proof is unavailable, stay Attention for teacher recovery. Do not delete uploaded files automatically.
- Canvas's documented upload completion may return a 3XX redirect or 201 with a `Location` header; complete it with a GET and obtain the exact file ID before marking the step applied. Treat upload parameters as opaque, keep the bearer token off the upload host, and authenticate only a validated Canvas completion URL. The existing 200/201 JSON case remains supported. See the official [Canvas file upload procedure](https://canvas.instructure.com/doc/api/file.file_uploads.html).
- Bind the contract's tinted **Printable** and **Attachments** lines from these IDs; paper Materials lists attachment labels as text. Keep paths and raw Canvas upload responses private. Remove the external `printable_path` prepare option, its tier refusal, old bare link, and stale web-UI-only comment. No new MCP parameters or Web UI surface.

## Preflight and stop

Confirm HEAD `719ca0b` on `dev`, with only unrelated mirror/store tests and `stubbed-workspace/` dirty. Confirm §1.4 and §6 named renderer, adapter, uploader, ordered-step, runtime-path and contract-delivery seams. Stop and report RED if a named seam differs, upload scope/folder behavior is inconsistent, safe URL binding requires changing reviewed semantic content, or the ledger cannot represent per-file uploads with its existing checkpoint primitives. Do not guess around a Canvas ambiguity.

## Verification gate and return

Run the exact §6 focused gate:

`py -m pytest -p no:randomly engine/tests api/tests/test_printable_attach.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_assignment_ordered_steps.py api/tests/test_page_operation.py api/tests/webui/test_af.py api/tests/webui/test_pf.py api/tests/mcp_server/test_content_push_tools.py`

Then run `py -m pytest -p no:randomly api/tests` because the authoring contracts and high-risk Canvas file writes change. Include direct failure, idempotency, resume, path-escape and file-drift tests. Run `git diff --check`. If any shared browser route/template/script changes, load every affected route in a pytest-isolated app and confirm zero new console errors. Do not run `api.*` outside pytest or start the real UI/MCP server. The teacher reviews the complete high-risk diff before merge; leave this brief current and report GREEN/YELLOW/RED, changed files, exact commands/counts, deviations and unresolved decisions in its Execution result and chat. Do not commit or retire the brief.

## Execution result

**GREEN for Batch 3 implementation; teacher diff review pending before merge.** No
commit was made. Base remains `719ca0b` on `dev`. The nine §6 criteria and stated
non-goals were checked against the implementation. There are no unresolved design
decisions or contract expansions beyond the senior-locked upload clarification.

Changed: `engine/rendering/forge/{printable.py,canvas_html.py,__init__.py}` and the
physical assignment template/tests/docs; AssignmentForge and PageForge validation,
authoring contracts, content-push path scrub comment, and MCP test; assignment,
tiered, whole, page, and new `forge_files.py` adapters; attachment validation;
`api/tests/test_printable_attach.py`, tier operation test, and pytest printable-path
isolation; ledger contract/map. Existing unrelated changes in `api/mirror/store.py`,
`api/tests/mirror/test_store.py`, and `stubbed-workspace/` were preserved.

Evidence:

- Exact named focused gate: **339 passed** (`-p no:randomly`).
- Full API suite: **2,003 passed, 1 failed** (`-p no:randomly`). The sole failure is
  `api/tests/test_presentation_contracts.py::test_all_live_templates_use_layouts_and_no_inline_styles`:
  unchanged `api/webui/templates/settings.html` contains `style="border-left:10px..."`.
  `git show 719ca0b:api/webui/templates/settings.html` confirms that line existed
  at the Batch 3 base; Batch 3 changed no browser template, script, or route.
- Targeted final review-hash check: `api/tests/test_printable_attach.py`, **21 passed**.
- `git diff --check`: passed (line-ending conversion warnings only).

Direct tests cover PDF variants and failure, file type/path rejection, file drift,
uncertain upload and exact-ID resume, documented redirect completion with origin
check, attachment and printable apply/resume for assignments, tiered assignments,
and pages. Final self-review and independent seam audit found no remaining Batch 3
gap. The complete high-risk diff awaits teacher review; do not merge or retire this
brief until that review.
