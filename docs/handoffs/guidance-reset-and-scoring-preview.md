# Guidance reset and scoring preview

Status: READY for a lead executor (Sonnet) with parallel Sonnet/Haiku subagents.
Senior: Opus, 2026-10-03. Baseline: `dev` at `3eacc87`.

Objective: turn the repo's absolute rules into a few firm rules plus reasoned defaults, lift the
integrity-analysis ban, and give Scoring Sessions a review step in the agent's own preview before
anything reaches Canvas, with agent commentary for the teacher and warnings before every push.

Background, for the curious (not required reading): the three `State of the Repo 10-3` takes in
`docs/reference/`. Everything you need to execute is in this brief.

## Teacher decisions (2026-10-03, locked)

1. **Only three firm rules:** no tokens or student data in the repo; pseudonyms at the agent
   boundary (CE is the only thing that holds the token and talks to Canvas); bind `127.0.0.1`.
   Everything else is a default with a reason.
2. **Live testing is allowed.** Canvas is the staging ground. Deliberate runs against the real
   workspace and real Canvas are fine; pseudonymity still holds.
3. **Deleted is not banned.** The retired-paths test stays as a tripwire for accidental
   resurrection only.
4. **The agent refreshes on its own.** No teacher permission for `refresh_mirror`,
   `refresh_course_structure`, or `refresh_scoring_session`. API limits are not a concern; the only
   cost of a pointless refresh is time, so skip refreshes when data is within policy.
5. **Drop "never read back grades."** It has no reason left.
6. **Integrity help is wanted.** Remove the integrity ban. CE gathers evidence; the agent reads
   it, does its own investigation (web search, reading level, anything useful), and writes a
   conversational analysis for the teacher. The teacher decides what to do.
7. **Scoring preview in the host.** After staging, the agent shows a preview of proposed scores
   and comments in its own conversation surface (Claude Desktop, ChatGPT Desktop). Agent
   commentary is visibly separate (highlighted yellow, labeled teacher-only). The teacher edits
   through conversation, then tells the agent to push. Canvas Live remains the record and the
   place for later edits.
8. **Pseudonyms in the preview.** The teacher knows the stand-in names. No real names, no
   CE-hosted preview page.
9. **Warn before pushing.** Before any Canvas push (scores, comments, content), the agent says
   what will change and any warnings, then waits for the teacher's go.
10. **Late-work policy is unresolved.** Show late facts in the preview; do not change late policy.
11. **Parallel execution.** Retire "only one agent writes." A lead executor may run writing
    subagents in parallel on disjoint files.

## How to run this brief

- **Lead (Sonnet):** reads `AGENTS.md`, this brief, and only the references named here. Runs the
  preflight, launches workstreams A, B and C as parallel subagents, integrates, owns the shared
  counters (schema version, snapshot, instruction budget), runs the full gate, commits, and writes
  the `Execution result`.
- **Subagents:** A, B, C are writers (Sonnet). Use Haiku helpers for read-only work: reference
  inventories (`grep` for renamed names and codes), running focused suites, checking docs for
  leftover absolutes.
- **File ownership:** each writing subagent edits only the files its workstream owns below. If a
  workstream needs a change in a file it doesn't own, it tells the lead, and the lead makes it or
  routes it. All work happens in the one `dev` working tree; no subagent commits.
- **Tests while working:** subagents run only their focused tests. The lead runs the full suite
  after all three finish, because a full run mid-edit sees other workstreams' half-done state.
- **Commits:** the lead makes one commit per workstream on `dev` after the gate passes. Don't push.
  End each message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Leave alone:** the three untracked `docs/reference/State of the Repo 10-3 - *.md` files.
- **Ask, don't guess:** if the code contradicts this brief, or a choice would change what the
  teacher sees in a way this brief doesn't cover, stop that thread and ask the teacher in chat.

## Preflight (lead, before launching writers)

1. `git status` shows only this brief and the three State of the Repo files as new (untracked or
   committed). `HEAD` is `3eacc87` or a later commit whose changes don't touch the files below. If
   they do, reread those seams first. Commit this brief with workstream A if it isn't committed.
2. Confirm the seams named in this brief still exist (`grep` the function names). Report any that
   moved.
3. **Canvas posting-policy field.** Confirm that `GET /api/v1/courses/:course_id/assignments/:id`
   returns `post_manually` (boolean). Use Canvas's REST docs, or, with the teacher's OK in chat, one
   live read of a real assignment through CE's read transport. If the field doesn't exist, ask the
   teacher before workstream C builds the posting warning (fallback idea: GraphQL
   `assignment { postPolicy { postManually } }`).
