from api.mcp_server import tools as mcp_tools
"""Session-wide safety net: no test may touch this machine's real
config.json, profiles.json, or OneDrive-synced workspace.

A test that forgets to isolate these paths itself used to fall through to
the real machine state -- see the self-update brief's disclosed incident,
where an unmocked test wrote a fictional course into the real, live
OneDrive-synced settings.json. This fixture runs before every test in the
suite and points every known real-data path at a fresh, unique temp
location. A test's own explicit monkeypatch.setattr calls still take effect
normally, since they run after this fixture within the same test.
"""
import os
import copy
import fnmatch
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import feedback_vault, grading_policy, pseudonym_secret, runtime_paths
from api.platform_services import workspace
from api.platform_services.config import _io as config_io
from api.storage_support import atomic_write_json, interprocess_lock


class VaultTestDouble(feedback_vault.IdentityVault):
    """File-backed test adapter for the shared identity operations mixin.

    Production opens ``SharedVault`` only. This adapter keeps tests isolated
    from the teacher workspace while preserving the small on-disk behavior
    older unit fixtures need.
    """

    SCHEMA_VERSION = 3

    def __init__(self, path=None):
        self.path = str(path or "vault.json")
        self._by_id = {}
        self._by_pseudo = {}
        self.conflict_files = []
        self._load()

    def _lock_path(self):
        path = Path(self.path)
        return path.with_name(path.name + ".lock")

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            self._by_id, self._by_pseudo = {}, {}
            return
        except json.JSONDecodeError as error:
            raise feedback_vault.VaultSchemaError("Identity Vault document is invalid.") from error
        if (not isinstance(data, dict)
                or data.get("schema_version") != self.SCHEMA_VERSION
                or not isinstance(data.get("by_canvas_id"), dict)):
            raise feedback_vault.VaultSchemaError("Identity Vault document is invalid.")
        self._by_id = data["by_canvas_id"]
        self._by_pseudo = {
            entry["pseudonym"]: str(canvas_id)
            for canvas_id, entry in self._by_id.items()
            if isinstance(entry, dict) and entry.get("pseudonym")
        }
        for entry in self._by_id.values():
            if isinstance(entry, dict) and ("pseudo_first" in entry or "pseudo_last" in entry):
                raise feedback_vault.VaultSchemaError("Identity Vault document uses retired pseudonym fields.")

    def _save_unlocked(self):
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {
            "schema_version": self.SCHEMA_VERSION,
            "by_canvas_id": self._by_id,
            "written_by": "test",
            "written_at": "2026-01-01T00:00:00",
            "entry_count": len(self._by_id),
        })

    def save(self):
        with interprocess_lock(self._lock_path()):
            self._save_unlocked()

    @contextmanager
    def transaction(self):
        with interprocess_lock(self._lock_path()):
            self._load()
            before_id, before_pseudo = copy.deepcopy(self._by_id), dict(self._by_pseudo)
            try:
                yield self
            except Exception:
                self._by_id, self._by_pseudo = before_id, before_pseudo
                raise
            else:
                self._save_unlocked()

    def conflicts(self):
        directory = Path(self.path).parent
        own_name = Path(self.path).name.casefold()
        try:
            return sorted(
                name for name in os.listdir(directory)
                if name.casefold() != own_name
                and fnmatch.fnmatch(name.casefold(), "vault*.json")
            )
        except OSError:
            return []

    def set_pseudonym(self, canvas_id, value):
        """Seed a fixed synthetic pseudonym for a test case."""
        cid = str(canvas_id)
        canonical = feedback_vault._canonical_registry_word(value)
        if canonical is None:
            raise feedback_vault.InvalidPseudonymError("test pseudonym must be a registry word")
        holder = self._by_pseudo.get(canonical)
        if holder is not None and holder != cid:
            raise feedback_vault.PseudonymCollisionError("test pseudonym is already assigned")
        entry = self._by_id.setdefault(cid, {
            "pseudonym": "", "real_name": "", "sis_id": "",
            "nicknames": [], "first_seen": "2026-01-01T00:00:00",
        })
        old = entry.get("pseudonym") or ""
        self._by_pseudo.pop(old, None)
        entry["pseudonym"] = canonical
        self._by_pseudo[canonical] = cid


