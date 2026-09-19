# WebUI presentation system

This document governs retained pages in CanvasExpert's local control console. It does not
make the browser the product center or require browser parity for agent-facing work. The
local runtime and its host-neutral MCP contract are primary; new agent-facing capability
starts there. Browser changes should support setup, readiness, local state, review,
recovery, receipts, diagnostics, and other explicitly retained console responsibilities.

The presentation system separates private route behavior from shared visual chrome.
`base.html` is the private document root. Only templates in `layouts/` extend it.
Every live page extends a layout and receives only the
`ui/tokens.css`, `ui/foundation.css`, `ui/components.css`, and `ui/layouts.css`
bundle, in that order. Feature CSS belongs in `static/pages/` or its existing
page-owned stylesheet and is added through `head_extra`.

## Template API

- `layouts/workspace.html`: `workspace_variant`, `workspace_header`, `left_rail`,
  `primary`, `right_rail`, and `workspace_scripts`.
- `layouts/document.html`: `document_variant`, `primary`, and `document_scripts`.
- `layouts/wizard.html`: `primary` and `wizard_scripts`. It deliberately has no app
  header or readiness script so first-run setup stays focused.
- `layouts/_app_header.html`: the single migrated app header. It preserves the
  readiness strip and `theme-toggle` ID.
- `ui/_macros.html`: `page_header`, `panel`, `notice`, `empty_state`,
  `action_bar`, and `rail`. Native form controls remain native HTML.
  `page_header` emits a `div`, not a `<header>`: the app header is the page's only
  `<header>` and the contract test enforces that. `rail` wraps its caller content in
  `ce-rail__inner`, which is what makes rail contents follow the stage as it scrolls.

Workspace variants are `full`, `three`, and `left-main`. The layout owns outer
columns and responsive reflow; a page owns only real rail contents. At 1180px the
right rail flows below the stage; at 760px all workspace variants become one column.

## Who these pages are for

A teacher opening a retained Canvas Expert console page is at work, mid-day, often with a
class in the room. They are a professional using a control surface, not a visitor being
sold one. Every retained page is a working surface for a task the console still owns; the
connected desktop agent remains the primary working surface for agent-facing cooperation.

That rules out a whole category of page that is easy to write by reflex:

- **No taglines, no value propositions, no "why this exists" copy.** If a control needs
  a sentence explaining its worth, the control is wrong. Fix the control.
- **The working surface comes first.** A page opens on the thing the teacher operates:
  the list, the queue, the builder, the editor. Explanation goes below it, or in a rail
  group named Reference, or nowhere.
- **No onboarding-first layout.** Pages are seen hundreds of times and read once. A
  first-run tour occupying permanent space is a tax on every later visit. Setup guidance
  belongs in Welcome or Settings, not at the top of a daily surface.
- **Design carries the information.** Show state with state: a pressed button, a filled
  field, a live preview. Prose describing what the UI would do if you used it is a sign
  the UI is not showing it.
- **No roadmaps.** What the tool does not do yet is not page content.
- **Reference copy is terse and factual.** Limits, addresses, and steps are a short list.
  Nothing is repeated for reassurance.


## Page conventions

- **One title block per page.** Every page opens with a single `page_header` whose
  title matches its nav label, so a teacher never clicks one word and lands on
  another. Product names live inside the page, not instead of the nav word.
- **One heading scale**, set once in `components.css` at zero specificity via
  `:where()`: `--ce-text-title` (h1), `--ce-text-heading` (h2), `--ce-text-sub` (h3).
  11px uppercase eyebrows are labels, not a heading rank. A page overrides a size
  only with a reason.
- **Stage blocks are `ce-panel`.** A page may retune padding; it does not re-declare
  border, background, radius, or shadow.
- **Rail contents stick** 16px below the top, capped at viewport height with internal
  scroll, and go static under 760px where rails stack above the stage. The `<aside>`
  stays full height so its divider still runs the page length.
- **Checkbox and radio labels sit beside their words.** The global `label { display: grid }`
  is corrected by a zero-specificity `:where(:has(...))` rule, so a page's own checkbox
  styling still wins and no page needs its own antidote.

## CSS ownership

- `tokens.css`: palette, type, heading scale, radius, shadow, spacing, and widths.
- `foundation.css`: reset and native element defaults.
- `components.css`: shared component vocabulary (`ce-shell`, `ce-panel`, `ce-rail`,
  `ce-page-header`, `ce-btn`, `ce-field`, `ce-tabs`, `ce-notice`, `ce-actions`,
  `ce-table`, `ce-status-dot`, `ce-empty`).
- `layouts.css`: header, outer shells, and responsive columns.
- Feature CSS: page layout only; it consumes tokens and does not introduce palette,
  font, radius, or shadow literals.

Shared component classes are never JavaScript selectors. Behavior selectors use an
ID, existing feature class, or `data-ce-hook`.

## Migration map

The enforcement registry is `api/tests/test_presentation_contracts.py`. It is the
source of truth for route, template, layout, variant, rail count, and migration state.
This is a retained control-console presentation contract, not a mandate to add browser
routes or duplicate agent-facing workflows.
CanvasAgent uses `workspace/full`; Create uses `workspace/three`;
Gradebook, Students, and Settings use `workspace/left-main`;
Routines, Course Info, and About use `document/wide`; AI Expert uses
`document/standard`; and Welcome uses `wizard`. Student reports is not a route
presentation: it is a view inside the Students page (`_student_reports_panels.html`
included by `roster.html`, selected by `?focus=reports`), and `/students/reports`
redirects there. The document layout required
no interface adjustment at first use. The wizard shell provides the same responsive
outer-gutter ownership as the other layouts while intentionally omitting the app
header.

All registry rows are migrated. Its source checks are repo-wide: every live template
is layout-backed and free of static inline styles, all feature CSS consumes shared
tokens, and no live template can reference the removed legacy stylesheet pair.
`style.css`, `workbench.css`, `workbench_base.html`, `_workbench_header.html`,
`name_manager.html`, and `_course_picker.html` are retired; the route redirects they
previously accompanied remain route behavior, not template dependencies.

## Change propagation

| Change | Owner |
|---|---|
| Palette, type, radius, shadow | `ui/tokens.css` (and component consumption) |
| Header/navigation chrome | `layouts/_app_header.html` |
| Outer columns/responsive reflow | `ui/layouts.css` |
| Shared panel structure | `ui/_macros.html` or `ui/components.css` |
| Feature-only layout | page stylesheet |

Partials move with their first consumer. `_readiness_strip.html` moved with the
migrated header; `_push_common_scripts.html` remains behavior-only and keeps its
script order.
