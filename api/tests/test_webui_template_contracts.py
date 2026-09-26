"""Source-contract tests for WebUI templates and client JS.

These guard against specific P1 regression modes. They read source files
directly — no live Canvas, no server, no student data.

Only safety/workflow-wiring tests are retained. Visual composition, CSS,
DOM IDs, layout, copy, script ordering, template inheritance, and former
redesign-slice implementation snapshots are covered by rendered-route
verification per AGENTS.md testing policy.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _slurp(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── Typed operation-gateway routing ──────────────────────────────────

def test_operation_gateway_aliases_and_no_direct_legacy_calls():
    """All content types use typed operation aliases; no generic /api/content/push."""
    core = _slurp("api/webui/static/push/core.js")
    for alias, kind in {
        "qf": "content.quiz",
        "quick": "content.quick_assignment",
        "af": "content.assignment",
        "pf": "content.page",
    }.items():
        assert f'{alias}: "{kind}"' in core
    for rel in (
        "api/webui/static/push/assignment.js",
        "api/webui/static/push/page.js",
        "api/webui/static/course_expert/quick_assignment.js",
    ):
        assert "/api/content/push" not in _slurp(rel)
    assert '{ payload: payload, targets: targets }' in core


def test_quiz_push_uses_typed_operation_payloads_only():
    """Quiz browser uses typed operation preparation, not direct SSE or student IDs."""
    quiz = _slurp("api/webui/static/push/quiz.js")
    assert 'push.pushContent(' in quiz
    assert '"qf"' in quiz
    assert '{ mode: "whole", path: path, settings: settingsObj }' in quiz
    assert '{ mode: "differentiated", variants: variants, settings: settingsObj }' in quiz
    assert "push.stream" + "SSE(" not in quiz
    assert "push.canvasWriteReview(" not in quiz
    assert "studentIds:" not in quiz


def test_operation_summary_polling_uses_ordinal_labels_only():
    """Polling endpoint must not expose internal target/step identifiers."""
    core = _slurp("api/webui/static/push/core.js")
    assert '"/api/operations/" + encodeURIComponent(operationId) + "/status"' in core
    assert "Target ' + (targetIndex + 1)" in core
    assert "Step ' + (stepIndex + 1)" in core
    assert "target.target_key" not in core
    assert "step.step_key" not in core
    assert 'window.addEventListener("pagehide"' in core


def test_assignment_tier_review_is_content_only():
    core = _slurp("api/webui/static/push/core.js")
    start = core.index("if (first.tiered)")
    first_loop = core.index("    frozen.forEach(function (review)", start)
    end = core.index("    frozen.forEach(function (review)", first_loop + 1)
    assignment_review = core[start:end]
    assert "tier.group" not in assignment_review
    assert "tier.student_count" not in assignment_review
    assert "only_visible_to_overrides" not in assignment_review
    assert "unpublished, unrestricted assignment draft" in assignment_review
    assert "reviewTeacherAction" in assignment_review
    assert "assign each draft" in core


def test_shared_csrf_meta():
    """base.html must expose the shared CSRF meta tag."""
    base = _slurp("api/webui/templates/base.html")
    assert base.count('name="canvasexpert-csrf-token"') == 1
    assert 'content="{{ csrf_token }}"' in base


def test_course_expert_workspace_title_tracks_the_existing_tab_activation_path():
    """Workbench title follows the same activation path as deep links and key navigation."""
    template = _slurp("api/webui/templates/course_expert.html")
    tabs = _slurp("api/webui/static/course_expert/tabs.js")
    assert 'id="ce-workspace-title"' in template
    assert "function updateWorkspaceTitle(tabName)" in tabs
    assert "if (activated) updateWorkspaceTitle(tabName);" in tabs


def test_page_prepare_uses_shared_operation_helper():
    """Page push uses the shared prepare helper, not its own fetch/state."""
    page = _slurp("api/webui/static/push/page.js")
    assert 'push.prepareOnly("content.page"' in page
    assert "function postJson" not in page
    assert "function renderOperationsList" not in page


def test_operation_alias_runtime_cancellation_never_applies():
    """Runtime cancellation of prepare (via CE_WRITE_REVIEW.confirm=false) never applies."""
    core_path = ROOT / "api/webui/static/push/core.js"
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
const calls = [];
global.window = {
  CE_WRITE_REVIEW: { confirm: async () => false },
  CE_PUSH: { targetCourses: () => [{id: "101", name: "Fictional Course"}] }
};
global.document = {
  querySelector: () => ({getAttribute: () => "csrf-test"}),
  querySelectorAll: () => [],
  getElementById: () => null,
  addEventListener: () => {}
};
global.alert = () => {};
global.fetch = async (url, options) => {
  calls.push({url, body: options && options.body});
  if (url.includes("/prepare")) return {ok: true, status: 200, json: async () => ({ok: true, operation_id: "op-test"})};
  if (url.includes("/review")) return {ok: true, status: 200, json: async () => ({ok: true, batch_id: "batch-test", review_digest: "digest", frozen_reviews: [{course_name: "Fictional Course", assignment_name: "Test"}]})};
  throw new Error("unexpected fetch " + url);
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const log = {hidden: true, textContent: "", scrollTop: 0, scrollHeight: 0};
for (const alias of ["quick", "af", "pf"]) {
  await window.CE_PUSH.pushContent(alias, {name: "Test"}, log, null, {disabled: false}, "Review Test");
}
if (calls.some(c => c.url === "/api/content/push" || c.url.includes("/apply"))) process.exit(2);
if (calls.filter(c => c.url.includes("/prepare")).length !== 3) process.exit(3);
if (calls.filter(c => c.url.includes("/prepare")).some(c => JSON.parse(c.body).targets[0].course_id !== "101")) process.exit(4);
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(core_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_download_work_runtime_is_retired():
    """Course Expert must not ship the retired standalone acquisition client."""
    assert not (ROOT / "api/webui/static/push/download.js").exists()
    assert "/static/push/download.js" not in _slurp("api/webui/templates/course_expert.html")


# ── Shared Canvas-write review control ────────────────────────────────

def test_write_review_loaded_before_page_scripts():
    """write_review.js must load synchronously in <head> before any {% block content %}."""
    html = _slurp("api/webui/templates/base.html")
    wr_idx = html.index("/static/write_review.js")
    content_idx = html.index("{% block content %}")
    head_close_idx = html.index("</head>")
    assert html.count("/static/write_review.js") == 1
    assert wr_idx < content_idx, "write_review.js must load before {% block content %}"
    assert wr_idx < head_close_idx, "write_review.js must be inside <head>"
    tag_start = html.rindex("<script", 0, wr_idx)
    tag_end = html.index(">", tag_start)
    tag = html[tag_start:tag_end]
    assert "defer" not in tag
    assert "async" not in tag


def test_no_local_canvas_write_review_function():
    """No file in api/webui/static may define function canvasWriteReview."""
    import glob
    found = []
    for f in glob.glob(str(ROOT / "api/webui/static/**/*.js"), recursive=True):
        with open(f, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "function canvasWriteReview" in line:
                    found.append(f"{f}:{i}: {line.strip()}")
    assert not found, (
        "Legacy canvasWriteReview function definitions remain:\n" +
        "\n".join(found)
    )


# ── Shared course context ─────────────────────────────────────────────

def test_course_picker_uses_shared_context_without_dual_writes():
    """CoursePicker publishes to CE_CONTEXT and does not write the legacy storage key."""
    js = _slurp("api/webui/static/push/course_picker.js")
    assert "window.CE_CONTEXT" in js
    assert "context.setFocus" in js
    assert "context.setTargets" in js
    assert "authoritative: true" in js
    assert 'fetch("/api/courses")' not in js
    assert "loadAllCoursesIntoChecklist" not in js
    assert "canvasExpert.push.coursePicker.v1" not in js


def test_current_courses_are_the_only_operational_picker_scope():
    settings = _slurp("api/webui/templates/settings.html")
    canvasagent = _slurp("api/webui/templates/canvasagent.html")
    canvasagent_js = _slurp("api/webui/static/canvasagent.js")
    course_picker = _slurp("api/webui/static/push/course_picker.js")

    for heading in ("Current courses", "Previous courses", "Add courses from Canvas"):
        assert heading in settings
    assert "Move to Previous" in settings
    assert "Make Current" in settings
    assert 'href="/settings#current-courses-card"' in canvasagent
    assert "/api/work" not in canvasagent_js
    assert "/api/mirror/sync-now" in canvasagent_js
    assert "authoritative: true" in course_picker
    assert 'fetch("/api/courses")' not in course_picker


def test_canvasagent_surface_has_only_local_stdio_and_shared_buttons():
    template = _slurp("api/webui/templates/canvasagent.html")
    script = _slurp("api/webui/static/canvasagent.js")
    assert "Secure MCP Tunnel" not in template + script
    assert "tunnel-client" not in template + script
    assert 'class="button' not in template
    assert "className = \"ce-btn ce-agent-action" in script
    assert "generic-stdio-config" in template
