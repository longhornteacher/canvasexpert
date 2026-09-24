"""Canvas-sourced ingest: catalog + mirror -> AssignmentContext -> the store.

Fabricated data uses generic names ("Learner One") and made-up Canvas ids,
never a real district (same convention as `test_mcp_server_tools.py`). The
vault and workspace are isolated to `tmp_path` per test.
"""
from __future__ import annotations

import ast
import io
import os
import sys
from datetime import date

# Same bootstrap as test_mcp_server_tools.py: api/mcp_server/pseudonym.py
# reaches sibling top-level api/ modules with bare names, which only resolves
# once api/ itself is on sys.path.
_API_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(_API_DIR)
for _path in (_API_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest

from api import course_catalog, feedback_safety
from api.dailywriting import canvas_attachments, canvas_ingest, canvas_source
from api.dailywriting.cli import _common
from api.dailywriting.cli import ingest_canvas as cli_ingest_canvas
from api.dailywriting.core import ingest as core_ingest_module
from api.dailywriting.core import scrub
from api.dailywriting.fixtures import loader
from api.dailywriting.store.identity import MappingResolver, VaultResolver
from api.dailywriting.store.repo import Repository
from api.feedback_vault import Vault
from api.mcp_server import tools
from api.mirror import store as mirror_store
from api.platform_services import workspace

COURSE_ID = "111"
ASSIGNMENT_ID = "700010"
_ANY_YEAR = (date(2026, 1, 1), date(2026, 12, 31))

FIXTURE_USERS = [
    {"id": 900001, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900001",
     "enrollments": [{"course_section_id": 800001}]},
    {"id": 900002, "name": "Learner Two", "sortable_name": "Two, Learner",
     "short_name": "Learner Two", "sis_user_id": "SIS-900002",
     "enrollments": [{"course_section_id": 800001}]},
]
SECTION_MAP = {"800001": "Period 1"}


def _mount(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def _write_catalog(root, *, description="Write a paragraph.", due_at="2026-09-14T23:59:00Z",
                   assignment_id=ASSIGNMENT_ID, submission_types=("online_text_entry",)):
    row = {
        "id": assignment_id, "name": "Essay 1", "description": description,
        "points_possible": 10, "due_at": due_at, "unlock_at": None, "lock_at": None,
        "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
        "published": True, "submission_types": list(submission_types),
        "assignment_group_id": 44,
    }
    assignment = course_catalog.normalize_assignment(row)
    now = mirror_store.now_iso()
    scope = lambda records: {"state": "current", "last_success_at": now,
                             "last_attempt_at": now, "error_code": "", "records": records}
    document = {
        "version": course_catalog.CATALOG_VERSION,
        "course_id": COURSE_ID,
        "course_name": "Course 111",
        "updated_at": now,
        "assignments": scope({assignment_id: assignment}),
        "modules": scope([]),
        "assignment_groups": scope([]),
        "pages": scope([]),
    }
    course_catalog.write_catalog(document)
    return assignment


def _write_mirror(root, *, bodies_by_user=None, uploads_by_user=None,
                  assignment_id=ASSIGNMENT_ID, extra_subs=()):
    """Write the fixture mirror.

    `bodies_by_user` are typed rows: {user_id: (body_html, submitted_at)}.
    `uploads_by_user` are `online_upload` rows with no body -- exactly what
    Canvas records for an uploaded file, and the shape that makes the mirror
    unable to supply the text: {user_id: submitted_at}.
    """
    mirror_store.write_roster(COURSE_ID, FIXTURE_USERS, SECTION_MAP, root=root)
    mirror_store.write_assignments(COURSE_ID, [
        {"id": assignment_id, "name": "Essay 1", "due_at": "2026-09-14T23:59:00Z",
         "points_possible": 10},
    ], root=root)
    rows = [
        {"assignment_id": assignment_id, "user_id": uid, "workflow_state": "submitted",
         "submitted_at": submitted_at, "body": body,
         "submission_type": "online_text_entry"}
        for uid, (body, submitted_at) in (bodies_by_user or {}).items()
    ]
    rows += [
        {"assignment_id": assignment_id, "user_id": uid, "workflow_state": "submitted",
         "submitted_at": submitted_at, "body": "",
         "submission_type": "online_upload"}
        for uid, submitted_at in (uploads_by_user or {}).items()
    ]
    mirror_store.merge_submissions(COURSE_ID, assignment_id, rows + list(extra_subs),
                                   root=root, replace=True)
    mirror_store.record_pass(COURSE_ID, "full", ok=True, root=root)


# --- uploaded-DOCX fixtures --------------------------------------------------
#
# Built in memory with python-docx (already a declared dependency) rather than
# committed as a binary, so what each test feeds the extractor is readable in
# the test that feeds it.

def _docx_bytes(paragraphs, *, heading=None):
    from docx import Document
    document = Document()
    if heading:
        document.add_heading(heading, level=1)
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _attachment(filename, url, *, size=None, created_at="2026-09-14T20:00:00Z"):
    """One Canvas attachment record, in the shape the submissions API returns."""
    return {"filename": filename, "display_name": filename, "url": url,
            "size": size, "created_at": created_at,
            "content-type": "application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document"}


class _FakeStream:
    """The streamed response `canvas_stream_get` hands back."""

    def __init__(self, data: bytes):
        self._data = data
        self.closed = False

    def iter_content(self, chunk_size=16_384):
        for start in range(0, len(self._data), chunk_size):
            yield self._data[start:start + chunk_size]

    def close(self):
        self.closed = True


class FakeCanvas:
    """A Canvas that counts every call, so a test can assert none happened."""

    def __init__(self):
        self.submissions: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.fail_users: set[str] = set()
        self.get_calls: list[str] = []
        self.download_calls: list[str] = []
        self.streams: list[_FakeStream] = []

    def install(self, monkeypatch, *, token=True):
        client = canvas_attachments.canvas_client
        monkeypatch.setattr(client, "canvas_headers", lambda: (
            ({"Authorization": "Bearer fixture-token"}, "https://canvas.example")
            if token else (None, None)))
        monkeypatch.setattr(client, "canvas_get", self._get)
        monkeypatch.setattr(client, "canvas_stream_get", self._stream)
        return self

    def add_upload(self, user_id, attachments, files=None):
        self.submissions[str(user_id)] = {"user_id": str(user_id), "body": "",
                                          "attachments": list(attachments)}
        self.files.update(files or {})

    def _get(self, path, params=None, timeout=20):
        self.get_calls.append(path)
        user_id = str(path).rsplit("/", 1)[-1]
        if user_id in self.fail_users:
            return None, "HTTP 503: Canvas is briefly unavailable"
        record = self.submissions.get(user_id)
        if record is None:
            return None, "HTTP 404: no such submission"
        return record, None

    def _stream(self, url, timeout=120):
        self.download_calls.append(url)
        data = self.files.get(url)
        if data is None:
            return None, "HTTP 404"
        stream = _FakeStream(data)
        self.streams.append(stream)
        return stream, None


def _repository(tmp_path):
    vault = Vault(str(tmp_path / "vault.json"))
    repository = Repository(tmp_path / "store", resolver=VaultResolver(vault), vault=vault)
    return repository, vault


def _bind_tools(monkeypatch, repository, vault):
    """Make `get_writing_history` read the same store/vault the ingest wrote."""
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    monkeypatch.setattr(tools, "_dailywriting_repository_factory", lambda: repository)


def _history(pseudonym, **kwargs):
    """`get_writing_history`, with an explicit window wide enough to hold
    every fixture date here regardless of the real machine clock -- the
    tool's own default lookback is relative to `date.today()`, and these
    fixtures are dated 2026 on purpose (matching the rest of this package's
    fixtures), which can fall outside that default window."""
    return tools.get_writing_history(pseudonym, since="2020-01-01",
                                     until="2030-12-31", **kwargs)


# --- AC1/AC2/AC3: happy path, idempotent re-run, no leak ---------------------

def test_ingest_then_get_writing_history_projects_evidence_in_ascending_order(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={
        # Later submitted_at listed first in the dict, to prove the payload
        # is actually sorted rather than accidentally already in order.
        900002: ("<p>Second student's paragraph about the topic.</p>", "2026-09-14T21:00:00Z"),
        900001: ("<p>First student's paragraph about the topic.</p>", "2026-09-14T20:00:00Z"),
    })
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("2 submission(s) ingested" in line for line in log)

    resolver = VaultResolver(vault)
    pseudonym_one = resolver.to_pseudonym("900001")
    pseudonym_two = resolver.to_pseudonym("900002")

    for pseudonym in (pseudonym_one, pseudonym_two):
        history = _history(pseudonym)
        assert len(history["submissions"]) == 1
        row = history["submissions"][0]
        assert "raw_text" not in row
        assert {"student_word_count", "segments", "flags"} <= set(row)

    submitted_ats = sorted(
        s.submitted_at for pseudonym in (pseudonym_one, pseudonym_two)
        for s in repository.submissions_in_window(pseudonym, *_ANY_YEAR))
    assert [t.isoformat() for t in submitted_ats] == [
        "2026-09-14T20:00:00+00:00", "2026-09-14T21:00:00+00:00"]


def test_ingest_twice_is_read_idempotent(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={
        900001: ("<p>A paragraph, written once.</p>", "2026-09-14T20:00:00Z"),
    })
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID, repository=repository))
    pseudonym = VaultResolver(vault).to_pseudonym("900001")
    first = _history(pseudonym)
    assert len(first["submissions"]) == 1

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID, repository=repository))
    second = _history(pseudonym)
    assert len(second["submissions"]) == 1
    assert second["submissions"][0]["submission_id"] == first["submissions"][0]["submission_id"]
    assert second == first


