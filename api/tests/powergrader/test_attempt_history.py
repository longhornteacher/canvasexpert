from api.powergrader.attempt_history import summarize


def test_first_meaningful_skips_blank_text_and_keeps_quiz_attempts():
    result = summarize([
        {"attempt": 1, "submission_type": "online_text_entry", "body": "  "},
        {"attempt": 2, "submission_type": "online_quiz", "body": ""},
        {"attempt": 3, "submission_type": "online_text_entry", "body": "draft"},
    ], current_attempt=3)
    assert result["first_meaningful"]["attempt"] == 2
    assert result["latest"]["attempt"] == 3
    assert result["count"] == 3
    assert result["complete"] is True


def test_blank_upload_and_url_are_empty_but_missing_url_evidence_is_not():
    result = summarize([
        {"attempt": 1, "submission_type": "online_upload", "attachment_names": []},
        {"attempt": 2, "submission_type": "online_url", "url_present": False},
        {"attempt": 3, "submission_type": "online_url", "url_present": True},
    ], current_attempt=3)
    assert result["first_meaningful"]["attempt"] == 3


def test_old_url_attempt_without_presence_evidence_is_unknown():
    result = summarize([
        {"attempt": 1, "submission_type": "online_url"},
        {"attempt": 2, "submission_type": "online_text_entry", "body": "draft"},
    ], current_attempt=2)
    assert result["known"] is False


def test_attempt_coverage_gap_is_incomplete():
    result = summarize([{"attempt": 1, "submission_type": "online_quiz"}], current_attempt=2)
    assert result["complete"] is False
