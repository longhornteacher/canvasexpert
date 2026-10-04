"""Opaque synced requests for bounded read-only acquisition by the owner.

Requests and acknowledgements are immutable files. A machine never rewrites
another machine's request, and duplicate requests coalesce when read.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_NUMBER = re.compile(r"[0-9]+\Z")
_SCOPES = frozenset({"course.refresh", "course_context", "roster", "groups",
                     "course.scoring_refresh", "course.feedback_refresh",
                     "course.scoring_discovery_refresh", "submissions.course_delta",
                     "new_quizzes.metadata", "course.structure_refresh"})


@dataclass(frozen=True)
class FocusedRequest:
    course_id: str
    scope: str
    request_ids: tuple[str, ...]


def _atomic_new(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".request-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        # UUID paths are unique; os.replace gives cloud clients one complete file.
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AcquisitionRequests:
    def __init__(self, workspace_root: str | Path, source_key: str):
        if not isinstance(source_key, str) or not _HEX64.fullmatch(source_key):
            raise ValueError("invalid_source_key")
        self.source_key = source_key
        self.root = Path(workspace_root) / "_System" / "CanvasMirror Control" / "requests" / source_key
        self._cursor = 0

    def is_acknowledged(self, request_id: str) -> bool:
        if not _HEX32.fullmatch(request_id):
            raise ValueError("invalid_request_id")
        for path in (self.root / "acknowledged").glob(f"{request_id}.*.json"):
            try:
                if path.stat().st_size > 4096:
                    continue
                document = json.loads(path.read_text(encoding="utf-8"))
                if (set(document) == {"schema_version", "request_id", "owner_writer_key"}
                        and document["schema_version"] == 1
                        and document["request_id"] == request_id
                        and _HEX32.fullmatch(document["owner_writer_key"])
                        and path.name == f"{request_id}.{document['owner_writer_key']}.json"):
                    return True
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return False

    def submit(self, *, writer_key: str, course_id: str, scope: str) -> str:
        if not _HEX32.fullmatch(writer_key) or not _NUMBER.fullmatch(str(course_id)) or scope not in _SCOPES:
            raise ValueError("invalid_focused_request")
        request_id = uuid4().hex
        _atomic_new(self.root / "pending" / f"{request_id}.json", {
            "schema_version": 1, "request_id": request_id, "writer_key": writer_key,
            "course_id": str(course_id), "scope": scope,
        })
        return request_id

    def pending(self, *, limit: int = 32) -> tuple[FocusedRequest, ...]:
        if not isinstance(limit, int) or not 1 <= limit <= 256:
            raise ValueError("invalid_limit")
        groups: dict[tuple[str, str], list[str]] = {}
        for path in (self.root / "pending").glob("*.json"):
            if not _HEX32.fullmatch(path.stem) or self.is_acknowledged(path.stem):
                continue
            try:
                if path.stat().st_size > 4096:
                    continue
                document = json.loads(path.read_text(encoding="utf-8"))
                if (set(document) != {"schema_version", "request_id", "writer_key", "course_id", "scope"}
                        or document["schema_version"] != 1 or document["request_id"] != path.stem
                        or not _HEX32.fullmatch(document["writer_key"])
                        or not _NUMBER.fullmatch(document["course_id"])
                        or document["scope"] not in _SCOPES):
                    continue
            except (OSError, ValueError, TypeError, KeyError):
                continue
            groups.setdefault((document["course_id"], document["scope"]), []).append(path.stem)
        ordered = sorted(groups.items())
        if not ordered:
            return ()
        start = self._cursor % len(ordered)
        selected = (ordered[start:] + ordered[:start])[:limit]
        self._cursor = (start + len(selected)) % len(ordered)
        return tuple(FocusedRequest(course, scope, tuple(sorted(ids)))
                     for (course, scope), ids in selected)

    def acknowledge(self, request_ids: tuple[str, ...], *, owner_writer_key: str) -> None:
        if not _HEX32.fullmatch(owner_writer_key):
            raise ValueError("invalid_owner_identity")
        for request_id in request_ids:
            if not _HEX32.fullmatch(request_id):
                raise ValueError("invalid_request_id")
            _atomic_new(self.root / "acknowledged" / f"{request_id}.{owner_writer_key}.json", {
                "schema_version": 1, "request_id": request_id, "owner_writer_key": owner_writer_key,
            })
