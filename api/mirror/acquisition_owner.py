"""Advisory read-acquisition ownership, never a Canvas write or session lease.

Cloud visibility is eventual: overlapping owners are possible. Immutable evidence
publication, rather than this election, makes that overlap safe. All freshness is
measured since locally observed counter progress, never from a writer's clock.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HEARTBEAT_INTERVAL = 30.0
STALE_AFTER = 120.0
_OPAQUE = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {"incarnation", "claim_id", "lineage", "heartbeat_counter", "released", "advertised_commit_refs"}


def _identity(value, pattern=_OPAQUE):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("invalid_owner_identity")
    return value


def _validate(document):
    if not isinstance(document, dict) or set(document) != {"schema_version", "writer_key", "sources"} or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("invalid_presence")
    _identity(document["writer_key"])
    if not isinstance(document["sources"], dict):
        raise ValueError("invalid_presence")
    for source, row in document["sources"].items():
        _identity(source, _DIGEST)
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise ValueError("invalid_presence")
        _identity(row["incarnation"])
        _identity(row["claim_id"])
        for field, pattern in (("lineage", _OPAQUE), ("advertised_commit_refs", _DIGEST)):
            if not isinstance(row[field], list) or len(row[field]) != len(set(row[field])):
                raise ValueError("invalid_presence")
            for value in row[field]:
                _identity(value, pattern)
        if row["claim_id"] in row["lineage"] or type(row["heartbeat_counter"]) is not int or row["heartbeat_counter"] < 0 or type(row["released"]) is not bool:
            raise ValueError("invalid_presence")
    return document


@dataclass(frozen=True)
class OwnerStatus:
    owner_writer_key: str | None
    owner_incarnation: str | None
    owner_claim_id: str | None
    is_owner: bool
    state: str
    competing_claims: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


class AcquisitionOwner:
    """One process's observer/writer for one workspace/source.

    ``tick`` observes before publishing. ``reobserve`` must be called after sleep
    before resuming background work; it starts fresh local observation windows.
    ``is_owner`` is advisory only and cannot authorize mutations.
    """

    def __init__(self, workspace_root: str | Path, source_key: str, writer_key: str,
                 *, incarnation: str | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 stale_after: float = STALE_AFTER):
        self.source_key = _identity(source_key, _DIGEST)
        self.writer_key = _identity(writer_key)
        self.incarnation = _identity(incarnation or uuid.uuid4().hex)
        if not math.isfinite(stale_after) or stale_after <= 0:
            raise ValueError("invalid_stale_after")
        self.presence_directory = Path(workspace_root) / "_System" / "CanvasMirror Control" / "presence"
        self.presence_path = self.presence_directory / f"{writer_key}.json"
        self._clock = monotonic
        self._stale_after = stale_after
        self._seen: dict[str, tuple[dict, float]] = {}
        self._claim_id: str | None = None
        self._released = False
        self._issues: tuple[str, ...] = ()

    def _read(self, path):
        try:
            if path.stat().st_size > 1024 * 1024:
                return None
            document = _validate(json.loads(path.read_text(encoding="utf-8")))
            return document
        except (OSError, ValueError, TypeError):
            return None

    def _scan(self):
        now = self._clock()
        issues = set()
        for path in self.presence_directory.glob("*.json"):
            document = self._read(path)
            if document is None:
                issues.add("invalid_presence")
                continue
            if path.name != f"{document['writer_key']}.json":
                issues.add("presence_conflict")
                continue
            if self.source_key not in document["sources"]:
                continue
            writer = document["writer_key"]
            row = document["sources"][self.source_key]
            previous = self._seen.get(writer)
            if previous:
                old, changed = previous
                # Reject delayed ancestor/incarnation and counter regression.
                if row["claim_id"] in old["lineage"]:
                    continue
                if row["claim_id"] == old["claim_id"]:
                    if row["incarnation"] != old["incarnation"] or row["heartbeat_counter"] < old["heartbeat_counter"]:
                        continue
                    if row["heartbeat_counter"] == old["heartbeat_counter"]:
                        continue
                elif old["claim_id"] not in row["lineage"]:
                    # A writer cannot legitimately replace itself without lineage.
                    continue
            self._seen[writer] = (row, now)
        self._issues = tuple(sorted(issues))
        return now

    def _winner(self, now):
        # First reduce causal supersession, including released descendant claims.
        rows = [(writer, row, changed) for writer, (row, changed) in self._seen.items()]
        ancestors = {claim for _, row, _ in rows for claim in row["lineage"]}
        heads = [(writer, row, changed) for writer, row, changed in rows if row["claim_id"] not in ancestors]
        active = [(writer, row) for writer, row, changed in heads
                  if not row["released"] and now - changed < self._stale_after]
        active.sort(key=lambda pair: (pair[0], pair[1]["claim_id"]))
        return active[0] if active else None, tuple(sorted(row["claim_id"] for _, row in active))

    def _status(self, now):
        winner, claims = self._winner(now)
        if self._issues:
            writer, row = winner if winner else (None, {})
            return OwnerStatus(writer, row.get("incarnation"), row.get("claim_id"), False, "repair_required", claims, self._issues)
        if winner is None:
            return OwnerStatus(None, None, None, False, "released" if self._released else "available", claims)
        writer, row = winner
        mine = writer == self.writer_key and row["incarnation"] == self.incarnation and row["claim_id"] == self._claim_id and not self._released
        return OwnerStatus(writer, row["incarnation"], row["claim_id"], mine, "owner" if mine else "waiting", claims if len(claims) > 1 else ())

    def observe(self) -> OwnerStatus:
        return self._status(self._scan())

    def reobserve(self) -> OwnerStatus:
        # Keep anti-regression memory, but do not count time asleep as observation.
        now = self._clock()
        self._seen = {writer: (row, now) for writer, (row, _) in self._seen.items()}
        return self.observe()

    def _publish(self, row):
        document = self._read(self.presence_path)
        if self.presence_path.exists() and (document is None or document["writer_key"] != self.writer_key):
            raise ValueError("invalid_own_presence")
        document = document or {"schema_version": 1, "writer_key": self.writer_key, "sources": {}}
        document["sources"][self.source_key] = row
        _validate(document)
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.presence_directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.writer_key}.", suffix=".tmp", dir=self.presence_directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.presence_path)
        finally:
            Path(name).unlink(missing_ok=True)
        self._seen[self.writer_key] = (row, self._clock())

    def tick(self, advertised_commit_refs=()) -> OwnerStatus:
        refs = sorted(set(_identity(value, _DIGEST) for value in advertised_commit_refs))
        now = self._scan()
        status = self._status(now)
        if self._released or self._issues or status.owner_writer_key is not None and not status.is_owner:
            return status
        previous = self._seen.get(self.writer_key)
        # Never overwrite a later incarnation's own-file state.
        if previous and self._claim_id in previous[0]["lineage"]:
            return status
        if status.is_owner:
            row = dict(previous[0])
            row["heartbeat_counter"] += 1
        else:
            lineage = sorted({value for row, _ in self._seen.values() for value in [row["claim_id"], *row["lineage"]]})
            self._claim_id = uuid.uuid4().hex
            row = {"incarnation": self.incarnation, "claim_id": self._claim_id,
                   "lineage": lineage, "heartbeat_counter": 1, "released": False,
                   "advertised_commit_refs": []}
        row["advertised_commit_refs"] = refs
        self._publish(row)
        return self._status(self._clock())

    def release(self) -> OwnerStatus:
        self._scan()
        document = self._read(self.presence_path)
        row = document and document["sources"].get(self.source_key)
        if row and row["incarnation"] == self.incarnation and row["claim_id"] == self._claim_id:
            row = dict(row)
            row["released"] = True
            row["heartbeat_counter"] += 1
            self._publish(row)
        self._released = True
        return self.observe()