def test_no_real_name_leaks_into_get_writing_history(monkeypatch, tmp_path):
    """AC3: a roster real name embedded in another student's submission text
    must scrub before it can be quoted back out through get_writing_history."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={
        900001: ("<p>My friend Learner Two helped me plan this paragraph.</p>",
                 "2026-09-14T20:00:00Z"),
        900002: ("<p>An unrelated paragraph about the topic.</p>", "2026-09-14T20:05:00Z"),
    })
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID, repository=repository))

    pseudonym_one = VaultResolver(vault).to_pseudonym("900001")
    # The submitted body above carries a roster real name; confirm the scrub
    # removed it on the way in, so the scan below is checking scrubbed bytes
    # rather than text that never carried a name in the first place.
    stored = repository.submissions_in_window(pseudonym_one, *_ANY_YEAR)[0]
    assert "Learner Two" not in stored.raw_text

    result = _history(pseudonym_one, include_text=True)
    assert result["ok"] is True
    verdict = feedback_safety.scan_payload(result, vault)
    assert verdict["green"] is True
    assert verdict["soft"] == []


def test_scrub_bypass_would_be_caught_by_the_storage_leak_guard(monkeypatch, tmp_path):
    """Positive control: the storage guard rejects a scrub-bypassed ingest result."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={
        900001: ("<p>My friend Learner Two helped me plan this paragraph.</p>",
                 "2026-09-14T20:00:00Z"),
    })
    repository, vault = _repository(tmp_path)
    # `_repository` creates an empty isolated vault; give its roster scanner
    # the fabricated fixture identities so the storage guard sees this name.
    monkeypatch.setattr(
        vault,
        "all_real_identifiers",
        lambda: (
            {user["name"] for user in FIXTURE_USERS},
            {str(user["id"]) for user in FIXTURE_USERS},
        ),
    )

    # Patch the exact runtime handoff used by canvas_ingest. Return its normal
    # submission structure with the original text restored, modeling a scrub
    # bypass immediately before Repository.append_submission.
    from dataclasses import replace

    original_ingest = canvas_ingest.ingest_module.ingest
    bypassed_text = []
    unscrubbed_text = "My friend Learner Two helped me plan this paragraph."

    def bypass_scrub_at_ingest_call(**kwargs):
        # This scenario has already scrubbed the mirror text before this
        # handoff; restore the original fixture body in the returned record to
        # model the scrub-bypass regression the store guard must catch.
        assert "Learner Two" not in kwargs["text"]
        submission = original_ingest(**kwargs)
        submission = replace(submission, raw_text=unscrubbed_text)
        assert submission.raw_text == unscrubbed_text
        bypassed_text.append(submission.raw_text)
        return submission

    monkeypatch.setattr(
        canvas_ingest.ingest_module, "ingest", bypass_scrub_at_ingest_call)

    with pytest.raises(scrub.ScrubLeakError):
        list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                     repository=repository))
    assert bypassed_text == [
        unscrubbed_text
    ]