4. Baseline: see the Verification gate.

## Workstream A: guidance (docs, plus one test message)

**Owns:** `AGENTS.md`, `docs/reference/project-state.md`, `docs/mirror.md`, `docs/README.md`,
`docs/reference/powergrader-module-map.md` (delete), `api/tests/test_retired_paths.py`.

### A1. AGENTS.md

Replace the `## Non-negotiable guardrails` section with this text (adjust wrapping only):

```markdown
## Firm rules

These three hold everywhere. Everything else in this file is a default.

1. **No secrets or student data in the repo.** Tokens live in the OS credential store (or the
   gitignored `api/.env` for CLI use). Names, IDs, submissions, grades, comments, notes, and
   roster data never go into commits, fixtures, logs, or output that leaves the teacher's
   machine. Private output belongs in the teacher's workspace or gitignored folders. The
   pre-commit hook is only a backstop.
2. **Pseudonyms at the agent boundary.** Student data reaches an AI agent only as stable
   pseudonyms through Canvas Expert's gate. Canvas Expert is the only thing that holds the
   Canvas token and talks to Canvas.
3. **Local only.** The token-holding app binds `127.0.0.1`. No public routes or external
   exposure.

## Defaults

Good practice, each with its reason. When a default doesn't fit the situation, say so and ask
the teacher instead of following it blindly or quietly working around it.

- **Keep district and teacher configuration out of source.** The repo is public. Canvas URLs,
  rosters, rubric names, time zones, and school hours belong in the workspace or settings, and
  `CANVAS_BASE_DEFAULT` stays empty.
- **Describe AI privacy honestly.** SAFE artifacts are pseudonymized and scrubbed, not
  anonymous. Don't call them "FERPA safe."
- **Keep the runtime host-neutral.** Return semantic data, summaries, and warnings any MCP host
  can use. Hosts render previews their own way; core behavior shouldn't depend on one host.
- **Tests stay isolated; live runs are fine on purpose.** `api/tests/conftest.py` isolates
  config, `%LOCALAPPDATA%`, credentials, and the workspace, so test `api.*` code under pytest. A
  stray `py -c` that imports `api.*` touches the teacher's real stores, so don't do that by
  accident. Deliberately running the app or MCP server against the real workspace and real
  Canvas is fine and often the best check: Canvas is the staging ground. Say what you'll touch
  first, and keep anything students could see unpublished or unposted unless the teacher says
  otherwise.
- **Ask, don't guess.** If a brief, a doc, or the code disagrees with the situation in front of
  you, ask the teacher. Never tighten or reverse a teacher's decision without asking, and record
  decisions where the next agent will find them.
- **Deleted means deleted, not banned.** Removing a feature records a decision, not a rule.
  `api/tests/test_retired_paths.py` catches accidental resurrection (old-lineage merges); if the
  teacher wants something back, delete its row.
```

Replace `## Execution model: senior design, one executor` and its subsections (Senior
responsibilities, Executor responsibilities, Traffic lights, Durable context and escalation)
with:

```markdown
## Execution model

A senior agent owns architecture, scope, and acceptance, and writes one brief in
`docs/handoffs/`. A lead executor runs it. The lead may split the work across parallel
subagents (for example Sonnet or Haiku) that read, test, or write. Give each writing subagent
its own files so two never edit the same file; the brief's workstreams say who owns what. The
lead integrates, runs the gate, and reports.

### Senior

- Understand the teacher-visible outcome, and talk through choices that change direction.
- Write the brief: objective, teacher decisions, acceptance criteria, non-goals, workstreams
  with file ownership, the test gate, and what to ask about.
- Accept against the brief. Check risky seams and missing evidence instead of redoing the work.

### Lead executor

- Read this file, the brief, and the references it names.
- Preserve unrelated worktree changes.
- Run the brief's preflight. If an assumption is false, ask.
- Run workstreams in parallel where the brief allows, then integrate and self-review.
- Run the gate. Report in chat and in the brief's `Execution result`: traffic light, commit
  hashes, changed files, commands and counts, deviations, open questions.

### Traffic lights

- **GREEN:** the acceptance criteria hold, the gate passes, and nothing deviates undeclared.
- **YELLOW:** something bounded is unfinished, or one teacher or senior decision is needed.
- **RED:** the repo contradicts the brief in a way that changes the plan.

Decisions that matter go in the brief, a contract, or `docs/reference/`, not only in chat or
agent memory. After compaction, resume from the brief and the current diff.
```

