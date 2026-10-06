"""FastMCP wiring and single-process MCP attachment for Canvas Expert.

Thin ``@mcp.tool()`` wrappers delegate to the plain functions in
``tools.py`` so the tool layer stays testable without an MCP client. The
authoritative count and shape live in ``contract.TOOL_SCHEMA_VERSION`` and its
snapshot, not in prose here, so this docstring cannot drift. Run via
``api/mcp_server/__main__.py`` over stdio. The runtime host serves the same
FastMCP instance on loopback so a second stdio entry point can attach without
opening the stores a second time; the browser console is optional.

Token discipline: the shared privacy rules live ONCE in the server
instructions (not per tool), tool descriptions stay to a functional line or
two, and every result is serialized compactly here — FastMCP would otherwise
pretty-print dict returns with indent=2, which wastes client context on
whitespace. List data is shaped as {columns, rows} tables in ``tools.py``.
Wire character counts are a transport measure, not a per-turn token promise.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from typing import Literal, NotRequired, TypedDict
from functools import wraps

from api import operational_log

from mcp.server.fastmcp import FastMCP

from . import tools

_SERVER_INSTRUCTIONS = (
    "Local teacher-controlled runtime; identities, credentials and private paths stay local; "
    "student rows are pseudonyms. Never read Identity Vault or teacher-only Web UI routes. "
    "Use within_policy catalog/mirror as is; outside policy refresh_mirror yourself "
    "(structure_only=true for catalog), then continue. Tell the teacher when new/resubmitted work arrives. "
    "For broad grading, discover_scoring_work reads every Current course mirror: report all "
    "assignment and attention rows, wait for teacher direction. Prepare once per selected exact "
    "assignment; refresh refusals need refresh_mirror and retry (use_existing_mirror=true only "
    "if the teacher says nothing changed). On scoring_session_already_open, continue that session. "
    "For needs_scoring_norms ask its bounded question; never ask the teacher to choose a scoring transport. An explicit score/post direction authorizes the "
    "selected discovery rows together without reconfirming each assignment, never extends beyond those rows or another session. "
    "Read every SAFE get_scoring_packet page including contract/rubric; held work and evidence gaps are not empty. "
    "Stage exact packet results via stage_scoring_results with expected_packet_digest. Integrity concerns and teacher-only "
    "notes belong in agent_commentary, with evidence. For needs_teacher_input ask only its questions "
    "and resubmit unchanged results with review digest and answers. "
    "Show get_scoring_preview in your conversation: warnings first, comments exactly as returned, "
    "agent commentary separately in a yellow block labeled \"Agent commentary (teacher only)\". "
    "Edits mean restaging. Before any apply_* or push_content_live say what changes and warnings, "
    "then wait for the teacher's go; one go may cover selected rows/assignments; "
    "apply_staged_scoring_results only when told to push. "
    "Canvas Live is the record and later edit surface. Graded feedback uses the same scoring tools "
    "with prepare_scoring_session(mode='feedback_revision'); scores stay fixed. "
    "Across devices: transfer_work_item(action='hand_off') before switching and action='take_over' "
    "after sync; confirm_stale only once the prior device stopped. Content: get_product_guide with "
    "quiz, assignment or page topic. Attachments: exact canvas_file name or stage_attachment(source_path); "
    "never pass bytes. No local path: teacher chooses Canvas Files."
)


mcp = FastMCP("canvas-expert", instructions=_SERVER_INSTRUCTIONS)


def _result_outcome(result) -> str:
    """Classify diagnostics without imposing a result shape on tool dispatch."""
    try:
        text = result if isinstance(result, str) else result[0].text
        payload = json.loads(text)
        return "ok" if payload.get("ok") else "refused"
    except Exception:
        return "error"


def _log_tool_calls(manager) -> None:
    """Wrap registered dispatch once, including argument validation, without payload logging."""
    call_tool = manager.call_tool

    @wraps(call_tool)
    async def logged(name, arguments, *args, **kwargs):
        tool = manager.get_tool(name)
        if tool is None:
            # An unknown caller-controlled string is not an allowlisted tool name.
            return await call_tool(name, arguments, *args, **kwargs)
        started = time.perf_counter()
        outcome = "error"
        error_class = None
        result = None
        try:
            result = await call_tool(name, arguments, *args, **kwargs)
            return result
        except Exception as error:
            error_class = type(error.__cause__ or error)
            raise
        finally:
            if error_class is None:
                outcome = _result_outcome(result)
            fields = {"duration_ms": int((time.perf_counter() - started) * 1000)}
            if error_class is not None:
                fields["error_class"] = error_class
            operational_log.emit(f"mcp.tool.{tool.name}", outcome, **fields)

    manager.call_tool = logged


class RevisionResult(TypedDict):
    __pydantic_config__ = {"extra": "allow"}

    pseudonym: str
    comment_key: str
    feedback: str


class ScoringResult(TypedDict):
    """One SAFE packet row supplied to stage_scoring_results."""

    __pydantic_config__ = {"extra": "allow"}

    pseudonym: str
    item_id: str
    score: float | None
    feedback: str
    agent_commentary: NotRequired[str]
    insincere: NotRequired[bool]
    late_days: NotRequired[int]


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

    from api import runtime

    # The loopback host is optional for this owner: stdio remains useful even
    # when port 8765 is unavailable or the console has an import defect.
    port = 8765
    server = None
    server_thread = None
    published = False
    try:
        runtime.start()
        try:
            import uvicorn
            from api.runtime_host import create_host_app

            host_app = create_host_app()
            config = uvicorn.Config(
                host_app, host="127.0.0.1", port=port,
                log_level="critical", access_log=False,
            )
            server = uvicorn.Server(config)
            server_thread = threading.Thread(
                target=server.run, name="ce-local-runtime-host", daemon=True
            )
            server_thread.start()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and server_thread.is_alive() and not server.started:
                time.sleep(0.05)
            if server.started:
                publish_runtime(port)
                published = True
                if not _wait_for_runtime(timeout_seconds=2.0):
                    clear_runtime()
                    published = False
                    print(
                        "Canvas Expert local console and second-agent attach are unavailable.",
                        file=sys.stderr,
                    )
            else:
                print(
                    "Canvas Expert local console and second-agent attach are unavailable.",
                    file=sys.stderr,
                )
        except Exception:
            print("Canvas Expert local console and second-agent attach are unavailable.", file=sys.stderr)
        run_stdio()
    finally:
        try:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=5.0)
        finally:
            try:
                if published:
                    clear_runtime()
            finally:
                try:
                    runtime.stop()
                finally:
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
def reconcile_sis_grade_bridges(course_id: str) -> str:
    """Discover differentiated bridge families and return a student-free status matrix."""
    return _compact(tools.reconcile_sis_grade_bridges(course_id))




@mcp.tool(structured_output=False)
def preview_sis_grade_bridge(course_id: str, family_title: str, reconcile: bool=False, bridge_assignment_id: str | None=None, source_assignment_ids: list[str] | None=None) -> str:
    'Persist a local SIS bridge projection or reconciliation review.'
    return _compact(tools.preview_sis_grade_bridge(course_id=course_id, family_title=family_title, reconcile=reconcile, bridge_assignment_id=bridge_assignment_id, source_assignment_ids=source_assignment_ids))




@mcp.tool(structured_output=False)
def preview_grade_adjustment(
    course_id: str, assignment_id: str, adjustment: dict
) -> str:
    """Prepare a pseudonymized existing-grade adjustment for teacher review.
    adjustment: {kind:rule,model,settings} | {kind:explicit,entries} | {kind:revert,operation_id}."""
    return _compact(tools.preview_grade_adjustment(
        course_id, assignment_id, adjustment
    ))




@mcp.tool(structured_output=False)
def preview_attempts_grant(course_id: str, assignment_id: str, grant: dict) -> str:
    """Prepare a pseudonymized extra-attempts or reopen grant for review; no Canvas write.
    grant: students "all" or pseudonyms; extra_attempts 1-100 or "unlimited"; reopen {due_at, lock_at}."""
    return _compact(tools.preview_attempts_grant(course_id, assignment_id, grant))


















@mcp.tool(structured_output=False)
def get_roster(course_id: str, pseudonym: str | None=None, include: list[str] | None=None) -> str:
    "Read stand-ins, selected sections/groups, or one pseudonym's local settings."
    return _compact(tools.get_roster(course_id=course_id, pseudonym=pseudonym, include=include))




@mcp.tool(structured_output=False)
def preview_roster_student_change(course_id: str, pseudonym: str, patch: dict) -> str:
    """Preview a validated pseudonym-first roster settings change without writing."""
    return _compact(tools.preview_roster_student_change(course_id, pseudonym, patch))


@mcp.tool(structured_output=False)
def apply_roster_student_change(course_id: str, preview: dict,
                                preview_digest: str, expected_settings_digest: str) -> str:
    """Apply an unchanged preview of a local roster settings change."""
    return _compact(tools.apply_roster_student_change(
        course_id, preview, preview_digest, expected_settings_digest))




@mcp.tool(structured_output=False)
def get_submissions(course_id: str, assignment_id: str, include_text: bool=True, pseudonyms: str='', max_text_chars: int | None=None, history: bool=False, offset: int | None=None, limit: int | None=None) -> str:
    'Read pseudonymized submissions or paginated retained history from local stores.'
    return _compact(tools.get_submissions(course_id=course_id, assignment_id=assignment_id, include_text=include_text, pseudonyms=pseudonyms, max_text_chars=max_text_chars, history=history, offset=offset, limit=limit))


@mcp.tool(structured_output=False)
def get_assignment_evidence(course_id: str, assignment_id: str, view: str='attachments', offset: int=0, limit: int=50) -> str:
    'Read one assignment\'s durable attachments, comparisons, or notes from the local evidence store.'
    return _compact(tools.get_assignment_evidence(course_id=course_id, assignment_id=assignment_id, view=view, offset=offset, limit=limit))








@mcp.tool(structured_output=False)
def get_score_ledger(course_id: str, assignment_id: str,
                     pseudonyms: str = "", offset: int = 0, limit: int = 50) -> str:
    """Read bounded pseudonymized score evidence from the private local archive."""
    return _compact(tools.get_score_ledger(course_id, assignment_id, pseudonyms, offset, limit))


@mcp.tool(structured_output=False)
def get_gradebook_snapshot(course_id: str) -> str:
    """Read Current-course assignment grading counts and pseudonymized students.
    Assignment rows include ungraded and partially_scored. This tool never falls
    back to a live Canvas read."""
    return _compact(tools.get_gradebook_snapshot(course_id))




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
def preview_content_push(course_id: str, kind: str='quiz', label: str='', published: bool | None=None, module_name: str='', assignment_group_name: str='', due_at: str='', unlock_at: str='', lock_at: str='', post_to_sis: bool | None=None, module_id: str='', create_module: bool=False, variants: list | None=None, quiz_settings: dict | None=None) -> str:
    'Persist a local review of one staged draft or differentiated quiz variants.'
    return _compact(tools.preview_content_push(course_id=course_id, kind=kind, label=label, published=published, module_name=module_name, assignment_group_name=assignment_group_name, due_at=due_at, unlock_at=unlock_at, lock_at=lock_at, post_to_sis=post_to_sis, module_id=module_id, create_module=create_module, variants=variants, quiz_settings=quiz_settings))






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
def stage_content(kind: str, label: str, content: str) -> str:
    """Stage one completed Forge envelope in the teacher's review Inbox; no Canvas write."""
    return _compact(tools.stage_content(kind, label, content))


