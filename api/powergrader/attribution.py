"""Student-facing feedback is not labeled AI unless the teacher asked.

Feedback posted through PowerGrader arrives in Canvas under the teacher's own
name, because it is their token and their gradebook. Whether to tell a student
that an assistant drafted the words is a teacher choice. The agent may ask.
This module never invents that label.

Outbound paths still run text through ``attribute`` so a leftover banner from
an older session cannot reappear on a student.
"""

AUTOFEEDBACK_PREFIX = "Autofeedback from an automated assistant:"


def attribute(text: str) -> str:
    """Return student-facing feedback with no invented AI label.

    Empty stays empty so a caller can keep using falsiness to decide whether to
    send a comment at all. A leftover Autofeedback banner is stripped once.
    """
    body = str(text or "").strip()
    if body.startswith(AUTOFEEDBACK_PREFIX):
        body = body[len(AUTOFEEDBACK_PREFIX):].lstrip()
    return body


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def is_assistant_authored(outbound: str, ai_feedback: str) -> bool:
    """True when outbound feedback is the assistant's draft, not the teacher's.

    PowerGrader has one feedback box. "Use AI's feedback" copies the draft into
    it, after which the stored text alone cannot say who wrote it, so this
    compares the two. A teacher who accepted the draft and appended a line still
    counts as assistant-authored, because the assistant's words are the bulk of
    what the student receives. A teacher who replaced it entirely does not, and
    their writing goes out unmarked as it should.
    """
    draft = _normalize(ai_feedback)
    if not draft:
        return False
    return _normalize(outbound).startswith(draft)
