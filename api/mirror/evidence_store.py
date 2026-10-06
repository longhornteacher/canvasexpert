"""Immutable safe publication and a small causal scope reducer.

The caller supplies the privacy verifier. No store path opens the Identity Vault,
downloads Canvas data, or assumes that a structural allowlist scrubs free text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from api.platform_services import workspace
from api.mirror.evidence_schema import (
    EvidenceValidationError, SCOPE_KINDS, canonical_bytes, digest_record,
    validate_commit, validate_component, validate_digest, validate_fact,
)


@dataclass(frozen=True)
class StoreIssue:
    code: str
    digest: str | None = None
    scope: str | None = None
    scope_id: str | None = None
    source_key: str | None = None
    course_id: str | None = None


def validate_store_issue(issue: StoreIssue) -> StoreIssue:
    if not isinstance(issue, StoreIssue) or not isinstance(issue.code, str) or issue.code not in {"invalid_fact", "invalid_commit", "unsupported_schema", "invalid_reference_graph", "diagnostics_failed"}:
        raise EvidenceValidationError("invalid_store_issue")
    if issue.digest is not None:
        validate_digest(issue.digest)
    metadata = (issue.scope, issue.scope_id, issue.source_key, issue.course_id)
    if any(value is not None for value in metadata):
        if not isinstance(issue.scope, str) or issue.scope not in SCOPE_KINDS:
            raise EvidenceValidationError("invalid_store_issue")
        validate_component(issue.scope_id)
        validate_digest(issue.source_key)
        validate_component(issue.course_id)
    return issue


@dataclass(frozen=True)
class ScopeState:
    status: str
    current_refs: tuple[str, ...] = ()
    member_keys: tuple[str, ...] = ()
    history_refs: tuple[str, ...] = ()
    heads: tuple[str, ...] = ()
    last_good_refs: tuple[str, ...] = ()
    pending_commits: tuple[str, ...] = ()
    ambiguous_entities: tuple[str, ...] = ()
    tombstones: tuple[str, ...] = ()
    membership_complete: bool = False
    observation_discrepancies: dict[str, tuple[str, ...]] = field(default_factory=dict)
    established_submitted_at: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class StoreSnapshot:
    facts: dict[str, dict]
    commits: dict[str, dict]
    issues: tuple[StoreIssue, ...]
    revision: str
    verify_safe: Callable[[dict], None] | None = field(default=None, repr=False, compare=False)

    @property
    def scopes(self) -> dict[tuple[str, str, str, str], ScopeState]:
        keys = {(c["source_key"], c["course_id"], c["scope"], c["scope_id"]) for c in self.commits.values()}
        for issue in self.issues:
            validate_store_issue(issue)
            if issue.scope is not None:
                keys.add((issue.source_key, issue.course_id, issue.scope, issue.scope_id))
        return {key: reduce_scope(self, key[2], key[3], source_key=key[0], course_id=key[1]) for key in sorted(keys)}


def _scope_key(commit):
    return tuple(commit[key] for key in ("source_key", "course_id", "scope", "scope_id"))


def validate_reference_graph(commit: dict, facts: dict[str, dict], commits: dict[str, dict]) -> None:
    """Reject cross-scope ancestry and references before exposing a commit."""
    seen = {}
    for ref in commit["record_refs"]:
        fact = facts.get(ref)
        if fact is None:
            continue  # arrival gaps are pending, not corruption
        if (fact["source_key"], fact["course_id"]) != (commit["source_key"], commit["course_id"]):
            raise EvidenceValidationError("reference_scope_mismatch")
        if fact["kind"] not in SCOPE_KINDS[commit["scope"]]:
            raise EvidenceValidationError("reference_kind_mismatch")
        assignment_id = fact["payload"].get("assignment_id")
        if commit["scope"].startswith("assignment.") and assignment_id != commit["scope_id"]:
            raise EvidenceValidationError("reference_scope_mismatch")
        if fact["kind"] != "attempt_observation":
            key = fact["entity_key"]
            if key in seen and seen[key] != ref:
                raise EvidenceValidationError("conflicting_commit_entity")
            seen[key] = ref
    for parent in commit["parents"]:
        if parent in commits and _scope_key(commits[parent]) != _scope_key(commit):
            raise EvidenceValidationError("parent_scope_mismatch")
    if all(ref in facts for ref in commit["record_refs"]):
        if not set(commit["member_keys"]).issubset(seen):
            raise EvidenceValidationError("missing_member_fact")
        if commit["membership_complete"] and set(commit["member_keys"]) != set(seen):
            raise EvidenceValidationError("membership_mismatch")


_CONTEXT_TOKEN = object()


class PublicationContext:
    """Validated course state for exactly one publication receipt.

    Only ``EvidenceStore.begin_publication`` can create one: it performs the
    single whole-course scan, so every fact and commit held here passed schema,
    privacy and digest checks. Records are added only by the store, after their
    own validation and a successful immutable publish. Callers cannot inject
    records or mark anything validated. A context is single-threaded and is
    never reused across receipts; a stale context only risks a safe refusal or
    a causal sibling branch, never an unchecked write.
    """

    def __init__(self, token, store: "EvidenceStore", base: StoreSnapshot, scan_seconds: float):
        if token is not _CONTEXT_TOKEN:
            raise TypeError("publication_context_private")
        self._store = store
        self._base = base
        self._facts = dict(base.facts)
        self._commits = dict(base.commits)
        self._by_scope = defaultdict(dict)
        for digest, commit in base.commits.items():
            self._by_scope[_scope_key(commit)][digest] = commit
        self._states: dict[tuple, ScopeState | None] = {}
        self._published: dict[tuple[str, str], int] = {}
        self._rechecked: set[tuple[str, str]] = set()
        self._stats = {"scans": 1, "scan_seconds": scan_seconds,
                       "base_facts": len(base.facts), "base_commits": len(base.commits),
                       "base_issues": len(base.issues), "facts_published": 0,
                       "commits_published": 0, "dependency_checks": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def initial_state(self, scope_key: tuple[str, str, str, str]) -> ScopeState | None:
        """Reduced state of one scope as of the initial scan (memoized)."""
        if scope_key not in self._states:
            commits = self._by_scope.get(scope_key)
            if not commits:
                self._states[scope_key] = None
            else:
                scoped = StoreSnapshot(self._base.facts, commits, self._base.issues,
                                       self._base.revision)
                self._states[scope_key] = reduce_scope(
                    scoped, scope_key[2], scope_key[3],
                    source_key=scope_key[0], course_id=scope_key[1])
        return self._states[scope_key]

    def initial_heads(self, scope_key: tuple[str, str, str, str]) -> list[str]:
        state = self.initial_state(scope_key)
        return list(state.heads) if state else []

    def fact(self, digest: str) -> dict | None:
        """A validated fact (initial scan or published here); treat as read-only."""
        return self._facts.get(digest)

    def _record(self, namespace: str, digest: str, checked: dict, size: int) -> None:
        checked = deepcopy(checked)  # the caller's dict can never alter validated state
        self._published[(namespace, digest)] = size
        if namespace == "objects":
            self._facts[digest] = checked
            self._stats["facts_published"] += 1
        else:
            self._commits[digest] = checked
            self._stats["commits_published"] += 1

    def _verify_on_disk(self, store: "EvidenceStore", namespace: str, digest: str) -> bool:
        """Targeted recheck of one dependency; counted, never a course scan."""
        marker = (namespace, digest)
        if marker in self._rechecked:
            return True
        self._stats["dependency_checks"] += 1
        path = store._path(namespace, digest)
        size = self._published.get(marker)
        try:
            if size is not None:
                ok = path.stat().st_size == size
            else:
                candidates = [path] if path.exists() else sorted(path.parent.glob(f"{digest}*.json"))
                ok = any(hashlib.sha256(candidate.read_bytes()).hexdigest() == digest
                         for candidate in candidates)
        except OSError:
            ok = False
        if ok:
            self._rechecked.add(marker)
        return ok


class EvidenceStore:
    def __init__(self, safe_root: Path, source_key: str, course_id: str, *, verify_safe: Callable[[dict], None], private_diagnostics_root: Path):
        if not callable(verify_safe):
            raise TypeError("privacy_verifier_required")
        self.safe_root = Path(safe_root).resolve()
        self.private_diagnostics_root = Path(private_diagnostics_root).resolve()
        if self.private_diagnostics_root.is_relative_to(self.safe_root) or self.safe_root.is_relative_to(self.private_diagnostics_root):
            raise EvidenceValidationError("diagnostics_root_overlap")
        self.source_key = validate_digest(source_key)
        self.course_id = validate_component(course_id)
        self.verify_safe = verify_safe
        self.course_root = self.safe_root / "sources" / source_key / "courses" / course_id
        self._contained(self.course_root)

    def _contained(self, path):
        try:
            Path(path).resolve().relative_to(self.safe_root)
        except ValueError:
            raise EvidenceValidationError("path_escape") from None
        return Path(path)

    def _validate(self, record, *, commit=False):
        checked = validate_commit(record) if commit else validate_fact(record)
        if checked["source_key"] != self.source_key or checked["course_id"] != self.course_id:
            raise EvidenceValidationError("record_scope_mismatch")
        # A verifier cannot mutate the validated record into a different payload.
        try:
            result = self.verify_safe(deepcopy(checked))
            if result is False:
                raise EvidenceValidationError("privacy_refused")
        except Exception:
            raise EvidenceValidationError("privacy_refused") from None
        return checked

    def _path(self, namespace, digest):
        return self._contained(self.course_root / namespace / digest[:2] / f"{digest}.json")

    def _publish(self, namespace, checked):
        return self._publish_sized(namespace, checked)[0]

    def _publish_sized(self, namespace, checked):
        payload = canonical_bytes(checked)
        digest = hashlib.sha256(payload).hexdigest()
        target = self._path(namespace, digest)
        _publish_exclusive(target, payload)
        return digest, len(payload)

    def _require_context(self, context):
        if context is not None and (not isinstance(context, PublicationContext) or context._store is not self):
            raise EvidenceValidationError("publication_context_mismatch")

    def begin_publication(self) -> PublicationContext:
        """One validated whole-course scan establishing a receipt's context."""
        started = time.monotonic()
        snapshot = self.scan()
        return PublicationContext(_CONTEXT_TOKEN, self, snapshot, time.monotonic() - started)

    def _diagnose(self, raw):
        digest = hashlib.sha256(raw).hexdigest()
        target = self.private_diagnostics_root / digest[:2] / f"{digest}.bin"
        if not target.resolve().is_relative_to(self.private_diagnostics_root):
            raise EvidenceValidationError("diagnostics_path_escape")
        _publish_exclusive(target, raw)

    def publish_fact(self, record: dict, *, context: PublicationContext | None = None) -> str:
        self._require_context(context)
        checked = self._validate(record)
        digest, size = self._publish_sized("objects", checked)
        if context is not None:
            context._record("objects", digest, checked, size)
        return digest

    def publish_commit(self, record: dict, *, context: PublicationContext | None = None) -> str:
        """Publish one commit after dependency and graph checks.

        Without a context this performs its own whole-course scan (standalone
        publishers). With a context from ``begin_publication`` it checks against
        that validated state plus records the context itself published, and
        rechecks each direct dependency on disk (targeted, counted) rather than
        rescanning the course. A commit that arrived by sync after the initial
        scan is not a parent here; the reducer treats that as a causal sibling.
        """
        self._require_context(context)
        checked = self._validate(record, commit=True)
        if context is None:
            snapshot = self.scan()
            facts, commits = snapshot.facts, snapshot.commits
            pending_source = lambda: reduce_scope(  # noqa: E731 - only this commit's scope can hold its parents
                snapshot, checked["scope"], checked["scope_id"],
                source_key=checked["source_key"], course_id=checked["course_id"]).pending_commits
        else:
            facts, commits = context._facts, context._commits
            pending_source = lambda: (context.initial_state(_scope_key(checked)) or ScopeState("unavailable")).pending_commits  # noqa: E731
        validate_reference_graph(checked, facts, commits)
        if any(ref not in facts for ref in checked["record_refs"]) or any(ref not in commits for ref in checked["parents"]):
            raise EvidenceValidationError("publication_dependencies_missing")
        if context is not None:
            for ref in checked["record_refs"]:
                if not context._verify_on_disk(self, "objects", ref):
                    raise EvidenceValidationError("publication_dependencies_missing")
            for parent in checked["parents"]:
                if not context._verify_on_disk(self, "commits", parent):
                    raise EvidenceValidationError("publication_dependencies_missing")
        if checked["parents"]:
            pending = set(pending_source())
            if any(parent in pending for parent in checked["parents"]):
                raise EvidenceValidationError("publication_dependencies_missing")
        digest, size = self._publish_sized("commits", checked)
        if context is not None:
            context._record("commits", digest, checked, size)
        return digest

    def scan(self) -> StoreSnapshot:
        """Verify synced files, preserving refused bytes in private diagnostics."""
        facts, commits = {}, {}
        issues = []
        for namespace, destination, is_commit in (("objects", facts, False), ("commits", commits, True)):
            base = self._contained(self.course_root / namespace)
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.json")):
                expected_match = re.match(r"^([0-9a-f]{64})(?:$|[^0-9a-f])", path.stem)
                expected = expected_match.group(1) if expected_match else None
                issue_scope = issue_scope_id = None
                issue_source = issue_course = None
                raw = None
                try:
                    self._contained(path)
                    raw = path.read_bytes()
                    record = json.loads(raw, object_pairs_hook=_unique_pairs)
                    if is_commit:
                        structured = validate_commit(record)
                        if (structured["source_key"], structured["course_id"]) == (self.source_key, self.course_id):
                            issue_scope, issue_scope_id = structured["scope"], structured["scope_id"]
                            issue_source, issue_course = self.source_key, self.course_id
                    checked = self._validate(record, commit=is_commit)
                    canonical = canonical_bytes(checked)
                    digest = hashlib.sha256(canonical).hexdigest()
                    if raw != canonical:
                        raise EvidenceValidationError("noncanonical_object")
                    if expected and digest != expected:
                        raise EvidenceValidationError("digest_mismatch")
                    destination[digest] = checked
                except (OSError, UnicodeError, json.JSONDecodeError, EvidenceValidationError, TypeError, RecursionError) as exc:
                    unsupported_schema = isinstance(exc, EvidenceValidationError) and exc.code == "unsupported_schema"
                    if unsupported_schema and is_commit and isinstance(record, dict):
                        raw_scope = record.get("scope")
                        raw_scope_id = record.get("scope_id")
                        if isinstance(raw_scope, str) and raw_scope in SCOPE_KINDS:
                            try:
                                validate_component(raw_scope_id)
                                issue_scope, issue_scope_id = raw_scope, raw_scope_id
                                issue_source, issue_course = self.source_key, self.course_id
                            except EvidenceValidationError:
                                pass
                    issues.append(StoreIssue(("unsupported_schema" if unsupported_schema else "invalid_commit" if is_commit else "invalid_fact"), expected, issue_scope, issue_scope_id, issue_source, issue_course))
                    if raw is not None and not unsupported_schema:
                        try:
                            self._diagnose(raw)
                        except (OSError, EvidenceValidationError):
                            issues.append(StoreIssue("diagnostics_failed", expected, issue_scope, issue_scope_id, issue_source, issue_course))
        # A missing dependency retains the commit so the reducer reports pending.
        # An invalid reference graph is refused, even when every file is valid JSON.
        for digest, commit in list(commits.items()):
            try:
                validate_reference_graph(commit, facts, commits)
            except EvidenceValidationError:
                issues.append(StoreIssue("invalid_reference_graph", digest, commit["scope"], commit["scope_id"], self.source_key, self.course_id))
                try:
                    self._diagnose(canonical_bytes(commit))
                except (OSError, EvidenceValidationError):
                    issues.append(StoreIssue("diagnostics_failed", digest, commit["scope"], commit["scope_id"], self.source_key, self.course_id))
                del commits[digest]
        issue_tuple = tuple(sorted(set(issues), key=lambda issue: (issue.code, issue.digest or "", issue.scope or "", issue.scope_id or "", issue.source_key or "", issue.course_id or "")))
        revision = digest_record({"facts": sorted(facts), "commits": sorted(commits), "issues": [{"code": i.code, "digest": i.digest, "scope": i.scope, "scope_id": i.scope_id, "source_key": i.source_key, "course_id": i.course_id} for i in issue_tuple]})
        return StoreSnapshot(facts, commits, issue_tuple, revision, self.verify_safe)


