"""FastMCP wiring and single-process MCP attachment for Canvas Expert.

Thin ``@mcp.tool()`` wrappers delegate to the plain functions in
``tools.py`` so the tool layer stays testable without an MCP client. The
authoritative count and shape live in ``contract.TOOL_SCHEMA_VERSION`` and its
snapshot, not in prose here, so this docstring cannot drift. Run via
``api/mcp_server/__main__.py`` over stdio. The Web UI process mounts the same
FastMCP instance on loopback so a second stdio entry point can attach without
opening the stores a second time.

Token discipline: the shared privacy rules live ONCE in the server
instructions (not per tool), tool descriptions stay to a functional line or
two, and every result is serialized compactly here — FastMCP would otherwise
pretty-print dict returns with indent=2, which wastes client context on
whitespace. List data is shaped as {columns, rows} tables in ``tools.py``.
Wire character counts are a transport measure, not a per-turn token promise.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from typing import NotRequired, TypedDict

from mcp.server.fastmcp import FastMCP

from . import tools

_SERVER_INSTRUCTIONS = (
    "CanvasExpert is a local teacher-controlled runtime. Real names, Canvas/SIS "
    "ids, credentials, and private paths stay local; student rows use stable "
    "pseudonyms. Never read the Identity Vault or teacher-only Web UI routes. "
    "Rely on a catalog or mirror read only when its freshness envelope has "
    "within_policy true. Otherwise, or on catalog_not_current or an unavailable "
    "projection, ask the teacher whether Canvas work changed (not a failure). "
    "Never refresh without an explicit teacher request. "
    "For broad grading, call discover_scoring_work first: it reads every Current "
    "course's mirror and returns the complete assignment and attention set. Report "
    "all rows, wait for teacher direction, then use selected exact rows. Call "
    "prepare_scoring_session once per exact assignment. If its snapshot is over "
    "threshold, ask whether Canvas work changed; refresh only after an explicit teacher request, or retry with "
    "use_existing_mirror=true. On scoring_session_already_open, use that session and "
    "do not prepare or refresh the assignment again. Then work locally from its "
    "immutable packet. For needs_scoring_norms, ask its question and retry with "
    "bounded scoring guidance; never ask the teacher to choose a scoring transport "
    "or assignment type. An explicit score/post direction authorizes the selected "
    "discovery rows together without reconfirming each assignment, but never "
    "extends beyond those rows or another session. Read every SAFE page with "
    "get_scoring_packet, including contract/rubric; held work and evidence gaps are "
    "not empty. Submit only that packet's results with expected_packet_digest to "
    "stage_scoring_results, summarize, and call apply_staged_scoring_results only on "
    "a direct teacher instruction; never read back grades. For needs_teacher_input, "
    "ask only its questions and resubmit the same results to stage_scoring_results "
    "with the review digest and answers. Canvas Live is the review surface; "
    "list_scoring_sessions resumes work; list_feedback_contracts gives feedback "
    "rules. Across devices: handoff_work_item before switching, "
    "take_over_work_item after sync; confirm stale takeover only once the prior "
    "device stopped. For content/product, call get_authoring_contract or "
    "get_product_guide."
)

mcp = FastMCP("canvas-expert", instructions=_SERVER_INSTRUCTIONS)


class ScoringResult(TypedDict):
    """One SAFE packet row supplied to stage_scoring_results."""

    pseudonym: str
    item_id: str
    score: float | None
    feedback: str
    writing_process_observations: NotRequired[str]


def run_stdio() -> None:
    mcp.run(transport="stdio")


def _wait_for_runtime(timeout_seconds: float = 10.0) -> str | None:
    from api.local_runtime import running_runtime

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        endpoint = running_runtime(timeout=0.2)
        if endpoint:
            return endpoint
        time.sleep(0.1)
    return None


def _run_stdio_proxy(endpoint: str) -> None:
    """Serve stdio MCP while forwarding requests to the lock-owning runtime."""
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    proxy = Server("canvas-expert", instructions=_SERVER_INSTRUCTIONS)

    async def run_proxy() -> None:
        async with streamablehttp_client(endpoint + "/mcp") as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as upstream:
                await upstream.initialize()

                @proxy.list_tools()
                async def list_tools():
                    result = await upstream.list_tools()
                    return result.tools

                @proxy.call_tool()
                async def call_tool(name: str, arguments: dict):
                    return await upstream.call_tool(name, arguments or {})

                async with stdio_server() as (stdio_read, stdio_write):
                    await proxy.run(
                        stdio_read,
                        stdio_write,
                        proxy.create_initialization_options(),
                    )

    try:
        anyio.run(run_proxy)
    except Exception as exc:
        # MCP stdout is reserved for protocol frames. Keep startup diagnostics
        # local and avoid printing tool arguments, paths, or student data.
        print(f"Canvas Expert could not attach to the running local runtime ({type(exc).__name__}).",
              file=sys.stderr)
        raise SystemExit(1) from exc


def run_managed_stdio(lock=None, *, owns_lock: bool | None = None) -> None:
    """Acquire the machine lock or attach to the existing local runtime."""
    from api.local_runtime import ProcessLock, clear_runtime, publish_runtime

    lock = lock or ProcessLock()
    if owns_lock is None:
        owns_lock = lock.acquire()
    if not owns_lock:
        endpoint = _wait_for_runtime()
        if not endpoint:
            print("Canvas Expert is already running, but its local endpoint did not answer.",
                  file=sys.stderr)
            raise SystemExit(1)
        _run_stdio_proxy(endpoint)
        return

    # When MCP is the first entry point, run the normal local control console
    # in this same process. The MCP client and browser then share one owner.
    port = 8765
    publish_runtime(port)
    server = None
    server_thread = None
    try:
        import uvicorn
        from api.webui.server import app

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, name="ce-local-webui", daemon=True)
        server_thread.start()
        if not _wait_for_runtime():
            raise RuntimeError("local_runtime_start_failed")
        run_stdio()
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=5.0)
        clear_runtime()
        lock.release()


def _compact(payload: dict) -> str:
    """Serialize ourselves: compact separators, no ASCII-escaping of student
    text. The tools layer applies a final structural privacy gate before the
    response is serialized. A ``str`` return passes through FastMCP verbatim."""
    payload = tools.final_response_gate(payload)
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@mcp.tool(structured_output=False)
def list_courses() -> str:
    """Call list_courses first to get the course_id used by course-scoped tools."""
    return _compact(tools.list_courses())


@mcp.tool(structured_output=False)
def list_sis_grade_bridges(course_id: str) -> str:
    """List SIS grade bridges for one Current course_id returned by list_courses."""
    return _compact(tools.list_sis_grade_bridges(course_id))


@mcp.tool(structured_output=False)
def reconcile_sis_grade_bridges(course_id: str) -> str:
    """Discover differentiated bridge families and return a student-free status matrix."""
    return _compact(tools.reconcile_sis_grade_bridges(course_id))


@mcp.tool(structured_output=False)
def preview_sis_grade_bridge_reconciliation(
    course_id: str,
    family_title: str,
    source_assignment_ids: list[str] | None = None,
    bridge_assignment_id: str | None = None,
) -> str:
    """Agent chooses sources; teacher confirms before write."""
    return _compact(tools.preview_sis_grade_bridge_reconciliation(
        course_id,
        family_title,
        source_assignment_ids,
        bridge_assignment_id,
    ))


@mcp.tool(structured_output=False)
def preview_sis_grade_bridge(course_id: str, family_title: str) -> str:
    """Freeze and persist a local SIS grade-bridge review for one differentiated family."""
    return _compact(tools.preview_sis_grade_bridge(course_id, family_title))


@mcp.tool(structured_output=False)
def apply_sis_grade_bridge(
    operation_id: str, batch_id: str, review_digest: str
) -> str:
    """Write the exact frozen SIS grade-bridge review to Canvas.
    Use only the unchanged coordinates returned by preview_sis_grade_bridge."""
    return _compact(tools.apply_sis_grade_bridge(
        operation_id, batch_id, review_digest
    ))


@mcp.tool(structured_output=False)
def preview_grade_adjustment(
    course_id: str, assignment_id: str, adjustment: dict
) -> str:
    """Prepare a pseudonymized existing-grade adjustment for teacher review."""
    return _compact(tools.preview_grade_adjustment(
        course_id, assignment_id, adjustment
    ))


@mcp.tool(structured_output=False)
def apply_grade_adjustment(
    operation_id: str, batch_id: str, review_digest: str
) -> str:
    """Write the exact frozen grade-adjustment review to Canvas."""
    return _compact(tools.apply_grade_adjustment(
        operation_id, batch_id, review_digest
    ))


@mcp.tool(structured_output=False)
def preview_workspace_reset() -> str:
    """Dry-run the explicitly authorized local assignment/evidence workspace reset."""
    return _compact(tools.preview_workspace_reset())


@mcp.tool(structured_output=False)
def apply_workspace_reset(preview_digest: str) -> str:
    """Apply only an unchanged, non-refused workspace reset preview."""
    return _compact(tools.apply_workspace_reset(preview_digest))


@mcp.tool(structured_output=False)
def list_sections(course_id: str) -> str:
    """List a saved course's section names from the local mirror. No student data."""
    return _compact(tools.list_sections(course_id))


