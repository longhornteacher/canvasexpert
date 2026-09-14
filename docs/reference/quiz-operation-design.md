# QuizForge operation-ledger design

**Origin:** Historical 11d discovery

**Decision date:** 2026-09-14

**Status:** Implemented architecture reference.

## Existing live path

Course Expert and standalone Quiz use the typed `content.quiz` Operation Ledger
prepare/review/apply path. `qf_pusher.py` remains the local plan and whole-quiz transport owner
consumed by the adapter. The former `push_tiers.py` direct differentiated CLI is retired; it
cannot bypass the reviewed family operation.

## Confirmed Canvas objects

Canvas New Quiz create returns a quiz backed by a Canvas assignment. Item create returns an exact
item ID and supports exact GET verification. The assignment supports ad-hoc student overrides,
`only_visible_to_overrides`, `omit_from_final_grade`, and `post_to_sis`. Assignment-type module
items return an exact ID and can be verified by item ID and content ID.

No separate Stimulus API object is created. QuizForge stimulus HTML is intentionally inlined into
the question payload before item creation.

Sources:

- <https://developerdocs.instructure.com/services/canvas/resources/new_quizzes>
- <https://developerdocs.instructure.com/services/canvas/resources/new_quiz_items>
- <https://developerdocs.instructure.com/services/canvas/resources/assignments>
- <https://developerdocs.instructure.com/services/canvas/resources/modules>

## Locked decisions

### Planning and subprocess isolation

- `qf_pusher.py::build_push_plan(path, settings)` produces a local normalized quiz-create
  payload, ordered item payloads, assignment settings, metadata, and module request.
- Preparation invokes JSON-plan mode through a bounded subprocess and validates the output before
  persisting it. The adapter never invokes a legacy live-write subprocess.
- Whole-class mode keeps one ordinary quiz and preserves its existing publish, module, date,
  category, and SIS choices.

### Targets and differentiated identity

- One operation target owns one selected course and the complete differentiated family.
- A family contains two or more ordered QuizForge files. Every file has the same exact trimmed,
  unsuffixed base title and declares one canonical pedagogical tier in `metadata.variant` or the
  supported `metadata.variant_label` alias.
- `Support`, `Core`, `Accelerate`, and `Extend` resolve through `config.get_tier_tags()` to required,
  unique, trimmed public Canvas tags. The server appends ` - <tag>`; an authored suffix or a
  differing title is rejected.
- Selected `group_name` remains separate Canvas membership authority. The server resolves groups
  in the teacher-selected category and requires nonempty, nonoverlapping exact active-roster
  coverage. Raw student IDs remain transient.
- Preparation requires one timezone-aware due timestamp, a selected module, equal New Quiz totals,
  one assignment group, valid public tags, and no unknown same-title collision.

### Ordered mutation steps

For each source in request order:

1. create the color-suffixed New Quiz and checkpoint its exact quiz/assignment ID;
2. restrict the assignment before any possible publish;
3. create and verify every transient group/extra-time override bucket by exact ID;
4. create and verify every ordered item by exact ID; and
5. patch and verify the assignment as published, override-only, points-graded, omitted from the
   final grade, SIS-disabled, and due at the requested timestamp.

No source gets a module item. After every source verifies, the shared differentiated-family tail
creates the unsuffixed bridge in a safe inactive shape, attaches only that exact bridge ID to the
selected module, activates the bridge, and registers the fully re-verified family. The bridge is
due at 23:59 on the same source date and UTC offset and carries the runtime Canvas Dashboard link.

### Durable progress and recovery

The polling progress endpoint exposes PII-minimized target and step state. Every Canvas send has a
write-ahead marker and exact-ID postcondition. Same-title matching never proves success. Unknown
matches block; only checkpointed exact IDs are excluded during retry.

An uncertain send is `sent_unknown` and is never repeated by guess. A definitive downstream
failure is partial and resumes only from exact-ID reconciliation. Retry cannot duplicate quizzes,
overrides, items, bridge, module, or module item. Registration occurs only after all required live
postconditions pass.

### Browser and teacher workflow

The browser sends ordered `{path, group_name}` variants and shared delivery settings. It sends no
Canvas URL, group/category ID, student ID, or pre-suffixed title. Frozen review shows each
pedagogical tier, public tag, exact source title, group/count facts, common settings, and the
planned bridge.

A teacher request to land the family authorizes the internal reviewed sequence for that exact
course and family. Results report the created source and bridge URLs and direct the teacher to
Canvas Live for review and teacher-owned Canvas Grade Sync. The retired differentiated CLI cannot
perform live writes.