Other AGENTS.md edits:

- **Required context:** reword the project-state sentence to: "Read `docs/reference/project-state.md`
  before deciding scope: Canvas Expert is pre-launch with one user through the first semester, so
  compatibility code for hypothetical users is out of scope and clean breaks are preferred."
- **Test taxonomy:** keep Law / Contract / Example as guidance. Delete "If a proposed test is none
  of them, do not write it." and "One, by rule." Add: "A regression test for a real bug is always
  welcome." Leave the two open house-style questions as they are.
- **Risk and verification:** replace the paragraph starting "Run the full API or engine suite only
  at an explicitly declared integration" with: "The full suite (`py -m pytest api/tests
  engine/tests -p no:randomly -q`) takes about two minutes. Run it whenever it helps, and before
  reporting GREEN on anything that crosses subsystems. Rerunning a check is cheap." Delete "Do not
  rerun a successful executor matrix unless..." Keep the rest of the section.
- **Routing table:**
  - Merge the two PowerGrader rows into one: start at `docs/reference/powergrader-scoring-map.md`
    and `docs/guides/scoring-sessions.md`; boundary: "Sessions are private. Review happens in the
    agent's preview before a push and in Canvas Live after. Agent commentary is teacher-only and
    never reaches Canvas."
  - Connections / diagnostics boundary: "Diagnostics are read-only. One-click Connect/Disconnect
    (`api/ai_clients.py`) intentionally edits the Claude Desktop and ChatGPT config files, with
    merge, backup, and rollback. Nothing installs software, changes `PATH`, elevates, or starts
    tunnels."
  - Agent runtime / MCP boundary: "Primary agent-facing boundary: pseudonymized reads through CE,
    bounded local actions with prepare/review/apply behavior, host-neutral results."
  - CanvasMirror boundary: "A disposable local copy for reads and planning. Operation-ledger writes
    re-check live Canvas before changing it."
  - Add rows: Score records (`docs/contracts/score-ledger-contract.md`,
    `docs/contracts/submission-history-contract.md`; private, append-only score evidence); Work
    discovery (`docs/contracts/work-registry-contract.md`); Oral reading
    (`docs/contracts/oral-reading-evidence-contract.md`; audio and transcripts stay on the
    machine); Daily Writing / writing records (`docs/reference/writing-record-module-map.md`).
- **Voice:** in AGENTS.md only, reword remaining NEVER/MUST-style absolutes that are really
  defaults into plain statements with a reason. Keep the three firm rules firm. Don't touch the
  Build, test, and run section beyond wording.

### A2. project-state.md

- Replace the "No migration code." bullet with: "**No compatibility code for hypothetical
  users.** Don't write dual-read shims, backward-compatibility mappings, retirement notices, or
  URL-stability indirection for users who don't exist. A one-time migration of the teacher's own
  live state is fine when the teacher wants it; delete it once it has run."
- Replace the "The app reports; it never accuses." bullet with: "**Integrity help is part of the
  job.** Canvas Expert gathers evidence (writing timeline, submission history, writing history,
  text overlap between submissions). The agent may investigate further (web search, reading
  level, anything useful) and tell the teacher plainly what it thinks, in teacher-only agent
  commentary. The teacher decides what happens. Agent commentary never reaches a student or
  Canvas; that boundary is enforced in code." Point its link at
  `api/default_docs/AI Authoring/Writing Timeline (tracked assignments).txt`.

### A3. docs/mirror.md

Rewrite the "strict mirror-only law" passage (around lines 17-23), the "staleness is never
silent / never quietly papered over" lines (around 43-44), and the "This is an absolute" passage
(around line 53) as a design choice with reasons:

- Agent reads come from the mirror because it's fast, consistent, and goes through the
  pseudonym gate.
- When a read is outside the freshness policy, the agent refreshes it itself (`refresh_mirror`,
  `refresh_course_structure`, `refresh_scoring_session`) and continues. Skip refreshes of data
  already within policy, because they cost time.
- Canvas Expert stays the only thing that talks to Canvas.
- Staleness is still reported honestly in every read.

Leave the sync mechanics (sidecars, deltas, passes) unchanged.

### A4. Pointer doc and guard message

- Delete `docs/reference/powergrader-module-map.md`. Repoint its inbound links (`AGENTS.md`,
  `docs/README.md`, `docs/reference/project-state.md`) to
  `docs/reference/powergrader-scoring-map.md`. Writing Timeline references point to the authoring
  doc named in A2.