# --- AC4: structured refusal on a missing/stale catalog or mirror -----------

def test_missing_catalog_refuses_without_writing(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_mirror(root, bodies_by_user={900001: ("<p>Text.</p>", "2026-09-14T20:00:00Z")})
    repository, _vault = _repository(tmp_path)

    with pytest.raises(canvas_ingest.CanvasIngestError, match="catalog"):
        list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                     repository=repository))
    assert repository.read_rep(canvas_source.rep_id_for(COURSE_ID, ASSIGNMENT_ID)) is None


def test_unknown_assignment_id_refuses(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={900001: ("<p>Text.</p>", "2026-09-14T20:00:00Z")})
    repository, _vault = _repository(tmp_path)

    with pytest.raises(canvas_ingest.CanvasIngestError, match="No assignment"):
        list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, "999999", repository=repository))


def test_stale_mirror_refuses_without_writing(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    # Roster/submissions written, but no sync pass has ever recorded success
    # -- the "missing" half of AC4's "stale or missing".
    mirror_store.write_roster(COURSE_ID, FIXTURE_USERS, SECTION_MAP, root=root)
    mirror_store.write_assignments(COURSE_ID, [
        {"id": ASSIGNMENT_ID, "name": "Essay 1", "due_at": "2026-09-14T23:59:00Z"},
    ], root=root)
    mirror_store.merge_submissions(COURSE_ID, ASSIGNMENT_ID, [
        {"assignment_id": ASSIGNMENT_ID, "user_id": 900001, "workflow_state": "submitted",
         "submitted_at": "2026-09-14T20:00:00Z", "body": "<p>Text.</p>"},
    ], root=root, replace=True)
    repository, _vault = _repository(tmp_path)

    with pytest.raises(canvas_ingest.CanvasIngestError, match="CanvasMirror"):
        list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                     repository=repository))
    assert repository.read_rep(canvas_source.rep_id_for(COURSE_ID, ASSIGNMENT_ID)) is None


