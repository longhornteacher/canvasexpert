from __future__ import annotations

import copy
from pathlib import Path

import pytest

from api.operation_ledger import models
from api.operation_ledger.adapters import assignment as assignment_adapter
from api.operation_ledger.adapters import page as page_adapter
from api.operation_ledger.adapters import forge_files
from api.platform_services import canvas_client
from api import runtime_paths
from api.webui import af, pf
from api.webui.attachment_validation import ALLOWED_ATTACHMENT_EXTENSIONS


class FakeResponse:
    def __init__(self, status_code=201, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeContext:
    def __init__(self):
        self.events = []

    def before_send(self, key, digest):
        self.events.append(("before", key, digest))
        step = models.new_step(key)
        step.update({"state": "claimed", "outbound_started_at": "now"})
        return step

    def checkpoint_step(self, step, *, returned_object_id=None, returned_object_url=None):
        self.events.append(("checkpoint", step["step_key"], returned_object_id))
        if returned_object_id is not None:
            step["returned_object_id"] = returned_object_id
        if returned_object_url is not None:
            step["returned_object_url"] = returned_object_url
        return step


def test_upload_course_file_uploads_in_configured_folder(monkeypatch, tmp_path):
    pdf = tmp_path / "Water Cycle - Core - Printable.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(assignment_adapter, "_allowed_printable_roots", lambda: [str(tmp_path)])
    calls = {"send": [], "upload": []}

    def fake_canvas_send(method, path, payload, timeout=30):
        calls["send"].append((method, path, payload))
        return {"upload_url": "https://upload.invalid", "upload_params": {"key": "abc"}}, None

    def fake_upload(url, data, files, timeout=60, allow_redirects=True):
        calls["upload"].append((url, data, files["file"][0], allow_redirects))
        return FakeResponse(payload={"id": 55, "display_name": pdf.name})

    monkeypatch.setattr(assignment_adapter.canvas_client, "_canvas_send", fake_canvas_send)
    monkeypatch.setattr(assignment_adapter.requests, "post", fake_upload)
    result, error = assignment_adapter._upload_course_file("42", pdf)

    assert error is None
    assert result["id"] == 55
    assert calls["send"][0][2]["parent_folder_path"] == "Canvas Expert Printables"
    assert calls["upload"] == [("https://upload.invalid", {"key": "abc"}, pdf.name, False)]


def test_upload_completion_redirect_uses_only_validated_canvas_path(monkeypatch, tmp_path):
    pdf = tmp_path / "teacher-handout.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(assignment_adapter, "_allowed_printable_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(canvas_client, "canvas_headers", lambda: ({"Authorization": "Bearer token"}, "https://canvas.invalid"))
    calls = {"upload": [], "get": []}
    monkeypatch.setattr(canvas_client, "_canvas_send", lambda *_a, **_k: (
        {"upload_url": "https://signed-upload.invalid", "upload_params": {}}, None))

    def fake_upload(url, data, files, timeout=60, allow_redirects=True):
        calls["upload"].append((url, allow_redirects))
        return FakeResponse(status_code=301, headers={"Location": "/api/v1/files/73/create_success?uuid=abc"})

    def fake_get(path, params=None, timeout=20):
        calls["get"].append(path)
        return {"id": 73, "display_name": pdf.name}, None

    monkeypatch.setattr(assignment_adapter.requests, "post", fake_upload)
    monkeypatch.setattr(canvas_client, "canvas_get", fake_get)
    result, error = assignment_adapter._upload_course_file("42", pdf)
    assert error is None
    assert result["id"] == 73
    assert calls == {
        "upload": [("https://signed-upload.invalid", False)],
        "get": ["/api/v1/files/73/create_success?uuid=abc"],
    }


def test_upload_completion_rejects_external_origin(monkeypatch, tmp_path):
    pdf = tmp_path / "teacher-handout.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(assignment_adapter, "_allowed_printable_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(canvas_client, "canvas_headers", lambda: ({"Authorization": "Bearer token"}, "https://canvas.invalid"))
    monkeypatch.setattr(canvas_client, "_canvas_send", lambda *_a, **_k: (
        {"upload_url": "https://signed-upload.invalid", "upload_params": {}}, None))
    monkeypatch.setattr(assignment_adapter.requests, "post", lambda *_a, **_k: FakeResponse(
        status_code=302, headers={"Location": "https://steal.invalid/api/v1/files/73"}))
    gets = []
    monkeypatch.setattr(canvas_client, "canvas_get", lambda *a, **k: gets.append(a) or (None, "unexpected"))
    result, error = assignment_adapter._upload_course_file("42", pdf)
    assert result is None
    assert "outside the configured Canvas origin" in error
    assert gets == []


def test_upload_failure_is_reported_without_adopting_an_old_file(monkeypatch, tmp_path):
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(assignment_adapter, "_allowed_printable_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(assignment_adapter.canvas_client, "_canvas_send", lambda *_a, **_k: (
        {"upload_url": "https://upload.invalid", "upload_params": {}}, None))
    monkeypatch.setattr(assignment_adapter.requests, "post", lambda *_a, **_k: FakeResponse(
        status_code=500, text="upload failed"))
    result, error = assignment_adapter._upload_course_file("42", pdf)
    assert result is None
    assert "HTTP 500" in error