@mcp.tool(structured_output=False)
def stage_attachment(source_path: str) -> str:
    """Stage a local Forge attachment.
    Returns name and size only."""
    return _compact(tools.stage_attachment(source_path))


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
    quiz_settings: dict | None = None,
) -> str:
    """Stage and create one authored draft in Canvas; dates use the preview pair."""
    return _compact(tools.push_content_live(
        course_id, kind, label, content,
        published=published, module_name=module_name,
        assignment_group_name=assignment_group_name,
        post_to_sis=post_to_sis, module_id=module_id,
        create_module=create_module, quiz_settings=quiz_settings,
    ))


@mcp.tool(structured_output=False)
def verify_live(course_id: str, kind: str, id: str = "", title: str = "") -> str:
    """The one cheap Live check an agent makes after a push, by exact id or exact title.
    One Canvas call (two only when module_ids isn't already on the object); writes nothing."""
    return _compact(tools.verify_live(course_id, kind, id=id, title=title))


@mcp.tool(structured_output=False)
def resume_operation(operation_id: str) -> str:
    """Resume a teacher-approved operation from its last recorded step; not a new write.
    Refuses applied, abandoned, or another attempt's held operation."""
    return _compact(tools.resume_operation(operation_id))


@mcp.tool(structured_output=False)
def abandon_operation(operation_id: str) -> str:
    """Mark one existing, teacher-approved operation abandoned; makes no Canvas call.
    Blocks later resume_operation or apply and returns a repair_plan of what it already created."""
    return _compact(tools.abandon_operation(operation_id))


