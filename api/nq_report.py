"""Parse a New Quizzes "Student Analysis" CSV into structured per-student data.

Pure stdlib — no app or network imports, so it is trivially unit-testable offline.
Format reference + rationale: docs/reference/new-quizzes-student-analysis-csv.md.

Layout (positional, variable width):
  [ 9 fixed lead ] [ N x 5-col item block ] [ 5 fixed trailing ]
  lead:     Name, ID, SISID, SectionIDs, SectionNames, SectionSISIDs,
            Submitted, ElapsedTime, Attempt
  block:    ItemID, ItemType, <question text (header) / response (cell)>,
            EarnedPoints, Status        # names repeat -> parse by POSITION
  trailing: NumberOfCorrect, NumberOfIncorrect, NoResponse, PointsPossible,
            OverallScore

The 3rd column of each block carries the prompt in the HEADER row and the student's
response in each data row. Choice responses are the chosen option *text*; essay
responses are HTML (use html_to_text). Essays contain literal newlines inside quoted
fields, so a single record spans multiple physical lines — csv.reader handles this.
"""
import csv
import html as _html
import io
from html.parser import HTMLParser

LEAD_COLS = 9
TRAILING_COLS = 5
BLOCK = 5


# --------------------------------------------------------------------------
# HTML -> readable text (essay responses)
# --------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Collect text, turning block-level tags into line breaks."""
    _BREAKS = {"p", "br", "div", "li", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)  # entities decoded in handle_data
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str) -> str:
    """Convert essay HTML to clean plain text. Plain (non-HTML) input passes through.
    Collapses blank lines and trims each line."""
    if not value:
        return ""
    if "<" not in value:
        return _html.unescape(value).strip()
    p = _TextExtractor()
    p.feed(value)
    raw = "".join(p.parts)
    lines = [ln.strip() for ln in raw.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif out and not blank:
            out.append("")          # keep a single paragraph gap
            blank = True
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def parse_student_analysis(text: str) -> dict:
    """Parse the CSV text. Returns:
      {
        "quiz": {"item_prompts": [str, ...], "points_possible": float|None},
        "students": [
          {name, canvas_id, sis_id, section, submitted, elapsed, attempt,
           num_correct, num_incorrect, no_response, points_possible, overall_score,
           items: [{item_id, type, prompt, response, earned_points, status}]}
        ],
      }
    """
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any((c or "").strip() for c in r)]  # drop blank lines
    if not rows:
        return {"quiz": {"item_prompts": [], "points_possible": None}, "students": []}

    header = rows[0]
    block_region = len(header) - LEAD_COLS - TRAILING_COLS
    num_items = max(0, block_region // BLOCK)
    prompts = [header[LEAD_COLS + i * BLOCK + 2] for i in range(num_items)]

    students = []
    for r in rows[1:]:
        if len(r) < LEAD_COLS + num_items * BLOCK + TRAILING_COLS:
            continue  # malformed row — skip rather than misalign
        trailing = r[-TRAILING_COLS:]
        items = []
        for i in range(num_items):
            base = LEAD_COLS + i * BLOCK
            items.append({
                "item_id":       r[base],
                "type":          r[base + 1],
                "prompt":        prompts[i],
                "response":      r[base + 2],
                "earned_points": _to_float(r[base + 3]),
                "status":        r[base + 4],
            })
        students.append({
            "name":           r[0],
            "canvas_id":      r[1],
            "sis_id":         r[2],
            "section":        r[4],
            "submitted":      r[6],
            "elapsed":        r[7],
            "attempt":        _to_int(r[8]),
            "num_correct":    _to_int(trailing[0]),
            "num_incorrect":  _to_int(trailing[1]),
            "no_response":    _to_int(trailing[2]),
            "points_possible": _to_float(trailing[3]),
            "overall_score":  _to_float(trailing[4]),
            "items":          items,
        })

    # Per-item points-possible is NOT in the CSV (only per-item earned + the quiz
    # total). Infer each item's max from the cohort: the highest earned across all
    # students. Exact for auto-graded choice; reliable for essays when anyone maxes
    # the item. (Phase 2 can replace this with exact values from the items API.)
    item_max = [None] * num_items
    for s in students:
        for i, it in enumerate(s["items"]):
            ep = it["earned_points"]
            if ep is not None:
                item_max[i] = ep if item_max[i] is None else max(item_max[i], ep)
    for s in students:
        for i, it in enumerate(s["items"]):
            it["points_possible_est"] = item_max[i]

    pts = students[0]["points_possible"] if students else None
    return {"quiz": {"item_prompts": prompts, "points_possible": pts,
                     "item_points_possible": item_max},
            "students": students}


def parse_student_analysis_file(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:   # tolerate a BOM from Canvas/Excel
        return parse_student_analysis(f.read())


def constructed_responses(student: dict) -> list:
    """Items that are written responses (essay / short-answer), not auto-graded choice."""
    return [it for it in student.get("items", []) if it.get("type") != "choice"]