# Older tests import this storage-shaped double from the production module.
# Keep the alias inside pytest collection only; runtime code has no base Vault.
feedback_vault.Vault = VaultTestDouble


@pytest.fixture(autouse=True)
def _isolate_real_machine_and_workspace_paths(tmp_path, monkeypatch):
    fake_root = tmp_path / "_isolated_runtime"

    monkeypatch.setattr(config_io, "CONFIG_PATH", str(fake_root / "config.json"))
    monkeypatch.setattr(workspace, "CONFIG_PATH", str(fake_root / "config.json"))

    # LOCALAPPDATA must be an explicit fake path, not unset: runtime_paths.local_app_dir()
    # falls back to Path.home() -- the real user profile -- when it's absent.
    monkeypatch.setenv("LOCALAPPDATA", str(fake_root / "LocalAppData"))
    # Forge printable preparation now writes PDFs during build_payload. Keep
    # those outputs in this test's private temp tree even without a workspace.
    monkeypatch.setattr(runtime_paths, "printables_dir", lambda: fake_root / "Printables")
    # SharedVault derives only synthetic identities during tests. Never read
    # or write the developer's machine credential from an automated suite.
    monkeypatch.setattr(pseudonym_secret, "get_secret", lambda: b"t" * 32)
    # OneDrive/OneDriveCommercial are safe to simply unset: workspace.onedrive_root()
    # already treats "unset" as "no workspace", matching how most tests here already
    # model a no-workspace machine.
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)


@pytest.fixture
def grading_policy_files(tmp_path, monkeypatch):
    """Write ``Library/Grading Policy.txt`` and/or ``Library/Calendars/
    Holidays.csv`` into an isolated workspace root (decision 1 /
    docs/contracts/grading-policy-contract.md section 4).

    Replaces the retired ``config.get_grading_policy`` / ``set_grading_policy``
    and ``config.get_no_school_dates`` / ``set_no_school_dates`` settings keys
    that tests used to monkeypatch directly: consumers now read these two
    plain files, so tests write the real files under a real (isolated)
    workspace root instead.
    """
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))

    def policy(floor_percent=30):
        path = os.path.join(workspace.library_root(), grading_policy.POLICY_FILENAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                f"floor_percent: {floor_percent}\n"
            )

    def raw_policy(text):
        path = os.path.join(workspace.library_root(), grading_policy.POLICY_FILENAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def holidays(rows):
        directory = workspace.library_folder(grading_policy.HOLIDAYS_SUBFOLDER)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, grading_policy.HOLIDAYS_FILENAME)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            for row in rows:
                handle.write(",".join(row) + "\n")

    return SimpleNamespace(policy=policy, raw_policy=raw_policy, holidays=holidays)


# ── Attempts grant: a stateful fake Canvas, vault, and step context ──────────

ATTEMPTS_COURSE = "course-1"
ATTEMPTS_ASSIGNMENT = "assignment-1"
ATTEMPTS_QUIZ = "quiz-1"
# Canvas id -> pseudonym. "student-4" is in the roster but not on the assignment.
ATTEMPTS_STUDENTS = {"student-1": "Pikachu", "student-2": "Eevee",
                     "student-3": "Snorlax", "student-4": "Togepi"}
ATTEMPTS_OVERRIDE_ID_BASE = 987650


