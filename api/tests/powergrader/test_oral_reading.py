"""Offline laws and one example for local read-aloud evidence."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from api.powergrader import oral_reading


ROOT = Path(__file__).resolve().parents[2]


def _events(*words, confidence=0.95):
    return [{"word": word, "start": index * 0.5, "end": index * 0.5 + 0.4,
             "probability": confidence} for index, word in enumerate(words)]


@pytest.mark.parametrize(("raw", "expected"), [
    ("  Hello, WORLD! ", ["hello", "world"]),
    ("don’t well-known", ["don't", "well-known"]),
    ("ＡＢＣ", ["abc"]),
])
def test_normalization_v1_laws(raw, expected):
    assert oral_reading.normalize_tokens(raw) == expected


@pytest.mark.parametrize("value", ["", "hello 123", "hola señor", " ".join(["word"] * 3001)])
def test_passage_rejects_non_english_or_out_of_range(value):
    tokens, error = oral_reading.validate_passage(value)
    assert tokens is None and error


def test_exact_reading_metrics_and_private_events_projection():
    report = oral_reading.make_report(canonical_sha256="a" * 64, attempt=2, passage="One two three",
                                      duration_seconds=30, language="en", word_events=_events("one", "two", "three"), model_version="test")
    assert report["status"] == "complete"
    assert report["metrics"] == {"source_words": 3, "exact_matched_words": 3, "accuracy": 1.0, "wcpm": 6.0}
    projected = oral_reading.review_projection(report)
    assert projected["transcript"] == "one two three"
    assert "word_events" not in projected and "canonical_sha256" not in projected


def test_report_status_vocabulary_is_closed():
    assert oral_reading.REPORT_STATUSES == ("complete", "needs_review", "unavailable")


@pytest.mark.parametrize(("source", "observed", "kind"), [
    ("one two three", ("one", "three"), "omission"),
    ("one three", ("one", "two", "three"), "insertion"),
    ("one two", ("one", "too"), "substitution"),
])
def test_edit_distance_difference_kinds(source, observed, kind):
    report = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage=source, duration_seconds=60,
                                      language="en", word_events=_events(*observed))
    assert any(candidate["kind"] == kind for candidate in report["difference_candidates"])


def test_repetition_and_likely_self_correction_are_candidates_only():
    repeated = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one two", duration_seconds=60,
                                        language="en", word_events=_events("one", "one", "two"))
    corrected = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one two", duration_seconds=60,
                                         language="en", word_events=_events("one", "too", "two"))
    assert any(item.get("candidate") == "repetition" for item in repeated["difference_candidates"])
    assert any(item.get("candidate") == "self_correction" for item in corrected["difference_candidates"])
    assert repeated["metrics"]["exact_matched_words"] == 2


def test_early_stop_empty_language_confidence_and_difference_cap_fail_closed():
    early = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one two three", duration_seconds=30,
                                     language="en", word_events=_events("one"))
    empty = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one", duration_seconds=30,
                                     language="en", word_events=[])
    language = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one", duration_seconds=30,
                                        language="es", word_events=_events("uno"))
    confidence = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one", duration_seconds=30,
                                          language="en", word_events=_events("one", confidence=0.2))
    cap = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one", duration_seconds=30,
                                   language="en", word_events=_events(*(["extra"] * 101)))
    assert early["metrics"]["exact_matched_words"] == 1
    assert empty["status"] == "needs_review"
    assert language["uncertainty"] == ["unsupported_language"]
    assert confidence["status"] == "needs_review" and "low_confidence" in confidence["uncertainty"]
    assert cap["status"] == "needs_review" and cap["difference_candidates_truncated"] is True
    assert len(cap["difference_candidates"]) == 100


def test_invalid_duration_and_report_reuse_binding():
    report = oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one two", duration_seconds=60,
                                      language="en", word_events=_events("one", "two"))
    record = {"canonical_sha256": "hash", "attempt": 1}
    assert oral_reading.reusable(report, record, "one two")
    assert not oral_reading.reusable(report, {"canonical_sha256": "other", "attempt": 1}, "one two")
    assert oral_reading.make_report(canonical_sha256="hash", attempt=1, passage="one", duration_seconds=0,
                                    language="en", word_events=_events("one"))["error_code"] == "duration_invalid"


def test_missing_model_never_calls_transcriber(monkeypatch):
    monkeypatch.setattr(oral_reading, "construct_transcriber", lambda: (_ for _ in ()).throw(
        oral_reading.LocalTranscriberUnavailable("model_missing", "Local speech model is not installed.")
    ))
    called = False
    def transcribe(_):
        nonlocal called
        called = True
        raise AssertionError("normal analysis must not download or transcribe")
    report = oral_reading.analyze_recording({"canonical_path": "synthetic.wav", "canonical_sha256": "hash", "duration_seconds": 1}, "one")
    assert report["error_code"] == "model_missing" and not called


def test_stubbed_adapter_is_local_and_never_uses_network(monkeypatch):
    record = {"canonical_path": "synthetic.wav", "canonical_sha256": hashlib.sha256(b"synthetic").hexdigest(), "duration_seconds": 10, "attempt": 1}
    report = oral_reading.analyze_recording(record, "one two", transcribe=lambda _: ("en", _events("one", "two"), "stub"))
    assert report["status"] == "complete" and report["model_version"] == "stub"


def test_session_projection_never_exposes_private_events_or_cache_data():
    view = oral_reading.project_session({"students": [{"attachments": [{
        "oral_reading": {"transcript": "one two"},
        "oral_reading_private": {"word_events": [{"token": "one"}], "model_cache": "C:/private"},
    }]}]})
    attachment = view["students"][0]["attachments"][0]
    assert attachment["oral_reading"]["transcript"] == "one two"
    assert "oral_reading_private" not in attachment
