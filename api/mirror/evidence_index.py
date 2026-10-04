"""Disposable safe SQLite projection of validated immutable evidence snapshots."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .evidence_schema import digest_record, validate_commit, validate_fact

INDEX_SCHEMA_VERSION = 1
MAX_PAGE_SIZE = 100
# This registry defines direct-read columns and generates the reader contract.
VIEW_COLUMNS = {
    "courses": ("source_key", "course_id", "title", "selection_status"),
    "assignment_context": ("source_key", "course_id", "assignment_id", "entity_key", "fact_ref", "payload"),
    "group_context": ("source_key", "course_id", "entity_key", "fact_ref", "payload"),
    "module_context": ("source_key", "course_id", "entity_key", "fact_ref", "payload"),
    "page_context": ("source_key", "course_id", "entity_key", "fact_ref", "payload"),
    "assignment_group_context": ("source_key", "course_id", "entity_key", "fact_ref", "payload"),
    "current_submissions": ("source_key", "course_id", "assignment_id", "pseudonym", "attempt", "entity_key", "fact_ref", "payload"),
    "attempt_history": ("source_key", "course_id", "assignment_id", "pseudonym", "attempt", "submitted_at", "established_submitted_at", "entity_key", "fact_ref", "payload"),
    "attachment_associations": ("source_key", "course_id", "assignment_id", "pseudonym", "attempt", "attachment_key", "entity_key", "fact_ref", "payload"),
    "attachment_extractions": ("source_key", "course_id", "assignment_id", "pseudonym", "attempt", "attachment_key", "original_digest", "entity_key", "fact_ref", "payload"),
    "attachment_blocks": ("source_key", "course_id", "assignment_id", "fact_ref", "payload"),
    "scope_status": ("source_key", "course_id", "scope", "scope_id", "status", "membership_complete", "heads", "pending_commits", "ambiguous_entities", "last_success_at"),
    "comparison_evidence": ("source_key", "course_id", "assignment_id", "fact_ref", "payload"),
    "agent_notes": ("source_key", "course_id", "assignment_id", "note_id", "revision", "fact_ref", "payload"),
}


class IndexReadError(ValueError):
    """A bounded, value-free query refusal."""


class EvidenceIndex:
    def __init__(self, path: Path):
        self.path = Path(path)

    def ingest(self, snapshot, *, selected_courses=()) -> str:
        """Replace the projection atomically; unchanged snapshots cost no rewrite.

        Callers supply EvidenceStore.scan() output, never raw Canvas records.
        Selection is explicit application metadata, independent of store presence.
        """
        from .evidence_store import StoreSnapshot, validate_reference_graph, validate_store_issue
        if not isinstance(snapshot, StoreSnapshot) or not callable(getattr(snapshot, "verify_safe", None)):
            raise ValueError("validated_snapshot_required")
        issues = tuple(validate_store_issue(issue) for issue in snapshot.issues)
        facts = {}
        for ref, fact in snapshot.facts.items():
            validated = validate_fact(fact)
            if digest_record(validated) != ref:
                raise ValueError("fact_digest_mismatch")
            self._verify(snapshot.verify_safe, validated)
            facts[ref] = validated
        commits = {}
        for ref, commit in snapshot.commits.items():
            validated = validate_commit(commit)
            if digest_record(validated) != ref:
                raise ValueError("commit_digest_mismatch")
            self._verify(snapshot.verify_safe, validated)
            commits[ref] = validated
        for commit in commits.values():
            validate_reference_graph(commit, facts, commits)
        snapshot = StoreSnapshot(facts=facts, commits=commits, issues=issues, revision=snapshot.revision, verify_safe=snapshot.verify_safe)
        selected = frozenset(str(value) for value in selected_courses)
        revision = hashlib.sha256(json.dumps([snapshot.revision, sorted(facts), sorted(commits), sorted(json.dumps(asdict(issue), sort_keys=True) for issue in issues), sorted(selected)], separators=(",", ":")).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=2000")
            self._initialize(db)
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
            if old and old[0] == revision:
                return revision
            for table in ("current_refs", "history_refs", "scope_coverage", "safe_facts", "course_selection"):
                db.execute(f"DELETE FROM {table}")
            for ref, fact in sorted(facts.items()):
                p = fact["payload"]
                db.execute("INSERT INTO safe_facts VALUES (?,?,?,?,?,?,?,?,?,?)", (ref, fact["source_key"], fact["course_id"], fact["kind"], fact["entity_key"], p.get("assignment_id"), p.get("pseudonym"), p.get("attempt"), p.get("submitted_at"), json.dumps(p, sort_keys=True, ensure_ascii=False, separators=(",", ":"))))
            courses = {(f["source_key"], f["course_id"]) for f in facts.values()}
            courses.update((c["source_key"], c["course_id"]) for c in snapshot.commits.values())
            for source, course in sorted(courses):
                db.execute("INSERT INTO course_selection VALUES (?,?,?)", (source, course, "selected" if course in selected else "retained"))
            for key, state in sorted(snapshot.scopes.items()):
                source, course, scope, scope_id = key
                finished = [snapshot.commits[h]["acquisition_finished_at"] for h in state.heads if h in snapshot.commits and snapshot.commits[h]["mode"] != "import"]
                db.execute("INSERT INTO scope_coverage VALUES (?,?,?,?,?,?,?,?,?,?)", (*key, state.status, int(state.membership_complete), json.dumps(state.heads), json.dumps(state.pending_commits), json.dumps(state.ambiguous_entities), max(finished, default=None)))
                for ref in state.current_refs:
                    if ref in facts:
                        db.execute("INSERT OR IGNORE INTO current_refs VALUES (?,?,?)", (ref, scope, scope_id))
                for ref in state.history_refs:
                    if ref in facts:
                        stamp = state.established_submitted_at.get(facts[ref]["entity_key"])
                        db.execute("INSERT OR IGNORE INTO history_refs VALUES (?,?)", (ref, stamp))
            db.execute("INSERT OR REPLACE INTO index_metadata VALUES ('revision',?)", (revision,))
        return revision

    @staticmethod
    def _verify(verifier, record):
        try:
            if verifier(deepcopy(record)) is False:
                raise ValueError('privacy_refusal')
        except Exception:
            raise ValueError('privacy_refusal') from None

    @staticmethod
    def _initialize(db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS index_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS safe_facts(fact_ref TEXT PRIMARY KEY,source_key TEXT NOT NULL,course_id TEXT NOT NULL,kind TEXT NOT NULL,entity_key TEXT NOT NULL,assignment_id TEXT,pseudonym TEXT,attempt INTEGER,submitted_at TEXT,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS fact_scope ON safe_facts(source_key,course_id,assignment_id,kind);
        CREATE TABLE IF NOT EXISTS current_refs(fact_ref TEXT REFERENCES safe_facts(fact_ref),scope TEXT,scope_id TEXT,PRIMARY KEY(fact_ref,scope,scope_id));
        CREATE TABLE IF NOT EXISTS history_refs(fact_ref TEXT PRIMARY KEY REFERENCES safe_facts(fact_ref),established_submitted_at TEXT);
        CREATE TABLE IF NOT EXISTS course_selection(source_key TEXT,course_id TEXT,selection_status TEXT,PRIMARY KEY(source_key,course_id));
        CREATE TABLE IF NOT EXISTS scope_coverage(source_key TEXT,course_id TEXT,scope TEXT,scope_id TEXT,status TEXT,membership_complete INTEGER,heads TEXT,pending_commits TEXT,ambiguous_entities TEXT,last_success_at TEXT,PRIMARY KEY(source_key,course_id,scope,scope_id));
        CREATE VIEW IF NOT EXISTS courses AS SELECT s.source_key,s.course_id,(SELECT json_extract(f.payload,'$.title') FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='course' AND f.source_key=s.source_key AND f.course_id=s.course_id ORDER BY fact_ref LIMIT 1) title,s.selection_status FROM course_selection s;
        CREATE VIEW IF NOT EXISTS assignment_context AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='assignment';
        CREATE VIEW IF NOT EXISTS current_submissions AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='submission';
        CREATE VIEW IF NOT EXISTS attempt_history AS SELECT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,f.submitted_at,r.established_submitted_at,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN history_refs r USING(fact_ref) WHERE f.kind='attempt_observation';
        CREATE VIEW IF NOT EXISTS attachment_associations AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,json_extract(f.payload,'$.attachment_key') attachment_key,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='attachment';
        CREATE VIEW IF NOT EXISTS attachment_extractions AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,json_extract(f.payload,'$.attachment_key') attachment_key,json_extract(f.payload,'$.original_digest') original_digest,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='attachment_extraction';
        CREATE VIEW IF NOT EXISTS scope_status AS SELECT * FROM scope_coverage;
        """)
        for kind in ("group", "module", "page", "assignment_group"):
            db.execute(f"CREATE VIEW IF NOT EXISTS {kind}_context AS SELECT DISTINCT f.source_key,f.course_id,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='{kind}'")
        for name in ("attachment_blocks", "comparison_evidence"):
            db.execute(f"CREATE VIEW IF NOT EXISTS {name} AS SELECT source_key,course_id,assignment_id,fact_ref,payload FROM safe_facts WHERE 0")
        db.execute("CREATE VIEW IF NOT EXISTS agent_notes AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,json_extract(f.payload,'$.note_id') note_id,json_extract(f.payload,'$.revision') revision,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='note'")
        db.execute("INSERT OR IGNORE INTO index_metadata VALUES ('schema_version',?)", (str(INDEX_SCHEMA_VERSION),))
        db.commit()

    @contextmanager
    def read_connection(self):
        """Pin a SQLite read transaction; direct agents use the same URI mode."""
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=2000")
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    def query_page(self, view: str, *, limit=100, offset=0, revision=None, source_key=None, course_id=None, assignment_id=None, connection=None):
        if view not in VIEW_COLUMNS:
            raise IndexReadError("unknown_view")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or type(offset) is not int or not 0 <= offset <= 1000000:
            raise IndexReadError("invalid_page")
        if assignment_id is not None and "assignment_id" not in VIEW_COLUMNS[view]:
            raise IndexReadError("unsupported_filter")
        if connection is None:
            with self.read_connection() as db:
                return self.query_page(view, limit=limit, offset=offset, revision=revision, source_key=source_key, course_id=course_id, assignment_id=assignment_id, connection=db)
        current = connection.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
        current = current[0] if current else None
        if revision is not None and revision != current:
            raise IndexReadError("revision_changed")
        filters = {k: v for k, v in (("source_key", source_key), ("course_id", course_id), ("assignment_id", assignment_id)) if v is not None}
        where = " AND ".join(f"{k}=?" for k in filters) or "1"
        order = ",".join(column for column in ("source_key", "course_id", "assignment_id", "pseudonym", "attempt", "entity_key", "fact_ref", "scope", "scope_id") if column in VIEW_COLUMNS[view])
        rows = connection.execute(f"SELECT * FROM {view} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*filters.values(), limit + 1, offset)).fetchall()
        return {"revision": current, "records": [dict(row) for row in rows[:limit]], "next_offset": offset + limit if len(rows) > limit else None}