class FakeAttemptsCanvas:
    """In-memory Canvas for one assignment of a chosen kind.

    It answers the reads and writes the attempts-grant adapter uses and records
    every send. ``fault`` makes the next matching write fail; ``lands=True``
    means the write still took effect (a lost reply).
    """

    def __init__(self, kind="regular"):
        import copy as _copy
        self._copy = _copy
        self.kind = kind
        types = {"regular": ["online_text_entry"], "classic_quiz": ["online_quiz"],
                 "new_quiz": ["external_tool"]}[kind]
        self.assignment = {
            "id": ATTEMPTS_ASSIGNMENT, "name": "Synthetic Essay",
            "submission_types": types, "allowed_attempts": 2,
            "due_at": "2020-01-10T05:00:00Z", "lock_at": "2020-01-11T05:00:00Z",
        }
        if kind == "classic_quiz":
            self.assignment["quiz_id"] = ATTEMPTS_QUIZ
        if kind == "new_quiz":
            self.assignment["is_quiz_lti_assignment"] = True
        self.quiz = {"id": ATTEMPTS_QUIZ, "allowed_attempts": 2,
                     "due_at": "2020-01-10T05:00:00Z", "lock_at": "2020-01-11T05:00:00Z"}
        self.new_quiz = {"quiz_settings": {"multiple_attempts": {
            "multiple_attempts_enabled": True, "attempt_limit": True,
            "max_attempts": 2, "score_to_keep": "latest"}}}
        self.overrides = []
        self.extra = {}
        self.sends = []
        self.faults = []
        self.assignment_params = []
        self.ineligible = set()
        self.accommodation_reply = None

    # -- paths ---------------------------------------------------------------

    @property
    def assignment_path(self):
        return f"/api/v1/courses/{ATTEMPTS_COURSE}/assignments/{ATTEMPTS_ASSIGNMENT}"

    @property
    def quiz_path(self):
        return f"/api/v1/courses/{ATTEMPTS_COURSE}/quizzes/{ATTEMPTS_QUIZ}"

    @property
    def new_quiz_path(self):
        return f"/api/quiz/v1/courses/{ATTEMPTS_COURSE}/quizzes/{ATTEMPTS_ASSIGNMENT}"

    def attempts_total(self):
        """The live attempts total the kind's own setting reports (-1 unlimited)."""
        if self.kind == "regular":
            return self.assignment["allowed_attempts"]
        if self.kind == "classic_quiz":
            return self.quiz["allowed_attempts"]
        settings = self.new_quiz["quiz_settings"]["multiple_attempts"]
        return settings["max_attempts"] if settings.get("attempt_limit") else -1

    def set_unlimited(self):
        self.assignment["allowed_attempts"] = -1
        self.quiz["allowed_attempts"] = -1
        self.new_quiz["quiz_settings"]["multiple_attempts"]["attempt_limit"] = False

    def writes(self, method=None, suffix=""):
        return [item for item in self.sends
                if (method is None or item[0] == method) and item[1].endswith(suffix)]

    def fault(self, method, suffix, error="timed out", *, lands=False, times=1):
        self.faults.append({"method": method, "suffix": suffix, "error": error,
                            "lands": lands, "times": times})

    # -- reads ---------------------------------------------------------------

    def get(self, path, params=None, timeout=20):
        if path == self.assignment_path:
            self.assignment_params.append(params)
            return self._copy.deepcopy(self.assignment), None
        if path == self.quiz_path:
            return self._copy.deepcopy(self.quiz), None
        if path == self.new_quiz_path:
            return self._copy.deepcopy(self.new_quiz), None
        if path.startswith(self.assignment_path + "/overrides/"):
            found = next((row for row in self.overrides
                          if str(row["id"]) == path.rsplit("/", 1)[-1]), None)
            return (self._copy.deepcopy(found), None) if found else (None, "HTTP 404: gone")
        if path.startswith(self.assignment_path + "/submissions/"):
            user_id = path.rsplit("/", 1)[-1]
            return {"user_id": user_id, "extra_attempts": self.extra.get(user_id)}, None
        return None, "HTTP 404: not found"

    def get_all(self, path, params=None, timeout=30):
        if path == self.quiz_path + "/submissions":
            return [{"quiz_submissions": [
                {"user_id": user_id, "extra_attempts": value}
                for user_id, value in sorted(self.extra.items())]}], None
        return None, "HTTP 404: not found"

    def get_all_complete(self, path, params=None, timeout=30):
        if path == self.assignment_path + "/overrides":
            return self._copy.deepcopy(self.overrides), None, True
        return None, "HTTP 404", False

    # -- writes --------------------------------------------------------------

    def send(self, method, path, payload, timeout=30):
        self.sends.append((method, path, self._copy.deepcopy(payload)))
        for fault in self.faults:
            if (fault["times"] > 0 and fault["method"] == method
                    and path.endswith(fault["suffix"])):
                fault["times"] -= 1
                if fault["lands"]:
                    self._apply(method, path, payload)
                return None, fault["error"]
        return self._apply(method, path, payload), None

    def _apply(self, method, path, payload):
        if method == "PUT" and path == self.assignment_path:
            self.assignment.update(payload["assignment"])
        elif method == "PUT" and path == self.quiz_path:
            self.quiz.update(payload["quiz"])
        elif method == "PATCH" and path == self.new_quiz_path:
            self.new_quiz["quiz_settings"]["multiple_attempts"].update(
                payload["quiz_settings"]["multiple_attempts"])
        elif method == "POST" and path == self.assignment_path + "/overrides":
            body = payload["assignment_override"]
            self.overrides.append({
                "id": ATTEMPTS_OVERRIDE_ID_BASE + len(self.overrides) + 1,
                "student_ids": list(body["student_ids"]),
                "due_at": body["due_at"], "lock_at": body["lock_at"]})
            return self._copy.deepcopy(self.overrides[-1])
        elif method == "POST" and path.endswith("/extensions"):
            key = "quiz_extensions" if "/quizzes/" in path else "assignment_extensions"
            applied = [item for item in payload[key]
                       if str(item["user_id"]) not in self.ineligible]
            for item in applied:
                self.extra[str(item["user_id"])] = item["extra_attempts"]
            return {key: self._copy.deepcopy(applied)}
        elif method == "POST" and path.endswith("/accommodations"):
            if self.accommodation_reply is not None:
                return self.accommodation_reply(payload)
            return {"successful": [{"user_id": item["user_id"]} for item in payload],
                    "failed": []}
        return {}


