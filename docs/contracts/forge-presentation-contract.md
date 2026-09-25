# Forge presentation contract

Status: **locked 2026-09-25** by the teacher. The execution plan for these decisions is
`docs/reference/forge-presentation-plan.md`.

This contract covers how AssignmentForge assignments and PageForge pages look to students
in Canvas and on paper, and the differentiation tier model behind that look. It sits beside
the authoring contracts in `api/default_docs/AI Authoring/`. Those files describe what an
agent writes; this file describes what Canvas Expert renders from it. If the two disagree,
this file wins, and the authoring contract is the one that gets fixed.

## 1. Principles

1. **Agents write content. Canvas Expert owns the look.** An agent authors plain,
   semantic HTML fragments and structured fields. It never chooses colors, widths, boxes,
   or inline styles. Canvas Expert renders every student-facing wrapper (banner, header
   line, boxes, printable link, unit info) at push time from one renderer and one palette.
   Every agent on every platform therefore produces the same look.
2. **Paper uses the PDF.** Every eligible assignment gets a generated, standalone
   "Printable" PDF, uploaded to Canvas and linked in the assignment. The Canvas page itself
   does not need to print well.
3. **White and quiet, with color for meaning.** White backgrounds, very light tints, and
   one accent color. The teacher chooses each tier's color, and the untiered default,
   from a fixed set of swatches in synced Settings (§2). Colors never go beyond those
   swatches.
4. **Width follows the container.** Nothing rendered or authored has a fixed width. Only
   `width:100%` and `max-width:100%` are allowed.
5. **Non-essential help is collapsed.** The rubric, supports, extra help, and unit info
   render as `<details>` boxes that start closed. The teacher has confirmed these work in
   Canvas. Canvas mobile support is not a requirement.

## 2. Tier model: three tiers, no student knowledge

| Canonical tier | Readiness | Public tag (teacher Settings) | Color (teacher Settings, default) |
|---|---|---|---|
| Support | below level | Silver | `silver` |
| Core | on level | Red | `red` |
| Accelerate | above level | Blue | `blue` |
| (untiered assignments and all pages) | n/a | n/a | `teal` |

- **"Extend" is removed** from every Forge, every setting, and all code. Accelerate is the
  highest tier. There is no fourth tier and no alias for one.
- **Colors are a teacher preference.** They live in synced state, like `tier_tags`,
  under the key `tier_colors`. It holds one swatch key per canonical tier plus
  `untiered`. The defaults are shown above.
  - **Allowed values:** each value must be a §3 swatch key. Free hex colors are not
    allowed.
  - **No shared colors:** the three tiers must use three different swatches.
    `untiered` may match one of them.
  - **Independent of tags:** renaming a public tag does not change its color, and
    changing a color does not change its tag.
  - **Layout is not a preference:** section order, collapsed boxes, and widths are
    product-owned.
- **The label stays private.** Student-visible text (Canvas HTML and the printable) shows
  the public tag, never the canonical label. A Support student sees "Silver", never
  "Support".
- **Canvas Expert knows tiers, not students.** It has no student-to-tier mapping: no roster
  tier scheme, no per-student tier, no group set chosen as a tier source, no tier data in
  scoring session rows. The teacher assigns each tier's Canvas assignment to students or
  pods in Canvas. Only assigned students see a tier assignment.
- An untiered assignment and every page use the `untiered` color.
- **The review freezes the color.** The push resolves colors when it prepares, and the
  frozen review keeps them. A Settings change after preview does not alter an apply.

## 3. Palette (fixed swatches)

Every color in rendered output comes from this table. Ink `#2d3b45` is the Canvas body
text color. Contrast ratios were measured against WCAG 2.x, and all meet AA for normal
text, including white badge text on the dark shade. A new swatch needs the same
measurements before it is added.

| Key | Dark (headings, borders, badges) | Tint (shading) | Dark on white | Dark on tint |
|---|---|---|---|---|
| `silver` | `#4f5b66` | `#f1f3f5` | 6.95 | 6.25 |
| `red` | `#a63a2f` | `#fbefed` | 6.43 | 5.71 |
| `blue` | `#1f5a96` | `#edf3fa` | 7.09 | 6.35 |
| `teal` | `#1e6f6a` | `#e7f3f1` | 5.94 | 5.23 |
| `green` | `#2e6b34` | `#edf5ee` | 6.42 | 5.78 |
| `gold` | `#80600f` | `#fbf5e3` | 5.84 | 5.36 |
| `purple` | `#63428f` | `#f3eff9` | 7.76 | 6.84 |
| `orange` | `#a44a12` | `#fcf0e8` | 5.89 | 5.26 |