- `api/tests/test_retired_paths.py`: keep the list and the test. Change the module docstring and
  the failure message to say: these paths were deleted on purpose; if this is an intentional
  revival, delete the row; if not, it's probably an old-lineage merge.

**Focused tests (A):** `py -m pytest -p no:randomly -q api/tests/test_retired_paths.py`, plus any
test that reads AGENTS.md or docs text (find them with `grep -rln "AGENTS.md\|project-state"
api/tests`).

## Workstream B: integrity unban and overlap evidence

**Owns:** `api/powergrader/writing_timeline.py`, `api/feedback_results.py`,
`api/feedback_contract.py`, `api/powergrader/session_builder.py`, `api/feedback_artifacts.py`,
`api/powergrader/scoring_preparation.py`, `api/powergrader/scoring_packet.py`, new
`api/powergrader/overlap.py`, `docs/contracts/feedback-scoring-contract.md`,
`api/default_docs/AI Authoring/Writing Timeline (tracked assignments).txt`, and their tests
outside `api/tests/mcp_server/`, except the test files workstream C names as its own.

### B1. Remove the ban

- `writing_timeline.py`: delete `OBSERVATION_WITHHELD_NOTICE`, `_INTEGRITY_CONCLUSION_PATTERNS`,
  `_EVIDENCE_LIMITATION_CLAUSE`, `sanitize_process_observation`, and any helper only they use
  (check `_observation_clauses`).
- `feedback_results.py`: in `reidentify` (around line 300), delete the sanitizer call and its
  comment.
- `api/tests/powergrader/test_writing_timeline.py`: delete the sanitizer tests.

### B2. Rename the field to `agent_commentary`

Rename `writing_process_observations` to `agent_commentary` everywhere outside
`api/mcp_server/` (workstream C renames inside it). Known sites: `feedback_contract.py:32`,
`feedback_results.py` (validation around 232, `reidentify`, `merge_rows_by_uid` around 364),
`session_builder.py:107`, `feedback-scoring-contract.md:124`. This is a clean break, with no
alias for the old name. It also applies to the file-based (Copilot) result shape, which shares
this contract.

### B3. Agent-facing rules (`feedback_contract.py`)

These rules reach agents on packet page 0, so the wording matters. Replace lines 59-61 with
rules carrying this meaning, written briefly:

- You may return `agent_commentary`: teacher-only notes on anything the teacher should know about
  this submission, including possible integrity concerns (copying, AI-generated text, work that
  doesn't match the student's other writing).
- Investigate as you see fit: Canvas Expert's evidence (`writing_timeline`, `evidence.overlap`,
  `get_submission_history`, `get_writing_history`) and your own checks (web search for
  distinctive phrases, reading level, anything useful).
- Say what you found and how strong you think it is, in plain words, and cite the evidence.
  Detector-style percentages aren't reliable, so don't invent them.
- Agent commentary doesn't change the score or the student-facing feedback by itself; the
  teacher decides.

On line 65 (oral reading), remove only "intent, cheating". Keep the speech-recognition and
disability limits.

Update `feedback-scoring-contract.md` (row 124 and any integrity prose) and the Writing Timeline
authoring doc (around lines 17 and 146): the Writing Timeline is evidence for the teacher and
the agent, and the agent may draw conclusions in teacher-only commentary.

### B4. Overlap evidence (new, deterministic)

New pure module `api/powergrader/overlap.py` (standard library only, no I/O):

`find_overlaps(responses, *, shared_text="", n=8, min_shared_words=25, max_pairs=5, max_samples=2, sample_words=30) -> dict[str, list[dict]]`

- `responses`: a list of `{pseudonym, item_id, text}` built from the **SAFE** (already scrubbed)
  response text.
- Normalize: lowercase, turn non-alphanumerics into spaces, split into words.
- Shingles: word n-grams (`n=8`). Remove every shingle that also appears in `shared_text`
  (assignment directions, rubric, shared materials, teacher-supplied context), so quoted prompt
  text doesn't count.
- Compare only responses with the same `item_id` and different pseudonyms. `shared_words` is the
  number of word positions in A covered by a shingle A shares with B.
- Report a pair when `shared_words >= min_shared_words`. Each side gets its own entry:
  `{"with": other_pseudonym, "item_id", "shared_words", "share": round(shared_words /
  len(words_a), 2), "samples": [up to 2 passages of up to 30 words from A's SAFE text]}`. Sort by
  `shared_words` descending and keep the top `max_pairs` per pseudonym.

