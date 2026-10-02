import hashlib
import json

import pytest

from api import score_ledger


def _event():
    return {"source": "ce_stage", "action": "staged", "course_id": "c1",
            "assignment_id": "a1", "student_id": "u1", "attempt": 2,
            "raw_score": 53, "entered_score": 67, "canvas_score": None,
            "feedback": "Private feedback", "curve_rule_id": "r1"}


def test_append_only_deduplicates_logical_events_and_exports_recoverable_history(tmp_path, monkeypatch):
    monkeypatch.setattr(score_ledger.local_runtime, "machine_id", lambda: "COMPUTER-PRIVATE-1234")
    first = score_ledger.append_event(_event(), idempotency_key="stage:one", root=tmp_path)
    replay = score_ledger.append_event(_event(), idempotency_key="stage:one", root=tmp_path)
    correction = score_ledger.append_event({**_event(), "entered_score": 68},
                                           idempotency_key="stage:correction", root=tmp_path)
    score_ledger.flush_exports("c1", "a1", root=tmp_path)
    rows = score_ledger.list_events("c1", "a1", root=tmp_path)
    assert first["event_id"] == replay["event_id"]
    assert len(rows) == 2
    assert correction["entered_score"] == 68
    assert first["feedback_sha256"] == hashlib.sha256(b"Private feedback").hexdigest()
    assert first["device_id"] != "COMPUTER-PRIVATE-1234"
    export_dir = tmp_path / "_System" / "Archive" / "Score Ledger" / "Exports" / "c1" / "a1"
    assert any(path.suffix == ".json" for path in export_dir.iterdir())
    assert any(path.suffix == ".csv" for path in export_dir.iterdir())


def test_altered_or_corrupt_evidence_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(score_ledger.local_runtime, "machine_id", lambda: "HOST-ABCD1234")
    row = score_ledger.append_event(_event(), idempotency_key="stage:two", root=tmp_path)
    path = next((tmp_path / "_System" / "Archive" / "Score Ledger" / "Events" / "c1" / "a1").glob("*.json"))
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["raw_score"] = 52
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(score_ledger.ScoreLedgerError, match="score_ledger_corrupt"):
        score_ledger.list_events("c1", "a1", root=tmp_path)


def test_invalid_boolean_and_nonfinite_scores_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(score_ledger.local_runtime, "machine_id", lambda: "HOST-ABCD1234")
    with pytest.raises(score_ledger.ScoreLedgerError, match="score_ledger_invalid_number"):
        score_ledger.append_event({**_event(), "raw_score": True}, idempotency_key="bad", root=tmp_path)
    with pytest.raises(score_ledger.ScoreLedgerError, match="score_ledger_invalid_number"):
        score_ledger.append_event({**_event(), "entered_score": float("nan")}, idempotency_key="bad2", root=tmp_path)