@mcp.tool(structured_output=False)
def list_groups(course_id: str) -> str:
    """List current-course group-set and group names from the local mirror only."""
    return _compact(tools.list_groups(course_id))


@mcp.tool(structured_output=False)
def get_course_assignments(course_id: str, full_descriptions: bool = False) -> str:
    """Read a saved course's assignments from the local course catalog.
    Descriptions are previews unless full_descriptions=true. No student data."""
    return _compact(tools.get_course_assignments(course_id, full_descriptions))


@mcp.tool(structured_output=False)
def get_modules(course_id: str, include_items: bool = False) -> str:
    """Read a saved course's modules from the local course catalog.
    Set include_items=true to include module items."""
    return _compact(tools.get_modules(course_id, include_items))


@mcp.tool(structured_output=False)
def get_course_pages(course_id: str, full_text: bool = False,
                     include_unpublished: bool = True) -> str:
    """Read course pages from the local v3 catalog, including unpublished by default.
    Set full_text=true for complete normalized bodies or include_unpublished=false to omit drafts."""
    return _compact(tools.get_course_pages(course_id, full_text, include_unpublished))


@mcp.tool(structured_output=False)
def list_learning_objectives(course_id: str) -> str:
    """Reviewed objectives for the Current course as a {columns, rows} table."""
    return _compact(tools.list_learning_objectives(course_id))