def test_assignment_and_page_attachment_shape_validation():
    valid = [{"file": "teacher handout.pdf", "label": "Read the handout"}]
    assert not any("attachments" in issue for issue in af.validate({
        "version": "2.0-json", "type": "ASSIGNMENT", "title": "Task", "points": 5,
        "overview": "<p>Task</p>", "directions": [{"html": "<p>Do it</p>", "response": "none"}],
        "attachments": valid,
    }))
    assert not any("attachments" in issue for issue in pf.validate({
        "version": "2.0-json", "type": "PAGE", "title": "Page",
        "layout": "standard", "overview": "<p>Page</p>", "attachments": valid,
    }))
    for invalid in (
        [{"file": "../secret.pdf", "label": "Private"}],
        [{"file": "A.PDF", "label": "A"}, {"file": "a.pdf", "label": "B"}],
        [{"file": "handout.pdf", "label": " "}],
    ):
        assert any("attachments" in issue for issue in af.validate({
            "version": "2.0-json", "type": "ASSIGNMENT", "title": "Task", "points": 5,
            "overview": "<p>Task</p>", "directions": [{"html": "<p>Do it</p>", "response": "none"}],
            "attachments": invalid,
        }))


@pytest.mark.parametrize(
    ("extension", "accepted"),
    [(extension, True) for extension in sorted(ALLOWED_ATTACHMENT_EXTENSIONS)]
    + [(extension, False) for extension in ("exe", "txt", "zip")],
)
def test_attachment_type_contract_for_both_forges(extension, accepted):
    attachment = [{"file": f"handout.{extension}", "label": "Teacher file"}]
    assignment_problems = af.validate({
        "version": "2.0-json", "type": "ASSIGNMENT", "title": "Task", "points": 5,
        "overview": "<p>Task</p>",
        "directions": [{"html": "<p>Do it</p>", "response": "none"}],
        "attachments": attachment,
    })
    page_problems = pf.validate({
        "version": "2.0-json", "type": "PAGE", "title": "Page",
        "layout": "standard", "overview": "<p>Page</p>",
        "attachments": attachment,
    })
    assert any("attachments" in issue for issue in assignment_problems) is not accepted
    assert any("attachments" in issue for issue in page_problems) is not accepted


