"""One rule, in one place: a durable artifact must not key a student by pseudonym.

A pseudonym is a label the teacher can change at any time, from the Students
page or the Name Manager. Anything that stores it as the identity of a record
silently loses that record on the next rename. Anything that stores a stable
Canvas id and resolves the label when reading does not.

So, for every store that durably holds student work:

* persist the **canvas_id**, and resolve the current pseudonym at read;
* if the stored form is prose a teacher reads, the pseudonym may stay inside
  the text (`api/dailywriting/core/scrub.py` measured and rejected redacting a
  name out of student writing), but then a rename has to rewrite it, which is
  what `api/pseudonym_rename.py` coordinates;
* never let the canvas_id reach an artifact that leaves the machine.

If this test fails, the fix is almost never to change the assertion.
"""
from datetime import date, datetime, timezone

from api.dailywriting.core import ingest
from api.dailywriting.core.models import AssignmentContext
from api.dailywriting.store import codec

PSEUDONYM = "Sparky McGee"
CANVAS_ID = "canvas-77"
def test_writing_evidence_persists_the_canvas_id_not_the_pseudonym():
    """The pseudonym stays inside the prose on purpose, but it must not be the
    key: `Repository` resolves it to a canvas_id on write and back to the
    current pseudonym on read."""
    submission = ingest.ingest(
        submission_id="sub-1", rep_id="rep-1", pseudonym_id=PSEUDONYM,
        submitted_at=datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc),
        text="One paragraph about a favorite hobby.",
        context=AssignmentContext(
            rep_id="rep-1", date=date(2026, 9, 1),
            prompt_text="Write one paragraph about a favorite hobby."),
    )
    document = codec.submission_to_dict(submission, canvas_id=CANVAS_ID)

    assert document["canvas_id"] == CANVAS_ID
    assert PSEUDONYM not in document, "the pseudonym must not be a stored key"
    assert not any(value == PSEUDONYM for value in document.values()), (
        "the pseudonym must not be a stored field either; it belongs only "
        "inside the prose, where a rename rewrites it"
    )