class FakeAttemptsVault:
    def __init__(self):
        self.by_id = dict(ATTEMPTS_STUDENTS)

    def transaction(self):
        from contextlib import nullcontext
        return nullcontext(self)

    def get_or_assign(self, canvas_id, *args, **kwargs):
        return self.by_id[str(canvas_id)]

    def reverse(self, pseudonym):
        for canvas_id, value in self.by_id.items():
            if value == pseudonym:
                return {"canvas_id": canvas_id}
        return None


class FakeStepContext:
    """Records before_send and checkpoint_step the way the ledger context does."""

    def __init__(self):
        self.before_send_calls = []
        self.checkpoints = []

    def before_send(self, step_key, payload_digest):
        self.before_send_calls.append((step_key, payload_digest))
        return {"step_key": step_key, "state": "claimed",
                "payload_digest": payload_digest,
                "outbound_started_at": "2026-09-30T12:00:00+00:00"}

    def checkpoint_step(self, step, **kwargs):
        import copy as _copy
        saved = _copy.deepcopy(step)
        saved.update(kwargs)
        self.checkpoints.append(saved)
        return saved


@pytest.fixture
def step_context():
    return FakeStepContext()


@pytest.fixture
def attempts_world(monkeypatch):
    """``world(kind)`` -> fake Canvas, vault, and preview/apply helpers.

    The mirror seam is stubbed to a fresh roster of four students, three of
    them on the assignment; every Canvas read and write goes to the fake.
    """
    from api import attempts_grant
    from api.operation_ledger.adapters import attempts_grant as adapter_module
    from api.platform_services import canvas_client, config

    def build(kind="regular"):
        canvas, vault = FakeAttemptsCanvas(kind), FakeAttemptsVault()
        monkeypatch.setattr(config, "active_courses",
                            lambda: [{"id": ATTEMPTS_COURSE, "name": "Synthetic Course"}])
        monkeypatch.setattr(attempts_grant, "_vault", lambda: vault)
        monkeypatch.setattr(canvas_client, "canvas_get", canvas.get)
        monkeypatch.setattr(canvas_client, "canvas_get_all", canvas.get_all)
        monkeypatch.setattr(canvas_client, "canvas_get_all_complete", canvas.get_all_complete)
        monkeypatch.setattr(canvas_client, "_canvas_send", canvas.send)
        monkeypatch.setattr(adapter_module, "_mirror_students", lambda course, assignment: {
            "roster": [{"id": user_id, "name": f"Real Name {index}"}
                       for index, user_id in enumerate(sorted(ATTEMPTS_STUDENTS), 1)],
            "on_assignment_ids": ["student-1", "student-2", "student-3"],
            "synced_at": "2026-09-30T12:00:00Z",
            "freshness": {"state": "current", "within_policy": True},
        })

        def preview(grant):
            return attempts_grant.preview_attempts_grant(
                ATTEMPTS_COURSE, ATTEMPTS_ASSIGNMENT, grant)

        def apply(reviewed):
            return mcp_tools.apply_operation(
                reviewed["operation_id"], reviewed["batch_id"], reviewed["review_digest"])

        return SimpleNamespace(canvas=canvas, vault=vault, preview=preview, apply=apply,
                               module=attempts_grant)

    return build


