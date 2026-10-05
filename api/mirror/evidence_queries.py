"""Host-neutral direct reads and the generated safe reader contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

from api.mirror.evidence_index import EvidenceIndex, IndexReadError, INDEX_SCHEMA_VERSION, VIEW_COLUMNS


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


def write_reader_descriptor(index_path: Path, *, revision: str) -> Path:
    """Atomically write local read guidance bound to one committed index revision."""
    index_path = Path(index_path)
    target = index_path.parent / "reader.json"
    payload = {
        "descriptor_version": READER_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "index_revision": revision,
        "index_file": "query.sqlite3",
        "views": {name: list(columns) for name, columns in sorted(VIEW_COLUMNS.items())},
        "read_mode": "SQLite URI mode=ro with PRAGMA query_only=ON; no immutable=1",
        "revision_check": "SELECT value FROM index_metadata WHERE key='revision'",
        "sql_example": SQL_EXAMPLE,
        "privacy": "Pseudonymized and scrubbed, not anonymous. Originals and identity mappings are outside this root.",
    }
    raw = (json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")) + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".reader-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


class EvidenceQueryService:
    """Semantic read envelope over the same named views exposed to direct SQL."""

    def __init__(self, index_path: Path):
        self.index = EvidenceIndex(index_path)

    def read_attempt_attachments(self, *, source_key: str, course_id: str,
                                 assignment_id: str, attempts: list[tuple[str, int]],
                                 revision: str, max_files: int = 200) -> dict:
        """Read bounded captured metadata for exact historical attempt keys."""
        if (not isinstance(attempts, list) or len(attempts) > 100
                or any(not isinstance(person, str) or not person
                       or type(number) is not int or number < 1
                       for person, number in attempts)
                or type(max_files) is not int or not 1 <= max_files <= 200):
            raise IndexReadError("invalid_filter")
        with self.index.read_connection() as db:
            row = db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
            if not row or row[0] != revision:
                raise IndexReadError("revision_changed")
            if not attempts:
                return {"records": [], "truncated": False}
            pairs = sorted(set(attempts))
            conditions = " OR ".join("(f.pseudonym=? AND f.attempt=?)" for _ in pairs)
            sql = ("SELECT f.pseudonym,f.attempt,f.entity_key,f.fact_ref,f.payload "
                   "FROM safe_facts f JOIN history_refs h USING(fact_ref) "
                   "WHERE f.kind='attachment' AND f.source_key=? AND f.course_id=? "
                   "AND f.assignment_id=? AND json_extract(f.payload,'$.status')='captured' "
                   f"AND ({conditions}) "
                   "ORDER BY f.pseudonym,f.attempt,f.entity_key,f.fact_ref LIMIT ?")
            params = [source_key, course_id, assignment_id]
            for person, number in pairs:
                params.extend((person, number))
            rows = db.execute(sql, (*params, max_files + 1)).fetchall()
            records = []
            for item in rows[:max_files]:
                payload = json.loads(item["payload"])
                records.append({"pseudonym": item["pseudonym"], "attempt": item["attempt"],
                                "attachment_key": payload["attachment_key"],
                                "original_digest": payload.get("original_digest"),
                                "media_type": payload.get("media_type"),
                                "size": payload.get("size"), "status": "captured"})
            return {"records": records, "truncated": len(rows) > max_files}

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
        if view == "attachments":
            return self._read_attachment_page(source_key=source_key, course_id=course_id,
                assignment_id=assignment_id, limit=limit, offset=offset, revision=revision)
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

    def _read_attachment_page(self, *, source_key, course_id, assignment_id,
                              limit, offset, revision):
        """Page the three safe attachment views as one stable bounded result."""
        with self.index.read_connection() as db:
            current = db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
            current = current[0] if current else None
            if revision is not None and revision != current:
                raise IndexReadError("revision_changed")
            sql = """SELECT * FROM (
                SELECT 'attachment_associations' AS index_view,source_key,course_id,assignment_id,fact_ref,payload FROM attachment_associations
                UNION ALL SELECT 'attachment_extractions',source_key,course_id,assignment_id,fact_ref,payload FROM attachment_extractions
                UNION ALL SELECT 'attachment_blocks',source_key,course_id,assignment_id,fact_ref,payload FROM attachment_blocks
                ) WHERE source_key=? AND course_id=? AND assignment_id=?
                ORDER BY fact_ref,index_view,payload LIMIT ? OFFSET ?"""
            rows = db.execute(sql, (source_key, course_id, assignment_id, limit + 1, offset)).fetchall()
            coverage_rows = db.execute(
                "SELECT scope,status,membership_complete,pending_commits,ambiguous_entities,last_success_at "
                "FROM scope_status WHERE source_key=? AND course_id=? AND scope_id=? "
                "AND scope IN ('assignment.submissions','assignment.attachments','assignment.extractions')",
                (source_key, course_id, assignment_id)).fetchall()
            association_rows = db.execute(
                "SELECT payload FROM attachment_associations WHERE source_key=? AND course_id=? AND assignment_id=?",
                (source_key, course_id, assignment_id)).fetchall()
            extraction_rows = db.execute(
                "SELECT payload FROM attachment_extractions WHERE source_key=? AND course_id=? AND assignment_id=?",
                (source_key, course_id, assignment_id)).fetchall()
            association_statuses = [json.loads(row[0]).get("status") for row in association_rows]
            extraction_availability = [json.loads(row[0]).get("availability") for row in extraction_rows]
            attachment_summary = {
                "associations": len(association_rows),
                "captured": sum(status == "captured" for status in association_statuses),
                "pending": sum(status == "pending" for status in association_statuses),
                "gaps": sum(status in {"too_large", "unavailable", "foreign_origin"} for status in association_statuses),
                "extracted": len(extraction_rows),
                "extraction_gaps": sum(state in {"partial", "unavailable"} for state in extraction_availability),
            }
            records = []
            for row in rows[:limit]:
                records.append({"source_key": row["source_key"], "course_id": row["course_id"],
                    "assignment_id": row["assignment_id"], "fact_ref": row["fact_ref"],
                    "index_view": row["index_view"], "payload": json.loads(row["payload"])})
            statuses = {row["scope"]: row for row in coverage_rows}
            submission = statuses.get("assignment.submissions")
            stamps = [row["last_success_at"] for row in coverage_rows if row["last_success_at"]]
            observed_at = max(stamps, default=None)
            age_seconds = None
            if observed_at:
                stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                age_seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
            complete = all(statuses.get(scope) is not None
                           and statuses[scope]["status"] == "ready"
                           and statuses[scope]["membership_complete"]
                           and not json.loads(statuses[scope]["pending_commits"])
                           and not json.loads(statuses[scope]["ambiguous_entities"])
                           for scope in ("assignment.attachments", "assignment.extractions"))
            return {"view": "attachments", "revision": current, "records": records,
                "next_offset": offset + limit if len(rows) > limit else None,
                "freshness": {"last_success_at": observed_at, "age_seconds": age_seconds,
                              "age_refuses_read": False},
                "membership": {"state": ("complete" if submission["membership_complete"] else "incomplete")
                               if submission else "unknown"},
                "evidence": {"state": "available_attachment_evidence" if records else
                             "none" if complete else "incomplete"},
                "synchronization": {"state": "incomplete" if not complete else "current",
                    "pending_commits": [digest for row in coverage_rows
                                        for digest in json.loads(row["pending_commits"])],
                    "ambiguous_entities": [entity for row in coverage_rows
                                           for entity in json.loads(row["ambiguous_entities"])]},
                "acquisition": {"state": "reported" if coverage_rows else "unknown"},
                "attachment_summary": attachment_summary}

    def read(self, view: str, *, source_key: str, course_id: str,
             assignment_id: str | None = None, limit: int = 50,
             offset: int = 0, revision: str | None = None,
             pseudonym: str | None = None,
             pseudonyms: list[str] | tuple[str, ...] | None = None) -> dict:
        with self.index.read_connection() as db:
            page = self.index.query_page(
                view, source_key=source_key, course_id=course_id,
                assignment_id=assignment_id, limit=limit, offset=offset,
                pseudonym=pseudonym, pseudonyms=pseudonyms, revision=revision, connection=db,
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
            elif view == "comparison_evidence" and assignment_id:
                scope = ("assignment.submissions", assignment_id)
            elif view == "courses":
                scope = ("course.context", course_id)
            elif view == "roster":
                scope = ("course.roster", course_id)
            elif view == "sections":
                scope = ("course.sections", course_id)
            elif view == "group_context":
                scope = ("course.groups", course_id)
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
                "evidence": {"state": ("available_comparison_evidence" if page["records"] else
                    "none" if coverage and coverage["status"] == "ready"
                    and coverage["membership_complete"] and not json.loads(coverage["pending_commits"])
                    and not json.loads(coverage["ambiguous_entities"]) else "incomplete")
                    if view == "comparison_evidence" else
                    "available_text" if page["records"] and view in {"current_submissions", "attempt_history"} else "unknown"},
                "synchronization": {
                    "state": coverage["status"] if coverage else "unknown",
                    "pending_commits": json.loads(coverage["pending_commits"]) if coverage else [],
                    "ambiguous_entities": json.loads(coverage["ambiguous_entities"]) if coverage else [],
                },
                "acquisition": {"state": "not_reported"},
            }
