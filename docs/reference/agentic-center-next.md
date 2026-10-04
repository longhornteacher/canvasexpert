# Agentic center: next batch pointer

The accepted Batch 1 execution report is in Git history at `628a4d7`. Batch 2 is the next
ready unit; read only the routed sections below and inspect current code before writing a new
brief.

## Batch 2: MCP lean

Merge and remove MCP tools while preserving the host-neutral cooperation loop, explicit review
boundaries, verified writes, and durable receipts:

- Merge the five ledger `apply_*` tools into `apply_operation`.
- Fold `preview_differentiated_quiz_push` into `preview_content_push`.
- Drop `list_sis_grade_bridges`; fold reconciliation preview into `preview_sis_grade_bridge`.
- Fold `get_work_item` into `list_work_items`; `refresh_course_structure` into `refresh_mirror`;
  and `list_sections` plus `list_groups` into one tool.
- Drop `list_staged_content`; fold `clear_roster_student_field` into the roster preview.
- Decide whether feedback revision becomes a scoring-session mode and where the workspace-reset
  pair belongs. Keep `push_content_live`. Name-only tool-use telemetry is optional and requires
  an explicit decision in the new brief.

Required reading, limited to these sections:

- `docs/contracts/agent-runtime-product-contract.md`: **Primary interface**, **Canonical
  cooperation loop**, **Runtime boundaries** (Agent runtime, Read spine, Action spine), and
  **Development expectations**.
- `docs/mcp-server.md`: **Tools**, **Token-lean results**, and **Verifying it works**.
- `docs/reference/operation-ledger-module-map.md`: **Facades**, **Execution Owners**,
  **Safety Boundaries**, and **Test Routing**.
- `docs/contracts/score-ledger-contract.md`: **1. Authority and private storage**,
  **2. Curves and review**, **3. Posting, verification, resume**.
- `docs/contracts/sis-grade-bridge-contract.md`: **5. Ordered write and verification path**,
  **7. Privacy, review, and recovery**, and **7a. Reconciliation and repair**.
- `docs/contracts/work-registry-contract.md`: **Purpose**, **Persistence boundary**,
  **Authority and adapters**, **Course context**, and **Forbidden behavior**.
- `docs/contracts/course-catalog-contract.md`: **Location and identity**, **Reads and refresh**,
  and **Durability and forbidden material**.
- `docs/contracts/feedback-scoring-contract.md`: **Direction 2 - Results** and **Session
  consumption and write safety**.
- `docs/guides/more-than-one-computer.md`: **Identity and agent privacy** and **Setting up
  another computer** (for workspace-reset placement and synced state).

The new brief owns scope and acceptance, including the next free `TOOL_SCHEMA_VERSION`, schema
snapshot regeneration, and measured tool-listing budget. The current known version is v76 with
62 tools; do not treat that count as a target after the merges.

## Batch 3 direction

After Batch 2, reduce the console to its charter: move operation review/retry and receipt access
to `/`; retire Create (`/course-expert`), `/course`, and `/ai-expert` with its MagicSchool
toolkit; shrink Students to the private Names page. Write a separate brief only when Batch 2 is
accepted. No Batch 3 choices are opened by this pointer.