@mcp.tool(structured_output=False)
def preview_learning_objective(course_id: str, objective: str,
                               effective_start: str, effective_end: str,
                               source_refs: list, replaces: str = None) -> str:
    """Preview one evidence-grounded learning objective without writing."""
    return _compact(tools.preview_learning_objective(
        course_id, objective, effective_start, effective_end, source_refs, replaces))


@mcp.tool(structured_output=False)
def apply_learning_objective(course_id: str, preview: dict,
                             preview_digest: str, expected_revision: int) -> str:
    """Apply an exact reviewed objective preview after revision and source checks."""
    return _compact(tools.apply_learning_objective(
        course_id, preview, preview_digest, expected_revision))


@mcp.tool(structured_output=False)
def delete_learning_objective(course_id: str, entry_id: str,
                              expected_revision: int) -> str:
    """Delete one reviewed objective using an expected document revision."""
    return _compact(tools.delete_learning_objective(course_id, entry_id, expected_revision))


@mcp.tool(structured_output=False)
def get_roster(course_id: str) -> str:
    """Read a Current roster as stable one-word student stand-ins and section names."""
    return _compact(tools.get_roster(course_id))


@mcp.tool(structured_output=False)
def get_roster_student_settings(course_id: str, pseudonym: str) -> str:
    """Read one Current roster student's safe local settings by pseudonym.
    Excludes stored nicknames and all identity IDs."""
    return _compact(tools.get_roster_student_settings(course_id, pseudonym))


@mcp.tool(structured_output=False)
def preview_roster_student_change(course_id: str, pseudonym: str, patch: dict) -> str:
    """Preview a validated pseudonym-first roster settings change without writing."""
    return _compact(tools.preview_roster_student_change(course_id, pseudonym, patch))


@mcp.tool(structured_output=False)
def apply_roster_student_change(course_id: str, preview: dict,
                                preview_digest: str, expected_settings_digest: str) -> str:
    """Apply an unchanged roster preview; a canvas_group patch changes Canvas membership.
    All other supported fields stay local."""
    return _compact(tools.apply_roster_student_change(
        course_id, preview, preview_digest, expected_settings_digest))


