from api.operation_ledger.adapters.assignment import _ordered_steps


def _step(key):
    return {"step_key": key}


def test_family_tail_steps_run_after_all_tiers():
    steps = [
        _step("register_family"),
        _step("attach_source_module:1"),
        _step("create_tier_assignment:2"),
        _step("create_bridge"),
        _step("restrict_assignment:0"),
        _step("attach_source_module:0"),
        _step("activate_bridge"),
        _step("create_tier_assignment:0"),
        _step("create_override:1"),
    ]
    ordered = [step["step_key"] for step in _ordered_steps({"steps": steps})]

    tail_start = ordered.index("create_bridge")
    tier_keys = {"create_tier_assignment:0", "create_tier_assignment:2", "restrict_assignment:0", "create_override:1"}
    for key in tier_keys:
        assert ordered.index(key) < tail_start

    assert ordered[tail_start:] == [
        "create_bridge",
        "attach_source_module:0",
        "attach_source_module:1",
        "activate_bridge",
        "register_family",
    ]


def test_single_tier_without_family_tail_is_unaffected():
    steps = [
        _step("create_tier_assignment:0"),
        _step("restrict_assignment:0"),
        _step("create_override:0"),
        _step("publish_assignment:0"),
    ]
    ordered = [step["step_key"] for step in _ordered_steps({"steps": steps})]
    assert ordered == [
        "create_tier_assignment:0",
        "restrict_assignment:0",
        "create_override:0",
        "publish_assignment:0",
    ]
