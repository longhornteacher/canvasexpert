"""Contract and privacy laws for the retained-history MCP read.

The functional read path serves attempt history from the pseudonymized evidence
index (``attempt_history`` view). The retired private-file history reader
(``_submission_history``) and its local-only file extraction cases were removed
with the functional read path; the laws that still apply to evidence history are
kept here.
"""
from __future__ import annotations

import json

from api.mcp_server import tools


def test_history_never_blanks_an_attempt_and_marks_omitted_text(evidence_mirror):
    """LAW: every observed attempt is returned exactly once; include_text=False
    marks text omitted without dropping the attempt."""
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10", history=True)
    assert result["ok"] is True, result
    assert result["source"] == "mirror_evidence"
    attempts = result["attempts"]
    assert attempts
    for item in attempts:
        assert item["text_status"] in {"included", "no_body"}
        assert item["pseudonym"]
        assert item["observation_digest"]

    omitted = tools.get_submissions("1", "10", include_text=False, history=True)
    assert omitted["ok"] is True
    for item in omitted["attempts"]:
        assert item["text_status"] == "omitted"
        assert item["text"] is None


def test_history_is_pseudonymized_and_never_leaks_private_values(evidence_mirror):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10", history=True)
    assert result["ok"] is True
    serialized = json.dumps(result)
    for secret in ("Avery Sample", "Morgan Sample", "synthetic-user-01",
                   "synthetic-user-02", "essay.docx", "broken.pdf"):
        assert secret not in serialized


def test_history_pagination_is_bounded(evidence_mirror):
    evidence_mirror["publish"]("read_path")
    assert tools.get_submissions("1", "10", limit=0, history=True)["ok"] is False
    assert tools.get_submissions("1", "10", max_text_chars=20001, history=True)["ok"] is False
    first = tools.get_submissions("1", "10", limit=1, history=True)
    assert first["ok"] is True
    assert len(first["attempts"]) == 1