@mcp.tool(structured_output=False)
def clear_roster_student_field(course_id: str, pseudonym: str, field: str,
                               expected_settings_digest: str) -> str:
    """Clear one supported local roster setting using a fresh hidden digest.
    Nickname fields are never clearable through MCP."""
    return _compact(tools.clear_roster_student_field(
        course_id, pseudonym, field, expected_settings_digest))


@mcp.tool(structured_output=False)
def get_submissions(course_id: str, assignment_id: str,
                    include_text: bool = True, pseudonyms: str = "",
                    max_text_chars: int = 2000) -> str:
    """Read pseudonymized mirror submissions; no live Canvas fallback or attachments.
    include_text=false returns status/scores; pseudonyms and max_text_chars narrow output."""
    return _compact(tools.get_submissions(
        course_id, assignment_id,
        include_text=include_text, pseudonyms=pseudonyms,
        max_text_chars=max_text_chars,
    ))


@mcp.tool(structured_output=False)
def get_writing_history(pseudonym: str, since: str = "", until: str = "",
                        include_text: bool = False,
                        max_text_chars: int = 2000) -> str:
    """Read one pseudonym's private Writing Record evidence across time.
    It does not score, coach, or judge work and has no course_id. since/until
    are YYYY-MM-DD. include_text=false omits student prose; max_text_chars=0
    returns it in full."""
    return _compact(tools.get_writing_history(
        pseudonym, since=since, until=until,
        include_text=include_text, max_text_chars=max_text_chars,
    ))


@mcp.tool(structured_output=False)
def get_gradebook_snapshot(course_id: str) -> str:
    """Read Current-course assignment grading counts and pseudonymized students.
    Assignment rows include ungraded and partially_scored. This tool never falls
    back to a live Canvas read."""
    return _compact(tools.get_gradebook_snapshot(course_id))


@mcp.tool(structured_output=False)
def get_authoring_contract(kind: str) -> str:
    """Return the canonical Forge authoring contract; no student data."""
    return _compact(tools.get_authoring_contract(kind))


@mcp.tool(structured_output=False)
def get_product_guide(topic: str = "") -> str:
    """Read CanvasExpert's product guide; omit topic for the overview."""
    return _compact(tools.get_product_guide(topic))


@mcp.tool(structured_output=False)
def list_staged_content(kind: str = "") -> str:
    """List drafts currently staged in the teacher's local review Inbox.
    Pass kind to filter or omit it for all drafts. No student data."""
    return _compact(tools.list_staged_content(kind))


@mcp.tool(structured_output=False)
def preview_content_push(
    course_id: str,
    kind: str,
    label: str,
    published: bool | None = None,
    module_name: str = "",
    assignment_group_name: str = "",
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
    post_to_sis: bool | None = None,
    module_id: str = "",
    create_module: bool = False,
) -> str:
    """Persist a local frozen staged quiz, assignment, or page draft; no Canvas write."""
    return _compact(tools.preview_content_push(
        course_id, kind, label,
        published=published, module_name=module_name,
        assignment_group_name=assignment_group_name,
        due_at=due_at, unlock_at=unlock_at, lock_at=lock_at,
        post_to_sis=post_to_sis, module_id=module_id,
         create_module=create_module,
    ))


@mcp.tool(structured_output=False)
def preview_differentiated_quiz_push(
    course_id: str, variants: list, published: bool = False,
    module_name: str = "", assignment_group_name: str = "", due_at: str = "",
    unlock_at: str = "", lock_at: str = "", post_to_sis: bool = False,
    module_id: str = "", create_module: bool = False,
) -> str:
    """Freeze staged QuizForge labels into a reviewed unrestricted family plan."""
    return _compact(tools.preview_differentiated_quiz_push(
        course_id, variants, published=published, module_name=module_name,
        assignment_group_name=assignment_group_name, due_at=due_at,
        unlock_at=unlock_at, lock_at=lock_at, post_to_sis=post_to_sis,
        module_id=module_id, create_module=create_module,
    ))


