from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from api import runtime_paths
from api.mirror import store
from api.platform_services import canvas_client, workspace
from api.mirror import service as mirror_service


@pytest.fixture
def _client_with(monkeypatch):
    def client_with(responses):
        calls = []
        queue = iter(responses)
        monkeypatch.setattr(
            canvas_client, "canvas_headers",
            lambda: ({"Authorization": "Bearer test"}, "https://canvas.test"),
        )

        def get(url, *, headers, params, timeout):
            calls.append((url, params, timeout))
            return next(queue)

        monkeypatch.setattr(canvas_client.requests, "get", get)
        return calls

    return client_with


@pytest.fixture
def _configure(monkeypatch, tmp_path):
    def configure(courses=({"id": "111", "name": "Course"},)):
        courses = list(courses)
        monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
        monkeypatch.setattr(mirror_service.config, "token_is_set", lambda: True)
        monkeypatch.setattr(mirror_service.config, "mirror_enabled", lambda: True)
        monkeypatch.setattr(mirror_service.config, "saved_courses", lambda: courses)
        monkeypatch.setattr(
            mirror_service.config, "active_courses",
            lambda: [course for course in courses if course.get("active", True)],
        )
        monkeypatch.setattr(mirror_service, "load_group_categories", lambda _course_id: ([], None, ""))

    return configure


@pytest.fixture
def _seed_delta_watermarks(_configure):
    _configure()
    store.record_pass(
        "111", "delta", ok=True, attempted_at="2026-07-16T11:00:00Z",
        watermarks={"submitted_since": "2026-07-16T10:50:00Z",
                    "graded_since": "2026-07-16T10:50:00Z"},
    )


@pytest.fixture
def _isolate_local_app_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_paths, "local_app_dir", lambda: tmp_path / "CanvasExpert")


@pytest.fixture
def _release_json():
    def release_json(
        tag="v1.0.0",
        zip_url="https://github.com/x/releases/download/v1.0.0/CanvasExpert.zip",
        sums_url="https://github.com/x/releases/download/v1.0.0/SHA256SUMS.txt",
        zip_name="CanvasExpert.zip",
    ):
        return {
            "tag_name": tag,
            "published_at": "2026-07-20T00:00:00Z",
            "html_url": "https://github.com/x/releases/tag/" + tag,
            "assets": [
                {"name": zip_name, "browser_download_url": zip_url},
                {"name": "SHA256SUMS.txt", "browser_download_url": sums_url},
            ],
        }

    return release_json


@pytest.fixture
def _make_zip():
    def make_zip(path: Path, files: dict) -> bytes:
        with zipfile.ZipFile(path, "w") as archive:
            for arcname, data in files.items():
                archive.writestr(arcname, data)
        return path.read_bytes()

    return make_zip