def test_attachment_resolver_rejects_missing_and_symlink_escape(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    attachments = workspace / "To Review" / "Attachments"
    attachments.mkdir(parents=True)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private")
    monkeypatch.setattr(runtime_paths, "workspace_root", lambda: workspace)
    try:
        (attachments / "escape.pdf").symlink_to(outside)
    except OSError:
        pass
    else:
        try:
            forge_files.resolve_attachments([{"file": "escape.pdf", "label": "Escape"}])
        except ValueError as exc:
            assert "outside To Review/Attachments" in str(exc)
        else:
            raise AssertionError("symlink escape was accepted")
    try:
        forge_files.resolve_attachments([{"file": "missing.pdf", "label": "Missing"}])
    except ValueError as exc:
        assert "missing or outside" in str(exc)
    else:
        raise AssertionError("missing attachment was accepted")
    for outside_name in ("../outside.pdf", str(outside)):
        with pytest.raises(ValueError):
            forge_files.resolve_attachments([{"file": outside_name, "label": "Outside"}])


def test_upload_checkpoint_is_idempotent_and_exact_id_verified(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    path = workspace / "To Review" / "Attachments" / "handout.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"teacher file")
    monkeypatch.setattr(runtime_paths, "workspace_root", lambda: workspace)
    record = {"file": path.name, "filename": path.name, "path": str(path),
              "sha256": forge_files.sha256_file(path)}
    context = FakeContext()
    steps = []
    uploads = []
    uploaded, failure = forge_files.ensure_uploaded_file(
        record=record, step_key="upload_attachment:0", folder="Canvas Expert Attachments",
        course_id="42", steps=steps, context=context,
        upload_file=lambda *a, **k: uploads.append((a, k)) or ({"id": 12}, None),
        get_file=lambda *_a: ({"id": 12}, None),
    )
    assert failure is None
    assert uploaded["id"] == 12
    assert [event[0] for event in context.events] == ["before", "checkpoint"]
    assert forge_files.canvas_file_url(uploaded["id"]) in forge_files.bind_link_slots(
        '<a href="{{ce:attachment:0}}">Handout</a>', "attachment", [uploaded])

    again, failure = forge_files.ensure_uploaded_file(
        record=record, step_key="upload_attachment:0", folder="Canvas Expert Attachments",
        course_id="42", steps=steps, context=context,
        upload_file=lambda *a, **k: uploads.append((a, k)) or (None, "resend forbidden"),
        get_file=lambda _course, file_id: ({"id": file_id}, None),
    )
    assert failure is None
    assert again["id"] == "12"
    assert len(uploads) == 1


def test_upload_file_drift_and_unknown_result_stop_before_retry(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    path = workspace / "To Review" / "Attachments" / "handout.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    monkeypatch.setattr(runtime_paths, "workspace_root", lambda: workspace)
    record = {"file": path.name, "filename": path.name, "path": str(path),
              "sha256": forge_files.sha256_file(path)}
    context = FakeContext()
    steps = []
    uploads = []
    path.write_bytes(b"changed")
    _, failure = forge_files.ensure_uploaded_file(
        record=record, step_key="upload_attachment:0", folder="Canvas Expert Attachments",
        course_id="42", steps=steps, context=context,
        upload_file=lambda *a, **k: uploads.append((a, k)) or (None, "should not upload"),
        get_file=lambda *_a: (None, "should not read"),
    )
    assert failure["state"] == "blocked"
    assert failure["error_code"] == "file_drift"
    assert context.events == []
    assert uploads == []

    path.write_bytes(b"original")
    _, failure = forge_files.ensure_uploaded_file(
        record=record, step_key="upload_attachment:0", folder="Canvas Expert Attachments",
        course_id="42", steps=steps, context=context,
        upload_file=lambda *_a, **_k: ({"id": 16}, None),
        get_file=lambda *_a: (None, "must not read"),
    )
    # This step has no marker because the drift stopped before sending; it is safe to retry.
    assert failure is None

    uncertain = models.new_step("upload_attachment:1")
    uncertain["outbound_started_at"] = "now"
    steps.append(uncertain)
    _, failure = forge_files.ensure_uploaded_file(
        record=record, step_key="upload_attachment:1", folder="Canvas Expert Attachments",
        course_id="42", steps=steps, context=context,
        upload_file=lambda *_a, **_k: (None, "must not resend"),
        get_file=lambda *_a: (None, "must not search by name"),
    )
    assert failure["state"] == "sent_unknown"
    assert failure["error_code"] == "file_upload_unresolved"


def test_upload_success_without_id_is_attention_and_never_resends(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    path = workspace / "To Review" / "Attachments" / "handout.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    monkeypatch.setattr(runtime_paths, "workspace_root", lambda: workspace)
    record = {"file": path.name, "filename": path.name, "path": str(path),
              "sha256": forge_files.sha256_file(path)}
    context = FakeContext()
    steps = []
    uploads = []
    for _ in range(2):
        _, failure = forge_files.ensure_uploaded_file(
            record=record, step_key="upload_attachment:0", folder="Canvas Expert Attachments",
            course_id="42", steps=steps, context=context,
            upload_file=lambda *a, **k: uploads.append((a, k)) or ({"display_name": path.name}, None),
            get_file=lambda *_a: (None, "filename search is not proof"),
        )
        assert failure["state"] == "sent_unknown"
    assert len(uploads) == 1
    assert len([event for event in context.events if event[0] == "before"]) == 1


def test_assignment_attachment_and_printable_apply_then_resume(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    handout = tmp_path / "host-file" / "handout.pdf"
    handout.parent.mkdir(parents=True)
    handout.write_bytes(b"teacher handout")
    monkeypatch.setattr(runtime_paths, "workspace_root", lambda: workspace)
    monkeypatch.setattr(forge_files, "_private_store_roots", lambda: [workspace])
    staged = forge_files.stage_attachment(str(handout))
    assert staged == {"ok": True, "file": "handout.pdf", "size_bytes": len(b"teacher handout")}
    monkeypatch.setattr(assignment_adapter.af, "parse_file", lambda _path: ({
        "title": "Practice", "points": 5,
        "overview": "<p>Read and respond.</p>",
        "directions": [{"html": "<p>Write one claim.</p>", "response": "short"}],
        "submission": {"types": ["online_text_entry"]},
        "attachments": [{"file": "handout.pdf", "label": "Read the handout"}],
    }, []))

    def fake_pdf(_html, _css, out_path):
        Path(out_path).write_bytes(b"%PDF synthetic")
        return out_path

    uploads = []

    def fake_upload(_course, path, *, folder="Canvas Expert Printables"):
        file_id = str(71 + len(uploads))
        uploads.append((folder, Path(path).name, file_id))
        return {"id": file_id}, None

    sent = []

    def fake_send(method, path, body, timeout=30):
        sent.append((method, path, body))
        return {"id": 90, "html_url": "https://canvas.invalid/assignments/90"}, None

    def fake_get(path, params=None, timeout=20):
        if path.endswith("/assignments/90"):
            return {"id": 90, "html_url": "https://canvas.invalid/assignments/90"}, None
        raise AssertionError(path)

    monkeypatch.setattr(assignment_adapter, "html_to_pdf", fake_pdf)
    monkeypatch.setattr(assignment_adapter, "_upload_course_file", fake_upload)
    monkeypatch.setattr(assignment_adapter, "_get_course_file", lambda _course, file_id: ({"id": file_id}, None))
    monkeypatch.setattr(canvas_client, "_canvas_send", fake_send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake_get)

    adapter = assignment_adapter.AssignmentAdapter()
    payload = adapter.build_payload({"path": "synthetic.assignmentforge.json"})
    review = adapter.freeze_review(payload, {"course_id": "42"}, {})
    assert review["printables"][0]["sha256"] == payload["printables"][0]["sha256"]
    assert "path" not in review["printables"][0]
    context = FakeContext()
    first = adapter.execute(payload, {"course_id": "42", "steps": []}, {}, {}, context)
    assert first["state"] == "applied"
    assert [row[0] for row in uploads] == ["Canvas Expert Attachments", "Canvas Expert Printables"]
    description = sent[0][2]["assignment"]["description"]
    assert "Read the handout" in description and "Printable:" in description
    assert "/files/71/download" in description and "/files/72/download" in description
    assert "{{ce:" not in description

    again = adapter.execute(payload, {"course_id": "42", "steps": copy.deepcopy(first["steps"])}, {}, {}, FakeContext())
    assert again["state"] == "applied"
    assert len(uploads) == 2 and len(sent) == 1


def test_page_attachment_apply_then_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(page_adapter.pf, "parse_file", lambda _path: ({
        "title": "Reference", "layout": "standard", "overview": "<p>Read this.</p>",
        "attachments": [{"canvas_file": "slides.pptx", "label": "Class slides"}],
    }, []))

    uploads = []
    sent = []

    def fake_upload(_course, path, *, folder):
        uploads.append((folder, Path(path).name))
        return {"id": 82}, None

    def fake_send(method, path, body, timeout=30):
        sent.append((method, path, body))
        return {"url": "reference", "html_url": "https://canvas.invalid/pages/reference"}, None

    def fake_get(path, params=None, timeout=20):
        if path.endswith("/pages/reference"):
            return {"url": "reference", "html_url": "https://canvas.invalid/pages/reference"}, None
        raise AssertionError(path)

    monkeypatch.setattr(page_adapter, "_upload_course_file", fake_upload)
    file_info = {"id": 81, "display_name": "slides.pptx", "size": 17,
                 "updated_at": "2026-09-25T12:00:00Z"}
    monkeypatch.setattr(page_adapter, "_get_course_file", lambda _course, file_id: (file_info, None))
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", lambda path, params: (
        [file_info], None, True))
    monkeypatch.setattr(canvas_client, "_canvas_send", fake_send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake_get)

    adapter = page_adapter.PageAdapter()
    payload = adapter.build_payload({"path": "synthetic.pageforge.json", "course_id": "42"})
    first = adapter.execute(payload, {"course_id": "42", "steps": []}, {}, {}, FakeContext())
    assert first["state"] == "applied"
    assert uploads == []
    body = sent[0][2]["wiki_page"]["body"]
    assert "Class slides" in body and "/files/81/download" in body
    assert "{{ce:" not in body

    again = adapter.execute(payload, {"course_id": "42", "steps": copy.deepcopy(first["steps"])}, {}, {}, FakeContext())
    assert again["state"] == "applied"
    assert len(uploads) == 0 and len(sent) == 1


def test_canvas_file_drift_blocks_before_content_creation(monkeypatch):
    payload = {"title": "Reference", "body": '<a href="{{ce:attachment:0}}">Guide</a>',
               "published": False, "attachments": [{
                   "canvas_file": "Guide.pdf", "canvas_file_id": "81",
                   "label": "Guide", "size": 17,
                   "updated_at": "2026-09-25T12:00:00Z",
               }]}
    monkeypatch.setattr(page_adapter, "_get_course_file", lambda *_args: ({
        "id": "81", "size": 18, "updated_at": "2026-09-25T12:00:00Z",
    }, None))
    monkeypatch.setattr(canvas_client, "_canvas_send",
                        lambda *_args, **_kwargs: pytest.fail("content was created after file drift"))
    result = page_adapter.PageAdapter().execute(
        payload, {"course_id": "42", "steps": []}, {}, {}, FakeContext())
    assert result["state"] == "blocked"
    assert result["error_code"] == "file_drift"
