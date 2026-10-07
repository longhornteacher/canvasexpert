# Scoring diagnostic: repair slices

Status: senior slice plan, 2026-10-07. Slice A implementation authorized and in
progress with Luna subagents; slices B/C remain planning. Baseline checkout:
`a6be405` on `dev`; connected runtime build was not verified at preflight.

## 1. Outcome and evidence

The teacher asked for slices/briefs to address the monitored scoring diagnostic.
The outcome is a teacher-agent scoring loop that can read submitted work,
explain and recover holds without losing drafts, and reach exact-stage review
with fewer wasted calls and less repeated context.

The source report remains in the teacher's private local diagnostic artifacts.
Do not copy its transcript or private session data into this public repository.
This plan carries only content-free measurements and engineering findings.

| Observed | What it supports | What it does not prove |
|---|---|---|
| Five usable response rows, 54 held responses across five packets; structural health true throughout | Hold reasons and scoring readiness must be distinct and actionable | All holds have one cause or all should be released |
| Four held pseudonyms had readable current mirror text, with no frozen attempt metadata exposed; mirror advanced during the run | Expose frozen attempt identity and provide explicit recovery | A same-attempt hydration failure in those exact sessions |
| Refresh keeps the old bundle when there are no added/replaced submissions | Existing holds need a recovery path independent of new submissions | Repeated refresh alone will fix the incident |
| 41,286 packet-wrapper characters, including 22,562 contract characters | Separate generic instructions, teacher guidance, basis, and response costs before slimming packets | All contract content is redundant or safely removable |
| Three unproductive calls out of ten: lost discovery result, wrong ID, history-only limit refusal | Clear input semantics and reliable host result handling | Three server failures; the first two were agent handling mistakes |
| Host exposed 79,876 repeated preamble characters across 38 tools; checkout has one shared instruction block | Measure the host boundary separately from server listing size | Actual model tokens, earlier Haiku exposure, or a compaction cause |

Ten calls took 14,317 ms and returned 59,122 serialized wrapper characters.
These are one observed run, not a timing SLA, exact bytes, or model-token counts.
All five reads were single-page and included context. Later-page repetition is
a source-supported opportunity, not measured by this diagnostic.

## 2. Sequence and current pointer

**Execute next: slice A**, using the revised current brief
`../handoffs/scoring-evidence-simplification.md`. That brief owns acceptance and
its execution result. It incorporates recovery rather than creating a second
brief for the same hold pipeline. The other current CanvasMirror brief retains
its independent scope; no changes to its desktop/S12 decisions are proposed here.

| Slice | Teacher-visible result | Dependency and status |
|---|---|---|
| A — Readable work and safe hold recovery | Text-only work is available; each hold is explained; an explicit refresh repairs eligible frozen holds while preserving drafts/history; empty packets give truthful next steps | Existing brief in execution after teacher's go |
| B — Smaller packets and clearer calls | Required instructions appear once at the right point; ordinary reads disclose narrowing options; session identifiers and continuations are easy to use | Draft an execution brief after A lands, using section 4 below |
| C — Host metadata overhead | Server and connected-host costs are measured separately; any demonstrated CE-owned duplication is fixed at its owner | Can measure independently; choose implementation only after attribution, using section 5 |

Later rows are planning notes, not a queue of READY handoffs. On acceptance of A, retire its brief and
replace this pointer with B, naming this document's section 4 and the exact
canonical sections below. Keep unresolved host attribution and the teacher's
text-plus-unread-file choice as explicit carried decisions.

## 3. Slice A scope and acceptance

The current brief is authoritative for implementation details. Its additions:

- A single per-submission hold decision, ignoring assignment attachment coverage.
- Typed held rows with pseudonym, response key, reason, and frozen attempt/time;
  an explicit aggregate count and scoring readiness separate from packet health.
- State-dependent instructions for scorable, mixed, held-only, and empty packets.
- Same-attempt recovery through the existing explicit session refresh, even with
  zero added/replaced submissions. Recover only held/unposted work through the
  privacy path; changed attempts retain the existing teacher replacement choice.
- Preserved unrelated drafts, stored review choices, posted history, receipts,
  grade baselines and earlier artifacts; changed packet input invalidates old
  approval/digests and requires new review, without discarding authored work.
- Recovery reports exact recovered keys, including those before the new-row
  offset. Unknown attempt identity is a blocker, not permission to substitute.
- Synthetic failure/idempotency and stale-approval tests plus private, deliberate
  field checks on the tested runtime. No fabricated live grades or forced resets.

Keep the current rule that body text plus an unread required file is held unless
the teacher changes that choice. Fixing text-only work does not depend on it.
No new tool, dashboard, extraction engine, host grader, or Canvas write bypass.

## 4. Slice B: packet efficiency and usable tool contracts

### Required context and source owners

Read the canonical Scoring Sessions guide, AssignmentForge authoring reference,
project state, and product contract's "Primary interface" and "Canonical
cooperation loop". Read feedback-scoring contract "Direction 1 - SAFE bundle"
and "Direction 2 - Results", and MCP guide "Token-lean results". Then inspect:

- `api/feedback_contract.py`: generic result rules and generated contract text.
- `api/powergrader/scoring_packet.py`: context sizing, segmentation, digest.
- `api/mcp_server/tools.py`: `get_scoring_packet`, `_get_submissions_impl`,
  `get_submissions`, `discover_scoring_work`, and affected next-step generation.