# --- AC5: a submission author missing from the identity vault ---------------

def test_unknown_author_is_skipped_and_reported_not_fatal(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    # 900001 is on the roster (gets a vault entry via roster sync); 900099 is
    # not -- a submission from someone the roster sync never covered.
    _write_mirror(root, bodies_by_user={
        900001: ("<p>A real paragraph from an enrolled student.</p>", "2026-09-14T20:00:00Z"),
    }, extra_subs=[
        {"assignment_id": ASSIGNMENT_ID, "user_id": 900099, "workflow_state": "submitted",
         "submitted_at": "2026-09-14T20:00:00Z", "body": "<p>From someone off the roster.</p>"},
    ])
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("1 submission(s) ingested" in line for line in log)
    assert any("1 skipped (no identity-vault entry" in line for line in log)

    pseudonym = VaultResolver(vault).to_pseudonym("900001")
    result = _history(pseudonym)
    assert len(result["submissions"]) == 1
    with pytest.raises(Exception):
        VaultResolver(vault).to_pseudonym("900099")


# --- AC7: an assignment with an empty description still ingests ------------

def test_empty_description_ingests_with_visibly_empty_prompt(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, description="")
    _write_mirror(root, bodies_by_user={
        900001: ("<p>A paragraph against a blank prompt.</p>", "2026-09-14T20:00:00Z"),
    })
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID, repository=repository))

    rep = repository.read_rep(canvas_source.rep_id_for(COURSE_ID, ASSIGNMENT_ID))
    assert rep.prompt_text == ""

    pseudonym = VaultResolver(vault).to_pseudonym("900001")
    result = _history(pseudonym)
    assert result["submissions"][0]["prompt_text"] == ""


# --- date derivation (Section 5.1) ------------------------------------------

def test_rep_date_falls_back_due_then_unlock_then_created():
    assert canvas_source.rep_date({"due_at": "2026-09-14T23:59:00Z",
                                   "unlock_at": "2026-09-01T00:00:00Z",
                                   "created_at": "2026-08-01T00:00:00Z"}
                                  ).isoformat() == "2026-09-14"
    assert canvas_source.rep_date({"due_at": "", "unlock_at": "2026-09-01T00:00:00Z",
                                   "created_at": "2026-08-01T00:00:00Z"}
                                  ).isoformat() == "2026-09-01"
    assert canvas_source.rep_date({"due_at": "", "unlock_at": "",
                                   "created_at": "2026-08-01T00:00:00Z"}
                                  ).isoformat() == "2026-08-01"


def test_rep_date_refuses_rather_than_inventing_a_date():
    with pytest.raises(canvas_source.DateDerivationError):
        canvas_source.rep_date({"id": "1", "due_at": "", "unlock_at": "", "created_at": ""})


# --- AC8: the typed path still reaches Canvas zero times --------------------
#
# This replaces an import-shape guard that asserted `canvas_ingest` imported no
# Canvas transport at all. That is deliberately no longer true: reading an
# uploaded file requires one. The guarantee that actually matters -- a typed
# submission is served entirely from the mirror -- is asserted at runtime here
# instead, which is stronger: an import can be present and unused, but a call
# counter cannot lie about a call that happened.

def test_typed_only_assignment_makes_zero_canvas_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={
        900001: ("<p>A typed paragraph, already in the mirror.</p>", "2026-09-14T20:00:00Z"),
        900002: ("<p>Another typed paragraph.</p>", "2026-09-14T20:05:00Z"),
    })
    fake = FakeCanvas().install(monkeypatch)
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("2 submission(s) ingested" in line for line in log)
    assert fake.get_calls == []
    assert fake.download_calls == []


