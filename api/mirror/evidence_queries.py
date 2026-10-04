"""Host-neutral direct reads and the generated safe reader contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

from api.mirror.evidence_index import EvidenceIndex, IndexReadError, VIEW_COLUMNS


READER_SCHEMA_VERSION = 1
SQL_EXAMPLE = (
    "SELECT assignment_id, pseudonym, attempt, submitted_at, payload "
    "FROM attempt_history WHERE source_key = ? AND course_id = ? "
    "AND assignment_id = ? ORDER BY pseudonym, attempt, fact_ref LIMIT 50"
)


def reader_contract() -> dict:
    """One generated definition for direct readers and future MCP guidance."""
    return {
        "schema_version": READER_SCHEMA_VERSION,
        "description": "Pseudonymized CanvasMirror evidence; read-only local SQLite projection",
        "views": {name: list(columns) for name, columns in sorted(VIEW_COLUMNS.items())},
        "read_mode": "SQLite URI mode=ro with PRAGMA query_only=ON; no immutable=1",
        "sql_example": SQL_EXAMPLE,
        "privacy": "Pseudonymized and scrubbed, not anonymous. Originals and identity mappings are outside this root.",
    }


def reader_contract_bytes() -> bytes:
    return (json.dumps(reader_contract(), sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def publish_reader_contract(safe_root: Path) -> Path:
    """Publish exact versioned bytes once; never overwrite an incompatible peer."""
    root = Path(safe_root)
    payload = reader_contract_bytes()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "reader.v1.json"
    descriptor, temporary = tempfile.mkstemp(prefix=".reader-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise IndexReadError("reader_contract_conflict") from None
    finally:
        os.unlink(temporary)
    return target


class EvidenceQueryService:
    """Semantic read envelope over the same named views exposed to direct SQL."""

    def __init__(self, index_path: Path):
        self.index = EvidenceIndex(index_path)

    # Assignment-evidence views map a public view name to its index view.
    ASSIGNMENT_VIEWS = {
        "attachments": ("attachment_associations", "attachment_extractions"),
        "comparisons": ("comparison_evidence",),
        "notes": ("agent_notes",),
    }

    def read_assignment_evidence(self, view: str, *, source_key: str, course_id: str,
                                 assignment_id: str, limit: int = 50,
                                 offset: int = 0, revision: str | None = None) -> dict:
        """Read one assignment-evidence view; validate the view, never ignore it."""
        if view not in self.ASSIGNMENT_VIEWS:
            raise IndexReadError("unknown_view")
        index_views = self.ASSIGNMENT_VIEWS[view]
        pages = []
        for index_view in index_views:
            pages.append(self.read(index_view, source_key=source_key, course_id=course_id,
                                   assignment_id=assignment_id, limit=limit, offset=offset,
                                   revision=revision))
        records = [record for page in pages for record in page["records"]]
        return {
            "view": view, "revision": pages[0]["revision"] if pages else None,
            "records": records,
            "next_offset": next((page["next_offset"] for page in pages
                                 if page["next_offset"] is not None), None),
            "freshness": pages[0]["freshness"] if pages else {},
            "membership": pages[0]["membership"] if pages else {},
            "evidence": pages[0]["evidence"] if pages else {},
            "synchronization": pages[0]["synchronization"] if pages else {},
            "acquisition": pages[0]["acquisition"] if pages else {},
        }

    def read(self, view: str, *, source_key: str, course_id: str,
             assignment_id: str | None = None, limit: int = 50,
             offset: int = 0, revision: str | None = None) -> dict:
        with self.index.read_connection() as db:
            page = self.index.query_page(
                view, source_key=source_key, course_id=course_id,
                assignment_id=assignment_id, limit=limit, offset=offset,
                revision=revision, connection=db,
            )
            for row in page["records"]:
                if "payload" in row and row["payload"] is not None:
                    row["payload"] = json.loads(row["payload"])
            if view in {"current_submissions", "attempt_history"} and assignment_id:
                scope = ("assignment.submissions", assignment_id)
            elif view == "attachment_associations" and assignment_id:
                scope = ("assignment.attachments", assignment_id)
            elif view == "attachment_extractions" and assignment_id:
                scope = ("assignment.extractions", assignment_id)
            elif view == "agent_notes" and assignment_id:
                scope = ("assignment.notes", assignment_id)
            elif view == "courses":
                scope = ("course.context", course_id)
            else:
                scope = ("course.assignments", course_id)
            coverage = db.execute(
                "SELECT status,membership_complete,pending_commits,ambiguous_entities,last_success_at "
                "FROM scope_status WHERE source_key=? AND course_id=? AND scope=? AND scope_id=?",
                (source_key, course_id, *scope),
            ).fetchone()
            observed_at = coverage["last_success_at"] if coverage else None
            age_seconds = None
            if observed_at:
                stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                age_seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
            return {
                "view": view, "revision": page["revision"],
                "records": page["records"], "next_offset": page["next_offset"],
                "freshness": {"last_success_at": observed_at, "age_seconds": age_seconds,
                              "age_refuses_read": False},
                "membership": {"state": ("complete" if coverage["membership_complete"]
                                         else "incomplete") if coverage else "unknown"},
                "evidence": {"state": "available_text" if page["records"] and view in {
                    "current_submissions", "attempt_history"} else "unknown"},
                "synchronization": {
                    "state": coverage["status"] if coverage else "unknown",
                    "pending_commits": json.loads(coverage["pending_commits"]) if coverage else [],
                    "ambiguous_entities": json.loads(coverage["ambiguous_entities"]) if coverage else [],
                },
                "acquisition": {"state": "not_reported"},
            }