@mcp.tool(structured_output=False)
def apply_content_push(operation_id: str, batch_id: str, review_digest: str) -> str:
    """Create the exact frozen draft in the Canvas course its review was frozen against.
    Use only the unchanged coordinates returned by preview_content_push or
    preview_differentiated_quiz_push."""
    return _compact(tools.apply_content_push(
        operation_id, batch_id, review_digest
    ))


@mcp.tool(structured_output=False)
def preview_assignment_update(
    course_id: str,
    assignment_id: str,
    published: bool = None,
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
) -> str:
    """Freeze a publish/date patch for one existing Canvas assignment by id, refused with no Canvas call if no field is supplied."""
    return _compact(tools.preview_assignment_update(
        course_id, assignment_id, published=published,
        due_at=due_at, unlock_at=unlock_at, lock_at=lock_at,
    ))


@mcp.tool(structured_output=False)
def apply_assignment_update(operation_id: str, batch_id: str, review_digest: str) -> str:
    """Write the frozen assignment patch preview_assignment_update returned, blocked as drift_detected if the assignment changed since preview."""
    return _compact(tools.apply_assignment_update(
        operation_id, batch_id, review_digest
    ))


@mcp.tool(structured_output=False)
def stage_content(kind: str, label: str, content: str) -> str:
    """Stage one completed Forge envelope in the teacher's review Inbox; no Canvas write."""
    return _compact(tools.stage_content(kind, label, content))


@mcp.tool(structured_output=False)
def push_content_live(
    course_id: str,
    kind: str,
    label: str,
    content: str,
    published: bool | None = None,
    module_name: str = "",
    assignment_group_name: str = "",
    post_to_sis: bool | None = None, module_id: str = "", create_module: bool = False,
) -> str:
    """Stage and create one authored draft in Canvas; dates use the preview pair."""
    return _compact(tools.push_content_live(
        course_id, kind, label, content,
        published=published, module_name=module_name,
        assignment_group_name=assignment_group_name,
        post_to_sis=post_to_sis, module_id=module_id,
        create_module=create_module,
    ))


@mcp.tool(structured_output=False)
def verify_live(course_id: str, kind: str, id: str = "", title: str = "") -> str:
    """The one cheap Live check an agent makes after a push, by exact id or exact title.
    One Canvas call (two only when module_ids isn't already on the object); writes nothing."""
    return _compact(tools.verify_live(course_id, kind, id=id, title=title))


@mcp.tool(structured_output=False)
def resume_operation(operation_id: str) -> str:
    """Continue one existing, teacher-approved operation from its last recorded step.
    Not a new write capability -- refuses an operation that already applied, was
    abandoned, or is held by another attempt."""
    return _compact(tools.resume_operation(operation_id))


@mcp.tool(structured_output=False)
def abandon_operation(operation_id: str) -> str:
    """Mark one existing, teacher-approved operation abandoned; makes no Canvas call.
    Blocks later resume_operation or apply and returns a repair_plan of what it already created."""
    return _compact(tools.abandon_operation(operation_id))


@mcp.tool(structured_output=False)
def refresh_mirror(course_id: str) -> str:
    """Refresh a saved course's local CanvasMirror only after a read refuses as stale.
    It reports sync status, never data; after a successful sync, retry the refused read."""
    return _compact(tools.refresh_mirror(course_id))


@mcp.tool(structured_output=False)
def refresh_course_structure(course_id: str) -> str:
    """Refresh the local student-free Course Catalog module structure."""
    return _compact(tools.refresh_course_structure(course_id))


@mcp.tool(structured_output=False)
def list_feedback_contracts() -> str:
    """List teacher feedback contracts."""
    return _compact(tools.list_feedback_contracts())


@mcp.tool(structured_output=False)
def discover_scoring_work() -> str:
    """Discover grading work without preparing or writing."""
    return _compact(tools.discover_scoring_work())


@mcp.tool(structured_output=False)
def prepare_scoring_session(course_id: str, assignment_id: str,
                            scoring_guidance: str = "",
                            use_existing_mirror: bool = False,
                            scoring_guidance_provenance: str = "",
                            feedback_contract_id: str = "") -> str:
    """Prepare one exact assignment for packet paging or return a typed blocker."""
    return _compact(tools.prepare_scoring_session(
        course_id, assignment_id, scoring_guidance, use_existing_mirror,
        scoring_guidance_provenance, feedback_contract_id))


