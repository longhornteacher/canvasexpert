import json

from api.feedback_vault import Vault
from api.powergrader import scoring_artifacts


def _submission(body="A response."):
    return {"user_id": "u1", "body": body, "user": {"name": "Ada Lovelace"},
            "attachments": [], "workflow_state": "submitted"}


def _build(monkeypatch, tmp_path, submissions=None):
    monkeypatch.setattr(scoring_artifacts.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_artifacts.privacy, "feedback_artifact_dirs",
                        lambda **_kwargs: (str(tmp_path / "SAFE"), str(tmp_path / "PRIVATE")))
    monkeypatch.setattr(scoring_artifacts.context, "vault",
                        lambda: Vault(str(tmp_path / "vault.json")))
    return scoring_artifacts.build_scoring_artifacts(
        submitted=list(submissions or [_submission()]), assignment_name="Essay",
        assignment_description="Write.", course_id="c1", course_name="Course",
        assignment_id="a1", session_id="s1", protected=set(),
    )


def test_scoring_builder_writes_only_one_safe_bundle(monkeypatch, tmp_path):
    result = _build(monkeypatch, tmp_path)
    assert result["ok"] is True
    safe = tmp_path / "SAFE"
    assert [path.name for path in safe.iterdir()] == ["Essay__bundle.json"]
    assert not (tmp_path / "PRIVATE").exists()
    payload = json.loads((safe / "Essay__bundle.json").read_text(encoding="utf-8"))
    blob = json.dumps(payload)
    assert "Ada Lovelace" not in blob
    assert "u1" not in blob
    assert result["privacy_artifacts"]["safe_bundle"].endswith("Essay__bundle.json")


def test_hard_identity_violation_blocks_safe_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(scoring_artifacts, "_scrub_bundle",
                        lambda bundle, _vault, protected=None: {
                            "students": [{"real_name": "Ada Lovelace", "pseudonym": "Pikachu",
                                           "responses": [{"response": "Ada Lovelace"}]}]
                        })
    result = _build(monkeypatch, tmp_path)
    assert result["ok"] is False
    assert not (tmp_path / "SAFE").exists() or not list((tmp_path / "SAFE").iterdir())


def test_survivor_excludes_only_that_student(monkeypatch, tmp_path):
    submissions = [_submission(), {**_submission("Other response."), "user_id": "u2",
                                   "user": {"name": "Grace Hopper"}}]
    original = scoring_artifacts.feedback_scrub.verify_clean
    calls = {"count": 0}

    def verify(text, vault):
        calls["count"] += 1
        return ["survivor"] if calls["count"] == 1 else original(text, vault)

    monkeypatch.setattr(scoring_artifacts.feedback_scrub, "verify_clean", verify)
    result = _build(monkeypatch, tmp_path, submissions)
    assert result["ok"] is True
    payload = json.loads((tmp_path / "SAFE" / "Essay__bundle.json").read_text(encoding="utf-8"))
    assert len(payload["students"]) == 1
    assert result["privacy_artifacts"]["excluded_count"] == 1
