"""Disposable safe SQLite projection of validated immutable evidence snapshots."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .cooperative import NoSlice
from .evidence_schema import digest_record, validate_commit, validate_fact

INDEX_SCHEMA_VERSION = 2
MAX_PAGE_SIZE = 100


@contextmanager
def _write_connection(path):
    db = sqlite3.connect(path, timeout=2)
    try:
        with db:
            yield db
    finally:
        db.close()
# This registry defines direct-read columns and generates the reader contract.
VIEW_COLUMNS = {
    "courses": ("source_key", "course_id", "title", "selection_status"),
    "roster": ("source_key", "course_id", "pseudonym", "entity_key", "fact_ref", "payload"),
    "sections": ("source_key", "course_id", "section_id", "name", "entity_key", "fact_ref", "payload"),
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

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class IndexBusy(IndexReadError):
    """The local index is temporarily held by another process."""

    def __init__(self):
        super().__init__("index_busy")


class EvidenceIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._discard_allowed = False
        self._discard_signature = None

    def _file_signature(self):
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _mark_discardable(self):
        self._discard_allowed = True
        self._discard_signature = self._file_signature()

    def ingest(self, snapshot, *, selected_courses=(), pacer=None) -> str:
        """Replace the projection atomically; unchanged snapshots cost no rewrite.

        Callers supply EvidenceStore.scan() output, never raw Canvas records.
        Selection is explicit application metadata, independent of store presence.
        """
        from .evidence_store import StoreSnapshot, validate_reference_graph, validate_store_issue
        if not isinstance(snapshot, StoreSnapshot) or not callable(getattr(snapshot, "verify_safe", None)):
            raise ValueError("validated_snapshot_required")
        pacer = pacer or NoSlice()
        issues = tuple(validate_store_issue(issue) for issue in snapshot.issues)
        facts = {}
        for ref, fact in snapshot.facts.items():
            pacer.checkpoint()
            validated = validate_fact(fact)
            if digest_record(validated) != ref:
                raise ValueError("fact_digest_mismatch")
            self._verify(snapshot.verify_safe, validated)
            facts[ref] = validated
        commits = {}
        for ref, commit in snapshot.commits.items():
            pacer.checkpoint()
            validated = validate_commit(commit)
            if digest_record(validated) != ref:
                raise ValueError("commit_digest_mismatch")
            self._verify(snapshot.verify_safe, validated)
            commits[ref] = validated
        for commit in commits.values():
            pacer.checkpoint()
            validate_reference_graph(commit, facts, commits)
        snapshot = StoreSnapshot(facts=facts, commits=commits, issues=issues, revision=snapshot.revision, verify_safe=snapshot.verify_safe)
        selected = frozenset(str(value) for value in selected_courses)
        revision = hashlib.sha256(json.dumps([INDEX_SCHEMA_VERSION, snapshot.revision, sorted(facts), sorted(commits), sorted(json.dumps(asdict(issue), sort_keys=True) for issue in issues), sorted(selected)], separators=(",", ":")).encode()).hexdigest()
        if self.path.exists():
            with self.read_connection():
                pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _write_connection(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=2000")
            self._initialize(db)
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
            if old and old[0] == revision:
                return revision
            for table in ("current_refs", "history_refs", "scope_coverage", "safe_facts", "course_selection", "attachment_block_rows", "comparison_rows"):
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
            self._project_derived(db, facts, snapshot, revision)
        self._discard_allowed = False
        self._discard_signature = None
        return revision

    def ingest_many(self, snapshots, *, selected_courses=(), pacer=None) -> str:
        """Build one complete source projection from all safely scanned courses."""
        from .evidence_store import StoreSnapshot
        snapshots = tuple(snapshots)
        if not snapshots or any(not isinstance(item, StoreSnapshot) for item in snapshots):
            raise ValueError("validated_snapshot_required")
        facts, commits, issues, verifiers = {}, {}, [], {}
        for snapshot in snapshots:
            issues.extend(snapshot.issues)
            for ref, fact in snapshot.facts.items():
                if ref in facts and facts[ref] != fact:
                    raise ValueError("fact_digest_collision")
                facts[ref] = fact
            for ref, commit in snapshot.commits.items():
                if ref in commits and commits[ref] != commit:
                    raise ValueError("commit_digest_collision")
                commits[ref] = commit
            for (source, course) in {(f["source_key"], f["course_id"]) for f in snapshot.facts.values()} | {(c["source_key"], c["course_id"]) for c in snapshot.commits.values()}:
                verifiers[(source, course)] = snapshot.verify_safe
        if not verifiers:
            raise ValueError("empty_source_snapshot")

        def verify(record):
            verifier = verifiers.get((record["source_key"], record["course_id"]))
            if not callable(verifier):
                raise ValueError("privacy_refusal")
            return verifier(record)

        aggregate_revision = hashlib.sha256(json.dumps(sorted(s.revision for s in snapshots), separators=(",", ":")).encode()).hexdigest()
        aggregate = StoreSnapshot(facts, commits, tuple(issues), aggregate_revision, verify)
        return self.ingest(aggregate, selected_courses=selected_courses, pacer=pacer)

    @staticmethod
    def _project_derived(db, facts, snapshot, revision):
        """Materialize extracted blocks and comparison evidence from safe facts."""
        for name in ("courses", "roster", "sections", "assignment_context", "current_submissions", "attempt_history",
                     "attachment_associations", "attachment_extractions", "scope_status",
                     "attachment_blocks", "comparison_evidence", "agent_notes", "group_context",
                     "module_context", "page_context", "assignment_group_context"):
            db.execute(f"DROP VIEW IF EXISTS {name}")
        db.execute("CREATE TABLE IF NOT EXISTS attachment_block_rows(source_key TEXT,course_id TEXT,assignment_id TEXT,fact_ref TEXT,payload TEXT,PRIMARY KEY(source_key,course_id,assignment_id,fact_ref))")
        db.execute("CREATE TABLE IF NOT EXISTS comparison_rows(source_key TEXT,course_id TEXT,assignment_id TEXT,fact_ref TEXT,payload TEXT,PRIMARY KEY(source_key,course_id,assignment_id,fact_ref))")
        statements = """
        CREATE VIEW courses AS SELECT s.source_key,s.course_id,(SELECT json_extract(f.payload,'$.title') FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='course' AND f.source_key=s.source_key AND f.course_id=s.course_id ORDER BY fact_ref LIMIT 1) title,s.selection_status FROM course_selection s;
        CREATE VIEW roster AS SELECT DISTINCT f.source_key,f.course_id,f.pseudonym,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='student';
        CREATE VIEW sections AS SELECT DISTINCT f.source_key,f.course_id,json_extract(f.payload,'$.section_id') section_id,json_extract(f.payload,'$.name') name,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='section';
        CREATE VIEW assignment_context AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='assignment';
        CREATE VIEW current_submissions AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='submission';
        CREATE VIEW attempt_history AS SELECT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,f.submitted_at,r.established_submitted_at,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN history_refs r USING(fact_ref) WHERE f.kind='attempt_observation';
        CREATE VIEW attachment_associations AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,json_extract(f.payload,'$.attachment_key') attachment_key,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='attachment';
        CREATE VIEW attachment_extractions AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,f.pseudonym,f.attempt,json_extract(f.payload,'$.attachment_key') attachment_key,json_extract(f.payload,'$.original_digest') original_digest,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='attachment_extraction';
        CREATE VIEW scope_status AS SELECT * FROM scope_coverage;
        CREATE VIEW attachment_blocks AS SELECT source_key,course_id,assignment_id,fact_ref,payload FROM attachment_block_rows;
        CREATE VIEW comparison_evidence AS SELECT source_key,course_id,assignment_id,fact_ref,payload FROM comparison_rows;
        CREATE VIEW agent_notes AS SELECT DISTINCT f.source_key,f.course_id,f.assignment_id,json_extract(f.payload,'$.note_id') note_id,json_extract(f.payload,'$.revision') revision,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='note';
        """
        for statement in statements.split(";\n"):
            if statement.strip():
                db.execute(statement.strip())
        for kind in ("group", "module", "page", "assignment_group"):
            if kind == "group":
                db.execute("CREATE VIEW group_context AS SELECT DISTINCT f.source_key,f.course_id,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind IN ('group','group_category')")
                continue
            db.execute(f"CREATE VIEW {kind}_context AS SELECT DISTINCT f.source_key,f.course_id,f.entity_key,f.fact_ref,f.payload FROM safe_facts f JOIN current_refs r USING(fact_ref) WHERE f.kind='{kind}'")
        blocks_by_assignment = {}
        active_extractions = {ref for (source, course, scope, _scope_id), state in snapshot.scopes.items()
                              if scope == "assignment.extractions" for ref in state.current_refs}
        active_attachments = {ref for (source, course, scope, _scope_id), state in snapshot.scopes.items()
                              if scope == "assignment.attachments" for ref in state.current_refs}
        active_submissions = {ref for (source, course, scope, _scope_id), state in snapshot.scopes.items()
                              if scope == "assignment.submissions" for ref in state.current_refs}
        history_attempts = {ref for (source, course, scope, _scope_id), state in snapshot.scopes.items()
                            if scope == "assignment.submissions" for ref in state.history_refs}
        for ref, fact in facts.items():
            if fact["kind"] != "attachment_extraction" or ref not in active_extractions:
                continue
            p = fact["payload"]
            base = {k: p.get(k) for k in ("assignment_id", "pseudonym", "attempt", "attachment_key", "original_digest", "availability", "method")}
            for ordinal, block in enumerate(p.get("blocks") or []):
                payload = {**base, "block_index": ordinal, "source_fact_ref": ref, **block}
                block_ref = hashlib.sha256(f"{ref}:{ordinal}".encode("ascii")).hexdigest()
                db.execute("INSERT INTO attachment_block_rows VALUES (?,?,?,?,?)", (fact["source_key"], fact["course_id"], p["assignment_id"], block_ref, json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))))
                blocks_by_assignment.setdefault((fact["source_key"], fact["course_id"], p["assignment_id"]), []).append({**block, "fact_ref": ref, **base})
        from .evidence_comparisons import project_comparison_evidence
        assignment_facts = {(f["source_key"], f["course_id"], f["payload"]["assignment_id"]): f["payload"] for f in facts.values() if f["kind"] == "assignment"}
        buckets = {}
        for ref, fact in facts.items():
            p = fact["payload"]
            if fact["kind"] not in {"submission", "attempt_observation", "attachment", "attachment_extraction"} or not p.get("assignment_id"):
                continue
            if fact["kind"] == "submission" and ref not in active_submissions:
                continue
            if fact["kind"] == "attempt_observation" and ref not in history_attempts:
                continue
            if fact["kind"] == "attachment" and ref not in active_attachments:
                continue
            if fact["kind"] == "attachment_extraction" and ref not in active_extractions:
                continue
            key = fact["source_key"], fact["course_id"], p["assignment_id"]
            bucket = buckets.setdefault(key, {"submission_rows": [], "attempt_rows": [], "attachment_rows": [], "extraction_block_rows": []})
            row = {**p, "fact_ref": ref, "payload": p}
            bucket[{"submission": "submission_rows", "attempt_observation": "attempt_rows", "attachment": "attachment_rows", "attachment_extraction": "extraction_block_rows"}[fact["kind"]]].append(row)
        for (source, course, assignment), bucket in buckets.items():
            cov = snapshot.scopes.get((source, course, "assignment.submissions", assignment))
            coverage_state = ("complete" if cov and cov.status == "ready"
                              and cov.membership_complete and not cov.pending_commits
                              and not cov.ambiguous_entities else "incomplete" if cov else "unknown")
            derived = project_comparison_evidence(source_key=source, course_id=course, assignment_id=assignment,
                input_revision=revision, coverage_state=coverage_state,
                shared_text=(assignment_facts.get((source, course, assignment), {}).get("description") or ""),
                submission_rows=bucket["submission_rows"], attempt_rows=bucket["attempt_rows"],
                attachment_rows=bucket["attachment_rows"], extraction_block_rows=blocks_by_assignment.get((source, course, assignment), []))
            for row in derived:
                db.execute("INSERT INTO comparison_rows VALUES (?,?,?,?,?)", (source, course, assignment, row["fact_ref"], json.dumps(row["payload"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))))

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
        CREATE TABLE IF NOT EXISTS attachment_block_rows(source_key TEXT,course_id TEXT,assignment_id TEXT,fact_ref TEXT,payload TEXT,PRIMARY KEY(source_key,course_id,assignment_id,fact_ref));
        CREATE TABLE IF NOT EXISTS comparison_rows(source_key TEXT,course_id TEXT,assignment_id TEXT,fact_ref TEXT,payload TEXT,PRIMARY KEY(source_key,course_id,assignment_id,fact_ref));
        """)
        db.execute("INSERT OR IGNORE INTO index_metadata VALUES ('schema_version',?)", (str(INDEX_SCHEMA_VERSION),))
        db.commit()

    @contextmanager
    def read_connection(self):
        """Pin a SQLite read transaction; direct agents use the same URI mode."""
        if not self.path.is_file():
            self._mark_discardable()
            raise IndexReadError("index_missing")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        try:
            db = sqlite3.connect(uri, uri=True, timeout=2)
        except sqlite3.DatabaseError:
            self._mark_discardable()
            raise IndexReadError("index_corrupt") from None
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=2000")
            db.execute("BEGIN")
            try:
                row = db.execute("SELECT value FROM index_metadata WHERE key='schema_version'").fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table: index_metadata" in str(exc).lower():
                    self._mark_discardable()
                    raise IndexReadError("index_schema_mismatch") from None
                self._mark_discardable()
                raise IndexReadError("index_corrupt") from None
            except sqlite3.DatabaseError:
                self._mark_discardable()
                raise IndexReadError("index_corrupt") from None
            if not row or row[0] != str(INDEX_SCHEMA_VERSION):
                self._mark_discardable()
                raise IndexReadError("index_schema_mismatch")
            self._discard_allowed = False
            self._discard_signature = None
            yield db
        except sqlite3.DatabaseError:
            self._mark_discardable()
            raise IndexReadError("index_corrupt") from None
        finally:
            db.close()

    def discard(self) -> None:
        """Remove an unusable index and SQLite sidecars before a fresh rebuild."""
        if self.path.exists() and (not self._discard_allowed
                                   or self._file_signature() != self._discard_signature):
            raise IndexReadError("index_healthy")
        try:
            self.path.unlink(missing_ok=True)
        except PermissionError:
            raise IndexBusy() from None
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            except PermissionError:
                raise IndexBusy() from None
        self._discard_allowed = False
        self._discard_signature = None

    def query_page(self, view: str, *, limit=100, offset=0, revision=None, source_key=None, course_id=None, assignment_id=None, pseudonym=None, pseudonyms=None, connection=None):
        if view not in VIEW_COLUMNS:
            raise IndexReadError("unknown_view")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or type(offset) is not int or not 0 <= offset <= 1000000:
            raise IndexReadError("invalid_page")
        if assignment_id is not None and "assignment_id" not in VIEW_COLUMNS[view]:
            raise IndexReadError("unsupported_filter")
        if (pseudonym is not None or pseudonyms is not None) and "pseudonym" not in VIEW_COLUMNS[view]:
            raise IndexReadError("unsupported_filter")
        if pseudonyms is not None and (not isinstance(pseudonyms, (list, tuple))
                or any(not isinstance(value, str) or not value for value in pseudonyms)):
            raise IndexReadError("invalid_filter")
        if connection is None:
            with self.read_connection() as db:
                return self.query_page(view, limit=limit, offset=offset, revision=revision, source_key=source_key, course_id=course_id, assignment_id=assignment_id, pseudonym=pseudonym, pseudonyms=pseudonyms, connection=db)
        current = connection.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
        current = current[0] if current else None
        if revision is not None and revision != current:
            raise IndexReadError("revision_changed")
        filters = {k: v for k, v in (("source_key", source_key), ("course_id", course_id), ("assignment_id", assignment_id), ("pseudonym", pseudonym)) if v is not None}
        where = " AND ".join(f"{k}=?" for k in filters) or "1"
        params = list(filters.values())
        if pseudonyms is not None:
            if not pseudonyms:
                where += " AND 0"
            else:
                where += " AND pseudonym IN (" + ",".join("?" for _ in pseudonyms) + ")"
                params.extend(pseudonyms)
        order = ",".join(column for column in ("source_key", "course_id", "assignment_id", "pseudonym", "attempt", "entity_key", "fact_ref", "scope", "scope_id") if column in VIEW_COLUMNS[view])
        rows = connection.execute(f"SELECT * FROM {view} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit + 1, offset)).fetchall()
        return {"revision": current, "records": [dict(row) for row in rows[:limit]], "next_offset": offset + limit if len(rows) > limit else None}
