"""Synthetic coverage for AssignmentForge Differentiated Hub operations."""
import copy

import pytest

from api.operation_ledger.adapters import assignment_hub, assignment as assignment_adapter
from api.platform_services import canvas_client


class Context:
    def __init__(self):
        self.steps = []

    def before_send(self, key, digest):
        row = next((copy.deepcopy(s) for s in self.steps if s["step_key"] == key),
                   {"step_key": key, "attempts": 0})
        row.update(state="claimed", payload_digest=digest, outbound_started_at="2026-09-25T12:00:00+00:00")
        self._put(row)
        return copy.deepcopy(row)

    def checkpoint_step(self, step, returned_object_id=None, returned_object_url=None):
        row = copy.deepcopy(step)
        if returned_object_id is not None: row["returned_object_id"] = returned_object_id
        if returned_object_url is not None: row["returned_object_url"] = returned_object_url
        self._put(row)
        return copy.deepcopy(row)

    def _put(self, row):
        self.steps = [s for s in self.steps if s["step_key"] != row["step_key"]]
        self.steps.append(copy.deepcopy(row))


class FakeCanvas:
    def __init__(self):
        self.pages = {}
        self.dates = {}
        self.groups = {"11": {"id": "11", "group_category_id": "5", "name": "Red",
                               "non_collaborative": True}}
        self.sends = []
        self.reads = []
        self.make_public_after_publish = False
        self.keep_published_after_unpublish = False
        self.mismatch_assignment_readback = False

    def get(self, path, params=None, timeout=20):
        self.reads.append((path, params))
        if "/date_details" in path:
            page_id = path.split("/pages/")[1].split("/")[0]
            return copy.deepcopy(self.dates.get(page_id)), None
        if "/pages/" in path:
            page_id = path.rsplit("/", 1)[-1]
            return copy.deepcopy(self.pages.get(page_id)), None
        if path.startswith("/api/v1/groups/"):
            group_id = path.rsplit("/", 1)[-1]
            return copy.deepcopy(self.groups.get(group_id)), None
        if "/assignments/" in path:
            return {"id": "700", "description": self.assignment_description}, None
        return None, None

    def get_all(self, path, params=None, timeout=30):
        self.reads.append((path, params))
        if path.endswith("/pages"):
            return copy.deepcopy(list(self.pages.values())), None
        raise AssertionError(f"Unexpected list read: {path}")

    def get_all_complete(self, path, params=None, timeout=30):
        self.reads.append((path, params))
        if path.endswith("/group_categories"):
            return [{"id": "5"}], None, True
        if path.endswith("/group_categories/5/groups"):
            return [{"id": "11", "name": "Red"}], None, True
        raise AssertionError(f"Unexpected complete read: {path}")

    def send(self, method, path, body, timeout=30):
        self.sends.append((method, path, copy.deepcopy(body)))
        wiki = body.get("wiki_page", {})
        if method == "POST" and path.endswith("/pages"):
            i = len(self.pages) + 1
            page_id, slug = str(100 + i), f"support-page-{i}"
            page = {"id": page_id, "page_id": page_id, "url": slug,
                    "html_url": f"https://canvas.invalid/courses/42/pages/{slug}",
                    "title": wiki["title"], "body": wiki["body"], "published": False}
            self.pages[page_id] = page
            self.dates[page_id] = {"visible_to_everyone": True, "overrides": []}
            return copy.deepcopy(page), None
        if method == "PUT" and path.endswith("/date_details"):
            assert "wiki_page" not in body
            page_id = path.split("/pages/")[1].split("/")[0]
            overrides = copy.deepcopy(body.get("assignment_overrides", []))
            if overrides and self.mismatch_assignment_readback:
                overrides = [{"group_id": 99}]
            self.dates[page_id] = {"visible_to_everyone": not body.get("only_visible_to_overrides", False),
                                   "overrides": overrides}
            return {}, None
        if method == "PUT" and "/pages/" in path:
            page_id = path.rsplit("/", 1)[-1]
            if wiki.get("published") is True or not self.keep_published_after_unpublish:
                self.pages[page_id]["published"] = wiki.get("published", False)
            if self.make_public_after_publish and wiki.get("published"):
                self.dates[page_id]["visible_to_everyone"] = True
            return copy.deepcopy(self.pages[page_id]), None
        raise AssertionError((method, path, body))


