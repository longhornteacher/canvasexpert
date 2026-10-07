"""Heartbeat cadence: which passes one course needs on a tick."""
from __future__ import annotations

import pytest

from api.mirror import service

NOW = "2026-10-07T12:00:00Z"


def _state(*, full="2026-10-07T00:00:00Z", delta="", roster="2026-10-07T11:00:00Z"):
    return {"passes": {name: {"last_success_at": value}
                       for name, value in (("full", full), ("delta", delta), ("roster", roster))}}


@pytest.mark.parametrize("state, expected", [
    pytest.param(_state(full=""), ["full"], id="never-full"),
    pytest.param(_state(full="2026-10-06T11:00:00Z"), ["full"], id="full-aged-out"),
    pytest.param(_state(delta=""), ["delta"], id="never-delta"),
    pytest.param(_state(delta="2026-10-07T11:58:30Z"), [], id="manual-refresh-just-before-tick"),
    pytest.param(_state(full="2026-10-07T11:57:00Z", delta="2026-10-07T09:00:00Z"), [],
                 id="recent-full-covers-delta"),
    pytest.param(_state(delta="2026-10-07T11:45:00Z"), ["delta"], id="previous-tick-start"),
    pytest.param(_state(delta="2026-10-07T12:30:00Z"), ["delta"], id="future-timestamp-runs"),
    pytest.param(_state(delta="2026-10-07T11:58:30Z", roster="2026-10-07T03:00:00Z"), ["roster"],
                 id="recent-delta-roster-still-due"),
])
def test_due_passes(state, expected):
    assert service.due_passes(state, NOW, serve_max_age_hours=6.0) == expected