def test_no_mcp_module_reaches_the_ingest_path():
    """The binding constraint: ingest must never be exposed as an MCP tool.

    A live Canvas path under the assistant is what `docs/mirror.md` design law
    6 forbids, so the assistant-facing package must not import this driver or
    its acquisition seam -- directly or by re-export.

    `api.platform_services.canvas_client` is held to the same rule. It is the raw Canvas
    HTTP transport, and the MCP tools have no business holding it: they serve
    from the local mirror, catalog, and writing store, and the one tool that
    does move Canvas data (`refresh_mirror`) goes through Canvas Expert's own
    sync engine instead. That indirection is the whole guarantee, and it is
    only cheap to verify while the transport is absent -- an unused import of
    it still costs a reader the work of proving nothing calls it.
    """
    forbidden = {
        "api.dailywriting.canvas_ingest",
        "api.dailywriting.canvas_attachments",
        "api.platform_services.canvas_client",
    }
    mcp_dir = os.path.join(_API_DIR, "mcp_server")
    checked = 0
    for name in sorted(os.listdir(mcp_dir)):
        if not name.endswith(".py"):
            continue
        checked += 1
        with open(os.path.join(mcp_dir, name), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module} if node.module else set()
                names |= {f"{node.module}.{alias.name}" for alias in node.names
                          if node.module}
            else:
                continue
            hit = names & forbidden
            assert not hit, f"api/mcp_server/{name} imports forbidden {hit}"
    assert checked > 1, "the MCP package should have been scanned"


# --- uploaded Word documents -------------------------------------------------