Hook: find the single place where a Scoring Session's final SAFE bundle is assembled. Both
`prepare_scoring_session` and `refresh_scoring_session` must pass through it; if they assemble
separately, call one shared helper from both. Attach `evidence: {"overlap": [...]}` to each
SAFE response row that has overlaps, and only those rows. On refresh, recompute over the whole
bundle so late work is compared with everything. Make `scoring_packet.validate_safe_bundle`
accept the new key. Packet digests change as a result; that's expected.

**Focused tests (B):** new `api/tests/powergrader/test_overlap.py`: a law for prompt-text
exclusion, the threshold edges (24 vs 25 words), the same-item-only rule, and the per-side caps;
plus one packet-level example in the existing preparation test module.
`py -m pytest -p no:randomly -q api/tests/powergrader api/tests/test_powergrader_import_results.py api/tests/test_powergrader_copilot_packet.py`
(use the paths that actually exist).

## Workstream C: scoring preview, warnings, refresh, readback (MCP surface)

**Owns:** `api/mcp_server/server.py`, `api/mcp_server/tools.py`, `api/mcp_server/contract.py`
and the schema snapshot, `api/powergrader/scoring_apply.py`, `api/powergrader/session_actions.py`
(read-only unless a test seam needs it), `docs/guides/scoring-sessions.md`,
`docs/reference/powergrader-scoring-map.md`, `docs/mcp-server.md`,
`docs/contracts/canvas-transport-owners.json`,
`api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` (Appendix D only), and tests under
`api/tests/mcp_server/` plus `api/tests/powergrader/test_scoring_apply.py`.

### C1. Store the agent commentary (fixes a silent drop)

Today `stage_scoring_results` accepts the teacher-only field, runs it through `reidentify`, and
then never saves it. The session update loop (around `tools.py:3679`) copies only `ai_score`,
`ai_feedback`, `ai_item_results`, `frozen_curve`, and `grading`. Fix:

- Rename the `ScoringResult` TypedDict field (`server.py:70`) to `agent_commentary`.
- Store `target["agent_commentary"] = staged.get("agent_commentary") or ""` beside `ai_feedback`,
  in both branches of that loop.
- **Law test:** `agent_commentary` text never appears in any Canvas send payload, for every
  grade mode. Assert against `session_actions._payload` and the push transport recorder.

### C2. Preview

`session_actions._payload` is the single builder of what Canvas receives (`posted_grade`, late
fields, `text_comment`), and `scoring_apply.build_plan` projects it per student into
`plan["payloads"]`. The preview must come from those projected payloads, never from the agent's
own copy of the results.

- **Stage response:** on `staged`, and on `needs_teacher_input`, add
  `preview_summary: {posting, warnings, counts, attention}`. `attention` lists the pseudonyms
  that have warnings or agent commentary (cap 50). Point `_with_next` at `get_scoring_preview`.
  Keep the existing `rows` and `score_rows`.
- **New read tool:** `get_scoring_preview(scoring_session_id, offset=0, limit=25)`.
  - Requires staged results, else `nothing_staged`.
  - Recomputes the plan from the session. If it no longer matches the stored stage digest,
    return `preview_stale` ("stage again").
  - Returns `{ok, scoring_session_id, stage_digest, grade_mode, posting, warnings, counts, rows,
    held, offset, limit, total, next_offset}`.
  - Each row: `{pseudonym, raw_score, entered, points_possible, late, comment, agent_commentary,
    warnings, attention}`.
    - `comment` is exactly the projected `text_comment`, character for character.
    - `entered` is the projected `posted_grade`, or null in feedback-only mode.
    - `late` gives the plan's late decision (status, days, applied or waived).
  - `held`: `[{pseudonym, reason}]` for rows not in the plan, with a reason when one is known.
  - Order by pseudonym. Pass through `pseudonym.gate`.
- **Row warnings** (information, never blocking):
  - replaces a score already in Canvas, from the session baseline score, labeled "as of session
    preparation";
  - a late penalty is applied, or waived;
  - the entered mark differs from the raw score (floor or curve).
- **Assignment warnings:**
  - the assignment posts automatically: students see scores and comments as soon as they're
    pushed;
  - the posting policy couldn't be checked;
  - held rows.
- **Posting policy:** one Canvas GET of the assignment at stage time through scoring's existing
  read transport. Store `posting_policy: {post_manually, checked_at}` on the session. A failed
  read yields the "couldn't check" warning and never blocks staging. Register the read in
  `docs/contracts/canvas-transport-owners.json` if its test requires it.

### C3. Server instructions (`_SERVER_INSTRUCTIONS`)

Required content:

