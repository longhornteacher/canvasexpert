# Direct brief: Forge Batch 3b — Canvas Files and chat attachments

Status: current for execution. Senior: `/root`. Executor: one Luna agent.

## Objective

A teacher can attach an existing file from the course's Canvas Files by exact name, or post a file in chat for the agent to hand to `stage_attachment(source_path)`. The teacher never manages `To Review/Attachments` manually. Existing review, apply, and resume boundaries remain authoritative.

## Preflight and scope

- Work on `dev` at `21c0fea` or its direct successor. Confirm the Batch 3 attachment path and schema v61 exist. Current `dev` matched `origin/dev` after fetch on 2026-09-25; `main` was six commits behind `origin/main` locally.
- Preserve unrelated modified `api/mirror/store.py`, `api/tests/mirror/test_store.py`, and untracked `stubbed-workspace/`. Do not inspect or stage their contents.
- Before writing, identify each private-store root from its existing owner: `api/runtime_paths.py`, `api/platform_services/workspace.py`, `api/platform_services/config/_io.py`, the credential owner, and the Identity Vault owner. If the roots cannot be safely enumerated from these definitions, stop.
- Own the shared validation and file service (`api/webui/attachment_validation.py`, `api/operation_ledger/adapters/forge_files.py`), both Forge adapters, the thin MCP wrapper/registration, versioned schema and inventory/docs, the two canonical authoring contracts, and directly corresponding tests. Touch adjacent code only when needed for the criteria below; report any expansion first.

## Locked decisions and acceptance

1. Entries have `label` and exactly one of `file` or `canvas_file`; `folder` is allowed only with `canvas_file`. The existing extension allowlist applies to both. Reject unsafe names and case-insensitive duplicate staged names or duplicate `(canvas_file, folder)` pairs. Keep one shared validator used by AssignmentForge and PageForge.
2. In each adapter's `build_payload`, search the live course Files endpoint by `search_term` through `api/platform_services/canvas_client.py::canvas_get_all_complete`. Require a complete paginated response; compare exact display names case-insensitively and narrow by the optional Canvas folder path. Never enumerate files to the agent. Freeze exactly one match's ID, size, and `updated_at` in the payload/digest. Zero or multiple matches refuse preview with `attachment_not_found` or `attachment_ambiguous`, exposing at most ten `{name, folder}` pairs and no IDs or nonmatching names. A hidden/locked match proceeds with `attachment_not_student_visible` warning.
3. At apply, verify each frozen Canvas file's exact ID with the existing `get_file` before any content creation. If absent or changed under the frozen identity/metadata rule, block with `file_drift`; do not upload it. Bind its ID through the existing frozen link slots. Keep checkpointed staged-file uploads and resume behavior intact, including shared tier links.
4. Add host-neutral `stage_attachment(source_path)` via thin `server.py` and `tools.py` wrappers, with logic in `forge_files.py` or a sibling service. Copy a regular source file to the private `To Review/Attachments` landing folder and return only `{ok, file, size_bytes}` (or a bounded refusal), never a private path. Refuse folders, Windows shortcuts, symlinks/junctions, files over 25 MB, disallowed extensions, all private-store roots (including workspace, app data/config, Identity Vault, credentials, and `api/.env`), and same-name/different-byte collisions. Reuse same-name/same-byte content. Security checks must happen before reading bytes; keep copy/check handling safe against path substitution.
5. Update server instructions, AssignmentForge and PageForge authoring contracts: distinguish the two sources; never list course files or pass bytes in tool arguments; if host provides no local path, plainly suggest teacher upload to Canvas Files; ask the teacher to choose when a preview returns candidates. Remove teacher-folder-placement instructions. Permanently refuse `{{file:…}}` with direction to `canvas_file`; leave `{{page:…}}` refusal intact. Update `docs/reference/forge-presentation-plan.md` §10 only for this placeholder narrowing.
6. Sync MCP registry, schema snapshot v62, version/count in `docs/mcp-server.md`, inventory/group and wrapper tests. Keep schema v61 intact. Update generated inventory from the live registry; no hand-maintained count.
7. Tests follow AGENTS.md taxonomy: one direct law for private-root/symlink/junction staging refusal; parametrized validation and lookup contracts (zero/one/many and hidden/locked); one assignment Canvas-file apply example; one staged-file apply/resume example. Exercise drift/refusal behavior at the owning seam. Keep test data synthetic and paths fixture-derived.

## Non-goals

No course-file browser/list tool, `{{page:…}}` resolution, upload deduplication against Canvas Files, MCP byte arguments, Web UI attachment control, new persistence format, or hosted-agent assumption. Do not alter existing pilot artifacts or Canvas objects during verification.

## References (bounded)

- `AGENTS.md`; `docs/reference/project-state.md`; `docs/contracts/agent-runtime-product-contract.md`.
- `docs/reference/forge-presentation-plan.md` §0, §1.6, §6b, §8 D6, §9 pointer, and §10 placeholder item only; `docs/contracts/forge-presentation-contract.md` §6.1 and §7.
- `docs/guides/scoring-sessions.md`; `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`; `api/default_docs/AI Authoring/Author a Page (PageForge).txt`.
- `docs/mcp-server.md` Tools/schema/version and inventory sections; `api/README.md` Canvas push boundary; exact files in Preflight and Scope above.

## Verification gate

Run `py -m pytest -p no:randomly engine/tests api/tests/test_printable_attach.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/webui api/tests/mcp_server`, then `py -m pytest -p no:randomly api/tests` because MCP schema changes. No live Web UI or MCP server. Report command, test counts, and any failure. The senior will inspect only risk seams and accept against these criteria.

## Stop conditions

Stop RED if private roots are not derivable from their owners; Canvas Files search needs scope/token changes; a complete preview search cannot fit review freeze and apply drift rules; the host-neutral contract breaks; a named seam is absent; or another subsystem/public contract must change. Stop YELLOW for an unavailable test gate or one bounded senior decision. Preserve any completed work and report evidence without guessing.

## Execution result

GREEN — implementation completed and acceptance gates pass.

- Implementation commit: `86df4d4` (`Add Forge Canvas file attachments`).
- Changed files: 25 implementation, contract, schema/inventory, and focused-test files across `api/`, `engine/`, and the routed Forge plan. `api/mirror/store.py`, `api/tests/mirror/test_store.py`, and `stubbed-workspace/` were preserved unstaged and untouched.
- Preflight: branch `dev`; brief/schema baseline and Batch 3 attachment seams present. Private filesystem roots were derived from their owners. The Windows credential store has no source-defined filesystem root; the staging API accepts filesystem paths, so it cannot address credential entries as source files. No credential path was invented.
- Verification: `py -m pytest -p no:randomly engine/tests api/tests/test_printable_attach.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/webui api/tests/mcp_server` — 736 passed. `py -m pytest -p no:randomly api/tests` — 2,024 passed, 5 warnings. Targeted final regression rerun — 66 passed. `git diff --cached --check` passed.
- Deviations: none. The first full-suite run exposed three test expectation/instruction-sync failures; these were corrected, then both required gates were rerun successfully.
- Outstanding decisions: none for execution. Senior closure remains pending.
