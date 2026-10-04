# Next: independent runtime startup

The agentic-center plan is complete. The next unit of work is to let the stdio MCP
runtime start without starting FastAPI. Write one execution brief before changing
startup ownership; preserve local recovery, mirror refresh, session hosting and the
console's independent launch path.

Read only:

- `docs/contracts/agent-runtime-product-contract.md`: **Primary interface**, **Runtime
  boundaries → Agent runtime / Control console**, and **Development expectations**;
- `docs/mcp-server.md`: **Running it** and **Verifying it works**;
- `api/webui/README.md`: **Pages and browser owners** and **Recovery and receipts**;
- startup owners `api/mcp_server/server.py`, `api/webui/server.py`, and `api/local_runtime.py`.

The roster-write boundary is already in `api/roster_service.py`. The completed console
batch deliberately scoped the no-WebUI-route-import criterion to roster writes only.
Remaining production imports outside the console are the startup planning inputs.
The console CLI launcher is an intentional consumer, separate from the MCP chain.

| External file | WebUI owner imported |
|---|---|
| `api/connections.py` | `api.webui: readiness` |
| `api/content_push.py` | `api.webui: deps` |
| `api/mcp_server/server.py` | `api.webui.server: app` |
| `api/mcp_server/tools.py` | `api.webui.deps: REPO_ROOT; api.webui: deps; api.webui: mirror_service` |
| `api/operation_ledger/adapters/assignment.py` | `api.webui: af` |
| `api/operation_ledger/adapters/forge_files.py` | `api.webui.attachment_validation: ALLOWED_ATTACHMENT_EXTENSIONS` |
| `api/operation_ledger/adapters/grade_adjustment.py` | `api.webui: mirror_service` |
| `api/operation_ledger/adapters/page.py` | `api.webui: pf` |
| `api/operation_ledger/adapters/sis_grade_bridge.py` | `api.webui: mirror_service` |
| `api/powergrader/context.py` | `api.webui: source_materials` |
| `api/powergrader/feedback_revision.py` | `api.webui: source_materials` |
| `api/powergrader/scoring_packet.py` | `api.webui: source_materials` |
| `api/powergrader/student_attachments.py` | `api.webui.source_material_extractors: collapse_ws, decode_bytes` |
| `api/qf_ui.py` | `api.webui.server: app` |
| `api/validate_qf.py` | `api.webui: af` |

Carry these teacher decisions forward without inferring answers:

- time zone: a shared setting or the Canvas course zone;
- free-text student names: block or flag;
- test house style: classes, and whether pytest-randomly remains enabled by default.