def test_uploaded_docx_ingests_like_a_typed_submission(monkeypatch, tmp_path):
    """AC1: a DOCX-only assignment reaches get_writing_history as evidence,
    for the right pseudonyms, in date order."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={
        900002: "2026-09-14T21:00:00Z",
        900001: "2026-09-14T20:00:00Z",
    })
    fake = FakeCanvas().install(monkeypatch)
    # Fixture text opens with lexicon words on purpose. The general scrub pass
    # treats an unknown capitalised token as a name, sentence-initial included,
    # so an essay opening "Dogs make better pets" would store "[name] make
    # better pets" -- deliberate privacy-first behaviour, identical for typed
    # text, and not something these tests should be quietly asserting around.
    fake.add_upload(900001, [_attachment("essay-one.docx", "https://files.example/one")],
                    {"https://files.example/one": _docx_bytes([
                        "I think dogs make better pets than cats, and my family agrees.",
                        "A dog greets you at the door every single day."])})
    fake.add_upload(900002, [_attachment("essay-two.docx", "https://files.example/two")],
                   {"https://files.example/two": _docx_bytes([
                       "A quiet apartment is a better home for a cat than for a dog.",
                       "They need less space and they clean themselves."])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("2 submission(s) ingested" in line for line in log)
    assert any("ingested from essay-one.docx" in line for line in log)

    resolver = VaultResolver(vault)
    for canvas_id, expected in (("900001", "dogs make better pets"),
                                ("900002", "quiet apartment is a better home")):
        history = _history(resolver.to_pseudonym(canvas_id), include_text=True)
        assert len(history["submissions"]) == 1
        row = history["submissions"][0]
        assert expected in row["raw_text"]
        assert {"student_word_count", "segments", "flags", "raw_text"} <= set(row)

    submitted = sorted(s.submitted_at for canvas_id in ("900001", "900002")
                       for s in repository.submissions_in_window(
                           resolver.to_pseudonym(canvas_id), *_ANY_YEAR))
    assert [t.isoformat() for t in submitted] == [
        "2026-09-14T20:00:00+00:00", "2026-09-14T21:00:00+00:00"]
    # Every stream was closed, so a refusal or a success never leaks a socket.
    assert all(stream.closed for stream in fake.streams)


def test_typed_body_and_upload_ingest_in_one_run_and_body_wins(monkeypatch, tmp_path):
    """AC2 + Section 5.1: both sources ingest in the same run, and the
    precedence is observable -- the typed row names the typed response as its
    source and costs no Canvas call, even though its row is an upload type."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, uploads_by_user={900002: "2026-09-14T21:00:00Z"},
                  extra_subs=[
        # An upload-typed row that also carries a body: the resubmission case.
        {"assignment_id": ASSIGNMENT_ID, "user_id": 900001,
         "workflow_state": "submitted", "submitted_at": "2026-09-14T20:00:00Z",
         "submission_type": "online_upload",
         "body": "<p>A typed paragraph that Canvas kept on an upload row.</p>"},
    ])
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900002, [_attachment("essay.docx", "https://files.example/two")],
                   {"https://files.example/two": _docx_bytes([
                       "An uploaded essay about the same prompt.",
                       "It argues the opposite side of the question."])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("2 submission(s) ingested" in line for line in log)
    assert any("ingested from typed response" in line for line in log)
    assert any("ingested from essay.docx" in line for line in log)
    # The typed row was never fetched; only the upload-only student was.
    assert fake.get_calls == [
        f"/api/v1/courses/{COURSE_ID}/assignments/{ASSIGNMENT_ID}/submissions/900002"]

    resolver = VaultResolver(vault)
    typed = _history(resolver.to_pseudonym("900001"), include_text=True)
    assert "typed paragraph" in typed["submissions"][0]["raw_text"]


def test_non_docx_attachment_is_skipped_and_named(monkeypatch, tmp_path):
    """AC3: a PDF is reported by name, not silently dropped."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900001, [_attachment("my-essay.pdf", "https://files.example/pdf")])
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("my-essay.pdf (not a Word document)" in line for line in log)
    assert any("no Word document on this submission" in line for line in log)
    assert any("0 submission(s) ingested" in line for line in log)
    assert any("only .docx is read" in line for line in log)
    # Nothing was downloaded, and nothing was stored for that student.
    assert fake.download_calls == []
    assert repository.submissions_in_window(
        VaultResolver(vault).to_pseudonym("900001"), *_ANY_YEAR) == []


def test_declared_oversize_is_refused_by_name_before_downloading(monkeypatch, tmp_path):
    """AC4, cheap half: Canvas's declared size refuses before a request is
    spent, and the run continues for the other student."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z",
                                         900002: "2026-09-14T20:05:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900001, [_attachment("huge.docx", "https://files.example/huge",
                                         size=canvas_attachments.MAX_ATTACHMENT_BYTES + 1)])
    fake.add_upload(900002, [_attachment("fine.docx", "https://files.example/fine")],
                   {"https://files.example/fine": _docx_bytes([
                       "A normal essay of an ordinary size.",
                       "It says what it needs to say and stops."])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("skipped huge.docx (10.0 MB exceeds the 10 MB limit" in line
               for line in log)
    assert any("1 submission(s) ingested" in line for line in log)
    assert any("over the size cap" in line for line in log)
    assert fake.download_calls == ["https://files.example/fine"]

    resolver = VaultResolver(vault)
    assert repository.submissions_in_window(resolver.to_pseudonym("900001"),
                                            *_ANY_YEAR) == []
    assert len(repository.submissions_in_window(resolver.to_pseudonym("900002"),
                                                *_ANY_YEAR)) == 1


def test_stream_over_the_cap_is_refused_and_stores_nothing(monkeypatch, tmp_path):
    """AC4, the half that matters: a declared size can be absent or wrong, so
    the cap is enforced again while reading. Nothing partial is stored."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    body = _docx_bytes(["An essay whose real bytes exceed the cap.",
                        "Canvas declared no size at all for it."])
    fake.add_upload(900001, [_attachment("sneaky.docx", "https://files.example/sneaky")],
                   {"https://files.example/sneaky": body})
    # A cap below the fixture's real size: the same refusal a 20 MB upload
    # would take, without building a 20 MB fixture.
    monkeypatch.setattr(canvas_attachments, "MAX_ATTACHMENT_BYTES", 512)
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("skipped sneaky.docx" in line and "exceeds the" in line for line in log)
    assert any("0 submission(s) ingested" in line for line in log)
    assert repository.submissions_in_window(
        VaultResolver(vault).to_pseudonym("900001"), *_ANY_YEAR) == []
    assert all(stream.closed for stream in fake.streams)


def test_roster_name_inside_a_docx_scrubs_before_it_can_be_quoted(monkeypatch, tmp_path):
    """AC5: a real name inside the extracted DOCX text is gone before storage,
    and the outbound scan is clean on both `green` and `soft` -- a roster name
    in free text is a soft finding, so green alone could not detect it."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900001, [_attachment("essay.docx", "https://files.example/one")],
                   {"https://files.example/one": _docx_bytes([
                       "My friend Learner Two helped me plan this paragraph.",
                       "We both agree that dogs are the better pet."])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                 repository=repository))

    pseudonym = VaultResolver(vault).to_pseudonym("900001")
    stored = repository.submissions_in_window(pseudonym, *_ANY_YEAR)[0]
    # The extracted DOCX text really did carry the name on the way in.
    assert "helped me plan this paragraph" in stored.raw_text
    assert "Learner Two" not in stored.raw_text

    result = _history(pseudonym, include_text=True)
    assert result["ok"] is True
    verdict = feedback_safety.scan_payload(result, vault)
    assert verdict["green"] is True
    assert verdict["soft"] == []


def test_docx_scrub_bypass_is_caught_by_the_storage_leak_guard(monkeypatch, tmp_path):
    """Positive control for the test above, on the DOCX path specifically: the
    green/soft assertion is not vacuously true. Bypass the scrub and the
    store's own guard raises rather than storing the leak."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900001, [_attachment("essay.docx", "https://files.example/one")],
                   {"https://files.example/one": _docx_bytes([
                       "My friend Learner Two helped me plan this paragraph."])})
    repository, _vault = _repository(tmp_path)

    monkeypatch.setattr(
        core_ingest_module.scrub, "scrub_writing",
        lambda text, **_: scrub.ScrubResult(text=text, findings=[]))

    with pytest.raises(scrub.ScrubLeakError):
        list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                     repository=repository))


def test_uploaded_ingest_twice_is_read_idempotent(monkeypatch, tmp_path):
    """AC6: ids are stable, so a re-run overwrites rather than duplicating --
    and it re-downloads, because a signed URL cannot be cached across runs."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900001, [_attachment("essay.docx", "https://files.example/one")],
                   {"https://files.example/one": _docx_bytes([
                       "An uploaded essay, ingested twice.",
                       "The second run must not create a second record."])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID, repository=repository))
    pseudonym = VaultResolver(vault).to_pseudonym("900001")
    first = _history(pseudonym, include_text=True)

    list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID, repository=repository))
    second = _history(pseudonym, include_text=True)

    assert len(second["submissions"]) == 1
    assert second == first
    assert len(fake.download_calls) == 2


