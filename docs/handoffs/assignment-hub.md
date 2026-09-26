# Direct brief: Differentiated Hub delivery for AssignmentForge

Status: current for execution. Senior: `/root`. Executor: one implementation agent.

## Objective

A teacher can push one AssignmentForge file as a **Differentiated Hub**:

- One ordinary whole-class assignment, the hub, holding the instructions, expectations,
  rubric text, and shared supports.
- One restricted Canvas Page per supplied tier, holding that tier's scaffolds.
- Canvas Expert assigns each page to the Canvas differentiation tag named by the tier's
  public tag.
- Any page it cannot assign stays hidden from every student and is named in the review
  and result for the teacher to assign in Canvas.
- Everyone and Bridge keep working. Bridge now requires an explicit style marker.

Product decisions (authority; do not restate or reinterpret):
`docs/reference/assignment-differentiation-design.md` "Delivery styles" and
"Differentiated Hub"; `docs/contracts/forge-presentation-contract.md` §2 (tier model
bullet), §4 (hub tier pages line and "Hub tier page layout"), §7, §8;
`docs/reference/forge-presentation-plan.md` §8 D7.

## Preflight and scope

- Work on `dev` at the commit that adds this brief, or its direct successor. Fetch and
  confirm `dev` matches `origin/dev`. Preserve the untracked `stubbed-workspace/`, and do
  not inspect or stage it.
- Confirm these seams exist as named, and stop RED if one does not:
  - `api/webui/af.py::_validate_tiers` (around :281) and `validate` (around :321).
  - `api/operation_ledger/adapters/differentiated_bridge.py::resolve_public_tags`
    (around :250, with the two-tier check inline), `normalize_base_title`, and
    `source_title`.
  - `api/operation_ledger/adapters/assignment.py`:
    - `AssignmentAdapter.build_payload` (tier branch around :122);
    - `capture_baseline`, `check_drift`, `freeze_review`, `execute` (tier branch
      around :538), `reconcile`, and `_ordered_steps` (around :709).
  - `api/operation_ledger/adapters/assignment_whole.py::execute`, including its
    `prepare_description` hook.
  - `assignment_tiered.py::_ambiguous_create_lookup` (the recovery pattern to mirror for
    pages).
  - `engine/rendering/forge/canvas_html.py::render_assignment` and `render_page`.
  - `api/content_push.py::_result_projection` (around :678).
  - `api/operation_ledger/catalog_reconcile.py::_scopes_for` and `_created_objects`.
  - `api/platform_services/canvas_client.py`: `canvas_get`, `canvas_get_all_complete`,
    and `_canvas_send`.
  - `docs/contracts/canvas-transport-owners.json`, enforced by
    `api/tests/test_canvas_mutation_ownership.py`.
- Owned surface:
  - the AssignmentForge validator and authoring contract;
  - `resolve_public_tags`;
  - the assignment adapter branch points;
  - a new `api/operation_ledger/adapters/assignment_hub.py` (execute, reconcile,
    differentiation-tag read, link substitution);
  - the two renderer additions;
  - the content-push result projection;
  - catalog reconcile scopes;
  - the transport-owner registry and `docs/reference/mutation-reconciliation-map.md`;
  - the tiered-family wording in `api/README.md`, the MCP staging appendix and
    docstrings, and `docs/mcp-server.md`;
  - `docs/reference/operation-ledger-module-map.md` owners;
  - corresponding tests.
- Report any other expansion before making it.

## Locked decisions and acceptance

1. **Style marker (clean break).**
   - AssignmentForge `2.0-json` gains top-level `differentiation`, either `"bridge"` or
     `"hub"`. It is required whenever `tiers` is present and refused when it is absent.
   - A tiered file without it is refused with one sentence telling the agent to ask the
     teacher which style to use and to re-fetch the authoring contract.
   - Bridge validation is unchanged: at least two tiers; `overview`, `directions`, and
     `supports` are replaceable.
   - Hub validation:
     - at least one tier;
     - tier fields limited to `{label, supports}`, with supports non-empty under the
       existing `_validate_supports`;
     - `overview` or `directions` on a hub tier is refused with a message saying hub
       instructions belong on the hub;
     - canonical, unique labels.
   - `resolve_public_tags` takes the minimum tier count from its caller, so the
     tag/collision/reserved checks are shared. The Bridge minimum stays two and the Hub
     minimum is one.
   - Update every tiered AssignmentForge fixture to `"differentiation": "bridge"`.
     QuizForge is untouched.

