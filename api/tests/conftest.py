"""Session-wide safety net: no test may touch this machine's real
config.json, profiles.json, or OneDrive-synced workspace.

A test that forgets to isolate these paths itself used to fall through to
the real machine state -- see the self-update brief's disclosed incident,
where an unmocked test wrote a fictional course into the real, live
OneDrive-synced settings.json. This fixture runs before every test in the
suite and points every known real-data path at a fresh, unique temp
location. A test's own explicit monkeypatch.setattr calls (e.g. migration
tests that deliberately exercise CONFIG_PATH/LEGACY_CONFIG_PATH) still take
effect normally, since they run after this fixture within the same test.
"""
import os
from types import SimpleNamespace

import pytest

from api import grading_policy, pseudonym_secret, runtime_paths
from api.platform_services import workspace
from api.platform_services.config import _io as config_io
from api.webui import profiles


@pytest.fixture(autouse=True)
def _isolate_real_machine_and_workspace_paths(tmp_path, monkeypatch):
    fake_root = tmp_path / "_isolated_runtime"

    monkeypatch.setattr(config_io, "CONFIG_PATH", str(fake_root / "config.json"))
    monkeypatch.setattr(config_io, "LEGACY_CONFIG_PATH", str(fake_root / "no-legacy-config.json"))
    monkeypatch.setattr(workspace, "CONFIG_PATH", str(fake_root / "config.json"))
    monkeypatch.setattr(workspace, "LEGACY_CONFIG_PATH", str(fake_root / "no-legacy-config.json"))
    monkeypatch.setattr(profiles, "PROFILES_PATH", str(fake_root / "profiles.json"))
    monkeypatch.setattr(profiles, "LEGACY_PROFILES_PATH", str(fake_root / "no-legacy-profiles.json"))

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

    def policy(floor_percent=30, missing_percent=20, sweep_after_school_days=15):
        path = os.path.join(workspace.library_root(), grading_policy.POLICY_FILENAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                f"floor_percent: {floor_percent}\n"
                f"missing_percent: {missing_percent}\n"
                f"sweep_after_school_days: {sweep_after_school_days}\n"
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
            return attempts_grant.apply_attempts_grant(
                reviewed["operation_id"], reviewed["batch_id"], reviewed["review_digest"])

        return SimpleNamespace(canvas=canvas, vault=vault, preview=preview, apply=apply,
                               module=attempts_grant)

    return build
