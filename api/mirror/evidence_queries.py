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

    def read_scoring_discovery(self, *, source_key: str, course_ids) -> dict:
        """Student-free grading counters for the selected courses, in one transaction.

        Local application read (not an MCP tool): the revision, scope coverage and
        every counter come from one pinned SQLite snapshot, so a concurrent index
        rebuild can never mix two revisions. Pseudonyms stay inside this method.
        Counting follows ``gradebook_snapshot.needs_grading``; a counter is ``None``
        when no eligible observation exists, never a fabricated zero.
        """
        from api.gradebook_snapshot import needs_grading

        course_ids = [str(value) for value in dict.fromkeys(course_ids or ()) if str(value)]
        if not course_ids:
            return {"revision": None, "courses": []}  # never "all courses"
        marks = ",".join("?" for _ in course_ids)
        params = (source_key, *course_ids)
        where = f"source_key=? AND course_id IN ({marks})"
        with self.index.read_connection() as db:
            row = db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
            revision = row[0] if row else None
            coverage_rows = db.execute(
                "SELECT course_id,scope,scope_id,status,membership_complete,pending_commits,"
                f"ambiguous_entities,last_success_at FROM scope_status WHERE {where}", params).fetchall()
            titles = {r["course_id"]: (r["title"], r["selection_status"]) for r in db.execute(
                f"SELECT course_id,title,selection_status FROM courses WHERE {where}", params)}
            roster = {}
            for r in db.execute(f"SELECT course_id,pseudonym FROM roster WHERE {where}", params):
                roster.setdefault(r["course_id"], set()).add(r["pseudonym"])
            assignments = db.execute(
                "SELECT course_id,assignment_id,fact_ref,"
                "json_extract(payload,'$.title') title,json_extract(payload,'$.due_at') due_at,"
                "json_extract(payload,'$.points_possible') points,"
                f"json_extract(payload,'$.published') published FROM assignment_context WHERE {where} "
                "ORDER BY course_id,assignment_id,fact_ref", params).fetchall()
            submissions = db.execute(
                "SELECT course_id,assignment_id,pseudonym,entity_key,"
                "json_extract(payload,'$.submitted_at') submitted_at,"
                "json_extract(payload,'$.workflow_state') workflow_state,"
                "json_extract(payload,'$.excused') excused,json_extract(payload,'$.score') score,"
                f"json_extract(payload,'$.late') late FROM current_submissions WHERE {where}",
                params).fetchall()

        scopes = {}
        for r in coverage_rows:
            complete = (r["status"] == "ready" and bool(r["membership_complete"])
                        and not json.loads(r["pending_commits"] or "[]")
                        and not json.loads(r["ambiguous_entities"] or "[]"))
            scopes[(r["course_id"], r["scope"], r["scope_id"])] = {
                "coverage": "complete" if complete else "incomplete",
                "observed_at": r["last_success_at"], "status": r["status"],
                "ambiguous": set(json.loads(r["ambiguous_entities"] or "[]")),
            }
        unknown = {"coverage": "unknown", "observed_at": None, "status": None, "ambiguous": set()}
        by_assignment = {}
        for r in submissions:
            by_assignment.setdefault((r["course_id"], r["assignment_id"]), []).append(r)
        assignment_rows = {}
        for r in assignments:
            assignment_rows.setdefault(r["course_id"], {}).setdefault(r["assignment_id"], r)

        courses = []
        for course_id in course_ids:
            roster_scope = scopes.get((course_id, "course.roster", course_id), unknown)
            assignment_scope = scopes.get((course_id, "course.assignments", course_id), unknown)
            known = roster.get(course_id, set())
            items = []
            for assignment_id, a in sorted(assignment_rows.get(course_id, {}).items()):
                if a["published"] is not None and not a["published"]:
                    continue  # clearly unpublished work is never gradable
                sub_scope = scopes.get((course_id, "assignment.submissions", assignment_id), unknown)
                rows = by_assignment.get((course_id, assignment_id), [])
                per_entity = {}
                for s in rows:
                    per_entity.setdefault(s["entity_key"], []).append(s)
                ambiguous = set(sub_scope["ambiguous"]) | {
                    key for key, group in per_entity.items() if len(group) > 1}
                observable = (sub_scope["coverage"] != "unknown"
                              and roster_scope["coverage"] != "unknown")
                counters = {"ungraded": None, "partially_scored": None, "late_ungraded": None}
                if observable:
                    counters = {"ungraded": 0, "partially_scored": 0, "late_ungraded": 0}
                    for key, group in per_entity.items():
                        s = group[0]
                        if key in ambiguous or s["pseudonym"] not in known:
                            continue
                        if not needs_grading({"excused": s["excused"], "submitted_at": s["submitted_at"],
                                              "workflow_state": s["workflow_state"]}):
                            continue
                        counters["ungraded"] += 1
                        counters["late_ungraded"] += 1 if s["late"] else 0
                        counters["partially_scored"] += 1 if s["score"] is not None else 0
                complete = (observable and sub_scope["coverage"] == "complete"
                            and roster_scope["coverage"] == "complete" and not ambiguous)
                items.append({
                    "assignment_id": assignment_id, "name": a["title"] or "",
                    "due_at": a["due_at"], "points": a["points"],
                    "published": a["published"] is not None,
                    "coverage": "complete" if complete else "unknown" if not observable else "incomplete",
                    "counts_complete": bool(complete),
                    "submissions_observed_at": sub_scope["observed_at"],
                    "ambiguous_entities": len(ambiguous), **counters,
                })
            title, selection = titles.get(course_id, (None, None))
            courses.append({
                "course_id": course_id, "title": title, "selection_status": selection,
                "revision": revision,
                "roster": {"coverage": roster_scope["coverage"], "observed_at": roster_scope["observed_at"],
                           "known_members": len(known)},
                "assignments_scope": {"coverage": assignment_scope["coverage"],
                                      "observed_at": assignment_scope["observed_at"],
                                      "status": assignment_scope["status"]},
                "assignments": items,
            })
        return {"revision": revision, "courses": courses}