2. **Payload and preview.** In `build_payload`, a Hub:
   - renders the hub description once, through `render_assignment` in the `untiered`
     color with no tag;
   - includes the §4 tier pages line, whose hrefs are renderer-owned placeholder tokens
     (one per tier, never author-producible, because §8 already refuses author
     `{{…}}`);
   - renders each tier page body once through a new `render_tier_page(...)` per the §4
     "Hub tier page layout";
   - titles the hub with `normalize_base_title` and each page with `source_title(base,
     tag)`.

   Whole-class options apply to the hub unchanged, including optional module placement
   and allowed `post_to_sis`. Pages are never placed in modules, and page publication
   follows the hub's effective `published`. No MCP input schema change is expected. If
   one becomes necessary, stop YELLOW.

3. **Live tag read, done once when preparing.**
   - The hub adapter reads differentiation tags from live Canvas, never the mirror:
     - `GET /api/v1/courses/:id/group_categories?collaboration_state=non_collaborative`;
     - then each category's groups, both through `canvas_get_all_complete`.
   - Keep only category ID, group ID, and group name. Never request memberships, users,
     or student IDs.
   - Match each tier's public tag to group names across all tag categories, trimmed and
     case-folded. Record one status per tier:
     - `matched`: exactly one group;
     - `not_found`: no group;
     - `ambiguous`: more than one group;
     - `unavailable`: 401/403/404 or an incomplete read, applied to every tier.
   - Freeze the matched group ID in the payload and digest.
   - Collaborative group sets are ignored entirely.
   - An unresolved tag never refuses preview. It adds a `teacher_actions` entry naming the
     exact page title and tag to assign in Canvas.
   - Title collisions: a page with a hub page's exact title that this operation did not
     create refuses preview with the stable code `tier_page_exists`. The refusal lists at
     most the colliding titles, with no IDs.

4. **Apply order, fail-closed, with checkpointed steps.** Hub steps run in this order:
   1. `create_tier_page:i`: POST unpublished.
      - Exact-ID re-verify on resume.
      - An unknown-outcome create uses an exact-title, bounded-window lookup mirroring
        `_ambiguous_create_lookup`.
      - Pages are recorded by `page_id`, and every re-read uses `page_id`.
   2. `restrict_tier_page:i`: PUT `date_details` with `only_visible_to_overrides: true`
      and no overrides, then re-read and require `visible_to_everyone: false`.
   3. `assign_tier_page:i`: runs only for `matched` tiers.
      - First re-read the frozen group and require the same name and
        `non_collaborative: true`.
      - PUT the single `group_id` override, then re-read and require exactly that one
        override.
      - A changed group or a Canvas 4xx refusal leaves the page restricted with nobody
        assigned and records a teacher action. The operation continues.
      - Network errors and 5xx remain ordinary recoverable failures.
   4. `publish_tier_page:i`: runs only when the hub publishes.
      - Refuse to publish unless the latest read shows `visible_to_everyone: false`.
      - After publishing, re-read. If the page is visible to everyone, immediately
        unpublish it and fail the operation recoverably.
   5. Hub assignment: substitute each token with `/courses/:course/pages/:slug` from the
      verified page, then delegate creation, module placement, publication, and
      verification to `assignment_whole.execute`. The narrowest seam is its
      `prepare_description` hook or a payload copy.
   6. Final verification: re-read the hub description and require every expected href.

   There is no family link or bridge, and no new persistence format. Recovery and
   reconcile use the operation's step records only.