@mcp.tool(structured_output=False)
def list_scoring_sessions() -> str:
    """List identity-free Scoring Session summaries."""
    return _compact(tools.list_scoring_sessions())


@mcp.tool(structured_output=False)
def list_work_items() -> str:
    """List shared in-flight work, current holder, sync progress, and orphan count."""
    return _compact(tools.list_work_items())


@mcp.tool(structured_output=False)
def get_work_item(work_id: str) -> str:
    """Get one work item's holder and event-sync status without its contents."""
    return _compact(tools.get_work_item(work_id))


@mcp.tool(structured_output=False)
def take_over_work_item(work_id: str, confirm_stale: bool = False) -> str:
    """Take a released work item, or confirm takeover after its old lease goes stale."""
    return _compact(tools.take_over_work_item(work_id, confirm_stale=confirm_stale))


@mcp.tool(structured_output=False)
def handoff_work_item(work_id: str) -> str:
    """Release this device's work lease before continuing on another device."""
    return _compact(tools.handoff_work_item(work_id))


@mcp.tool(structured_output=False)
def get_scoring_packet(scoring_session_id: str, offset: int = 0, limit: int = 10,
                       include_context: bool = True) -> str:
    """Read one page of the SAFE packet; read every page before staging."""
    return _compact(tools.get_scoring_packet(
        scoring_session_id, offset, limit, include_context))


@mcp.tool(structured_output=False)
def stage_scoring_results(
    scoring_session_id: str,
    results: list[ScoringResult],
    expected_packet_digest: str,
    review_digest: str = "",
    answers: dict[str, str] | None = None,
) -> str:
    """Validate and stage SAFE results locally; no Canvas write."""
    return _compact(tools.stage_scoring_results(
        scoring_session_id, results, expected_packet_digest, review_digest, answers))


@mcp.tool(structured_output=False)
def apply_staged_scoring_results(scoring_session_id: str,
                                 expected_stage_digest: str,
                                 idempotency_key: str = "") -> str:
    """Post the unchanged private stage to Canvas after direct teacher instruction."""
    return _compact(tools.apply_staged_scoring_results(
        scoring_session_id, expected_stage_digest, idempotency_key))


@mcp.tool(structured_output=False)
def reset_scoring_review(scoring_session_id: str) -> str:
    """Reopen the current local scoring review."""
    return _compact(tools.reset_scoring_review(scoring_session_id))


def _strip_generated_schema_titles(mcp_server) -> int:
    """Drop pydantic's generated ``title`` from every tool's input schema.

    FastMCP derives each schema from the function signature, and pydantic
    labels every property with a title made from that property's own name, so
    ``course_id`` ships as ``{"title": "Course Id", "type": "string"}``. The
    key already said that. It is nobody's authored text and it is a large part
    of the tool listing, so it is the cheapest wire weight on this surface to
    lose. Client and model token treatment varies.

    Result transport is explicitly text-only on every tool, so FastMCP does not
    advertise an output schema or structured result alongside the text. The
    input schema itself stays intact: names, types, defaults, and required lists
    remain available to clients.

    Names, types, defaults, and required lists are untouched, and the frozen
    ``tool_schema_vN.json`` snapshots record only property types, so no
    contract version moves. Runs once at import, after every tool is
    registered.
    """
    def _strip(node) -> int:
        stripped = 0
        if isinstance(node, dict):
            if node.pop("title", None) is not None:
                stripped += 1
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    # This dict's own keys are parameter names -- one may be
                    # literally "title" (e.g. verify_live) -- not generated
                    # title metadata, so only recurse into each property's
                    # own schema rather than popping this dict's keys.
                    for prop_schema in value.values():
                        stripped += _strip(prop_schema)
                else:
                    stripped += _strip(value)
        elif isinstance(node, list):
            for value in node:
                stripped += _strip(value)
        return stripped

    removed = 0
    registry = getattr(getattr(mcp_server, "_tool_manager", None), "_tools", {}) or {}
    for tool in registry.values():
        for attribute in ("parameters", "output_schema"):
            schema = getattr(tool, attribute, None)
            if isinstance(schema, dict):
                removed += _strip(schema)
    return removed


_STRIPPED_SCHEMA_TITLES = _strip_generated_schema_titles(mcp)
