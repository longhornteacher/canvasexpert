from __future__ import annotations

import copy
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from docx import Document


@pytest.fixture
def feedback_revision_work(tmp_path, monkeypatch):
    """Synthetic private feedback scope; real shared journal, fake Canvas only."""
    from api.feedback_vault import Vault
    from api.shared_work import SharedWorkStore
    from api.powergrader import feedback_revision as fr
    from api.mcp_server import tools
    from api.mirror import store as mirror_store

    vault = Vault(str(tmp_path / "vault.json"))
    labels = [vault.get_or_assign(uid, real_name=name) for uid, name in (
        ("900001", "Synthetic First"), ("900002", "Fictional Omega"))]
    vault.save()
    store = SharedWorkStore(root=str(tmp_path))
    stamp = mirror_store.now_iso()
    freshness = {"state": "current", "last_success_at": stamp,
                 "requires_teacher_confirmation": False}
    comments = {"state": "current", "last_success_at": stamp, "records": []}
    assignment = {"id": "700010", "name": "Practice response", "description": "Explain.",
                  "is_quiz": False, "quiz_kind": "", "is_quiz_lti_assignment": False,
                  "points_possible": 100}
    rows = [{"user_id": uid, "assignment_id": "700010", "workflow_state": "graded",
             "score": score, "grade": str(score), "late": False, "missing": False,
             "excused": False, "body": "A complete synthetic response.",
             "user": {"name": name}, "submission_comments": [
                 {"id": cid, "author_id": "900099", "author_role": "teacher",
                  "comment": "Original long feedback.", "created_at": stamp}]} for uid, cid, score, name in (
                     ("900001", "500001", 0, "Synthetic First"),
                     ("900002", "500002", 85, "Fictional Omega"))]
    calls = []
    monkeypatch.setattr(fr, "SharedWorkStore", lambda: store)
    monkeypatch.setattr(fr.assignment_refresh, "prepare_assignment_from_mirror",
                        lambda *_a: (copy.deepcopy(rows), copy.deepcopy(assignment), {"freshness": freshness}))
    monkeypatch.setattr(fr.read_service, "private_submission_comments", lambda *_a, **_kw: comments)
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    monkeypatch.setattr(tools, "_course_gate_check", lambda cid: None if cid == "111" else "unavailable")
    monkeypatch.setattr(fr.canvas_client, "_canvas_send",
                        lambda method, path, payload: (calls.append((method, path, copy.deepcopy(payload))) or {}, None))
    monkeypatch.setattr(fr.canvas_client, "canvas_get", lambda *_a, **_kw: pytest.fail("No Canvas GET allowed"))

    def prepare(**kwargs):
        return tools.prepare_feedback_revision("111", "700010", **kwargs)

    def revisions(packet, take=None):
        return [{"pseudonym": r["pseudonym"], "comment_key": r["comment_key"],
                 "feedback": "Submit a second typed draft that fixes the listed problems."}
                for r in packet["revisions"][:take]]

    return SimpleNamespace(store=store, vault=vault, labels=labels, rows=rows,
                           comments=comments, assignment=assignment, freshness=freshness,
                           calls=calls, prepare=prepare, revisions=revisions, tools=tools)


@pytest.fixture
def feedback_attachment_work(feedback_revision_work, tmp_path, monkeypatch):
    from api.powergrader import feedback_revision as fr

    w = feedback_revision_work
    folder = tmp_path / "To Review" / "Attachments"
    folder.mkdir(parents=True)
    w.file = folder / "Practice Exemplars.docx"
    w.file.write_bytes(b"synthetic unchanged exemplar bytes")
    w.uploads = []
    monkeypatch.setattr(fr.forge_files.runtime_paths, "workspace_root", lambda: str(tmp_path))
    def send(method, path, payload):
        w.calls.append((method, path, copy.deepcopy(payload)))
        if method == "POST":
            return {"upload_url": "https://signed-upload.invalid", "upload_params": {"key": "synthetic"}}, None
        return {}, None
    def multipart(url, *, data, files, timeout, allow_redirects):
        assert allow_redirects is False
        assert url == "https://signed-upload.invalid" and data == {"key": "synthetic"}
        w.uploads.append((files["file"][0], files["file"][1].read(), files["file"][2]))
        return SimpleNamespace(status_code=201, headers={}, json=lambda: {"id": 600000 + len(w.uploads)})
    monkeypatch.setattr(fr.canvas_client, "_canvas_send", send)
    monkeypatch.setattr(fr.assignment_whole.requests, "post", multipart)
    return w