def _publish_exclusive(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive hard-link publication shares the existing fsync/atomic pattern
    # without replacing a concurrently published immutable object.
    descriptor, temporary = tempfile.mkstemp(prefix=".evidence-", suffix=".tmp", dir=workspace.extended_path(str(target.parent)))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, workspace.extended_path(str(target)))
        except FileExistsError:
            if target.read_bytes() != payload:
                raise EvidenceValidationError("immutable_corruption") from None
    finally:
        os.unlink(temporary)


def _unique_pairs(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise EvidenceValidationError("duplicate_json_key")
        record[key] = value
    return record


def reduce_scope(snapshot: StoreSnapshot, scope: str, scope_id: str, *, source_key: str | None = None, course_id: str | None = None) -> ScopeState:
    commits = {digest: c for digest, c in snapshot.commits.items() if c["scope"] == scope and c["scope_id"] == scope_id and (source_key is None or c["source_key"] == source_key) and (course_id is None or c["course_id"] == course_id)}
    identities = {(c["source_key"], c["course_id"]) for c in commits.values()}
    if len(identities) > 1:
        raise EvidenceValidationError("scope_identity_required")
    ancestors, children = {}, defaultdict(list)
    remaining = {digest: len(c["parents"]) for digest, c in commits.items()}
    for digest, commit in commits.items():
        for parent in commit["parents"]:
            children[parent].append(digest)
    queue = deque(sorted(d for d, count in remaining.items() if count == 0))
    ordered = []
    while queue:
        digest = queue.popleft()
        commit = commits[digest]
        if any(ref not in snapshot.facts for ref in commit["record_refs"]):
            continue
        ancestors[digest] = {digest}.union(*(ancestors[parent] for parent in commit["parents"]))
        ordered.append(digest)
        for child in sorted(children[digest]):
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    usable = set(ordered)
    pending = tuple(sorted(set(commits) - usable))
    live = {d for d in usable if commits[d]["mode"] != "import"}
    active = live or usable
    heads = tuple(sorted(d for d in active if not any(d != other and d in ancestors[other] for other in active)))
    history = tuple(sorted({ref for d in usable for ref in commits[d]["record_refs"]}))
    models = {}

    for digest in ordered:
        commit = commits[digest]
        result = defaultdict(set)
        complete = False
        for parent in commit["parents"]:
            mapping, parent_complete = models[parent]
            complete = complete or parent_complete
            for key, refs in mapping.items():
                result[key].update(refs)
        if commit["mode"] == "snapshot" and commit["membership_complete"]:
            result = defaultdict(set)
            complete = True
        elif commit["mode"] == "snapshot":
            complete = False
        for ref in commit["record_refs"]:
            fact = snapshot.facts[ref]
            if fact["kind"] != "attempt_observation":
                result[fact["entity_key"]] = {ref}
        models[digest] = (dict(result), complete)

    merged = defaultdict(set)
    complete = bool(heads)
    for head in heads:
        mapping, head_complete = models[head]
        complete = complete and head_complete and commits[head]["mode"] != "import"
        for key, refs in mapping.items():
            merged[key].update(refs)
    # Absence on one complete branch conflicts with presence on another branch.
    for key in list(merged):
        for head in heads:
            mapping, head_complete = models[head]
            if head_complete and key not in mapping:
                merged[key].add(None)
    ambiguous = tuple(sorted(key for key, refs in merged.items() if len(refs) > 1))
    common = set.intersection(*(ancestors[h] for h in heads)) if heads else set()
    common_heads = [d for d in common if not any(d != o and d in ancestors[o] for o in common)]
    last_good = defaultdict(set)
    for digest in common_heads:
        for key, refs in models[digest][0].items():
            last_good[key].update(refs)
    current = []
    members = []
    for key, refs in merged.items():
        chosen = refs if len(refs) == 1 else last_good.get(key, set())
        if len(chosen) == 1 and next(iter(chosen)) is not None:
            current.extend(chosen)
            members.append(key)
    last_refs = tuple(sorted(ref for refs in last_good.values() if len(refs) == 1 for ref in refs))
    all_keys = {snapshot.facts[ref]["entity_key"] for ref in history if snapshot.facts[ref]["kind"] != "attempt_observation"}
    tombstones = tuple(sorted(all_keys - set(merged))) if complete else ()
    observations = defaultdict(set)
    owners = defaultdict(set)
    for digest in usable:
        for ref in commits[digest]["record_refs"]:
            fact = snapshot.facts[ref]
            if fact["kind"] == "attempt_observation":
                observations[fact["entity_key"]].add(ref)
                owners[ref].add(digest)
    established, discrepancies = {}, {}
    concurrent_timestamp_entities = set()
    for key, refs in observations.items():
        timed = {r for r in refs if snapshot.facts[r]["payload"].get("submitted_at") is not None}
        observed_nodes = {d for r in timed for d in owners[r]}
        first_nodes = {d for d in observed_nodes if not (ancestors[d] - {d}) & observed_nodes}
        earliest = {r for r in timed if owners[r] & first_nodes}
        times = {snapshot.facts[r]["payload"]["submitted_at"] for r in earliest}
        established[key] = next(iter(times)) if len(times) == 1 else None
        if len(times) > 1 and len(heads) > 1:
            concurrent_timestamp_entities.add(key)
        if len({snapshot.facts[r]["payload"].get("submitted_at") for r in timed}) > 1:
            discrepancies[key] = tuple(sorted(timed))
    ambiguous = tuple(sorted(set(ambiguous) | concurrent_timestamp_entities))
    issue_affects_scope = any(((i.scope, i.scope_id) == (scope, scope_id) and (source_key is None or i.source_key == source_key) and (course_id is None or i.course_id == course_id)) or i.digest in set(history) | set(commits) for i in snapshot.issues)
    status = "ambiguous" if ambiguous else "sync_pending" if pending or issue_affects_scope else "ready" if heads else "unavailable"
    return ScopeState(status=status, current_refs=tuple(sorted(current)), member_keys=tuple(sorted(members)), history_refs=history, heads=heads, last_good_refs=last_refs, pending_commits=pending, ambiguous_entities=ambiguous, tombstones=tombstones, membership_complete=complete and not ambiguous, observation_discrepancies=discrepancies, established_submitted_at=established)
