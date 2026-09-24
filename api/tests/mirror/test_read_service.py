from api import course_catalog
from api.mirror import read_service


STAMP = "2026-08-30T12:00:00+00:00"


def _page(page_id="p1", **changes):
    # Shaped like Canvas's real Wiki Pages API: keyed by `page_id`, not `id`
    # like every other resource `course_catalog` normalizes.
    row = {
        "page_id": page_id, "title": "  Unit <b>One</b> ",
        "body": "<p>Read <strong>this</strong>.</p><script>private()</script>",
        "published": True, "front_page": False, "updated_at": STAMP,
        "html_url": "https://canvas.invalid/courses/course-1/pages/unit-one",
    }
    row.update(changes)
    return row


def _refresh(tmp_path, page_rows):
    def complete(path, params):
        if path.endswith("/pages"):
            assert params == {"per_page": 100, "include[]": "body"}
            return page_rows, None, True
        if path.endswith("/modules") or path.endswith("/assignments") or path.endswith("/assignment_groups"):
            return [], None, True
        raise AssertionError(path)

    return course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=lambda path, params: ([], None),
        canvas_get_all_complete=complete, attempted_at=STAMP,
    )


def test_catalog_pages_are_exact_plain_text_and_typed_disk_read(tmp_path):
    result = _refresh(tmp_path, [_page()])
    page = result["catalog"]["pages"]["records"][0]
    assert page == {
        "id": "p1", "title": "Unit One", "body_text": "Read this.",
        "published": True, "front_page": False, "updated_at": STAMP,
    }
    assert read_service.catalog_pages(
        "course-1", root=str(tmp_path), catalog_reader=lambda _cid: course_catalog.read_catalog(_cid),
    )["records"] == [page]


def test_invalid_page_keeps_last_good_scope_and_empty_is_current(tmp_path):
    _refresh(tmp_path, [_page()])
    invalid = _refresh(tmp_path, [_page("p2"), {"page_id": "bad", "body": "\x00"}])
    assert invalid["catalog"]["pages"]["state"] == "incomplete"
    assert invalid["catalog"]["pages"]["records"] == [
        {"id": "p1", "title": "Unit One", "body_text": "Read this.",
         "published": True, "front_page": False, "updated_at": STAMP}
    ]
    empty = _refresh(tmp_path, [])
    assert empty["catalog"]["pages"]["state"] == "current"
    assert empty["catalog"]["pages"]["records"] == []
