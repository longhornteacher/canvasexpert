# AssignmentForge differentiated-family design

**Decision date:** 2026-09-19
**Status:** Current shared renderer-neutral family design, plus the Differentiated Hub
delivery style (decided 2026-09-25; see "Delivery styles").

## Boundary

AssignmentForge tiers are authored pedagogical variants. A tier label is not a
Canvas delivery instruction and does not authorize student placement. Assignment
and Quiz Forge are two renderers under one differentiated-family contract. The
shared owner is `api/operation_ledger/adapters/differentiated_bridge.py`.

The family owner is responsible for source safety, module placement, bridge creation and
activation, verification, recovery, reconciliation, and the private family link. Renderer adapters create and verify their source
objects, then call the shared family tail.

## Preparation and payload

The authored envelope contains content only. Delivery preview requires at least
two unique tiers. The teacher must also choose the due date, module placement, and publication
intent. A module is either an exact existing `module_id` or an explicit
`create_module` request with a name; the runtime never guesses.

AssignmentForge uses the `2.0-json` contract. Its canonical tier label selects the
fixed presentation palette; the teacher-configured public tag supplies the
student-visible title suffix. The offline `engine/rendering/forge/` renderer builds
each source description from normalized content before review freezes the payload.
The canonical label is not displayed to students and never maps students to a tier.

Canvas group and pod settings are intentionally ignored. Tier placement is manual and
teacher-owned after delivery.

## Canvas delivery

Each tier becomes an ordinary Assignment with the authored title, content, points,
submission settings, dates, and assignment group. The server owns the safety
fields: the source starts unpublished and has whole-course visibility, is omitted from the final grade,
and SIS-disabled. The source is
re-read after each mutation before the next step.

After all sources are verified, the shared tail attaches each source exactly once
to the selected module, proves that bridge items are absent from every module, creates
or adopts the family bridge, activates it with safe final settings, re-reads every
postcondition, and saves the family link last. Failed verification leaves a
recoverable operation rather than claiming a complete family.

## Review, result, and recovery

Preview and apply results expose exact source assignment IDs and URLs, safe tier
labels and group facts, the exact module, the bridge, and the family-link state.
Discovery, scoring, and reconciliation consume the verified family link and are
renderer-neutral. A missing or unverifiable link is actionable `needs_repair`, not
an inferred family link.

Recovery and reconciliation re-read live Canvas state, repair only the frozen
family invariants, remove legacy bridge-module placement when required, and save a
link only after the complete family is verified. No result contains student IDs,
names, or raw membership data.

## Delivery styles (decided 2026-09-25)

An AssignmentForge file uses one of three styles. The teacher chooses the style before
authoring, because Bridge and Hub tiers are different kinds of content. A tiered file
declares its style explicitly, and the runtime never infers the style from the shape.

| Style | Envelope | Canvas result |
|---|---|---|
| Everyone | no `tiers` | one ordinary assignment |
| Differentiated Bridge | `differentiation: "bridge"`, at least two tiers, each a complete variant | tier assignments plus the gradebook-only bridge (sections above) |
| Differentiated Hub | `differentiation: "hub"`, at least one tier, each supports-only | one ordinary assignment (the hub) linking to restricted per-tier pages |

QuizForge families remain Bridge only.

## Differentiated Hub

**Shape.** The hub is an ordinary whole-class assignment. It carries the shared
instructions, expectations, rubric, and shared supports, and follows whole-class option
semantics, including optional module placement and allowed SIS posting. Each hub tier
carries only tier `supports` (scaffolds, frames, guides) and becomes one Canvas Page
titled `<Base> - <public tag>`. A tier with no supplement gets no page. Students in
that tier see the hub, and none of the tier links work for them. Pages are never placed
in modules; students reach them only through the hub's links. Every student sees every
link, which the teacher accepts. A link to another tier's page shows Canvas's
access-denied page.

**Tier targeting.** Canvas **differentiation tags** (non-collaborative group categories)
are the source of truth for pods. A page's assign-to accepts a tag group. It refuses a
collaborative group-set group with `422 group_id is not valid` (verified 2026-09-25).
When preparing a push, the runtime reads tags live and matches each tier's configured
public tag to exactly one tag group by name. It keeps only category ID, group ID, and
name, and never reads membership. The runtime therefore knows tier → tag, and never
student → tier. Collaborative group sets (for example "Pods") are ignored.

**Teacher fallback.** If a tag cannot be resolved or assigned (none found, ambiguous,
unreadable, or refused by Canvas), the page stays restricted with nobody assigned and
the review/result names the exact page and tag for the teacher to assign in Canvas. An
unresolved tag never blocks the push and never makes a page visible to the class.

**Visibility law.** A tier page is created unpublished, restricted to overrides with
nobody assigned, and published only after a re-read confirms
`visible_to_everyone: false`. Canvas keeps that restriction across publishing, and a
restricted page with no assignees is hidden from every student (verified 2026-09-25 in
Student View: 403 on the restricted page, 200 on an unrestricted control page, with and
without module placement).

**Verified Canvas API facts (2026-09-25).**
- `PUT /api/v1/courses/:course/pages/:page_id/date_details` with
  `{only_visible_to_overrides, assignment_overrides:[{group_id}]}` returns 204.
- `GET` on the same path returns `visible_to_everyone` and `overrides[]`, with
  `group_id`, `group_category_id`, `non_collaborative`, and `title`.
- `GET /api/v1/courses/:course/group_categories?collaboration_state=non_collaborative`
  lists tag categories. The default `group_categories` call omits them.

## Non-goals

- Do not merge the AssignmentForge and QuizForge authoring grammars.
- Do not infer modules, due dates, publication, or bridge ownership.
- Do not leave family placement incomplete; tier sources are intentionally unrestricted and manually placed by the teacher.
- Do not place the bridge in a module or duplicate a source module item.
- Do not enable SIS posting or write New Quiz item scores.
- Do not change whole-class delivery or build browser UI parity with the host agent.