Shared neutrals: ink `#2d3b45`, muted `#5b6770`, rule `#d9dee2`, surface `#ffffff`, and
`#ffffff` text on a dark badge.

## 4. Assignment layout (Canvas HTML)

Canvas already shows the assignment's title, points, and due date. The description body
renders in this fixed order:

1. **Banner.** A tint-shaded box with a 6px top border in the dark color. Its eyebrow
   line is `<Public tag> · <unit>` (either part omitted when absent), followed by the base
   assignment title as the one `<h2>`. The banner has a white-leaning look, not a solid
   dark fill.
2. **Header line.** `<points> pts · <assignment group> · <how to submit>`, in muted text.
   There is never a due, unlock, or lock date. The assignment group segment is omitted
   when none was chosen. "Daily" and "Major" are simply assignment group names.
3. **Overview.** Author HTML: the hook plus what the student will do.
4. **Directions.** The authored steps, rendered as a full-width two-column table with a
   pill-shaped number badge in the dark color. There is no answer space online.
5. **Sections.** Optional authored sections, each either a heading plus body (heading in
   the dark color) or a tint-shaded callout.
6. **Rubric** (collapsed). Rendered from the structured rubric as a full-width table. The
   header row is shaded with the tint, criterion points are right-aligned, and a total row
   closes it.
7. **Supports** (collapsed). The summary reads "Supports", or "Go further" on the
   Accelerate tier. It holds the shared supports plus any tier supports: sentence frames,
   word bank, and HTML.
8. **Extras** (collapsed, zero or more). Authored non-essential help, each with its own
   summary line.
9. **Printable link.** A tint-shaded line: **Printable:** `<Title> - Printable (PDF)`.
   It is followed, when the payload has attachments, by an **Attachments** line listing
   each attachment's label as a link (§6.1).
10. **Unit info** (collapsed). Unit, TEKS codes, subject, and grade, as a small two-column
    table.

A section with no content is omitted entirely, with no empty headings or boxes.

### Submission wording (header line)

| Canvas submission type | Wording |
|---|---|
| `online_text_entry` | Type your answer in Canvas |
| `online_upload` | Upload a file (with extensions: "Upload a .docx or .pdf file") |
| tracked writing (`online_upload` with only `docx`) | Upload your Word document |
| `online_url` | Submit a link |
| `media_recording` | Record audio or video |
| `on_paper` | Hand in on paper |
| `none` | Nothing to submit |
| `external_tool` | Complete it in the linked tool |

When there are several types, the wordings are joined with " or ".

## 5. Page layout

A page's default look mirrors an assignment's.

- **Layout `standard`** (default):
  1. Banner. The eyebrow is the unit and the color is the teacher's `untiered` color.
  2. Overview.
  3. Sections. A section may have `kind: "collapsed"` to render as a `<details>` box.
  4. Extras.
  5. Attachments line, when present (§6.1).
  6. Unit info (collapsed).

  A page has no header line and no rubric.
- **Layout `freeform`** (override): the author supplies a full body. Inline `style` is
  allowed, but the width law (§7) and the palette law still hold, and the renderer still
  appends the Unit info box. `banner: false` suppresses the banner.

## 6. Printable PDF

- **One per assignment**, and one per tier for a tiered assignment. It is generated when
  the push is prepared, uploaded to the course's `Canvas Expert Printables` folder, and
  linked in section 9 of that tier's description. The filename is
  `<Title> - Printable.pdf` or `<Title> - <Public tag> - Printable.pdf`.
- **Standalone and designed for paper.** It opens with a Name / Date / Period line.
  Then comes the same banner content (title and public tag), with the header line's
  submission wording replaced by "Hand in on paper". After that: overview, directions,
  sections, **the full rubric**, **the full supports**, and the extras, all laid out open.
  Unit info closes it as a small footer.
