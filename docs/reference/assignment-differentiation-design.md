# AssignmentForge differentiated-family design

**Decision date:** 2026-09-19
**Status:** Current shared renderer-neutral family design.

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

## Non-goals

- Do not merge the AssignmentForge and QuizForge authoring grammars.
- Do not infer modules, due dates, publication, or bridge ownership.
- Do not leave family placement incomplete; tier sources are intentionally unrestricted and manually placed by the teacher.
- Do not place the bridge in a module or duplicate a source module item.
- Do not enable SIS posting or write New Quiz item scores.
- Do not change whole-class delivery or build browser UI parity with the host agent.