def _whole_execute(payload, target, context, **kwargs):
    assignment_step = {"step_key": "create_assignment", "state": "applied",
                             "returned_object_id": "700",
                             "returned_object_url": "https://canvas.invalid/a/700"}
    context.checkpoint_step(assignment_step)
    target["steps"] = kwargs["ordered_steps"]({"steps": target.get("steps", []) + [assignment_step]})
    fake.assignment_description = payload["description"]
    return {"state": "applied", "steps": kwargs["ordered_steps"](target),
            "returned_object_id": "700", "returned_object_url": "https://canvas.invalid/a/700"}


@pytest.mark.parametrize(("groups", "expected", "unavailable"), [
    ([{"id": "11", "name": "Red"}], "matched", False),
    ([], "not_found", False),
    ([{"id": "11", "name": "Red"}, {"id": "12", "name": " red "}], "ambiguous", False),
    ([], "unavailable", True),
])
def test_live_tag_resolution_is_name_only_and_never_reads_membership(monkeypatch, groups, expected, unavailable):
    fake = FakeCanvas()
    def complete(path, params=None, timeout=30):
        fake.reads.append((path, params))
        if unavailable:
            return None, "HTTP 403", False
        if path.endswith("/group_categories"):
            return [{"id": "5"}], None, True
        return groups, None, True
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", complete)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    payload = {"hub": True, "tiers": [{"label": "Support", "tag": "Red", "title": "T - Red"}]}
    assignment_adapter._capture_hub_baseline(payload, {"course_id": "42", "steps": []})
    assert payload["tiers"][0]["tag_status"] == expected
    assert not any("membership" in path or "/users" in path for path, _ in fake.reads)


def test_tag_ids_are_read_once_and_frozen_across_later_baseline_checks(monkeypatch):
    calls = []
    def complete(path, params=None, timeout=30):
        calls.append(path)
        return ([{"id": "5"}] if path.endswith("group_categories") else
                [{"id": "11", "name": "Red"}]), None, True
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", complete)
    monkeypatch.setattr(canvas_client, "canvas_get_all", lambda *_a, **_k: ([], None))
    adapter = assignment_adapter.AssignmentAdapter()
    payload = {"hub": True, "tiers": [{"label": "Support", "tag": "Red", "title": "Hub - Red"}]}
    target = {"course_id": "42", "steps": []}
    adapter.capture_baseline(payload, target)
    assert target["target_key"] == adapter.target_key(payload, "42")
    first_ids = (payload["tiers"][0]["matched_group_id"], payload["tiers"][0]["matched_category_id"])
    adapter.capture_baseline(payload, {"course_id": "42", "steps": [], "baseline": {"hub_tiers": []}})
    assert len(calls) == 2
    assert (payload["tiers"][0]["matched_group_id"], payload["tiers"][0]["matched_category_id"]) == first_ids


def test_existing_exact_tier_page_refuses_preview_without_ids(monkeypatch):
    fake = FakeCanvas()
    fake.pages["999"] = {"id": "999", "page_id": "999", "url": "hub-red",
                          "title": "Hub - Red"}
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fake.get_all_complete)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    payload = {"hub": True, "tiers": [{"label": "Support", "tag": "Red", "title": "Hub - Red"}]}
    result = assignment_adapter._capture_hub_baseline(payload, {"course_id": "42", "steps": []})
    assert result["blocking_error"] == "tier_page_exists"
    assert result["collision_titles"] == ["Hub - Red"]
    assert "999" not in str(result)