- `api/mcp_server/server.py`: those wrappers' exposed descriptions/defaults.
- `api/powergrader/scoring_discovery.py`: assignment table and session IDs.
- Matching packet, contract, discovery, submission and MCP schema tests.

### Proposed change

Slim CE's generic scoring prose while retaining the complete result shape,
privacy rules, teacher precedence, score ranges/modes, untrusted-text treatment,
and teacher-only commentary boundary. Measure generic prose separately from
verbatim teacher feedback contracts, assignment basis, and response evidence.
Do not shorten the teacher's saved guidance or remove assignment context just
to hit a transport target. Use one canonical generator, not a second skill or
host-specific orchestration document.

Resolve omitted `include_context` to true at page zero and false on later pages
for ordinary scoring. Allow explicit true to reload context. Page zero with
scorable work still requires the full contract and basis; a continuation must
retain the same packet digest, item identities, and ordered complete response
segments. Feedback revision keeps its existing option refusal.

For whole-session held-only/empty work, return the diagnostic/readiness and
hold information without the long scoring contract; explicitly mark omitted
context. This is an empty-work branch, not permission to score a later readable
packet without its page-zero contract. Update the context-required refusal and
docs deliberately. Keep A's truthful recovery instructions.

State in the actual exposed `get_submissions` tool description that offset/limit
require `history=true`; current reads narrow via pseudonyms, include_text and
max_text_chars. Preserve refusal of inapplicable options. Do not reinterpret a
current read as history to make a wrong call pass.

Inspect the real discovery table before changing identity presentation: it
already labels `scoring_session_id` separately from `mirror_revision`. Give a
short copy-the-session-ID hint in existing result guidance if useful; do not
duplicate every row into a second format or add look-up tools. Lost envelope
handling belongs to the host's consumer, not the CE grading engine. Document
the existing one-text-block JSON result and `ok` inspection without changing
transport globally on the strength of one decoding mistake.

### Acceptance and measurement

1. Generated generic contract characters fall at least 30% against A's baseline
   on the same synthetic packet. Verbatim teacher contract and required scoring
   basis survive byte-for-byte; semantic boundary tests remain passing. If the
   target requires dropping a rule, report the tradeoff rather than dropping it.
2. Held-only/empty packet results carry no long scoring contract and no staging
   instruction. Scorable page zero retains required context; continuation
   defaults omit it; explicit reload works. No response text is silently lost.
3. Published tool metadata exposes conditional options before a call. Valid
   current narrowing and paginated history continue to work, with typed
   refusal for incompatible options. Session IDs remain labeled and usable.
4. Compare serialized result sizes on fixed synthetic empty, mixed, scorable,
   and multi-page packets. Record component characters and estimated tokens
   separately; no arbitrary latency target and no token promises from characters.
5. A read-only host smoke check uses one discovery result, copies its labeled
   session ID, and makes a valid bounded cross-check without preventable refusal.
   Report host handling errors separately; avoid replacing results with another
   discovery call merely because the consumer forgot to decode/store them.

### Ownership and gate

One executor can own this small overlapping surface. If delegated, packet and
contract owner takes `feedback_contract.py`, `scoring_packet.py` and their tests;
agent-surface owner takes `tools.py`, `server.py`, discovery and MCP tests; lead
owns docs, schema version/snapshot, generated inventory and listing budgets.
Take the next free schema version where the published contract changes. Do not
expand the shared server instruction block to explain tool-specific options.

Focused gate:

```
py -m pytest api/tests/test_feedback_contracts.py api/tests/powergrader/test_scoring_packet.py api/tests/mcp_server -p no:randomly -q
```

Run the full suite before GREEN across these boundaries. Use A's baseline
failure record and record any new failures distinctly. No Web UI changes are
planned; adding them requires a scope decision and rendered-route verification.

## 5. Slice C: attribute host-expanded metadata before changing CE

Begin with a bounded read-only comparison of one server initialize/tools-list
result and one connected host's exposed tool metadata, on an identified loaded
build. Use synthetic/public tool metadata; keep raw host transcripts private.
Inspect `server.py` registration, `contract.py`, and
`api/tests/mcp_server/test_server_instructions.py`; use runtime startup owners
only if they are needed to establish loaded build identity.

Record separately: shared initialize instructions; server descriptions/schema
characters; host-expanded descriptions and repeated-prefix characters; actual
tool-result characters; and model-token/compaction evidence only when the host
exposes it. Do not sum ALL_TOOLS strings and call them loaded model context.
Do not add a new diagnostics tool or always-on transcript recorder for this.

If duplication is CE-owned, fix the registration/serialization owner and retain
essential per-tool input/write semantics. Verify unchanged tool behavior/schema
and remeasure both boundaries. If expansion is host-owned, keep CE concise and
record the limitation; propose an available host setting or upstream fix only
after checking its actual behavior. Do not strip all shared safety instructions
or make the runtime depend on one host to compensate.

Acceptance: identified runtime build, reproducible two-boundary measurements,
an attributed cause or explicitly bounded unknown, and before/after evidence
for any fix claimed. Server listing-budget success alone is not evidence of a
host fix. An actual earlier Haiku transcript is required to attribute those
compactions; absent it, compaction causality remains unknown.

For CE source changes run:

```
py -m pytest api/tests/mcp_server/test_server_instructions.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tool_dispatch.py -p no:randomly -q
```

Expand the gate only for the owner actually changed. Pure measurement requires
no product tests and is supporting evidence, not a standalone product repair
or a reason to delay A. Write the concrete implementation brief once attribution
makes the fix reviewable; keep unresolved host work out of A's acceptance.
