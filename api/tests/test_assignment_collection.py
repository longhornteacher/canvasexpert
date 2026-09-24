import copy

import pytest

from api import course_catalog
from api.mirror import store, sync


COURSE = "course-1"
STAMP_1 = "2026-07-18T12:00:00+00:00"
STAMP_2 = "2026-07-18T13:00:00+00:00"


def _catalog_from_receipt(receipt, root, attempted_at):
    def module_receipt(path, params):
        if path.endswith("/assignment_groups"):
            return [], None, True
        if path.endswith("/pages"):
            return [], None, True
        assert path.endswith("/modules")
        assert params == {"per_page": 100, "include[]": "items"}
        return [], None, True

    return course_catalog.refresh_catalog(
        COURSE,
        "Fictional Course",
        canvas_get_all=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("empty modules must not request module items")),
        canvas_get_all_complete=module_receipt,
        assignment_receipt=receipt,

        attempted_at=attempted_at,
    )


def test_complete_receipt_updates_catalog_and_mirror_including_empty_membership(tmp_path):
    receipt = ([{"id": "101", "name": "Fictional Reflection"}], None, True)

    catalog = _catalog_from_receipt(receipt, tmp_path, STAMP_1)["catalog"]
    mirror = sync.apply_assignment_collection_receipt(
        COURSE, receipt, root=str(tmp_path), attempted_at=STAMP_1)

    assert catalog["assignments"]["state"] == "current"
    assert set(catalog["assignments"]["records"]) == {"101"}
    assert mirror["ok"] is True
    assert set(store.read_assignments(COURSE, root=str(tmp_path))["assignments"]) == {"101"}

    empty_receipt = ([], None, True)
    emptied_catalog = _catalog_from_receipt(empty_receipt, tmp_path, STAMP_2)["catalog"]
    emptied_mirror = sync.apply_assignment_collection_receipt(
        COURSE, empty_receipt, root=str(tmp_path), attempted_at=STAMP_2)

    assert emptied_catalog["assignments"] == {
        "state": "current",
        "last_success_at": STAMP_2,
        "last_attempt_at": STAMP_2,
        "error_code": "",
        "records": {},
    }
    assert emptied_mirror["ok"] is True
    assert store.read_assignments(COURSE, root=str(tmp_path))["assignments"] == {}


@pytest.mark.parametrize(
    "receipt",
    [
        ([], None, False),
        (None, "HTTP 503: upstream", True),
        ([{"id": "101"}, {"id": "101"}], None, True),
    ],
    ids=["incomplete", "transport-failure", "invalid-duplicate"],
)
def test_unproven_or_invalid_receipt_never_replaces_mirror_membership(tmp_path, receipt):
    prior_receipt = ([{"id": "101", "name": "Fictional Reflection"}], None, True)
    assert sync.apply_assignment_collection_receipt(
        COURSE, prior_receipt, root=str(tmp_path), attempted_at=STAMP_1)["ok"] is True
    before = copy.deepcopy(store.read_assignments(COURSE, root=str(tmp_path)))

    result = sync.apply_assignment_collection_receipt(
        COURSE, receipt, root=str(tmp_path), attempted_at=STAMP_2)

    assert result["ok"] is False
    assert store.read_assignments(COURSE, root=str(tmp_path)) == before