@pytest.fixture
def mirror_assignment_input(tmp_path, monkeypatch):
    """Synthetic normalized mirror rows for the assignment preparation boundary."""
    from api.powergrader import assignment_refresh

    assignment = {
        "id": "assignment", "name": "Synthetic response", "points_possible": 10,
        "description_text": "Explain your reasoning.", "quiz_id": "",
        "is_quiz": False, "quiz_kind": "", "is_quiz_lti_assignment": False,
    }
    roster = {"state": "current", "records": [
        {"id": "member", "name": "Synthetic learner"},
        {"id": "waiting-member", "name": "Synthetic waiting learner"},
    ]}
    assignments = {"state": "current", "records": [assignment]}
    scope = {"state": "current", "mirror_revision": 1, "snapshot_id": "synthetic:1"}
    for projection in (roster, assignments):
        projection.update(scope)
    document = {"state": "current", "submissions": {}}

    def entry(user_id="nonmember", **overrides):
        raw = {
            "assignment_id": "assignment", "user_id": user_id,
            "workflow_state": "unsubmitted", "attempt": None,
        }
        raw.update(overrides)
        _user_id, current, attempts = assignment_refresh.mirror_store.normalize_submission(raw)
        return {"current": current, "attempts": attempts}

    monkeypatch.setattr(assignment_refresh.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(assignment_refresh.config, "course_display_name", lambda _: "Synthetic")
    monkeypatch.setattr(assignment_refresh.scoring_local, "load_scoring_snapshot",
                        lambda *args, **kwargs: {"snapshot": {}, "freshness": {"state": "current"}})
    monkeypatch.setattr(assignment_refresh.read_service, "private_roster", lambda *a, **kw: roster)
    monkeypatch.setattr(assignment_refresh.read_service, "private_assignments", lambda *a, **kw: assignments)
    monkeypatch.setattr(assignment_refresh.read_service, "private_submissions", lambda *a, **kw: scope)
    monkeypatch.setattr(assignment_refresh.mirror_store, "read_roster", lambda *a, **kw: roster)
    monkeypatch.setattr(assignment_refresh.mirror_store, "read_assignments", lambda *a, **kw: assignments)
    monkeypatch.setattr(assignment_refresh.mirror_store, "read_submissions", lambda *a, **kw: document)
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions",
                        lambda *a, **kw: pytest.fail("mirror preparation made a Canvas call"))

    return SimpleNamespace(
        entry=entry, document=document, assignment=assignment, roster=roster,
        prepare=lambda: assignment_refresh.prepare_assignment_from_mirror("course", "assignment"),
        snapshot=lambda: copy.deepcopy((document, roster, assignments, scope)),
    )


@pytest.fixture
def _signed_grader_http():
    def build(preview_url, *, launch_form=True):
        calls = []
        form = ('<form action="https://quiz.invalid/signed"><input name="participant_session_id" '
                'value="synthetic-participant"><input name="signature" value="synthetic-signature"></form>')
        replies = iter([
            {"session_url": "https://canvas.invalid/session"},
            {},
            {"data": {"assignment": {"submissionsConnection": {"nodes": [{"previewUrl": preview_url}]}}}},
            form if launch_form else '<script>ENV.NEW_QUIZZES = {"token":"native-only"};</script>',
            "window.launch_params = " + json.dumps({"access_token": "synthetic-launch-token",
                                                    "launch_url": "https://quiz.invalid/launch"}) + ";",
        ])

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            reply = next(replies)
            return SimpleNamespace(status_code=200, text=reply if isinstance(reply, str) else "",
                                   json=lambda: reply if isinstance(reply, dict) else None)

        return SimpleNamespace(get=lambda url, **kw: request("GET", url, **kw),
                               post=lambda url, **kw: request("POST", url, **kw)), calls

    return build


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@pytest.fixture
def _base_docx():
    def base_docx(body_text: str = "A fictional visible response.") -> bytes:
        document = Document()
        document.add_paragraph(body_text)
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()

    return base_docx


@pytest.fixture
def _revision_xml():
    def revision_xml(
        kind: str,
        text: str,
        *,
        author: str,
        timestamp: str = "2026-07-27T10:00:00-05:00",
    ) -> str:
        tag = "ins" if kind == "insertion" else "del"
        text_tag = "t" if kind == "insertion" else "delText"
        return (
            f'<w:{tag} w:author="{author}" w:date="{timestamp}">'
            f"<w:r><w:{text_tag}>{text}</w:{text_tag}></w:r>"
            f"</w:{tag}>"
        )

    return revision_xml


