# Project state and product principles

The single home for durable facts about *where Canvas Expert is* and *how that
constrains scope*. Read this before any decision about whether work is in-scope.
It exists so this context stops living in scattered chat threads and retired
handoffs and getting lost.

Keep it short and current. When a fact here changes (a launch date, a user
count), edit it — do not append a log.

## Status: active single-teacher pilot

Canvas Expert is not generally launched. During the first-semester pilot it is used by
exactly one teacher: the developer. That use includes real Canvas courses, assignments,
submissions, scoring work, and grade history.

## Userbase: 1 for the first semester

- As of **2026-09-16** the active userbase is exactly **1 — the developer, who is also
  the pilot teacher**.
- The userbase remains one through roughly December 2026. Additional users come only
  after a deliberate decision to widen the pilot.

## What this means for scope (the load-bearing consequence)

There is no multi-user compatibility population, but the pilot now has live teacher state.
Therefore:

- **No migration code.** Do not write folder-rename migrations, dual-read
  shims, retirement notices, backward-compatibility mappings, or "URL stability"
  indirection for hypothetical future users. Source and unsupported local formats may
  still take clean breaks when the active brief explicitly establishes that no live pilot
  state depends on them.
- **Protect live pilot state.** Never assume Canvas objects, submissions, grades,
  operation receipts, scoring sessions, or the private workspace are disposable. A clean
  source break does not authorize destructive cleanup or loss of teacher history.
- **No speculative legacy support.** Retired names and deprecated source paths should not
  remain for hypothetical users. Preserve or reconcile an existing pilot artifact only
  when the current task names that artifact and its safety boundary.

## Standing product principles

These follow from the state above and from repeated direction; they are not
slice-specific.

- **One source of truth per artifact.** Never ship a static copy *and* a
  generated copy of the same thing (e.g. an authoring contract or a scoring
  skill). Pick the one canonical source; generate or read from it everywhere.
- **Lean, and don't reinvent harnesses.** The authoring contracts and teacher
  docs must be lean and un-wordy. Do not invent bespoke personas, "paste this
  whole file" framing, or step-by-step orchestration on top of assistants that
  already provide it (Claude Cowork, MagicSchool, ChatGPT Work). Rely on the host
  assistant's own conversational ability; ship the contract, not a harness around
  it.
- **The app reports; it never accuses.** Where a feature could be read as an
  integrity judgement about a student — the Writing Timeline being the current
  example — it reports observable facts and states its own limits, and any rule
  against concluding is enforced in code rather than in prompt text. A model
  instruction is not an enforcement boundary. See
  `docs/reference/powergrader-module-map.md` → *Writing Timeline*.

See also `AGENTS.md` → *Lean engineering defaults* for the engineering-side
expression of the same instinct.