@mcp.tool(structured_output=False)
def refresh_mirror(course_id: str, include_comments: bool=False, structure_only: bool=False,
                   operation_id: str = "") -> str:
    'Refresh a saved course mirror, or its catalog with structure_only.'
    return _compact(tools.refresh_mirror(course_id=course_id, include_comments=include_comments,
                                         structure_only=structure_only, operation_id=operation_id))




@mcp.tool(structured_output=False)
def list_feedback_contracts() -> str:
    """List teacher feedback contracts."""
    return _compact(tools.list_feedback_contracts())


@mcp.tool(structured_output=False)
async def discover_scoring_work() -> str:
    """Discover grading work without preparing or writing."""
    return _compact(await asyncio.to_thread(tools.discover_scoring_work))


@mcp.tool(structured_output=False)
def prepare_scoring_session(course_id: str, assignment_id: str, scoring_guidance: str='', use_existing_mirror: bool=False, scoring_guidance_provenance: str='', feedback_contract_id: str='', late_policy: str='', mode: str='score') -> str:
    'Prepare a local scoring or feedback_revision session for one exact assignment.'
    return _compact(tools.prepare_scoring_session(course_id=course_id, assignment_id=assignment_id, scoring_guidance=scoring_guidance, use_existing_mirror=use_existing_mirror, scoring_guidance_provenance=scoring_guidance_provenance, feedback_contract_id=feedback_contract_id, late_policy=late_policy, mode=mode))


