from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def score_ledger_workspace(tmp_path, monkeypatch):
    """Keep durable score evidence separate from each disposable mirror root."""
    from api.platform_services import workspace
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path / "workspace"))


@pytest.fixture
def submission_history_support():
    class Response:
        def __init__(self, chunks, declared=None):
            self.chunks = list(chunks)
            self.headers = ({"Content-Length": str(declared)} if declared is not None else {})
            self.closed = False

        def iter_content(self, chunk_size):
            for chunk in self.chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk

        def close(self):
            self.closed = True

    def row(*, body="First draft", attachments=None, attempt=1):
        return {
            "assignment_id": 82001, "user_id": 991001,
            "attempt": attempt, "submitted_at": "2026-09-01T10:00:00Z",
            "workflow_state": "submitted", "body": body,
            "submission_type": "online_text_entry", "score": None,
            "grade": None, "attachments": list(attachments or []),
        }

    def file_record(file_id="501", payload=b"Original upload"):
        return {"id": file_id, "filename": "student-original.txt",
                "url": f"https://canvas.example.edu/files/{file_id}?download_secret=private",
                "size": len(payload), "content_type": "text/plain"}

    return {"Response": Response, "row": row, "file_record": file_record}
