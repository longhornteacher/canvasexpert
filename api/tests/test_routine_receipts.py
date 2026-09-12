"""Tests for the routine coordinator."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

from api.webui import routine_coordinator as coord
from api.operation_ledger import paths as ledger_paths


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(ledger_paths, "private_root", lambda: root)
    return root


def _fake_runner(result=None):
    def fn(params):
        return result or {"status": "applied", "summary": "ok"}
    return fn


# ── Claim tests ──────────────────────────────────────────────────────────

def test_acquire_claim_succeeds(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert coord._acquire_claim("sweep", None) is True


def test_acquire_claim_blocks_duplicate(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert coord._acquire_claim("sweep", None) is True
    assert coord._acquire_claim("sweep", None) is False


def test_acquire_claim_allows_different(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert coord._acquire_claim("sweep", None) is True
    assert coord._acquire_claim("download", None) is True


def test_release_claim_allows_rerun(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert coord._acquire_claim("sweep", None) is True
    coord._release_claim("sweep", None)
    assert coord._acquire_claim("sweep", None) is True


def test_expired_claim_can_be_reacquired(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    # Manually create an expired claim
    claims = coord._load_claims()
    claims["claims"].append({
        "key": "sweep|",
        "routine_id": "sweep",
        "scheduled_due": None,
        "acquired_at": "2020-01-01T00:00:00",
        "expires_at": "2020-01-01T01:00:00",
        "pid": 0,
    })
    coord._save_claims(claims)
    # Should be able to re-acquire
    assert coord._acquire_claim("sweep", None) is True


# ── Run tests ────────────────────────────────────────────────────────────

def test_run_routine_applied(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    result = coord.run_routine("sweep", _fake_runner({"status": "applied", "summary": "done"}))
    assert result["status"] == "applied"
    assert result["ok"] is True
    assert result["receipt"] is not None


def test_run_routine_skipped_when_duplicate(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    results = []
    def slow_runner(params):
        time.sleep(0.2)
        results.append("ran")
        return {"status": "applied", "summary": "ok"}

    # Start first run in a thread so it holds the claim
    t = threading.Thread(target=lambda: coord.run_routine("sweep", slow_runner))
    t.start()
    time.sleep(0.05)  # Let first run acquire the claim

    # Second run should be skipped
    r2 = coord.run_routine("sweep", _fake_runner())
    assert r2["status"] == "skipped"
    assert r2["receipt"] is None

    t.join()


def test_run_routine_failed(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    def failing(params):
        raise RuntimeError("something broke")
    result = coord.run_routine("sweep", failing)
    assert result["status"] == "failed"
    assert result["ok"] is False


def test_run_routine_no_effect(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    result = coord.run_routine("sweep", _fake_runner({"status": "no_effect", "summary": "nothing to do"}))
    assert result["status"] == "no_effect"
    assert result["ok"] is True


# ── Concurrent execution ─────────────────────────────────────────────────

def test_concurrent_runs_execute_once(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    results = []
    def runner(params):
        time.sleep(0.05)
        results.append("ran")
        return {"status": "applied", "summary": "ok"}

    threads = [threading.Thread(target=lambda: coord.run_routine("sweep", runner))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Only one should have actually run
    assert len(results) == 1


# ── Expired claim recovery ───────────────────────────────────────────────

def test_recover_stale_claims(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    claims = coord._load_claims()
    claims["claims"].append({
        "key": "stale|",
        "routine_id": "stale",
        "scheduled_due": None,
        "acquired_at": "2020-01-01T00:00:00",
        "expires_at": "2020-01-01T01:00:00",
        "pid": 0,
    })
    coord._save_claims(claims)
    expired = coord.recover_stale_claims()
    assert len(expired) == 1
    assert expired[0]["routine_id"] == "stale"


# ── Receipt verification ─────────────────────────────────────────────────

def test_receipt_written_on_success(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    result = coord.run_routine("sweep", _fake_runner({"status": "applied", "summary": "done"}))
    receipt = result["receipt"]
    assert receipt["subject_type"] == "routine"
    assert receipt["subject_id"] == "sweep"
    assert receipt["status"] == "applied"


def test_receipt_written_on_failure(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    def failing(params):
        raise ValueError("bad data")
    result = coord.run_routine("sweep", failing)
    receipt = result["receipt"]
    assert receipt["status"] == "failed"
    assert "bad data" in receipt.get("summary", "")