@pytest.fixture
def scoring_refresh_world(tmp_path, monkeypatch):
    """One synthetic open Scoring Session over a fake local mirror.

    The SAFE builder, vault, scrub, bundle files, stage and apply are real; only
    the mirror read, the in-memory session store (a deep copy per save and load,
    like a file), and the Canvas transport are faked. ``world.sent`` records
    every Canvas write the apply lane makes.
    """
    import contextlib
    import copy
    import json as _json

    from api.feedback_vault import Vault
    from api.mcp_server import tools
    from api.platform_services import config, workspace
    from api.powergrader import (
        assignment_refresh, scoring_apply, scoring_artifacts, scoring_preparation,
        session_store,
    )

    vault = Vault(str(tmp_path / "vault.json"))
    assignment = {"id": "a1", "name": "Essay", "description": "Write the essay.",
                  "points_possible": 10, "is_quiz_lti_assignment": False,
                  "quiz_kind": "", "rubric": []}
    world = SimpleNamespace(
        revision=1, rows=[], sessions={}, sent=[], vault=vault, tmp_path=tmp_path,
        freshness={"course_id": "c1", "state": "current",
                   "last_success_at": "2026-09-21T12:00:00Z", "age_minutes": 0,
                   "requires_teacher_confirmation": False},
    )

    def snapshot_id():
        return f"c1:{world.revision}"

    def add(user_id, name, body="A synthetic response.", **overrides):
        row = {"user_id": user_id, "id": f"sub-{user_id}", "workflow_state": "submitted",
               "submission_type": "online_text_entry", "body": body, "attempt": 1,
               "submitted_at": "2026-09-18T10:00:00Z", "score": None, "late": False,
               "user": {"name": name, "sortable_name": name}, "attachments": [],
               "assignment": {"id": "a1", "name": "Essay", "description": "Write the essay.",
                              "points_possible": 10}}
        row.update(overrides)
        world.rows.append(row)
        world.revision += 1
        return row

    def resubmit(user_id, body="A synthetic revised response.", attempt=2,
                 submitted_at="2026-09-19T10:00:00Z"):
        row = next(r for r in world.rows if r["user_id"] == user_id)
        row.update(body=body, attempt=attempt, submitted_at=submitted_at)
        world.revision += 1

    def session(session_id=None):
        key = session_id or next(iter(world.sessions))
        return copy.deepcopy(world.sessions[key])

    def bundle(record=None):
        record = record or session()
        with open(record["privacy_artifacts"]["safe_bundle"], encoding="utf-8") as handle:
            return _json.load(handle)

    def pseudonym(user_id):
        return vault.get_or_assign(user_id)

    def prepare():
        result = scoring_preparation.prepare_scoring_session("c1", "a1")
        assert result["status"] == "ready", result
        return result["scoring_session_id"]

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(config, "course_display_name", lambda _id: "Course")
    monkeypatch.setattr(config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(config, "get_extra_time", lambda _id: [])
    monkeypatch.setattr(config, "active_protected_names", lambda: set())
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    monkeypatch.setattr(
        assignment_refresh, "prepare_assignment_from_mirror",
        lambda _course, _assignment: (
            copy.deepcopy(world.rows), dict(assignment),
            {"status": "mirror", "manifest_path": None, "mirror_revision": world.revision,
             "snapshot_id": snapshot_id(), "freshness": dict(world.freshness)}))
    monkeypatch.setattr(scoring_artifacts.privacy, "feedback_artifact_dirs",
                        lambda **_kwargs: (str(tmp_path / "SAFE"), str(tmp_path / "PRIVATE")))
    monkeypatch.setattr(scoring_artifacts.context, "vault", lambda: vault)
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    monkeypatch.setattr(session_store, "save_session",
                        lambda value: world.sessions.__setitem__(
                            value["session_id"], copy.deepcopy(value)))
    monkeypatch.setattr(session_store, "load_session",
                        lambda sid: copy.deepcopy(world.sessions.get(sid)))
    monkeypatch.setattr(session_store, "list_session_summaries",
                        lambda: [session_store._summary(value) for value in world.sessions.values()])
    monkeypatch.setattr(session_store, "session_lock", lambda _sid: contextlib.nullcontext())
    monkeypatch.setattr(session_store, "scope_lock", lambda _c, _a: contextlib.nullcontext())
    monkeypatch.setattr(session_store, "assert_work_item_writable", lambda _sid: None)
    monkeypatch.setattr(
        tools.read_service, "private_submissions",
        lambda *_a, **_kw: {"state": "current", "mirror_revision": world.revision,
                            "snapshot_id": snapshot_id()})
    monkeypatch.setattr(
        tools.mirror_store, "read_submissions",
        lambda *_a, **_kw: {"submissions": {r["user_id"]: {"current": r} for r in world.rows}})
    monkeypatch.setattr(
        scoring_apply, "default_transports",
        lambda: (lambda method, path, payload, timeout=30:
                 (world.sent.append((method, path, copy.deepcopy(payload))) or ({"id": 1}, None))))
    def read_after_apply():
        """Return only the exact grades accepted by this synthetic transport."""
        def read(_path, params):
            requested = params.get("student_ids[]", [])
            if isinstance(requested, str):
                requested = [requested]
            rows = []
            for user_id in requested:
                sent = next((payload for _method, path, payload in reversed(world.sent)
                             if path.endswith(f"/submissions/{user_id}")
                             or path.endswith(f"/submissions/{user_id}?")), None)
                if sent is None:
                    continue
                grade = sent.get("submission", {}).get("posted_grade")
                if grade is None:
                    continue
                rows.append({"user_id": str(user_id), "score": float(grade),
                             "entered_score": float(grade), "points_deducted": None,
                             "late_policy_status": None})
            return rows, None
        return read

    monkeypatch.setattr(scoring_apply, "default_read_transport", read_after_apply)

    world.add, world.resubmit, world.session = add, resubmit, session
    world.bundle, world.pseudonym, world.prepare = bundle, pseudonym, prepare
    world.tools = tools
    return world
