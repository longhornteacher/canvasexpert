# Plagiarism / AI-writing detection — planning

Scratch notes for a possible Scoring Session triage + check path.
Not a contract, not a handoff, not implementation authority.

Last updated: 2026-09-14.

## Goal

During a CanvasAgent Scoring Session, spend a vendor/Grammarly-style check
only on papers that are *odd relative to process or this student*, not on
every constructed response and not because the scoring model “sounds AI.”

District instruction today: if you suspect AI writing, “use Grammarly or
something.” There is no Turnitin / district originality tool. Pseudonym
(Pokemon name) is accepted as good enough identity reduction for this
teacher’s own use.

## Non-goals (for now)

- Do not couple a check to score, student-facing feedback, or Canvas comments.
- Do not let the scoring agent invent the check set from prose vibe.
- Do not scan the whole class by default.
- Do not treat a detector percentage as a finding or a penalty.
- Do not surface fingerprint math, style stats, or “voiceprint” copy to
  the teacher. The teacher already reads the writing in Canvas.

The existing product instinct still applies as a *design preference*:
report observable facts; do not accuse. A detector, if added, is a second
look the teacher asked for after a cheap local gate.

## Current Scoring Session facts

Public flow: MCP `start_scoring_session` → `get_scoring_packet` →
`submit_scoring_results`. Canvas Live is review.

The agent already reads every scorable response to grade it. That is not
a reason to also send every response to a second vendor.

What a packet row currently contains: `pseudonym`, `item_id`, `text`
(plus first-page contract/rubric/shared context). Writing Timeline
aggregates can ride on tracked DOCX responses in the SAFE bundle /
Copilot packet, not as an integrity score.

`writing_process_observations` is already an optional teacher-only field
and is sanitized against integrity-conclusion language in code.

## Cheap gate (working rule)

CE / local Python computes allowlisted flags. The agent scores everyone,
notices which rows are flagged, and does not call a detector on its own.

Skip entirely unless the response is constructed writing with enough
student words (not short answers, code, oral reading).

Hard skip (do not flag for a vendor check):

1. **Respondus Lockdown Browser is in the assignment name.** In-class
   locked session is treated as “no need to check.” Matching is on the
   assignment title substring, not an LTI/quiz setting.

Clock is **not** a skip. Mon–Fri 8:00–16:00 America/Chicago is only
**less suspicious**. Off-hours weekdays and any weekend time are
**more suspicious**. An 8–4 weekday submit can still be flagged by
timeline mismatch or fingerprint jump.

Then flag on mismatch, not vibe:

- Tracked DOCX: no trail; one insertion that is most of the paper;
  tracking lock missing on a locked-handout assignment; weak edit-time
  vs length; `unrecognized_author_present` / `other_roster_author`.
- Any constructed writing with history: local Python says the piece
  jumped vs this student’s fingerprint (boolean / coarse flag for the
  agent; see below). Teacher-named papers always eligible.

Cap: a few papers per session (about 3, or ~10%), plus anyone the
teacher points at.

Innocent defaults (for agent/system copy, not a teacher fingerprint UI):

- No trail usually means they wrote in a blank doc, not that they wiped one.
- Word does not record paste vs type. Say “single block at 10:47,” never
  “pasted.”
- A clean trail / in-class submit does not prove authorship of the ideas.
- Retyped model output still looks organic.
- 8–4 weekday submit is not proof of classroom authorship.

## 1. Respondus skip (assignment name)

Teacher rule: Respondus is the Lockdown Browser product. If that name is
in the **assignment title**, do not check.

This is a naming convention the teacher already uses, not Canvas LTI
detection. CE already has `assignment_name` privately (session start /
`list_scoring_sessions` resume rows). Scoring-packet student rows do
**not** currently carry the title; they do not need to if CE flags
locally.

v0 match: case-insensitive substring `respondus` in the assignment name.
Do not also scrape `require_lockdown_browser`, New Quizzes LTI URLs, or
attempt-level “was LDB actually used.” Those remain unproven and are
out of scope unless the name convention fails in practice.

Caveats:

- A mistitled take-home essay named “Respondus practice” would skip.
- A locked quiz whose title omits “Respondus” would not skip.
- Repo “lockdown” hits are still a Writing Record fixture
  (`rep_t4_lockdown`), not this product.

## 2. Weekday 8–4 is less suspicious, not clean

Teacher rule:

- **Less suspicious:** Mon–Fri 08:00–16:00 America/Chicago on
  `submitted_at`. Do **not** auto-clear the paper.
- **More suspicious:** weekday outside that window, **or any weekend
  time.** Still not an accusation and not by itself a vendor call.

`submitted_at` **exists privately** and is **dropped from the scoring
packet**. That is the main “stripped needlessly” candidate for this
feature.

Where it lives now:

| Surface | `submitted_at` |
|---|---|
| Canvas / mirror submission objects | yes |
| Private session students (`session_builder`) | yes |
| SAFE bundle build (`feedback_artifacts.pseudonymize_submissions`) | read to pick latest; **not** copied onto SAFE response rows |
| `get_scoring_packet` rows | **no** — only `pseudonym`, `item_id`, `text` |
| MCP `get_submissions` | yes (pseudonymized columns) |
| Writing Record store + `get_writing_history` | yes (ISO) |
| Writing Timeline | insertion timestamps and `total_time_minutes`; not Canvas submit time |

CE can apply the clock locally. If the agent needs it at all, prefer a
coarse fact (`submit_window: in_school | off_hours | weekend`) over the
raw stamp. In-school does not mean “skip this student.”

Caveats:

- `submitted_at` is click-submit, not composition time. A paper written
  at 23:00 and submitted at 08:01 looks in-school. Treat that as a
  known hole, not a reason to pretend 8–4 is clean.
- Timezone: America/Chicago end to end (Writing Timeline already does).
  Do not leave packet stamps in UTC.
- “Weekday” vs instructional day: Calendar / bell schedule exists. A
  staff-only Monday or a Saturday school day may disagree with Mon–Fri.
  Default for v0 is weekday clock; calendar is a later refinement.
- New Quiz reported-at vs assignment `submitted_at` can disagree
  (`new_quiz_fetch` already treats this as a freshness concern).
- Off-hours is a **priority bump**. Stack with constructed-writing
  length, timeline mismatch, or fingerprint flag before spending a
  vendor check.

Other metadata that is stripped from SAFE / timeline on purpose (do not
casually un-strip):

- Real names, Canvas/SIS ids, signed URLs, local paths.
- Raw Office author strings (reduced to categories).
- Per-block revision text and the full insertion array (volume + billing;
  also contains student prose inside tracked runs).

Those are privacy/volume choices, not accidental. The accidental-feeling
gap for *this* plan is Canvas `submitted_at` (and Respondus-in-name)
never reaching the triage layer.

## 3. Fingerprint — Python-built, agent-only

Audience lock: **the fingerprint is for the scoring agent, not the
teacher.** The teacher already looks at writing in Canvas. Do not add a
teacher UI, observation paragraph, or “Bulbasaur’s sentences jumped”
copy. Do not attach style stats to `writing_process_observations`.

What the agent should see: a precomputed flag (and maybe a tiny enum
like `fingerprint: ok | jump | insufficient_history`). Not histograms,
not cosine scores, not compound-complex counts.

Build and diff are **local Python**, automatic, not an LLM task:

1. A script (or ingest hook) maintains a rolling per-student profile
   from Writing Record `student`-origin spans (not scaffolds, not
   quoted source).
2. At session / packet time, the same local code diffs the new piece
   against that profile.
3. The packet (or a sidecar the agent is allowed to read) only says
   whether it was flagged.
4. The agent does not recompute stylometry and does not narrate the
   math to the teacher.

Natural store: Writing Record / dailywriting, keyed like the existing
private identity model. Do not invent a parallel student table.

### Research answer (looked up 2026-09-14)

**Mean sentence length / “count the compound-complex sentences” is a
useful cheap observable, not the smart sole fingerprint.**

Classic stylometry (Wikipedia *Stylometry*; PAN authorship-verification
shared tasks; Stamatatos-style surveys):

- **Writer-stable cues are mostly closed-class and surface**, not
  topic words. Function-word frequencies (`the`, `of`, `and`, `that`,
  `to`, …) are the textbook invariant. Character n-grams are the
  workhorse for intrinsic plagiarism / style-change detection because
  they need no parser and survive messy text.
- **Average sentence/word length is common and coarse.** A mean of 18
  words can be all mid-length sentences *or* a mix of 5-word and
  40-word sentences. The **distribution** (variance, long-sentence
  rate) is more informative than the mean alone. Wikipedia notes this
  averaging problem explicitly.