@mcp.tool(structured_output=False)
def refresh_scoring_session(scoring_session_id: str, use_existing_mirror: bool = False,
                            replace_resubmitted: bool = False) -> str:
    """Add late or resubmitted mirror work to an open Scoring Session; tell the teacher what it added."""
    return _compact(tools.refresh_scoring_session(
        scoring_session_id, use_existing_mirror, replace_resubmitted))






class FeedbackRevision(TypedDict):
    pseudonym: str
    comment_key: str
    feedback: str






@mcp.tool(structured_output=False)
def list_scoring_sessions() -> str:
    """List identity-free Scoring Session summaries."""
    return _compact(tools.list_scoring_sessions())


@mcp.tool(structured_output=False)
def list_work_items(work_id: str | None=None) -> str:
    'Read shared work holders and sync status, or one work_id detail.'
    return _compact(tools.list_work_items(work_id=work_id))








@mcp.tool(structured_output=False)
def get_scoring_packet(scoring_session_id: str, offset: int=0, limit: int=10, include_context: bool | None=None) -> str:
    'Read one SAFE packet page for scoring or existing-comment revision.'
    return _compact(tools.get_scoring_packet(scoring_session_id=scoring_session_id, offset=offset, limit=limit, include_context=include_context))


@mcp.tool(structured_output=False)
def stage_scoring_results(scoring_session_id: str, results: list[ScoringResult | RevisionResult], expected_packet_digest: str, review_digest: str='', answers: dict | None=None, grade_mode: str | None=None, attachment_file: str | None=None) -> str:
    'Freeze scoring results or comment revisions locally; no Canvas write.'
    return _compact(tools.stage_scoring_results(scoring_session_id=scoring_session_id, results=results, expected_packet_digest=expected_packet_digest, review_digest=review_digest, answers=answers, grade_mode=grade_mode, attachment_file=attachment_file))


@mcp.tool(structured_output=False)
def get_scoring_preview(scoring_session_id: str, offset: int = 0, limit: int = 25) -> str:
    """Read a page of the staged review exactly as Canvas will receive it, with warnings."""
    return _compact(tools.get_scoring_preview(scoring_session_id, offset, limit))


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


@mcp.tool(structured_output=False)
def apply_operation(operation_id: str, batch_id: str, review_digest: str) -> str:
    'Write the exact frozen operation to Canvas through its existing owner.'
    return _compact(tools.apply_operation(operation_id=operation_id, batch_id=batch_id, review_digest=review_digest))


@mcp.tool(structured_output=False)
def get_course_content(course_id: str, kind: str, full_descriptions: bool | None=None, full_text: bool | None=None, include_unpublished: bool | None=None, include_items: bool | None=None) -> str:
    'Read local catalog assignments, pages or modules with kind-specific options.'
    return _compact(tools.get_course_content(course_id=course_id, kind=kind, full_descriptions=full_descriptions, full_text=full_text, include_unpublished=include_unpublished, include_items=include_items))


@mcp.tool(structured_output=False)
def transfer_work_item(work_id: str, action: str, confirm_stale: bool=False) -> str:
    'Take over or hand off one shared work lease after sync.'
    return _compact(tools.transfer_work_item(work_id=work_id, action=action, confirm_stale=confirm_stale))


@mcp.tool(structured_output=False)
def set_score_curve_rule(course_id: str, formula: dict | None=None, assignment_id: str | None=None, rule_id: str | None=None, active: bool=True) -> str:
    'Create or deactivate a local score curve rule without changing Canvas grades.'
    return _compact(tools.set_score_curve_rule(course_id=course_id, formula=formula, assignment_id=assignment_id, rule_id=rule_id, active=active))


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


_log_tool_calls(mcp._tool_manager)