- **Refresh:** when a read is outside policy, refresh it yourself (`refresh_mirror`,
  `refresh_course_structure`, `refresh_scoring_session`) and continue; skip refreshes when data
  is within policy. Tell the teacher when a refresh brought in new or resubmitted work.
- **Preview:** after staging, show the teacher a preview from `get_scoring_preview`, however the
  host allows (a rendered view if possible, otherwise a table):
  - warnings first;
  - each student-facing comment exactly as returned;
  - agent commentary in a clearly separate block, highlighted yellow and labeled "Agent
    commentary (teacher only)".

  Edits mean restaging. Call `apply_staged_scoring_results` only when the teacher says to push.
- **Commentary:** put integrity concerns and anything else the teacher should know in
  `agent_commentary`, citing CE's evidence and your own checks.
- **Before any push:** before any `apply_*` or `push_content_live` call, tell the teacher what
  will change and any warnings, then wait for their go. A single go may cover several selected
  rows or assignments, as today.
- **Remove:** "Never refresh without an explicit teacher request", "ask if Canvas changed;
  refresh only after an explicit teacher request", "refresh_scoring_session if teacher asks", and
  "never read back grades".

The budget is `INSTRUCTION_BUDGET = 2200` and the current length is about 2,198. Trim first. If
the required content still doesn't fit, raise the budget to the measured length rounded up to
the next 100, and report it.

Update `api/tests/mcp_server/test_server_instructions.py`:

- Delete the assertions that pin the old rules: `"never read back"`, `"refresh only after an
  explicit teacher request"`, `"refresh_scoring_session if teacher asks"`.
- Add assertions for `get_scoring_preview` and `agent_commentary`, and that
  `"explicit teacher request"` is absent.

Also update the user-facing refusal in `scoring_preparation.py` around line 296
(`mirror_freshness_confirmation_required`). Workstream B owns that file, so send this change
through the lead:

- Rename the code to `mirror_refresh_needed`.
- Set `user_action` to: "Refresh this course's mirror with refresh_mirror, then retry. If the
  teacher has said nothing changed, retry with use_existing_mirror=true instead."
- Rename the internal `requires_teacher_confirmation` flag only if that stays small (under
  about 10 references); otherwise leave it and report.

Fix the stale docstring pointers to `ScoringSession/SCORING_SESSIONS.md` (`tools.py` around
2747, 3001, 3110). That file no longer exists. Point to "the scoring contract on packet page 0"
or drop the sentence.

### C4. Readback reconciliation

The code verifies every posted numeric score once, and the docs still say late rows only. Make
the docs match the code:

- Rename `_check_late_rows` to `_verify_posted_scores` (`scoring_apply.py:395`) and fix the
  caller's docstring (around 537).
- Rename the codes `late_readback_mismatch` and `late_readback_unavailable` to
  `score_readback_mismatch` and `score_readback_unavailable`. Keep `late_not_honored`.
- Update `docs/guides/scoring-sessions.md` (around lines 102-106, 156, 170, 199-200), the
  scoring map, and the `canvas-transport-owners.json` reason text that says "late-decision rows
  only".

### C5. Guide, schema, inventory

- `docs/guides/scoring-sessions.md`:
  - add a "Preview before push" section;
  - add an "Integrity help" section, matching B3 in meaning;
  - update the refresh rows in the table (around 179 and 190-193), so the agent refreshes
    itself;
  - fix line 209, so Canvas Live is the record and later edit surface and the preview is the
    first review.
- Bump `TOOL_SCHEMA_VERSION` from 73 to 74 (`get_scoring_preview` plus the field rename).
  Regenerate `tool_schema_v74.json` under pytest the way `api/tests/mcp_server/test_contract.py`
  expects, and delete `tool_schema_v73.json`; only the current snapshot is kept. Update
  `docs/mcp-server.md`, which is pinned to the registry by a test, and add
  `get_scoring_preview` to Appendix D of `START HERE - CanvasAgent.txt`.
- Rename the field in `api/tests/mcp_server/test_new_quiz_scoring_tools.py` and any other
  `mcp_server` test that uses it.

**Focused tests (C):**
`py -m pytest -p no:randomly -q api/tests/mcp_server api/tests/powergrader/test_scoring_apply.py api/tests/test_beta075_mcp.py api/tests/test_canvas_mutation_ownership.py api/tests/test_transport_ownership.py`.
Add the new preview tests in the module next to the existing scoring-apply tool tests, and
cover:

- an example: stage, then preview;
- a law: preview `comment` equals the projected `text_comment`;
- `preview_stale`;
- the posting warning for automatic, manual, and a failed read;
- the C1 law.