def test_one_transport_failure_does_not_stop_the_rest(monkeypatch, tmp_path):
    """AC7: a failed fetch for one student is reported and skipped; the others
    still ingest, and the failure is named as transient so a re-run is the
    obvious next step."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z",
                                         900002: "2026-09-14T20:05:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.fail_users.add("900001")
    fake.add_upload(900002, [_attachment("essay.docx", "https://files.example/two")],
                   {"https://files.example/two": _docx_bytes([
                       "An essay that survives its classmate's failed fetch.",
                       "One student's outage is not everyone's outage."])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("could not read this submission from Canvas" in line and "503" in line
               for line in log)
    assert any("1 submission(s) ingested" in line for line in log)
    assert any("re-running picks them up" in line for line in log)

    resolver = VaultResolver(vault)
    assert repository.submissions_in_window(resolver.to_pseudonym("900001"),
                                            *_ANY_YEAR) == []
    assert len(repository.submissions_in_window(resolver.to_pseudonym("900002"),
                                                *_ANY_YEAR)) == 1


def test_latest_uploaded_docx_wins_and_the_earlier_one_is_named(monkeypatch, tmp_path):
    """Section 5.2, as the teacher chose: keep only the latest submitted."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    fake = FakeCanvas().install(monkeypatch)
    fake.add_upload(900001, [
        # Listed newest-first, so passing cannot come from taking the last item.
        _attachment("final-draft.docx", "https://files.example/final",
                    created_at="2026-09-14T19:00:00Z"),
        _attachment("first-try.docx", "https://files.example/first",
                    created_at="2026-09-13T08:00:00Z"),
    ], {"https://files.example/final": _docx_bytes([
            "The final draft, uploaded second, with a real conclusion."]),
        "https://files.example/first": _docx_bytes([
            "The first try, abandoned halfway through a sentence and"])})
    repository, vault = _repository(tmp_path)
    _bind_tools(monkeypatch, repository, vault)

    log = list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                       repository=repository))
    assert any("skipped first-try.docx (an earlier upload)" in line for line in log)
    assert any("ingested from final-draft.docx" in line for line in log)
    assert fake.download_calls == ["https://files.example/final"]

    history = _history(VaultResolver(vault).to_pseudonym("900001"), include_text=True)
    text = history["submissions"][0]["raw_text"]
    assert "final draft" in text
    assert "first try" not in text


def test_missing_canvas_token_refuses_before_any_write(monkeypatch, tmp_path):
    """An upload needs a token. Refusing before the rep is stored keeps
    `CanvasIngestError`'s promise that nothing was written."""
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root, submission_types=("online_upload",))
    _write_mirror(root, uploads_by_user={900001: "2026-09-14T20:00:00Z"})
    FakeCanvas().install(monkeypatch, token=False)
    repository, _vault = _repository(tmp_path)

    with pytest.raises(canvas_ingest.CanvasIngestError, match="Canvas token"):
        list(canvas_ingest.ingest_canvas_assignment(COURSE_ID, ASSIGNMENT_ID,
                                                     repository=repository))
    assert repository.read_rep(canvas_source.rep_id_for(COURSE_ID, ASSIGNMENT_ID)) is None


