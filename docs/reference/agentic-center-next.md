# Agentic center: next batch pointer

Batch 2, MCP lean, is implemented in commit `634f9d0`. Its focused and full gates pass, but
the final live revision stage is pending because both existing revision work items are leased
by another device. Do not start Batch 3 until that lease is intentionally released or taken
over under the cross-device rules. The next senior should then write a separate brief for
Batch 3 after reading only the routed sections below.

## Batch 3: console reduction

Reduce the control console to its charter around the local runtime:

- move operation review/retry and receipt access to `/`;
- retire Create (`/course-expert`) and `/course`;
- retire `/ai-expert` and its MagicSchool toolkit;
- shrink Students to the private Names page; and
- retain `list_staged_content` as the agent's cross-chat staged-item discovery tool.

Required reading for the next brief:

- `docs/contracts/agent-runtime-product-contract.md`: **Primary interface**, **Runtime
  boundaries → Control console**, and **Development expectations**;
- `api/webui/README.md`: **Rendered verification**, **Page map**, **CanvasAgent**, **Create**,
  **AI Helper Files**, **Course Info**, and **Roster module routing**; and
- `docs/reference/webui-presentation-system.md`: **Template API**, **Who these pages are for**,
  **Page conventions**, **CSS ownership**, and **Change propagation**.

No Batch 3 product choices are opened by this pointer. The next brief owns its scope and
acceptance criteria.
