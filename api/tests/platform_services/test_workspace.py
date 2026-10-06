"""Machine-config memo in ``workspace._machine_config`` (synthetic data only).

Law: the memo never serves a value the config file no longer holds, never
caches a file modified within the racy window, and never hands out the cached
dict itself.  Cost: an aged config is opened at most once however many times
``workspace_root()`` runs, with one stat per call.
"""
import builtins
import json
import os
import time

import pytest

from api.platform_services import workspace

AGED_NS = 60 * 1_000_000_000


@pytest.fixture(autouse=True)
def _fresh_memo(monkeypatch):
    # The memo is process state; start each test empty and restore afterwards.
    monkeypatch.setattr(workspace, "_machine_config_memo", None)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "machine" / "config.json"
    path.parent.mkdir()
    monkeypatch.setattr(workspace, "CONFIG_PATH", str(path))
    return path


def _write(path, data, *, age_ns=AGED_NS):
    """Write ``data`` (dict or raw text) and set the file's mtime ``age_ns`` ago."""
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    _set_mtime(path, time.time_ns() - age_ns)
    return path


def _set_mtime(path, mtime_ns):
    os.utime(path, ns=(mtime_ns, mtime_ns))


# --- Law: a changed config is never served stale ---------------------------

def test_rewrite_with_new_mtime_returns_new_value(config_file):
    _write(config_file, {"workspace_path": "AAAA"}, age_ns=AGED_NS)
    assert workspace._machine_config() == {"workspace_path": "AAAA"}
    # Same size, different (still aged) mtime.
    _write(config_file, {"workspace_path": "BBBB"}, age_ns=AGED_NS + 7_000_000_000)
    assert workspace._machine_config() == {"workspace_path": "BBBB"}
    assert workspace.workspace_root() == "BBBB"


def test_rewrite_with_new_size_returns_new_value_even_at_same_mtime(config_file):
    aged = time.time_ns() - AGED_NS
    _write(config_file, {"workspace_path": "AAAA"})
    _set_mtime(config_file, aged)
    assert workspace.workspace_root() == "AAAA"
    config_file.write_text(json.dumps({"workspace_path": "AAAAAAAA"}), encoding="utf-8")
    _set_mtime(config_file, aged)
    assert workspace.workspace_root() == "AAAAAAAA"


def test_racy_file_is_always_reread(config_file):
    # Same size and the same mtime tick, content differs: only the racy rule
    # can notice, because the stat signature is identical.
    fresh = time.time_ns() - 500_000_000
    for value in ("AAAA", "BBBB", "CCCC"):
        config_file.write_text(json.dumps({"workspace_path": value}), encoding="utf-8")
        _set_mtime(config_file, fresh)
        assert workspace.workspace_root() == value
    assert workspace._machine_config_memo is None


def test_future_mtime_is_treated_as_racy(config_file):
    future = time.time_ns() + 3_600 * 1_000_000_000
    for value in ("AAAA", "BBBB"):
        config_file.write_text(json.dumps({"workspace_path": value}), encoding="utf-8")
        _set_mtime(config_file, future)
        assert workspace._machine_config() == {"workspace_path": value}
    assert workspace._machine_config_memo is None


def test_memo_is_keyed_by_the_path_used_at_call_time(config_file, tmp_path, monkeypatch):
    # Two files with identical size and mtime_ns must not share an entry.
    other = tmp_path / "other.json"
    aged = time.time_ns() - AGED_NS
    for path, value in ((config_file, "AAAA"), (other, "BBBB")):
        path.write_text(json.dumps({"workspace_path": value}), encoding="utf-8")
        _set_mtime(path, aged)
    assert workspace._machine_config() == {"workspace_path": "AAAA"}
    monkeypatch.setattr(workspace, "CONFIG_PATH", str(other))
    assert workspace._machine_config() == {"workspace_path": "BBBB"}


# --- Law: missing/invalid config is {} and never memoized -------------------

def test_missing_config_returns_empty(config_file):
    assert not config_file.exists()
    assert workspace._machine_config() == {}
    assert workspace.workspace_root() is None


@pytest.mark.parametrize("raw", ["{" + "x" * 30, "[1, 2, 3]", '"text"', "null"])
def test_invalid_or_non_object_config_returns_empty(config_file, raw):
    _write(config_file, raw)
    assert workspace._machine_config() == {}
    assert workspace._machine_config() == {}


def test_unreadable_config_returns_empty_and_is_not_memoized(config_file):
    config_file.mkdir()  # stat succeeds, open fails
    _set_mtime(config_file, time.time_ns() - AGED_NS)
    assert workspace._machine_config() == {}
    assert workspace._machine_config_memo is None


def test_invalid_config_is_not_memoized(config_file):
    valid = json.dumps({"workspace_path": "AAAA"})
    aged = time.time_ns() - AGED_NS
    _write(config_file, "{" + "x" * (len(valid) - 1))
    _set_mtime(config_file, aged)
    assert workspace._machine_config() == {}
    # Repaired in place with the identical size and mtime: only an unmemoized
    # failure lets the next call see the repair.
    config_file.write_text(valid, encoding="utf-8")
    _set_mtime(config_file, aged)
    assert workspace._machine_config() == {"workspace_path": "AAAA"}


def test_deleted_config_after_caching_returns_empty(config_file):
    _write(config_file, {"workspace_path": "AAAA"})
    assert workspace.workspace_root() == "AAAA"
    config_file.unlink()
    assert workspace._machine_config() == {}
    assert workspace.workspace_root() is None


# --- Law: callers cannot mutate the cached value ----------------------------

@pytest.mark.parametrize("aged", [True, False], ids=["memoized", "racy"])
def test_mutating_a_returned_dict_does_not_change_the_next_result(config_file, aged):
    original = {"workspace_path": "AAAA", "saved_courses": [{"id": 1}], "nested": {"k": ["v"]}}
    _write(config_file, original, age_ns=AGED_NS if aged else 0)
    for _ in range(3):  # first call fills the memo; later calls hit it
        result = workspace._machine_config()
        assert result == original
        result["workspace_path"] = "mutated"
        result["saved_courses"].append({"id": 2})
        result["nested"]["k"].append("x")
        result["extra"] = True


# --- Cost: one stat per call, at most one open ------------------------------

def test_aged_config_is_opened_once_and_statted_once_per_call(config_file, monkeypatch):
    _write(config_file, {"workspace_path": "AAAA"})
    target = str(config_file)
    opens, stats = [], []
    real_open, real_stat = builtins.open, os.stat

    def spy_open(file, *args, **kwargs):
        if str(file) == target:
            opens.append(file)
        return real_open(file, *args, **kwargs)

    def spy_stat(path, *args, **kwargs):
        if str(path) == target:
            stats.append(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "stat", spy_stat)

    calls = 10
    assert [workspace.workspace_root() for _ in range(calls)] == ["AAAA"] * calls
    assert len(opens) <= 1
    assert len(stats) == calls
