"""Local roster validation refuses the whole patch before mutating any store."""
from api import roster_service


def test_invalid_later_field_prevents_nickname_and_settings_mutations(monkeypatch):
    def unexpected_mutation(*args, **kwargs):
        raise AssertionError("Malformed patch must never begin mutations")
    class UnopenedVault:
        transaction = unexpected_mutation
    monkeypatch.setattr(roster_service.config, "set_extra_time", unexpected_mutation)
    monkeypatch.setattr(roster_service.config, "set_monitored_student", unexpected_mutation)
    result = roster_service.update_student("course-1", "synthetic-id", {
        "add_nicknames": ["Synthetic"],
        "extra_time": {"enabled": True, "days": 2},
        "monitored": "invalid",
    }, UnopenedVault())
    assert result == {"ok": False, "error": "monitored must be an object."}