- **Answer space.** Each direction step has a `response` value:

  | `response` | On paper |
  |---|---|
  | `none` | Instruction only. |
  | `short` | Ruled writing lines, 4 by default, or `lines` from 1 to 12. |
  | `long` | A boxed note: "Answer on notebook paper." |

- **Tracked writing assignments** (a Word upload with revision history) get a
  **directions-only** printable. It uses the same layout, but every step renders as
  `none`, and the submission line reads "Write and submit in the Word document provided".
  This keeps the handwritten copy from replacing the tracked document.
- **`external_tool` assignments get no printable.**
- **Printing:** US Letter, 0.75in margins, tints printed, and the tier tag shown as text
  so a grayscale copy still identifies the tier. PDFs come from installed Microsoft Edge
  through Playwright, and nothing is downloaded.
- **Failure is visible, not fatal.** If Edge is unavailable or generation fails, the
  preview says so and the assignment pushes without a printable.

### 6.1 Attachments (teacher files)

- **What it is.** An assignment or page payload may list `attachments`: files the
  teacher provides, such as a handout PDF or a slide deck, that Canvas Expert uploads to
  the course and links in the rendered HTML. Each entry is
  `{ "file": "<file name>", "label": "<link text>" }`.
- **Where the files come from.** Files come only from the workspace folder
  `To Review/Attachments/`, matched by exact file name. No other path is accepted.
  Allowed types are `pdf`, `docx`, `pptx`, `xlsx`, `png`, `jpg`, and `jpeg`. An agent
  that cannot place files there asks the teacher to do it.
- **Missing files block the push.** A missing, disallowed, or duplicate attachment
  blocks the preview with the file name. This is unlike a printable, whose failure only
  warns, because a missing attachment means authored content is broken.
- **Upload.** Files upload at apply time to the course folder `Canvas Expert
  Attachments`, once per course. Every tier of a family links the same uploaded file.
- **On paper.** The printable lists the attachment labels under a "Materials" line, as
  text, so the paper copy still says what else the student needs.

## 7. Laws (tested directly, once each)

1. **Width.** Rendered Canvas HTML and printable HTML contain no `width`, `min-width`, or
   `max-width` declaration other than `100%`, and no `width` or `height` attribute. Author
   HTML carrying any of these is refused.
2. **Palette.** Every color in rendered output is a §3 value.
3. **Label privacy.** When a public tag differs from its canonical label, the label never
   appears in student-visible rendered text (Canvas HTML or printable).
4. **Standalone printable.** Every visible text node in a tier's rendered model appears in
   that tier's printable HTML.
5. **Author allowlist.** Author HTML outside the allowlist in §8 is refused with a specific
   message. It is never silently stripped.

## 8. Author HTML allowlist

Author HTML fields are the overview, directions, sections, supports, extras, and page
bodies.

- **Allowed tags:** `p`, `br`, `strong`, `em`, `b`, `i`, `u`, `sup`, `sub`, `code`, `pre`,
  `blockquote`, `hr`, `ul`, `ol`, `li`, `h3`, `h4`, `a` (`href`, `target`), `table`,
  `thead`, `tbody`, `tr`, `th`, `td` (`colspan`, `rowspan`), and `img` (`src`, required
  `alt`). Pages only: `iframe` (`src`, `title`).
- **Refused:** `h1` and `h2` (the banner owns the `<h2>`), and `style`, `class`, `id`,
  `width`, `height`, and event attributes. Also `<script>`, `<style>`, `<font>`,
  `<center>`, `<div>`, `<span>`, and `<details>` (the renderer owns boxes).
  `{{file:…}}` and `{{page:…}}` placeholders are refused too, until placeholder
  resolution exists.
- **Renderer decoration.** The renderer adds full-width table styling, cell padding,
  `img` `max-width:100%;height:auto`, and `iframe` `width:100%;height:360px;border:0`.
- **Freeform pages** may add inline `style`, but only under the width and palette laws.

## 9. Non-goals

- Printing directly from the Canvas page.
- Canvas mobile app fidelity.
- Free hex colors, or visual preferences other than swatch choice (layout stays
  product-owned).
- Emoji or icon decoration.
- Due dates in student-visible text.
- A fourth tier.
- Any Canvas Expert knowledge of which student is in which tier.
