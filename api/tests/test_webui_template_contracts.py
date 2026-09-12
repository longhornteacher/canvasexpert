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
        "rf": "content.rubric",
    }.items():
        assert f'{alias}: "{kind}"' in core
    for rel in (
        "api/webui/static/push/assignment.js",
        "api/webui/static/push/page.js",
        "api/webui/static/push/rubric.js",
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
for (const alias of ["quick", "af", "pf", "rf"]) {
  await window.CE_PUSH.pushContent(alias, {name: "Test"}, log, null, {disabled: false}, "Review Test");
}
if (calls.some(c => c.url === "/api/content/push" || c.url.includes("/apply"))) process.exit(2);
if (calls.filter(c => c.url.includes("/prepare")).length !== 4) process.exit(3);
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
    dashboard = _slurp("api/webui/templates/dashboard.html")
    desk = _slurp("api/webui/static/desk.js")
    gradebook = _slurp("api/webui/static/gradebook.js")
    course_picker = _slurp("api/webui/static/push/course_picker.js")

    for heading in ("Current courses", "Previous courses", "Add courses from Canvas"):
        assert heading in settings
    assert "Move to Previous" in settings
    assert "Make Current" in settings
    assert "window.CE_CONTEXT" not in desk
    assert "context.setFocus" not in desk
    assert "context.setTargets" not in desk
    assert "reconcile(" not in desk
    assert "data-desk-start" not in dashboard
    for text in (
        "Sync now", "Checking Canvas sync…", "Syncing Canvas data…",
        "Canvas data synced ",
    ):
        assert text in dashboard or text in desk
    assert "authoritative: true" in course_picker
    assert 'fetch("/api/courses")' not in course_picker
    assert 'fetch("/api/courses")' not in gradebook


def test_desk_runtime_keeps_semantic_presentation_after_refresh():
    desk_path = ROOT / "api/webui/static/desk.js"
    script = r'''
import fs from "node:fs";
import vm from "node:vm";

class Node {
  constructor(tag, id = "") {
    this.tagName = tag;
    this.id = id;
    this.children = [];
    this.dataset = {};
    this.className = "";
    this.textContent = "";
    this.options = [];
    this.value = "";
    this.selectedIndex = 0;
  }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter(item => item !== child); }
  get firstChild() { return this.children[0] || null; }
  addEventListener() {}
  focus() {}
}

const job = {
  job_id: "job-runtime",
  material_version: "material-runtime",
  origin: "intentional",
  kind: "grade.powergrader",
  status: "attention",
  title: "PowerGrader work",
  attention_reason: "Work needs attention",
  resumable_url: "/powergrader/session/runtime",
  counts: {total: 24, pending: 22, affected: 2}
};
const presentation = {
  course_label: "Fictional Course",
  title: "Fictional Reflection",
  summary: "24 students · 22 awaiting review · 2 approved, not posted",
  action_label: "Review & post"
};
const initialData = new Node("script", "desk-initial-data");
initialData.textContent = JSON.stringify({
  jobs: [job], presentations: {"job-runtime": presentation}, operations: [], receipts: []
});
const elements = {
  "desk-root": new Node("div", "desk-root"),
  "desk-initial-data": initialData,
  "desk-continue-list": new Node("div", "desk-continue-list"),
  "desk-attention-list": new Node("div", "desk-attention-list"),
  "desk-prepared-list": new Node("div", "desk-prepared-list"),
  "desk-receipts-list": new Node("div", "desk-receipts-list")
};

global.window = {
  prompt: () => null,
  confirm: () => false
};
global.document = {
  getElementById: id => elements[id] || null,
  createElement: tag => new Node(tag),
  querySelector: selector => selector.includes("csrf") ? {content: "csrf"} : null,
  querySelectorAll: () => [],
  addEventListener: () => {}
};
global.fetch = async url => ({
  ok: true,
  json: async () => {
    if (url.startsWith("/api/work")) {
      return {ok: true, jobs: [job], presentations: {"job-runtime": presentation}};
    }
    if (url === "/api/operations") return {ok: true, operations: []};
    if (url === "/api/receipts") return {ok: true, receipts: []};
    throw new Error("unexpected fetch " + url);
  }
});

function textOf(node) {
  return [node.textContent, ...node.children.flatMap(textOf)].filter(Boolean).join(" | ");
}

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const initialText = textOf(elements["desk-attention-list"]);
await new Promise(resolve => setTimeout(resolve, 0));
const refreshedText = textOf(elements["desk-attention-list"]);
for (const rendered of [initialText, refreshedText]) {
  if (!rendered.includes("Fictional Course")) process.exit(2);
  if (!rendered.includes("Fictional Reflection")) process.exit(3);
  if (!rendered.includes("24 students · 22 awaiting review · 2 approved, not posted")) process.exit(4);
  if (!rendered.includes("Review & post")) process.exit(5);
  if (rendered.includes("grade.powergrader") || rendered.includes("24 items")) process.exit(6);
}
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(desk_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
