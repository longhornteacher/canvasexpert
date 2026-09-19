# Roster Route Card

Routing scope: open this card only when the active handoff touches Roster, then use the
relevant row. It is not global executor context and does not authorize broad Roster
discovery.

This is a retained control-console implementation map. Agent-facing roster reads and
bounded local changes start at the runtime/MCP contract; the browser entries below describe
the console path and its safety seams, not a competing agent workflow.

Student Reports is a separate surface at `/students/reports`; it does not share Roster
state or mutation paths. Roster lenses are visual views over one loaded roster, not
separate datasets.

## Entry points and owners

| Concern | Owner |
|---|---|
| Route orchestration / roster merge | `api/webui/routes/roster.py` |
| Canvas sections and group mutations | `api/webui/routes/roster_canvas.py` |
| Pure normalization/warnings | `api/webui/routes/roster_helpers.py` |
| Group-set preferences, creation, labels | `api/webui/routes/roster_groups.py` |
| One-student and bulk validation/update | `api/webui/routes/roster_updates.py` |
| MCP adapter onto that same updater | `api/webui/roster_mcp.py` |
| Template/layout | `api/webui/templates/roster.html`, `api/webui/static/roster_workbench.css` |
| Shared browser state/bootstrap | `api/webui/static/roster.js` |
| Row markup and selection name map | `api/webui/static/roster/table.js` |
| Search, lenses, deep links | `api/webui/static/roster/filters.js` |
| Inline edit/debounced save | `api/webui/static/roster/inline_edit.js` |
| Selected Canvas group-set state | `api/webui/static/roster/group_state.js` |
| Bulk actions | `api/webui/static/roster/bulk.js` |
| Group builder/label editor | `api/webui/static/roster/groups.js` |
| Protected-name/scrub/export/vault tools | `api/webui/static/roster/safety.js` |

The template loads `roster.js` first, then `table.js`, `filters.js`, `inline_edit.js`,
`group_state.js`, `bulk.js`, `groups.js`, and `safety.js`. Preserve that order and the
`window.CE_ROSTER` seam for shared course/group/filter state, hooks, helpers, row status,
table rendering, and filtered/selected student access.

## Privacy and write boundaries

- Roster data is FERPA-protected: names, IDs, sections, accommodations, groups,
  pseudonyms, monitoring state, and private notes never enter the repo, fixtures, generic
  logs, or support output.
- Browser lenses/filter state do not create a second persistence or mutation path.
- An assistant can read and change local student settings over MCP
  (`get_roster`, `get_roster_student_settings`, `preview_roster_student_change`,
  `apply_roster_student_change`, `clear_roster_student_field`). This is a second entry
  point, not a second mutation path: `roster_mcp.update_student` calls
  `roster_updates.update_student` with the route's own injected dependencies, so route
  validation, extra-time/monitored handling, and group reconciliation all still apply.
  Changes are pseudonym-first and digest-protected; a write refuses when settings moved
  since the read. The reads are a deliberately narrow projection, omitting stored
  nicknames. Nicknames are
  unreachable through this path, blocked in the adapter as well as the tool layer,
  because `set_nicknames` would overwrite the teacher's scrub-coverage list.
- Canvas group membership changes remain explicit live Canvas mutations owned by
  `roster_canvas.py` and orchestrated through the existing route/update validation.
- Protected-name packs, identity exports, and vault backups remain private workspace
  artifacts. Do not print or fixture their contents.

## Symptom routing

| Symptom | Start with |
|---|---|
| Fetch/merge/warnings | `roster.py::roster_get`, `roster_helpers.py` |
| Student/bulk validation | `roster_updates.py`, `roster_student_update`, `roster_bulk_update` |
| Canvas group mutation | `roster_canvas.py`, `roster.py::_update_student_canvas_group` |
| Row markup/status/name map | `roster/table.js` |
| Search/lens/filter/deep link | `roster/filters.js` |
| Inline save/edit | `roster/inline_edit.js` |
| Group-set picker/state | `roster/group_state.js`, `roster_groups.py` |
| Group creation/labels | `roster/groups.js`, `roster_groups.py` |
| Bulk bar/actions | `roster/bulk.js`, `roster_updates.py` |
| Privacy/safety tools | `roster/safety.js` |

## Test routing

The handoff must name the focused Roster route/helper/template tests for the changed owner;
locate candidates with `rg --files api/tests | rg "roster"` only when the exact test is not
already known. Browser/template changes require a rendered `/roster` check, required
`window.CE_ROSTER` state, and zero new console errors. Use `tools/size_report.py` only for
size questions; this card does not carry line-count snapshots.
