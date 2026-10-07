# Project state and product principles

The single home for durable facts about *where Canvas Expert is* and *how that
constrains scope*. Read this before any decision about whether work is in-scope.
It exists so this context stops living in scattered chat threads and retired
handoffs and getting lost.

Keep it short and current. When a fact here changes (a launch date, a user
count), edit it; do not append a log.

## Status: active single-teacher pilot

Canvas Expert is not generally launched. During the first-semester pilot it is used by
exactly one teacher: the developer. That use includes real Canvas courses, assignments,
submissions, scoring work, and grade history.

## Userbase: 1 for the first semester

- As of **2026-09-16** the active userbase is exactly **1: the developer, who is also
  the pilot teacher**.
- The userbase remains one through roughly December 2026. Additional users come only
  after a deliberate decision to widen the pilot.

## What this means for scope (the load-bearing consequence)

There is no multi-user compatibility population, but the pilot now has live teacher state.
Therefore:

- **No compatibility code for hypothetical users.** Don't write dual-read shims,
  backward-compatibility mappings, retirement notices, or URL-stability indirection for
  users who don't exist. A one-time migration of the teacher's own live state is fine when
  the teacher wants it; delete it once it has run.
- **Protect live pilot state.** Never assume Canvas objects, submissions, grades,
  operation receipts, scoring sessions, or the private workspace are disposable. A clean
  source break does not authorize destructive cleanup or loss of teacher history.
- **No speculative legacy support.** Retired names and deprecated source paths should not
  remain for hypothetical users. Preserve or reconcile an existing pilot artifact only
  when the current task names that artifact and its safety boundary.

## Standing product principles

These follow from the state above and from repeated direction; they are not
slice-specific.

- **Minimize teacher effort.** Maximize useful output for the
  least total teacher effort, including setup, review, corrections, recovery,
  and maintenance. CE/CanvasMirror and the teacher's chosen agent own the
  supported creation, delivery, scoring, and revision workflows.
- **Keep creation agentic.** The teacher prefers their chosen cutting-edge
  consumer models for planning, material creation, and rubric design. Keep
  creation and delivery in the agent-teacher workflow as far as available tools
  allow: co-design the material and rubric, then have the agent deliver the
  reviewed work through CE's supported push path. For scoring, adequate quality
  and low review/correction effort matter more than the newest model. State
  delivery gaps honestly; do not promise capabilities CE has not implemented.
- **One source of truth per artifact.** Never ship a static copy *and* a
  generated copy of the same thing (e.g. an authoring contract or a scoring
  skill). Pick the one canonical source; generate or read from it everywhere.
- **Teacher-owned pedagogy and points.** The teacher and assistant choose teaching
  approach, standards, cognitive level, tier targets and rigor, feedback style, revision
  tasks, point values, and curve target/selection. Canvas Expert enforces delivery shapes,
  tier privacy, explicit review boundaries, and technical validity without prescribing
  those instructional choices.
- **Lean, and don't reinvent host interfaces.** The authoring contracts and teacher
  docs must be lean and un-wordy. Do not invent bespoke personas, make whole-file
  instructions the default for a connected agent, or add step-by-step orchestration
  on top of assistants that already provide conversation and rendering (Claude
  Desktop, ChatGPT desktop). Rely on the host assistant's own conversational
  ability; ship the runtime contract and narrow reference material, not a second
  harness around it. A standalone CanvasAgent reference may still include appendices
  for chat-only or setup use when no runtime connection is available.
- **Integrity help is part of the job.** Canvas Expert gathers evidence (writing
  timeline, submission history, text overlap between submissions). The
  agent may investigate further (web search, reading level, anything useful) and tell the
  teacher plainly what it thinks, in teacher-only agent commentary. The teacher decides
  what happens. Agent commentary never reaches a student or Canvas; that boundary is
  enforced in code. See
  `api/default_docs/AI Authoring/Writing Timeline (tracked assignments).txt`.

See also `AGENTS.md` → *Lean engineering defaults* for the engineering-side
expression of the same instinct.

## Open decisions

- Whether the time zone is a shared setting or comes from the Canvas course.
- Whether free-text student names are blocked or flagged.
- Whether tests use classes as house style and whether `pytest-randomly` stays enabled by default.
