from api.platform_services import config


def test_saved_family_link_reads_back_as_save_returned_it():
    """Law: get returns exactly what save returned, even when save drops a
    blank field (a module chosen by id alone has no module_name). The family
    link postcondition relies on this."""
    registration = {
        "family_title": "Practice",
        "source_assignment_ids": ["101", "102"],
        "source_titles": ["Practice - Silver", "Practice - Red"],
        "bridge_assignment_id": "103",
        "bridge_state_digest": "d" * 64,
        "module_id": "501",
        "module_name": "",
    }
    saved = config.save_sis_grade_bridge("42", registration)
    assert config.get_sis_grade_bridge("42", "Practice") == saved
    assert config.save_sis_grade_bridge_verified("42", registration) is True