## Acceptance criteria

1. AGENTS.md has a `Firm rules` section with exactly the three rules in A1 and a `Defaults`
   section in which every item carries its reason. There is no "Non-negotiable" heading. The
   execution model allows parallel writing subagents with file ownership. The Connections row
   matches `api/ai_clients.py`, and the four contracts plus the writing-record map are routed.
2. `project-state.md` has the new integrity principle and the narrowed migration rule; the
   phrase "never accuses" is gone.
3. `grep -rn "sanitize_process_observation\|_INTEGRITY_CONCLUSION_PATTERNS\|OBSERVATION_WITHHELD_NOTICE\|writing_process_observations" api engine docs AGENTS.md`
   returns nothing.
4. `agent_commentary` containing "plagiarism", "cheating", or "AI-generated" survives staging
   unchanged, is stored on the session student, and comes back in `get_scoring_preview` (test).
5. Law: `agent_commentary` never appears in a Canvas send payload, in any grade mode (test).
6. SAFE packet rows carry `evidence.overlap` when two same-item responses share 25 or more
   words outside the shared prompt text, and carry none below that (tests).
7. Every preview `comment` equals the projected `text_comment` exactly (law test).
   `preview_stale` fires when the session no longer matches the stage digest.
8. Staging warns when posting is automatic or unknown, and never blocks on the posting read
   (tests).
9. Server instructions carry the C3 content, stay within the (possibly raised) budget, and no
   longer contain "explicit teacher request" or "never read back".
10. The readback rename and the new codes are in place, and the docs describe "every posted
    numeric score".
11. Schema v74 is the only snapshot on disk; `test_contract.py` and the `docs/mcp-server.md` pin
    pass.
12. `mirror.md` describes mirror reads as a design choice, and the agent refreshes without
    asking.
13. The retired-paths guard message is reworded, and the list is unchanged.
14. The full suite passes: `py -m pytest api/tests engine/tests -p no:randomly -q`.

## Non-goals

- No CE-hosted preview page or console scoring UI. No real names in the preview.
- No change to late-work policy or its math. The preview only shows it.
- No live-Canvas relay tool for agent reads (mirror reads stay; only the absolutism goes).
- No AI-detector scores or percentages built into CE.
- No content-push code changes; only the C3 instruction line covers warn-before-push for
  content.