- **Full syntactic parse** (“this is a compound-complex sentence”) is
  *not* the cheap standard first feature. Constituency/dependency
  depth is used in richer models, not as a v0 classroom counter.
  Subordinate-clause *markers* (`because`, `although`, `which`, `;`)
  are a cheaper proxy for the teacher story.
- **Short school texts are a known weak setting.** PAN and later
  short-text work treat tweets / tiny answers as harder than novels.
  A 60-word constructed response cannot support a compound-complex
  count.
- **Genre and growth beat identity.** Style shifts by assignment type
  (lab vs narrative) and over a student’s year. Cross-domain
  authorship verification is the hard PAN setting for a reason.
- PAN remains the shared evaluation venue; recent tracks also include
  generative-AI authorship verification. Those systems are *not* a
  local classroom fingerprint. Do not import an LLM detector as the
  first implementation.

The Bulbasaur sentence-complexity example is how a human would notice a
jump. It is **not** teacher-facing product copy, and it is not the
Python implementation. Implement a **small feature bundle**, not a
grammar-labeler.

### Cheap local bundle (if this is ever built)

Count-based, topic-light, student-origin spans only:

1. Function-word / closed-class rates (small fixed English list).
2. Punctuation and contraction rates (`;:` `'ll` `n't`).
3. Sentence-length **distribution** (mean, stdev or IQR, fraction of
   sentences ≥ N words).
4. Character or word n-gram overlap vs this student’s recent pieces
   (cosine / Jaccard on hashed counts), not vs the web.
5. Type-token and rare-word rate as secondary; they move with prompt
   vocabulary.

Do **not** start with spaCy compound-complex classification. Sentence
splits + function-word histogram is enough to test whether the gate is
useful. Optional parser only if the histogram baseline is too weak in
real use.

Limits:

- Early-semester cold start: emit `insufficient_history`, never a jump.
- Prompt and genre shift: compare within similar length/genre when
  possible, or require a large jump.
- Growth and scaffolding: ESL and sentence-frame instruction *should*
  change syntax. A jump after a mini-lesson is expected.
- Short text: do not flag 60-word answers on syntax grounds.
- Scrubbing: Pokemon name tokens slightly perturb vocabulary stats;
  prefer function-word and length-distribution features.
- Do not store or project a “voiceprint” that could be read as a
  biometric. Keep aggregate counters and opaque student keys in the
  existing private Writing Record identity model.
- Because this is agent-only, do not leak the feature vector into
  teacher Canvas comments, scoring results, or printable logs.

Implementation sketch (not scheduled): Python counters on ingest;
Python diff at packet build; agent-visible boolean/enum only.

## Vendor check (only after the gate)

Still undecided which tool. Constraints if it happens:

- CE makes the HTTP call, or the teacher pastes into Grammarly; the
  scoring agent does not independently ship SAFE text to a third API.
- Pokemon names are enough for this user; still say honestly that
  student prose is leaving the machine.
- Result stays teacher-only. Same score and comment whether checked
  or not.
- Fail closed: timeout / unpaid / 403 means “check unavailable,” not
  a suspicion.

## Preferred shape if this becomes product

1. Local Python flags (Respondus-in-name skip; 8–4 = less suspicious,
   off-hours/weekend = more suspicious; timeline mismatch;
   fingerprint jump boolean).
2. Agent scores the class; uses the flags; does not explain fingerprint
   math to the teacher.
3. Teacher reviews writing in Canvas as usual; may ask for a check on
   named papers.
4. Optional vendor/Grammarly step off the grade path.

First build, if any: **flags + cap**, not an API. The fingerprint
scripts are part of that first build if the gate needs history. The
API is easy once the agent is not choosing the set from vibe.

## Open questions

- Confirm the Respondus title substring in real assignment names
  (typos, “LDB”, “LockDown Browser” without “Respondus”).
- Coarse `submit_window` on the packet vs CE-only clock.
- Weekday clock vs canonical school calendar / Saturday school.
- Minimum Writing Record history before a jump flag may fire.
- How strongly 8–4 down-weights vs how strongly weekend up-weights
  when stacking flags into the cap.
- Whether “plagiarism” (copied sources / classmates) is in scope at
  all, or only AI-writing suspicion. Current district prompt is the
  latter. Cross-student duplicate detection would be a different local
  feature (and a FERPA-shaped one if it quotes peers).
- Where the agent-visible flag lives (`get_scoring_packet` row vs a
  separate compact sidecar) without becoming teacher UI.