def test_publish_tier_page_refuses_public_visibility_and_unpublishes_if_postcheck_changes(monkeypatch):
    fake = FakeCanvas(); monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    fake.pages["101"] = {"id": "101", "published": False}
    fake.dates["101"] = {"visible_to_everyone": True, "overrides": []}
    steps, ctx = [], Context()
    refused = assignment_hub.publish_tier_page("42", "101", 0, steps, ctx)
    assert refused["error_code"] == "tier_page_publish_visibility_refused"
    assert not fake.sends

    fake.dates["101"]["visible_to_everyone"] = False
    fake.make_public_after_publish = True
    failed = assignment_hub.publish_tier_page("42", "101", 0, steps, ctx)
    assert failed["error_code"] == "tier_page_visibility_law_violation"
    assert fake.pages["101"]["published"] is False
    assert fake.sends[-1][2]["wiki_page"]["published"] is False
    cleanup = next(s for s in failed["steps"] if s["step_key"] == "unpublish_tier_page:0")
    publish = next(s for s in failed["steps"] if s["step_key"] == "publish_tier_page:0")
    assert cleanup["state"] == "applied"
    assert cleanup["payload_digest"] != publish["payload_digest"]


def test_public_page_cleanup_checkpoint_reports_unverified_unpublish(monkeypatch):
    fake = FakeCanvas(); monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    fake.pages["101"] = {"id": "101", "published": False}
    fake.dates["101"] = {"visible_to_everyone": False, "overrides": []}
    fake.make_public_after_publish = True
    fake.keep_published_after_unpublish = True
    steps, ctx = [], Context()

    failed = assignment_hub.publish_tier_page("42", "101", 0, steps, ctx)

    assert failed["state"] == "sent_unknown"
    assert failed["error_code"] == "tier_page_unpublish_unverified"
    cleanup = next(s for s in failed["steps"] if s["step_key"] == "unpublish_tier_page:0")
    assert cleanup["state"] == "sent_unknown"
    assert cleanup["payload_digest"]


def test_unreadable_post_publish_visibility_is_compensated_and_verified(monkeypatch):
    fake = FakeCanvas(); monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    fake.pages["101"] = {"id": "101", "published": False}
    fake.dates["101"] = {"visible_to_everyone": False, "overrides": []}
    steps, ctx = [], Context()

    # The initial visibility read succeeds; only the post-publish read fails.
    original_get = fake.get
    failed_once = {"value": False}
    def get(path, params=None, timeout=20):
        if "/date_details" in path and any(
            method == "PUT" and body.get("wiki_page", {}).get("published") is True
            for method, _request_path, body in fake.sends
        ) and not failed_once["value"]:
            failed_once["value"] = True
            return None, "read failed"
        return original_get(path, params, timeout)
    monkeypatch.setattr(canvas_client, "canvas_get", get)

    result = assignment_hub.publish_tier_page("42", "101", 0, steps, ctx)

    assert result["state"] == "sent_unknown"
    assert result["error_code"] == "tier_page_publish_unverified"
    assert fake.pages["101"]["published"] is False
    cleanup = next(s for s in result["steps"] if s["step_key"] == "unpublish_tier_page:0")
    publish = next(s for s in result["steps"] if s["step_key"] == "publish_tier_page:0")
    assert cleanup["state"] == "applied"
    assert cleanup["payload_digest"] != publish["payload_digest"]