- Not in this batch (see Opus's take):
  - legacy/migration code removal;
  - the quiz planner subprocess seam;
  - runtime-startup ownership;
  - learning-objective staleness fields;
  - the hard-coded `America/Chicago` and school hours;
  - deleting `workbench-canonical-flow-map.md`.

## Verification gate

**Baseline** at `3eacc87`, `py -m pytest api/tests engine/tests -p no:randomly -q`: 2660 passed,
5 warnings, 156.62s, no failures (senior, 2026-10-03). Don't rerun the baseline; start from it.

**Focused:** each workstream's focused command passes before the lead integrates.

**Full:** after integration, the full suite above passes once. The lead reports the count and the
time.

**Live (optional, lead):** with the teacher's OK in chat, run one real Scoring Session through the
canvas-expert MCP tools as far as `get_scoring_preview`: prepare, read packet, stage, preview.
Do not apply. Report what the preview returned (counts, warnings, whether commentary
round-tripped), not student content.

**Live acceptance (teacher):** after GREEN, the teacher runs a real Scoring Session in Claude
Desktop to the preview step and judges it. Pushing is the teacher's call.

**Report:** traffic light, commit hashes, changed files per workstream, test commands and counts,
the instruction length and budget, deviations, open questions.

## Execution result

Lead: Sonnet 5.5, 2026-10-03. Not pushed.

**Traffic light: GREEN** on the 14 acceptance criteria and the full gate. Live acceptance
(teacher) and the optional live run are still to do; see Open questions.

**Commits on `dev`**

- A `ca495a6`: AGENTS.md (Firm rules, Defaults, Execution model, routing), project-state.md,
  mirror.md, docs/README.md, powergrader-module-map.md deleted, retired-paths guard message,
  plus softened "mirror-only law" wording in `api/README.md` and two dailywriting docstrings.
- B `cbecdac`: integrity ban removed, `agent_commentary` rename, new `api/powergrader/overlap.py`
  and its hook, packet rules, `mirror_refresh_needed` rename.
- C `003b15d`: `get_scoring_preview`, preview/warnings/refresh instructions, readback rename,
  schema v74, scoring docs, START HERE and seeded-doc refresh.

**Gate**

- Full: `py -m pytest api/tests engine/tests -p no:randomly -q`: 2684 passed, 5 warnings
  (same five as baseline), 143.50s. Baseline was 2660, so 24 tests added.
- Focused (at the time each writer finished): A 41 passed; B 428 passed; C 382 passed with one
  failure that was the START HERE overview size cap, fixed before the full run.
- Acceptance grep for the removed names returns only stale `.pyc` files.
- Instruction block 2,667 characters; `INSTRUCTION_BUDGET` raised 2,200 to 2,700 (the brief's
  measured-length rule). `LISTING_BUDGET` 23,948 to 24,279 for the new tool. 69 tools, schema
  v74 is the only snapshot.

**Review.** One independent read-only review of the riskiest seams found three real defects,
all fixed and tested before the commits: (1) the preview did not go stale when apply would
refuse for a changed packet or a deactivated curve, so those checks are now shared helpers
used by both; (2) the posting-policy read ran under the scope lock, so it now runs before it;
(3) the agent rules contradicted each other on quoting pseudonyms, and nothing told the agent
to keep integrity concerns out of student-facing `feedback`. It found no `agent_commentary`
leak path to Canvas, receipts, the ledger, or any other MCP result.

**Deviations from the brief (all small, none change what the teacher sees beyond the brief)**

1. Workstream C ran as two writers (code, docs) on disjoint files.
2. The posting-policy read needed a new single-object read, `scoring_apply.default_assignment_read`
   and `read_posting_policy`, because the existing scoring read transport only accepts list
   pages. The MCP layer still never imports `canvas_client`.
3. `build_plan` has no `payloads`; the preview calls `scoring_apply.preview_rows`, built on
   `_projected_payload`, the builder the plan digest covers. It gained an optional
   `waive_user_ids` so a `waive_late` answer previews the waived bytes.
4. Readback rename was narrower than the brief implied: `score_readback_*` already existed as
   verification statuses. Renamed the remaining `late_readback_*` strings and
   `_check_late_rows` to `_verify_posted_scores`.
5. Overlap hook: `build_packet` only projects pages. The SAFE bundle is written by
   `scoring_artifacts.build_scoring_artifacts`, which was not in workstream B's file list, so
   `_attach_overlap_evidence` in `scoring_preparation.py` runs right after it in both prepare
   and refresh. `evidence` also had to be added to `_PACKET_STUDENT_COLUMNS` in `tools.py` and
   carried by `build_packet` (first segment only) so the agent actually receives it.
6. Edits outside the brief, all wording that contradicted decisions 4, 5 or 9:
   `mirror_freshness_confirmation_required` renamed in `feedback_revision.py` too; "ask the
   teacher whether to refresh" strings in `sis_grade_bridge.py`, `operation_ledger/executor.py`
   and three test pins; `_freshness_attention` and catalog stale notes in `tools.py`; content
   push next-step text, the authoring-contract footer, `docs/mcp-server.md` and START HERE now
   say "say what will land and wait for the go" instead of "their ask is the authorization"
   (text only, no content-push behavior change); `RETIRED_FILES` gained the replaced START HERE
   and Writing Timeline hashes so existing workspaces refresh; the stale PowerGrader test
   command in AGENTS.md was repointed; START HERE Appendix B was tightened to 7,115 characters
   (cap 7,200) to make room.

**Open questions and follow-ups**

- The `ask_teacher_confirmation` attention action code is unchanged. It is emitted from several
  places (`tools.py`, `attempts_grant`, `grade_adjustment`, two ledger adapters) and pinned by tests; only
  its reason text now says to refresh. Renaming the code is a wider change.
- `requires_teacher_confirmation` has 27 references and was left.
- Writing Timeline output is not projected onto packet pages today (`build_packet` never
  carries `writing_timeline`), though the new agent rule lists it as evidence. Follow-up if the
  agent should see it in the packet rather than via `get_writing_history`.
- Minor, unfixed: overlap `shared_words` counts positions, so a response that repeats a passage
  can look one-sided; a sample's 30-word cap counts tokens, not whitespace words; multi-item
  `agent_commentary` is joined with no item label; `late_not_honored` looks unreachable because
  `score_mismatch` classifies first.
- Claude Code still cuts the instruction block near 2,048 characters. The preview, commentary
  and push rules now end at 2,043 (pinned by a test); the cross-device, content and attachment
  hints at the tail remain cut, as before.
- Not run: the optional live preview run (needs the teacher's OK in chat) and the teacher's
  live acceptance in Claude Desktop.
