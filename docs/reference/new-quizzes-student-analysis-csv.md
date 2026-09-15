# Reference: New Quizzes "Student Analysis" CSV format

What the New Quizzes **student_analysis** report looks like, learned from a real
export (THG Ch1–9 test, MC + short-answer/essay). This is the on-disk format the
shipped parser reads (see "Shipped parser" below). The same CSV is produced two ways:

- **Manual:** New Quiz → … → Reports → Student Analysis → download (always works,
  no API auth question — see `api/README.md`).
- **API (future):** `POST /api/quiz/v1/courses/:cid/quizzes/:aid/reports`
  `report_type=student_analysis` → poll Progress → fetch the file. Whether the API
  CSV is byte-identical to the UI download is **unverified** (no test tenant yet).

> ⚠️ **PII:** a "Student Analysis" CSV is full student responses + identifiers. It
> lands only in gitignored output / the synced workspace, **never** the repo. Even
> a vendor-"anonymized" file keeps real `ID`/`SISID`/section data and essay text —
> repo fixtures must be **fully synthetic**.

## Row model

One row **per student per attempt**. (Sample had only Attempt 1; multi-attempt
behavior is an open question below.)

## Column layout (positional, variable width)

```
[ 9 fixed lead ] [ N × 5-col item block ] [ 5 fixed trailing ]
```

**Fixed lead (9):**
`Name, ID, SISID, SectionIDs, SectionNames, SectionSISIDs, Submitted, ElapsedTime, Attempt`

**Per-item block (5 columns, repeats once per quiz item, in quiz order):**
1. `ItemID` — stable numeric question id (identical across all student rows), e.g. `8250658`
2. `ItemType` — `choice` | `essay` (other question types untested — see below)
3. **`<full question text>`** — the column **header is the question prompt**; the
   **cell is the student's response**. This is how response ↔ question is mapped.
4. `EarnedPoints` — float; `> 0` ⇒ credited/correct
5. `Status` — `Graded` (likely `NotGraded`/pending for ungraded essays — untested)

**Fixed trailing (5):**
`NumberOfCorrect, NumberOfIncorrect, NoResponse, PointsPossible, OverallScore`

Sample shape: 22 items = 20 `choice` + 2 `essay`; choice items worth 4 or 3 pts,
essays ~15 pts; `PointsPossible` 100. So the block count and per-item points are
**per-quiz** — parse the structure, don't hardcode widths.

## Parsing facts (non-negotiable for the importer)

- **Use a real CSV parser** (Python `csv`). Essay cells are **HTML with literal
  newlines and embedded quotes inside quoted fields** — a row spans multiple
  physical lines. Naive line/`,` splitting corrupts the data.
- **Duplicate header names.** `ItemID`/`ItemType`/`EarnedPoints`/`Status` repeat
  for every item. In our sample they appeared as `ItemID`, `ItemID.1`, `ItemID.2`…
  because the file was re-saved through pandas during anonymization (pandas
  auto-suffixes dup columns). The **raw Canvas export almost certainly repeats the
  bare names** — so **parse positionally by 5-col block**, never by header name.
  The question-text headers (col 3 of each block) are unique.
- **Choice cells store the chosen option *text*** (e.g. `He takes tesserae for
  himself and his family each year`), not an index/letter. The correct option and
  the full option set are **not** in the CSV. Infer "correct" = the option that
  earns full points across students, or cross-reference the authored answer key.
- **Essay cells are HTML** (`<p>`, `<span style=…>`, `&nbsp;`, `&quot;`). Strip to
  plain text for scoring; keep raw if fidelity matters.
- `NumberOfCorrect`/`NumberOfIncorrect` count **auto-graded selection items only**;
  essays are excluded (partial credit). `NoResponse` counts blank items.
- `Submitted` is **UTC**. `ElapsedTime` is `HH:MM:SS` and **can exceed 24h**
  (sample had `25:23:18` — quiz left open). Don't assume `< 24h`.

## Open questions

1. **Raw vs anonymized headers** — confirm the real Canvas export repeats bare
   `ItemID,ItemType,…` (our positional parser handles either, but worth knowing).
2. **Report `ItemID` vs API item id** — does the report `ItemID` equal the id from
   `/api/quiz/v1/courses/:cid/quizzes/:aid/items`? Community reports they may differ
   and need a mapping. Matters only if we cross-link authored (QuizForge) items to
   responses.
3. **Multiple attempts** — extra rows per student, or extra columns? (Sample is
   Attempt 1 only.)
4. **Other ItemType encodings** — multiple-answer, matching, categorization,
   numeric, fill-in-blank: how is the response cell encoded for each?
5. **Ungraded/pending essays** — `Status` value and whether `EarnedPoints` is blank.
6. **API vs UI parity** — is the API-generated CSV identical to the UI download?
   (Verify once a New-Quizzes test tenant exists.)

## Likely use cases (confirm before building)

- **MC item analysis** — per-question difficulty + distractor frequency by tallying
  chosen option texts across students (the CSV supports this directly).
- **Constructed-response extraction** — pull essay/short-answer responses
  (HTML → text) for rubric-based scoring (MagicSchool / Copilot / LLM scorer),
  matching the existing Scoring Session flow.

## PowerGrader note

PowerGrader's New Quiz written-response path uses the Student Analysis **JSON**
report API for its local snapshots. This CSV document remains the manual fallback
and parser reference; it is not used by the PowerGrader fetch path.

## Shipped parser

The read-only parser is built: `api/nq_report.py::parse_student_analysis` (and
`parse_student_analysis_file`) does the positional 5-column-block parsing described above.
It runs offline against synthetic fixtures — no live Canvas, no PII. Canvas Expert does
not use this CSV to write New Quiz item scores or per-item feedback.