def test_resume_with_published_page_and_unreadable_dates_unpublishes(monkeypatch):
    fake = FakeCanvas()
    fake.pages["101"] = {"id": "101", "published": True}
    fake.dates["101"] = {"visible_to_everyone": False, "overrides": []}
    monkeypatch.setattr(canvas_client, "canvas_get", lambda path, *a, **k:
                        (None, "read failed") if "/date_details" in path else fake.get(path, *a, **k))
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    steps, ctx = [], Context()
    ctx.checkpoint_step({"step_key": "publish_tier_page:0", "state": "sent_unknown",
                         "attempts": 1, "payload_digest": "earlier-publish"})
    steps = copy.deepcopy(ctx.steps)

    result = assignment_hub.publish_tier_page("42", "101", 0, steps, ctx)

    assert result["state"] == "sent_unknown"
    assert result["error_code"] == "tier_page_publish_visibility_unverified"
    assert fake.pages["101"]["published"] is False
    cleanup = next(s for s in result["steps"] if s["step_key"] == "unpublish_tier_page:0")
    assert cleanup["state"] == "applied"
    assert cleanup["payload_digest"] != "earlier-publish"


def test_mismatched_group_readback_uses_distinct_checkpointed_clear(monkeypatch):
    fake = FakeCanvas(); monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    fake.dates["101"] = {"visible_to_everyone": False, "overrides": []}
    fake.mismatch_assignment_readback = True
    tier = {"tag": "Red", "tag_status": "matched", "matched_category_id": "5",
            "matched_group_id": "11"}
    steps, ctx = [], Context()

    result = assignment_hub._assign("42", tier, "101", 0, steps, ctx)

    assert result is None
    assert tier["tag_status"] == "unavailable"
    assign = next(s for s in steps if s["step_key"] == "assign_tier_page:0")
    clear = next(s for s in steps if s["step_key"] == "clear_tier_page_assignment:0")
    assert assign["state"] == "skipped"
    assert clear["state"] == "applied"
    assert clear["payload_digest"] != assign["payload_digest"]
    assert fake.dates["101"] == {"visible_to_everyone": False, "overrides": []}


@pytest.mark.parametrize(("field", "value"), [
    ("name", "Renamed"),
    ("group_category_id", "999"),
    ("id", "12"),
])
def test_changed_group_on_resume_clears_previous_override(monkeypatch, field, value):
    fake = FakeCanvas(); monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    fake.groups["11"][field] = value
    fake.dates["101"] = {"visible_to_everyone": False, "overrides": [{"group_id": 11}]}
    ctx = Context()
    ctx.checkpoint_step({"step_key": "assign_tier_page:0", "state": "applied",
                         "attempts": 1, "payload_digest": "original-assignment"})
    steps = copy.deepcopy(ctx.steps)
    tier = {"tag": "Red", "tag_status": "matched", "matched_category_id": "5",
            "matched_group_id": "11"}

    result = assignment_hub._assign("42", tier, "101", 0, steps, ctx)

    assert result is None
    assert tier["tag_status"] == "unavailable"
    assert fake.dates["101"] == {"visible_to_everyone": False, "overrides": []}
    assign = next(s for s in steps if s["step_key"] == "assign_tier_page:0")
    clear = next(s for s in steps if s["step_key"] == "clear_tier_page_assignment:0")
    assert assign["state"] == "skipped"
    assert clear["state"] == "applied"
    assert ("/api/v1/groups/11", None) in fake.reads
    assert not any("group_categories/5/groups/11" in path for path, _ in fake.reads)


def test_hub_push_creates_pages_for_supplied_tiers_and_substitutes_links(monkeypatch):
    global fake
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    payload = {"hub": True, "name": "Hub", "description": '<a href="{{ce-tier-page:0}}">Red</a> <a href="{{ce-tier-page:1}}">Blue</a>',
               "published": False, "tiers": [
                   {"label": "Support", "tag": "Red", "title": "Hub - Red", "description": "<p>Frame</p>", "tag_status": "matched", "matched_category_id": "5", "matched_group_id": "11"},
                   {"label": "Core", "tag": "Blue", "title": "Hub - Blue", "description": "<p>Words</p>", "tag_status": "not_found"},
               ]}
    result = assignment_hub.execute(payload, {"course_id": "42", "steps": []}, Context(),
                                    ordered_steps=assignment_adapter._ordered_steps,
                                    whole_execute=_whole_execute, upload_course_file=lambda *a, **k: None,
                                    find_assignment_group=lambda *a: None, read_modules=lambda *a: ([], None),
                                    prepare_description=lambda **k: (k["payload"]["description"], None))
    assert result["state"] == "applied"
    keys = [s["step_key"] for s in result["steps"]]
    assert keys[:7] == ["create_tier_page:0", "restrict_tier_page:0", "assign_tier_page:0",
                        "create_tier_page:1", "restrict_tier_page:1", "assign_tier_page:1", "create_assignment"][:7]
    assert "/courses/42/pages/support-page-1" in fake.assignment_description
    assert "/courses/42/pages/support-page-2" in fake.assignment_description
    assert len(fake.pages) == 2