@pytest.fixture
def _rewrite_docx():
    def rewrite_docx(base: bytes, replacements: dict[str, bytes], additions: dict[str, bytes] | None = None) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(base)) as source, zipfile.ZipFile(
            output, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                if info.filename in replacements:
                    target.writestr(info, replacements[info.filename])
                else:
                    target.writestr(info, source.read(info.filename))
            existing = set(source.namelist())
            for name, payload in (additions or {}).items():
                if name not in existing:
                    target.writestr(name, payload)
        return output.getvalue()

    return rewrite_docx


@pytest.fixture
def _timeline_docx(_base_docx, _revision_xml, _rewrite_docx):

    def timeline_docx(
        *,
        blocks: list[tuple[str, str, str]] | None = None,
        track_revisions: bool = True,
        protection: str = "none",
        creator: str = "Fictional Learner",
        last_modified: str = "Fictional Learner",
        total_time: str = "37",
        revision: str = "8",
        stories: dict[str, str] | None = None,
        body_text: str = "A fictional visible response.",
        timestamp: str = "2026-07-27T10:00:00-05:00",
    ) -> bytes:
        base = _base_docx(body_text)
        with zipfile.ZipFile(io.BytesIO(base)) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        revision_nodes = "".join(
            _revision_xml(kind, text, author=author, timestamp=timestamp)
            for kind, text, author in (blocks or [])
        )
        document_xml = document_xml.replace("</w:body>", f"<w:p>{revision_nodes}</w:p></w:body>")
        protection_xml = ""
        if protection == "unlocked":
            protection_xml = '<w:documentProtection w:edit="trackedChanges" w:enforcement="0"/>'
        elif protection == "locked":
            protection_xml = '<w:documentProtection w:edit="trackedChanges" w:enforcement="1"/>'
        settings_xml = (
            f'<w:settings xmlns:w="{W_NS}">'
            + ("<w:trackRevisions/>" if track_revisions else "")
            + protection_xml
            + "</w:settings>"
        )
        core_xml = (
            '<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:creator>{creator}</dc:creator>"
            f"<cp:lastModifiedBy>{last_modified}</cp:lastModifiedBy>"
            "</cp:coreProperties>"
        )
        app_xml = (
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            f"<TotalTime>{total_time}</TotalTime><Revision>{revision}</Revision>"
            "</Properties>"
        )
        story_parts = {}
        for name, inner in (stories or {}).items():
            story_parts[name] = (
                f'<w:hdr xmlns:w="{W_NS}"><w:p>{inner}</w:p></w:hdr>'
            ).encode("utf-8")
        return _rewrite_docx(
            base,
            {
                "word/document.xml": document_xml.encode("utf-8"),
                "word/settings.xml": settings_xml.encode("utf-8"),
                "docProps/core.xml": core_xml.encode("utf-8"),
                "docProps/app.xml": app_xml.encode("utf-8"),
            },
            story_parts,
        )

    return timeline_docx


@pytest.fixture
def _tracked_docx_submission(_timeline_docx):
    def tracked_docx_submission(user_id: str, name: str, local_file):
        payload = _timeline_docx(blocks=[("insertion", "fictional work", name)])
        local_file.write_bytes(payload)
        return {
            "user_id": user_id,
            "submission_type": "online_upload",
            "workflow_state": "submitted",
            "submitted_at": "2026-09-11T15:20:00Z",
            "cached_due_date": "2026-09-10T23:59:00Z",
            "attempt": 1,
            "body": "",
            "user": {"name": name, "sortable_name": name},
            "assignment": {"id": "assignment-1", "name": "Essay"},
            "attachments": [{
                "filename": "answer.docx",
                "local_path": str(local_file),
                "declared_size": len(payload),
                "actual_size": len(payload),
                "download_status": "downloaded",
                "extraction_status": "extracted",
                "ai_eligible": True,
                "local_only": False,
                "item_id": "assignment-1",
            }],
            "expected_attachment_count": 1,
        }

    return tracked_docx_submission

@pytest.fixture(autouse=True)
def _score_evidence_workspace(tmp_path, monkeypatch):
    """Numeric write examples get an isolated private evidence archive."""
    from api.platform_services import workspace

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
