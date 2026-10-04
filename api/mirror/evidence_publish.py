"""Private-to-safe publication for one complete text assignment receipt.

The receipt is acquired elsewhere. This module does no Canvas I/O and writes no
unprocessed student value into the synchronized evidence root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from api import feedback_scrub
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_queries import publish_reader_contract
from api.mirror.evidence_schema import canonical_bytes, validate_fact
from api.mirror.evidence_store import EvidenceStore
from api.mirror.evidence_schema import EvidenceValidationError
from api.platform_services import workspace


_TEXT_KEYS = frozenset({"title", "description", "body", "text"})
_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_PRIVATE_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home)/)[^\s<>]+")
_NAVIGATION_KEYS = frozenset({
    "source_key", "course_id", "assignment_id", "section_id", "group_id",
    "comment_id", "override_id", "schema_version", "acquisition_started_at",
    "acquisition_finished_at", "submitted_at", "updated_at", "created_at",
    "due_at", "unlock_at", "lock_at", "record_refs", "parents", "watermarks",
})


class _VisibleHtml(HTMLParser):
    """Preserve visible text and line breaks, never HTML attributes or scripts."""

    _BLOCKS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        elif not self.ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        elif not self.ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def _visible_text(value: str) -> str:
    parser = _VisibleHtml()
    parser.feed(value)
    return "".join(parser.parts)


def _private_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(parsed.username or parsed.password or parsed.query or
                    "/files/" in parsed.path.lower() or
                    "download" in parsed.path.lower())
    except ValueError:
        return True


class PublicationRefused(ValueError):
    """A value-free refusal suitable for logs or agent status."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TextPublication:
    fact_refs: tuple[str, ...]
    commit_refs: tuple[str, ...]
    gaps: tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_stamp(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        raise PublicationRefused("invalid_timestamp") from None


def _timestamp_or_gap(value, gaps: list[str]) -> str | None:
    try:
        return _utc_stamp(value)
    except PublicationRefused:
        gaps.append("invalid_timestamp")
        return None


class EvidencePublisher:
    def __init__(self, *, workspace_root: str | Path, source_key: str,
                 course_id: int | str, vault):
        self.workspace_root = Path(workspace_root)
        self.source_key = source_key
        self.course_id = str(course_id)
        if not self.course_id.isdecimal():
            raise PublicationRefused("invalid_course")
        self.vault = vault
        self._replacement_map = feedback_scrub.build_replacement_map(vault.entries(), set())
        safe_root = workspace.canvas_mirror_evidence_root(self.workspace_root)
        private = local_source_root(source_key, self.workspace_root) / "staging" / "diagnostics"
        self.store = EvidenceStore(safe_root, source_key, self.course_id,
                                   verify_safe=self.verify_safe,
                                   private_diagnostics_root=private)

    def _scrub_text(self, value: str | None, *, html: bool = False) -> str:
        source = _visible_text(value or "") if html else unescape(value or "")
        text = feedback_scrub.scrub_text(source, self._replacement_map)
        # A source document may contain an ordinary cited URL. Remove only
        # transport links and query credentials; keep useful visible prose.
        def clean_url(match):
            url = match.group(0)
            if _private_url(url):
                return "[link removed]"
            return url.split("#", 1)[0]
        text = _URL.sub(clean_url, text)
        return _PRIVATE_PATH.sub("[path removed]", text)

    def _scrub_payload(self, value, *, html_fields=frozenset(), depth=0):
        if isinstance(value, dict):
            return {key: self._scrub_text(item, html=depth == 0 and key in html_fields)
                    if key in _TEXT_KEYS and isinstance(item, str)
                    else self._scrub_payload(item, html_fields=html_fields, depth=depth + 1)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._scrub_payload(item, html_fields=html_fields, depth=depth + 1)
                    for item in value]
        return value

    def verify_safe(self, record: dict) -> None:
        """Recheck synced records before local indexing as well as publication."""
        if record.get("source_key") != self.source_key or record.get("course_id") != self.course_id:
            raise PublicationRefused("scope_mismatch")
        if "kind" in record:
            kind = record["kind"]
            payload = record.get("payload", {})
            pseudonym = payload.get("pseudonym")
            assignment_id = payload.get("assignment_id")
            entity_key = record.get("entity_key")
            if kind == "student" and entity_key != f"student:{pseudonym}":
                raise PublicationRefused("identity_refused")
            if kind == "submission" and entity_key != f"submission:{assignment_id}:{pseudonym}":
                raise PublicationRefused("identity_refused")
            if kind == "attempt_observation":
                attempt = payload.get("attempt")
                expected = f"attempt:{assignment_id}:{pseudonym}:{attempt}"
                unresolved = f"attempt_unresolved:{assignment_id}:{pseudonym}:"
                if (attempt is not None and entity_key != expected) or (
                        attempt is None and not str(entity_key).startswith(unresolved)):
                    raise PublicationRefused("identity_refused")
        names, ids = self.vault.all_real_identifiers()
        stable_pseudonyms = {
            str(entry.get("pseudonym")) for entry in self.vault.entries()
            if entry.get("pseudonym") and not entry.get("provisional")
        }

        def check(value, key=""):
            if isinstance(value, dict):
                for child_key, child in value.items():
                    check(child, child_key)
            elif isinstance(value, list):
                for child in value:
                    check(child, key)
            elif isinstance(value, str):
                if key in {"course_id", "assignment_id", "section_id", "group_id",
                           "comment_id", "override_id"} and not value.isdecimal():
                    raise PublicationRefused("invalid_navigation_id")
                if key in {"pseudonym", "author_pseudonym", "student_pseudonyms"}:
                    if value not in stable_pseudonyms:
                        raise PublicationRefused("identity_refused")
                if key not in _NAVIGATION_KEYS:
                    if feedback_scrub.find_token_matches(value, names):
                        raise PublicationRefused("privacy_refused")
                    if any(re.search(rf"\b{re.escape(identifier)}\b", value)
                           for identifier in ids if len(identifier) >= 3):
                        raise PublicationRefused("privacy_refused")
                if _PRIVATE_PATH.search(value) or any(
                        _private_url(match.group(0)) for match in _URL.finditer(value)):
                    raise PublicationRefused("private_reference")
        check(record)

    def _fact(self, kind: str, entity_key: str, payload: dict,
              *, html_fields=frozenset()) -> tuple[dict, str]:
        record = validate_fact({
            "schema_version": 1, "kind": kind, "source_key": self.source_key,
            "course_id": self.course_id, "entity_key": entity_key,
            "payload": self._scrub_payload(payload, html_fields=html_fields),
        })
        self.verify_safe(record)
        return record, self.store.publish_fact(record)

    def _pseudo(self, raw_user_id, real_name: str = "") -> str:
        if raw_user_id in (None, ""):
            raise PublicationRefused("identity_unresolved")
        try:
            pseudonym = self.vault.get_or_assign(raw_user_id, real_name=real_name)
            self.vault.require_stable(raw_user_id)
        except Exception:
            raise PublicationRefused("identity_unresolved") from None
        if not pseudonym or not isinstance(pseudonym, str):
            raise PublicationRefused("identity_unresolved")
        return pseudonym

    def _commit(self, *, scope: str, scope_id: str, refs: list[str], members: list[str],
                complete: bool, writer_key: str, run_id: str, acquired_at: str,
                gaps: list[str]) -> str:
        snapshot = self.store.scan()
        key = (self.source_key, self.course_id, scope, scope_id)
        state = snapshot.scopes.get(key)
        record = {
            "schema_version": 1, "source_key": self.source_key, "course_id": self.course_id,
            "scope": scope, "scope_id": scope_id, "writer_key": writer_key,
            "run_id": run_id, "parents": list(state.heads if state else ()),
            "acquisition_started_at": acquired_at,
            "acquisition_finished_at": acquired_at,
            "mode": "snapshot", "membership_complete": complete and not gaps,
            "record_refs": sorted(set(refs)), "member_keys": sorted(set(members)),
            "gaps": [{"code": code} for code in sorted(set(gaps))], "watermarks": {},
        }
        return self.store.publish_commit(record)

    def publish_text_assignment(self, *, course_title: str, assignment: dict,
                                roster: list[dict], submissions: list[dict],
                                roster_complete: bool, submissions_complete: bool,
                                writer_key: str,
                                acquired_at: str | None = None,
                                run_id: str | None = None) -> TextPublication:
        """Publish one in-memory existing-acquisition receipt, including attempts.

        Each completeness flag must come from its own pagination-bearing
        acquisition receipt. An empty list without that receipt proves no
        absence outside the exact scope.
        """
        acquired_at = _utc_stamp(acquired_at) or _utc_now()
        run_id = run_id or uuid4().hex
        assignment_id = str(assignment.get("id") or assignment.get("assignment_id") or "")
        if not assignment_id.isdecimal():
            raise PublicationRefused("invalid_assignment")
        gaps = []
        rubric = []
        for criterion in assignment.get("rubric") or []:
            if not isinstance(criterion, dict):
                gaps.append("invalid_rubric")
                continue
            rubric.append({
                "criterion_id": str(criterion.get("criterion_id") or criterion.get("id") or ""),
                "description": str(criterion.get("description") or ""),
                "points": criterion.get("points"),
                "ratings": [
                    {"rating_id": str(rating.get("rating_id") or rating.get("id") or ""),
                     "description": str(rating.get("description") or ""),
                     "points": rating.get("points")}
                    for rating in (criterion.get("ratings") or []) if isinstance(rating, dict)
                ],
            })
        planned: list[tuple[str, str, dict, frozenset[str]]] = [
            ("course", f"course:{self.course_id}", {"title": course_title}, frozenset()),
            ("assignment", f"assignment:{assignment_id}", {
                "assignment_id": assignment_id,
                "title": str(assignment.get("title") or assignment.get("name") or ""),
                "description": str(assignment.get("description") or ""),
                "points_possible": assignment.get("points_possible"),
                "due_at": _timestamp_or_gap(assignment.get("due_at"), gaps),
                "updated_at": _timestamp_or_gap(assignment.get("updated_at"), gaps),
                "rubric": rubric,
            }, frozenset({"description"})),
        ]
        identity = {}
        for row in roster:
            try:
                pseudo = self._pseudo(row.get("id") or row.get("user_id"),
                                      str(row.get("name") or ""))
            except PublicationRefused:
                gaps.append("identity_unresolved")
                continue
            identity[str(row.get("id") or row.get("user_id"))] = pseudo
            key = f"student:{pseudo}"
            planned.append(("student", key, {"pseudonym": pseudo, "section_ids": []}, frozenset()))
        for row in submissions:
            raw_id = str(row.get("user_id") or "")
            pseudo = identity.get(raw_id)
            if pseudo is None:
                gaps.append("identity_unresolved")
                continue
            key = f"submission:{assignment_id}:{pseudo}"
            body = str(row.get("body") or "")
            current = {
                "assignment_id": assignment_id, "pseudonym": pseudo,
                "attempt": row.get("attempt"),
                "submitted_at": _timestamp_or_gap(row.get("submitted_at"), gaps),
                "body": body, "score": row.get("score"), "grade": row.get("grade"),
                "late": bool(row.get("late")), "missing": bool(row.get("missing")),
                "workflow_state": str(row.get("workflow_state") or ""),
                "updated_at": _timestamp_or_gap(row.get("updated_at"), gaps),
            }
            current_html = frozenset({"body"}) if row.get("submission_type") == "online_text_entry" else frozenset()
            planned.append(("submission", key, current, current_html))
            for observed in [row, *(row.get("submission_history") or [])]:
                if not isinstance(observed, dict):
                    gaps.append("invalid_attempt")
                    continue
                attempt = observed.get("attempt")
                evidence = {
                    "assignment_id": assignment_id, "pseudonym": pseudo,
                    "attempt": attempt,
                    "submitted_at": _timestamp_or_gap(observed.get("submitted_at"), gaps),
                    "body": str(observed.get("body") or ""),
                    "score": observed.get("score"), "grade": observed.get("grade"),
                    "late": bool(observed.get("late")),
                    "missing": bool(observed.get("missing")),
                    "workflow_state": str(observed.get("workflow_state") or ""),
                    "updated_at": _timestamp_or_gap(observed.get("updated_at"), gaps),
                }
                if attempt is None:
                    suffix = hashlib.sha256(canonical_bytes(evidence)).hexdigest()[:16]
                    attempt_key = f"attempt_unresolved:{assignment_id}:{pseudo}:{suffix}"
                else:
                    attempt_key = f"attempt:{assignment_id}:{pseudo}:{attempt}"
                observed_html = frozenset({"body"}) if observed.get(
                    "submission_type", row.get("submission_type")) == "online_text_entry" else frozenset()
                planned.append(("attempt_observation", attempt_key, evidence, observed_html))
        if hasattr(self.vault, "save"):
            self.vault.save()
        self._replacement_map = feedback_scrub.build_replacement_map(self.vault.entries(), set())
        publish_reader_contract(workspace.canvas_mirror_evidence_root(self.workspace_root))
        published: dict[str, list[tuple[str, str]]] = {"course": [], "assignment": [],
                                                       "student": [], "submission": [],
                                                       "attempt_observation": []}
        for kind, entity_key, payload, html_fields in planned:
            try:
                _, digest = self._fact(kind, entity_key, payload,
                                      html_fields=html_fields)
                published[kind].append((entity_key, digest))
            except (EvidenceValidationError, PublicationRefused):
                gaps.append("publication_refused")
        # A rejected row cannot prove complete membership. Retain all
        # publishable siblings and make the scope gap explicit.
        commits = [
            self._commit(scope="course.context", scope_id=self.course_id,
                         refs=[ref for _, ref in published["course"]],
                         members=[key for key, _ in published["course"]],
                         complete=True, writer_key=writer_key, run_id=run_id,
                         acquired_at=acquired_at, gaps=gaps),
            self._commit(scope="course.assignments", scope_id=self.course_id,
                         refs=[ref for _, ref in published["assignment"]],
                         members=[key for key, _ in published["assignment"]],
                         complete=False, writer_key=writer_key, run_id=run_id,
                         acquired_at=acquired_at, gaps=[]),
            self._commit(scope="course.roster", scope_id=self.course_id,
                         refs=[ref for _, ref in published["student"]],
                         members=[key for key, _ in published["student"]],
                         complete=roster_complete, writer_key=writer_key, run_id=run_id,
                         acquired_at=acquired_at, gaps=gaps),
            self._commit(scope="assignment.submissions", scope_id=assignment_id,
                         refs=[ref for _, ref in published["submission"] + published["attempt_observation"]],
                         members=[key for key, _ in published["submission"]],
                         complete=submissions_complete,
                         writer_key=writer_key, run_id=run_id,
                         acquired_at=acquired_at, gaps=gaps),
        ]
        return TextPublication(tuple(sorted({ref for rows in published.values() for _, ref in rows})),
                               tuple(commits), tuple(sorted(set(gaps))))