def test_acquisition_goes_through_the_real_client_and_emits_a_valid_scope(monkeypatch,
                                                                          tmp_path):
    """Everything above stubs `canvas_get` and `canvas_stream_get`, which
    leaves the transport layer itself unexercised: the operational log accepts
    a fixed set of priorities and rejects anything else by raising, so a bad
    telemetry scope here would fail only in front of a teacher. This test
    stands in one layer lower -- at `requests.get` -- so the real client, the
    real telemetry context and the real log all run.
    """
    _mount(monkeypatch, tmp_path)
    client = canvas_attachments.canvas_client
    monkeypatch.setattr(client, "canvas_headers",
                        lambda: ({"Authorization": "Bearer fixture-token"},
                                 "https://canvas.example"))

    docx = _docx_bytes(["An essay fetched through the real Canvas client.",
                        "Only requests.get itself is standing in."])

    class _JsonResponse:
        status_code = 200
        headers: dict = {}
        text = ""

        @staticmethod
        def json():
            return {"attachments": [_attachment("essay.docx",
                                                "https://files.example/one")]}

    class _FileResponse:
        status_code = 200
        headers: dict = {}
        text = ""

        def __init__(self):
            self.closed = False

        def iter_content(self, chunk_size=16_384):
            yield docx

        def close(self):
            self.closed = True

    file_response = _FileResponse()
    seen = []

    def fake_get(url, *, headers, params, timeout, **kwargs):
        seen.append({"url": url, **kwargs})
        return file_response if "files.example" in url else _JsonResponse()

    monkeypatch.setattr(client.requests, "get", fake_get)

    acquired = canvas_attachments.text_for(COURSE_ID, ASSIGNMENT_ID, "900001")

    assert acquired.outcome == "extracted"
    assert "An essay fetched through the real Canvas client." in acquired.text
    assert acquired.source == "essay.docx"
    # The JSON call is a plain GET; only the file call asks for a stream.
    assert "stream" not in seen[0]
    assert seen[1]["stream"] is True
    assert file_response.closed is True


def test_extractor_annotations_never_become_student_text():
    """`_docx_segments` annotates its own output for a reader. Those are the
    extractor's words: they must not be counted, attributed or quoted as the
    student's. A plain essay carries none of them and passes through intact."""
    plain = "First paragraph of the essay.\n\nSecond paragraph of the essay."
    assert canvas_attachments.submission_text(plain) == plain

    annotated = ("[Heading 1] My Argument Essay\n\n"
                 "Dogs make better pets than cats.\n\n"
                 "[Inline image 1]\n\n"
                 "[Table]\nClaim | Evidence\nDogs are loyal | They greet you")
    assert canvas_attachments.submission_text(annotated) == (
        "My Argument Essay\n\n"
        "Dogs make better pets than cats.\n\n"
        "Claim | Evidence\nDogs are loyal | They greet you")


def test_a_real_docx_round_trips_through_the_extractor_without_annotations():
    """The measurement the batch was designed on, pinned: a plain essay comes
    out of `_docx_segments` as paragraphs joined by blank lines -- the same
    shape a typed submission already stores -- with no annotations at all."""
    from api.powergrader import student_attachments
    data = _docx_bytes(["Dogs make better pets than cats, and my family proves it.",
                        "First, a dog greets you at the door every single day."])
    extracted, media = student_attachments._docx_segments(data)
    assert media == []
    assert extracted == ("Dogs make better pets than cats, and my family proves it.\n\n"
                         "First, a dog greets you at the door every single day.")
    assert canvas_attachments.submission_text(extracted) == extracted


# --- CLI parity --------------------------------------------------------------

def test_cli_ingest_canvas_runs_the_same_driver(monkeypatch, tmp_path, capsys):
    _mount(monkeypatch, tmp_path)
    root = str(tmp_path)
    _write_catalog(root)
    _write_mirror(root, bodies_by_user={
        900001: ("<p>Text for the CLI parity path.</p>", "2026-09-14T20:00:00Z"),
    })
    identity_path = tmp_path / "identity.json"
    identity_path.write_text('{"900001": "Fixture Pseudonym"}', encoding="utf-8")

    exit_code = cli_ingest_canvas.main([
        "--course-id", COURSE_ID, "--assignment-id", ASSIGNMENT_ID,
        "--identity-map", str(identity_path),
        "--store-root", str(tmp_path / "store"),
    ])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "1 submission(s) ingested" in out

    repository = Repository(tmp_path / "store",
                            resolver=MappingResolver({"900001": "Fixture Pseudonym"}),
                            vault=None)
    submissions = repository.submissions_in_window("Fixture Pseudonym", *_ANY_YEAR)
    assert len(submissions) == 1


def test_cli_ingest_canvas_surfaces_the_refusal_as_a_command_error(monkeypatch, tmp_path, capsys):
    _mount(monkeypatch, tmp_path)
    identity_path = tmp_path / "identity.json"
    identity_path.write_text("{}", encoding="utf-8")

    exit_code = _common.run(cli_ingest_canvas.main, [
        "--course-id", COURSE_ID, "--assignment-id", ASSIGNMENT_ID,
        "--identity-map", str(identity_path),
        "--store-root", str(tmp_path / "store"),
    ])
    assert exit_code == 2
    out = capsys.readouterr().out
    assert "error:" in out
    assert "catalog" in out
