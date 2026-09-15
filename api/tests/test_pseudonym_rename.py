"""Carry a pseudonym rename through the Writing Record.

The manual-rename and regenerate routes exercised here are the live
`POST /api/roster/student` Roster Console route -- the dead
`/api/names/pseudonym` and `/api/names/pseudonym/regenerate` routes were
deleted rather than ported, but the rename-ordering law lives in the shared
`roster_updates.update_student` updater both the Web UI and MCP call, so the
law is exercised the same way either route reaches it.
"""
import json

from fastapi.testclient import TestClient

from api import feedback_vault, pseudonym_rename
from api.feedback_vault import Vault
from api.webui import server
from api.webui.routes import roster as roster_routes
from api.webui.routes import roster_updates

_WORDS = feedback_vault._REGISTRY_WORDS


def _vault(tmp_path):
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign("9001", "Synthetic One", "SIS-1")
    vault.set_pseudonym("9001", _WORDS[0])
    vault.save()
    return vault


def test_current_pseudonym_does_not_mint_for_an_unknown_student(tmp_path):
    """Asking what the old name was must not create one as a side effect, which
    `get_or_assign` would."""
    vault = _vault(tmp_path)
    before = len(vault)
    assert pseudonym_rename.current_pseudonym(vault, "9001") == _WORDS[0]
    assert pseudonym_rename.current_pseudonym(vault, "does-not-exist") == ""
    assert pseudonym_rename.current_pseudonym(vault, "") == ""
    assert len(vault) == before


def test_a_no_op_rename_rewrites_nothing(monkeypatch):
    """Same name in and out, or a missing name, must not walk the writing store."""
    def explode(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("the writing store must not be opened for a no-op")

    monkeypatch.setattr(
        "api.dailywriting.store.repo.Repository.default", explode)
    assert pseudonym_rename.rewrite_writing_spans("Same Name", "Same Name") == ""
    assert pseudonym_rename.rewrite_writing_spans("", "New Name") == ""
    assert pseudonym_rename.rewrite_writing_spans("Old Name", "") == ""


def _wire_order(monkeypatch, tmp_path, *, rewrite_error=""):
    """Record the Writing Record rewrite around the vault write."""
    calls = []
    vault = _vault(tmp_path)
    # roster.py captured its own `_vault` reference at import time (`from
    # .names import _vault`), so the live rename route's factory must be
    # patched on roster_routes itself -- patching names_routes._vault would
    # not reach it.
    monkeypatch.setattr(roster_routes, "_vault", lambda: vault)

    def rewrite(old, new):
        calls.append(("rewrite", old, new))
        return rewrite_error

    # roster_updates.py imports the same `api.pseudonym_rename` module object
    # names.py does, so patching it here reaches both entry points.
    monkeypatch.setattr(roster_updates.pseudonym_rename, "rewrite_writing_spans", rewrite)
    return calls, vault


def _rename_via_roster(pseudonym=None, *, regenerate=False):
    patch = {"regenerate_pseudonym": True} if regenerate else {"pseudonym": pseudonym}
    return TestClient(server.app).post(
        "/api/roster/student",
        data={"course_id": "1", "user_id": "9001", "patch": json.dumps(patch)})


def test_the_manual_rename_route_rewrites_writing_after_the_vault_update(monkeypatch, tmp_path):
    calls, vault = _wire_order(monkeypatch, tmp_path)
    response = _rename_via_roster(_WORDS[1])

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == [("rewrite", _WORDS[0], _WORDS[1])]
    assert pseudonym_rename.current_pseudonym(vault, "9001") == _WORDS[1]


def test_the_regenerate_route_carries_the_rename_through_too(monkeypatch, tmp_path):
    calls, vault = _wire_order(monkeypatch, tmp_path)
    response = _rename_via_roster(regenerate=True)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert [call[0] for call in calls] == ["rewrite"]
    minted = calls[0][2]
    assert calls[0][1] == _WORDS[0]
    assert pseudonym_rename.current_pseudonym(vault, "9001") == minted


def test_an_unfinished_rewrite_surfaces_instead_of_reporting_success(monkeypatch, tmp_path):
    """The name is saved by then, so this cannot be silent: the teacher has to
    know the writing record still refers to the old one."""
    _wire_order(monkeypatch, tmp_path, rewrite_error=f"stored writing still says {_WORDS[0]}")
    response = _rename_via_roster(_WORDS[1])

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert _WORDS[0] in response.json()["error"]


def test_a_refused_collision_never_reaches_the_writing_store(monkeypatch, tmp_path):
    """A rename that the vault rejects must not rewrite anything, or stored text
    would move to a name no student holds."""
    calls, vault = _wire_order(monkeypatch, tmp_path)
    vault.get_or_assign("9002", "Synthetic Two", "SIS-2")
    vault.set_pseudonym("9002", _WORDS[2])
    vault.save()

    response = _rename_via_roster(_WORDS[2])

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert calls == [], "the rewrite must not have run"
    assert pseudonym_rename.current_pseudonym(vault, "9001") == _WORDS[0]
