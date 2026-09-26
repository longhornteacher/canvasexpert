# Authoring CS project assignments for LLM scoring

A practical guide for writing AssignmentForge assignments so they score and give good
feedback through the feedback tools. Tuned for the intro CS course (8th graders, first time
with HTML/Python, project-based).

## Why this matters

The scorer only ever sees three things: the **assignment `description`** (pulled from
Canvas as the prompt), the **rubric** (now inlined into the SAFE `HOW-TO-SCORE.txt`), and
the **student's submission**. It does *not* see your starter code, your slides, your verbal
instructions, or anything outside the Canvas assignment. So scoring quality is decided by
what you put in the description and the rubric — nothing else reaches the LLM.

## The one rule

> **Everything the LLM needs to judge the work must live in the assignment `description` or
> the rubric.** If it's only in a starter file, a linked doc, or said out loud — the LLM
> never sees it.

## Description: the four sections that score well

Author the AssignmentForge `description` (HTML is fine) with these, in this order:

1. **What they're building** — one or two plain sentences. *"Build a webpage that introduces
   a fictional character using headings, a paragraph, and an image."*
2. **Learning objectives** — the skills this project is checking. The LLM uses these to know
   what to look for. *"Demonstrates: `<h1>`/`<h2>` headings, at least one `<p>`, one `<img>`
   with an `alt` attribute, and valid nesting."*
3. **Success criteria** — concrete, checkable statements (these often mirror the rubric).
   *"Done when: the page has a title heading, one image that loads, and no unclosed tags."*
4. **What was provided vs. what they added** — the LLM never sees your starter file, so tell
   it. *"Starter gave them the `<html>`/`<body>` shell and a TODO comment; they wrote the
   heading, paragraph, and image tag."* For short starters, paste the snippet right in.

Keep it skimmable — these are 8th graders, and the LLM reads the same field they do.

## Pair every project with a rubric

The rubric is now inlined into the SAFE `HOW-TO-SCORE.txt`, so whatever rubric you select in
the guided flow travels to your LLM automatically — no separate attach step. For CS:

- Write criteria as **observable code facts**, not vibes: *"Uses a loop instead of repeated
  lines,"* *"Variable names describe their contents,"* *"Image tag has an `alt` attribute"* —
  not *"good style."* Observable criteria score far more consistently.
- Keep the point scale small (the product-owned Glows and Grows shape expects a single
  rubric score). Per-criterion scoring is a future extension.

## Submission type: paste or upload — both work

Either reaches the scorer:

- **`online_text_entry` (paste code)** → the code lands in `submission.body` and flows through
  scoring.
- **`online_upload` (file)** → plain-text code/text files (`.py`, `.html`, `.css`, `.js`, `.txt`,
  `.md`, `.json`, `.csv`, up to 256 KB) are downloaded and folded into the scored response, raw,
  so an HTML submission's tags survive. Each file is headed with `--- filename ---`.
- **Not scored:** non-text attachments (images, PDF, DOCX). Those submissions are listed in the
  run summary as "score manually" rather than silently dropped.

Set this in AssignmentForge via `submission.types` (and `allowed_extensions` like `py`, `html`
for upload).

## Privacy note

Tell students **not to put their real name in the code** (no `# Name: Jose` headers) — the
scrub will replace it with their fake name anyway, but a clean habit avoids odd-looking SAFE
output. Their Canvas identity is what the feedback tools anchor on; the header name is noise.

## Quick checklist before you push

- [ ] Description has: what they're building, objectives, success criteria, starter context
- [ ] A rubric is selected for the assignment (criteria are observable code facts)
- [ ] Submission type is `online_text_entry` (paste) so the work reaches the scorer
- [ ] No instruction lives *only* in the starter file or said aloud