def test_resume_after_page_creation_reuses_exact_checkpoint_without_duplicate(monkeypatch):
    global fake
    fake = FakeCanvas()
    fake.pages["101"] = {"id": "101", "page_id": "101", "url": "support-page",
                         "html_url": "https://canvas.invalid/courses/42/pages/support-page",
                         "title": "Hub - Red", "body": "<p>Frame</p>", "published": False}
    fake.dates["101"] = {"visible_to_everyone": False, "overrides": []}
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    ctx = Context()
    ctx.checkpoint_step({"step_key": "create_tier_page:0", "state": "applied",
                         "returned_object_id": "101", "returned_object_url": fake.pages["101"]["html_url"],
                         "page_slug": "support-page"})
    payload = {"hub": True, "name": "Hub", "description": '<a href="{{ce-tier-page:0}}">Red</a>',
               "published": False, "tiers": [{"label": "Support", "tag": "Red",
                   "title": "Hub - Red", "description": "<p>Frame</p>", "tag_status": "not_found"}]}
    result = assignment_hub.execute(payload, {"course_id": "42", "steps": ctx.steps}, ctx,
                                    ordered_steps=assignment_adapter._ordered_steps,
                                    whole_execute=_whole_execute, upload_course_file=lambda *a, **k: None,
                                    find_assignment_group=lambda *a: None, read_modules=lambda *a: ([], None),
                                    prepare_description=lambda **k: (k["payload"]["description"], None))
    assert result["state"] == "applied"
    assert len(fake.pages) == 1
    assert not any(method == "POST" and path.endswith("/pages") for method, path, _ in fake.sends)


def test_result_projection_contains_hub_summary_without_private_group_ids():
    from api.content_push import _result_projection
    operation = {"kind": "content.assignment", "normalized_payload": {
        "hub": True, "name": "Hub", "published": False,
        "tiers": [{"label": "Support", "tag": "Red", "title": "Hub - Red",
                   "tag_status": "not_found", "matched_group_id": "private-group-11",
                   "matched_category_id": "private-category-5"}],
    }, "targets": [{"course_id": "42", "steps": [
        {"step_key": "create_tier_page:0", "state": "applied", "returned_object_id": "101",
         "returned_object_url": "https://canvas.invalid/page"},
        {"step_key": "create_assignment", "state": "applied", "returned_object_id": "700",
         "returned_object_url": "https://canvas.invalid/assignment"},
    ]}]}
    result = {"ok": True, "operation_id": "op", "status": "applied", "target_results": [{
        "state": "applied", "steps": operation["targets"][0]["steps"],
    }]}
    projection = _result_projection(operation, result)
    review = assignment_adapter.AssignmentAdapter().freeze_review(
        operation["normalized_payload"], {"course_id": "42", "steps": []}, {})
    rendered = str(projection)
    assert projection["targets"][0]["hub"]["tiers"][0]["page_id"] == "101"
    assert projection["targets"][0]["hub"]["teacher_actions"]
    assert "private-group-11" not in rendered and "private-category-5" not in rendered
    assert "private-group-11" not in str(review) and "private-category-5" not in str(review)