5. **Review and result.**
   - `freeze_review` and `_result_projection` expose a `hub` block:
     - the hub assignment;
     - per tier page: tier, tag, title, `page_id`, URL, published state, tag status, and
       teacher action;
     - `teacher_actions[]`.
   - Neither shows category IDs, group IDs, membership, or student data.
   - Bridge projections are unchanged.
   - `catalog_reconcile` also invalidates the pages scope for a hub push.
   - Receipt and executor variant code must not crash on hub step keys.

6. **Authoring guidance.**
   - Update `Author an Assignment (AssignmentForge).txt` §3/§5/§7/§8 to describe the
     three styles, and tell the agent to ask the teacher for the style before
     authoring.
   - Describe hub tiers as supports-only supplements, one or more, with a missing tier
     meaning no page.
   - Keep the no-student-placement rule. Update the example to carry
     `"differentiation"`.
   - Update the tiered wording in the MCP `_staging_appendix`, `api/README.md`, and
     `docs/mcp-server.md` to match. Docstring-only changes need no schema bump.

7. **Tests (AGENTS.md taxonomy; synthetic data; fake Canvas).**
   - Law: a tier page is never published while visible to everyone. Test this once at
     `publish_tier_page`, covering both the pre-publish refusal and the post-publish
     unpublish.
   - Law: the tag read issues no membership or user request, and no MCP-visible review or
     result contains group IDs or membership.
   - Contract: extend the existing parametrized AssignmentForge validation cases for
     missing, invalid, and misplaced `differentiation`, and for hub tier-field rules.
     Parametrize tag resolution over the four statuses.
   - Example: one hub push with two tier pages (one `matched`, one `not_found`) and no
     page for the third tier, asserting the steps, the hrefs, and the teacher action.
   - Example: one resume after an interruption between page creation and hub creation
     that creates no duplicate pages.
   - Renderer: tier page and hub line checks run under the existing §7 law tests.

## Non-goals

- Mirror or catalog storage of tags.
- Any change to `list_groups` or collaborative group reads.
- A per-course tier → tag setting.
- A back-link from tier pages to the hub.
- Editing hub pages after delivery.
- Printable tier scaffolds.
- The Canvas rubric (Batch 4; plan §7 now covers Hub).
- Scoring-packet inclusion of tier pages.
- Hub support in QuizForge.
- Web UI hub-specific review UI.
- Live Canvas writes by the executor.

## References (bounded)

- `AGENTS.md`; `docs/reference/project-state.md`.
- The design, contract, and plan sections named under Objective.
- `docs/reference/operation-ledger-module-map.md` (owners, safety rules, test routing).
- `docs/contracts/canvas-transport-owners.json` and
  `docs/reference/mutation-reconciliation-map.md` entries for the tiered and page
  adapters.
- `api/README.md` "Assignments (AssignmentForge)".
- The files named in Preflight.

## Verification gate

First run the focused gate:

```
py -m pytest -p no:randomly engine/tests api/tests/webui/test_af.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_assignment_ordered_steps.py api/tests/test_differentiated_bridge.py api/tests/test_page_operation.py api/tests/test_canvas_mutation_ownership.py api/tests/test_operation_ledger.py api/tests/mcp_server
```

Then run `py -m pytest -p no:randomly api/tests` once, because the style marker changes
fixtures across the tiered path. Do not start a live Web UI or MCP server. Report
commands, counts, and failures.

If `api/webui/static/push/core.js` fails on the hub review shape, make the minimal
tolerant change and report YELLOW so the senior can verify the rendered route.

After acceptance, the senior runs one live smoke in ELA 7 with an unpublished `[TEST]`
hub and deletes it.

## Stop conditions

Stop RED in any of these cases:

- a named seam is absent;
- `assignment_whole.execute` cannot accept a substituted description without changing
  its public behavior;
- the tag read cannot avoid membership;
- the fail-closed publish order cannot be checkpointed;
- another subsystem or public contract must change.

Stop YELLOW for:

- an MCP schema change;
- a Web UI script change;
- an unavailable gate;
- one bounded senior decision.

Preserve completed work and report evidence without guessing.

## Execution result

Pending.
