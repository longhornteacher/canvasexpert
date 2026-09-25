"""The CanvasAgent instruction set, and the retirement of what it replaced.

This file is pasted into a teacher's AI assistant and is the assistant's only
source of truth about CanvasExpert, so a claim in it that no longer matches the
code is worse than no claim at all: the assistant will state it confidently.
These tests pin the claims that can drift.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

from api.webui import ai_ta


AGENT_NAME = "START HERE - CanvasAgent.txt"
AGENT_PATH = os.path.join(ai_ta.DEFAULT_AI_TA_DIR, AGENT_NAME)
CORE_BEGIN = "CORE: begin"
CORE_END = "CORE: end"

# Longest custom-instructions field we are willing to assume a teacher has.
# ChatGPT's per-box limit is the binding constraint at 1500 characters.
CORE_CHAR_BUDGET = 1500


@pytest.fixture(scope="module")
def text() -> str:
    with open(AGENT_PATH, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def core(text) -> str:
    body = text.split(CORE_BEGIN, 1)[1].split(CORE_END, 1)[0]
    return body.strip("= \n")


def test_the_instruction_set_ships(text):
    assert text.strip(), f"{AGENT_NAME} is empty"


def test_core_block_is_delimited_both_ends(text):
    assert text.count(CORE_BEGIN) == 1
    assert text.count(CORE_END) == 1
    assert text.index(CORE_BEGIN) < text.index(CORE_END)


def test_core_block_fits_a_custom_instructions_box(core):
    """The core exists specifically to be pasted where length is capped."""
    assert len(core) <= CORE_CHAR_BUDGET, (
        f"CORE is {len(core)} chars, over the {CORE_CHAR_BUDGET} budget, so it no "
        "longer fits the box it exists for"
    )


def test_no_em_dashes_or_smart_punctuation(text):
    """House style, and this text gets mirrored back by whatever reads it."""
    banned = {"—": "em-dash", "–": "en-dash", "‘": "curly quote",
              "’": "curly apostrophe", "“": "curly quote",
              "”": "curly quote"}
    found = {name for ch, name in banned.items() if ch in text}
    assert not found, f"found {sorted(found)}"


def test_stays_ascii(text):
    """Pasted through unknown chat clients and read back on a cp1252 console."""
    offenders = sorted({ch for ch in text if ord(ch) > 127})
    assert not offenders, f"non-ASCII characters: {[hex(ord(c)) for c in offenders]}"


def test_the_write_default_is_in_the_core(core):
    """The core must carry the exact Scoring Session authorization boundary."""
    lowered = core.lower()
    assert "scoring session" in lowered
    assert "stage_scoring_results" in lowered
    assert "apply_staged_scoring_results" in lowered
    assert "direct teacher" in lowered
    assert "canvas live" in lowered


def test_core_routes_to_every_appendix(text, core):
    """CORE carries the loop and delegates the rest, which is the only way it
    fits its budget. An appendix CORE never names is unreachable for the
    teacher who pasted CORE alone, and invisible to the assistant."""
    letters = sorted(set(re.findall(r"^Appendix ([A-Z])\.", text, re.M)))
    assert letters, "no appendices found"
    missing = [letter for letter in letters
               if not re.search(rf"\b{letter} [a-z]|Appendix {letter}\b", core)]
    assert not missing, f"CORE routes to no appendix for {missing}"


def test_core_carries_the_tracked_choice(core):
    """The assistant must ask rather than infer, and cannot ask about a choice
    it was never told exists. This line was dropped once for budget; the
    concision pass that made room for it is what this pins."""
    lowered = core.lower()
    assert "tracked" in lowered and "not tracked" in lowered


def test_core_names_every_envelope_tag(core):
    """An assistant that guesses a tag produces a file that cannot validate."""
    for tag in ("QUIZFORGE_JSON", "ASSIGNMENTFORGE_JSON", "PAGEFORGE_JSON"):
        assert tag in core, f"CORE does not name {tag}"


def test_assignment_and_page_contracts_are_2_0_content_only():
    """Forge agents provide content; the renderer owns student-facing layout."""
    root = Path(REPO_ROOT)
    assignment = (root / "api" / "default_docs" / "AI Authoring" /
                  "Author an Assignment (AssignmentForge).txt").read_text(encoding="utf-8")
    page = (root / "api" / "default_docs" / "AI Authoring" /
            "Author a Page (PageForge).txt").read_text(encoding="utf-8")
    for body, tag in ((assignment, "ASSIGNMENTFORGE_JSON"),
                      (page, "PAGEFORGE_JSON")):
        assert "2.0-json" in body
        assert "1.0-json` is retired" in body
        assert f"<{tag}>" in body
        assert "Author content" in body
        assert "Canvas Expert" in body and "palette" in body
        assert "style attributes" in body
        assert "{{file:" in body and "{{page:" in body
        assert "resolved per course at push time" not in body
    assert '"response": "short"' in assignment
    assert '"lines": 3' in assignment
    assert "missing rubric" in assignment.lower() and "teacher" in assignment.lower()
    assert '"layout": "standard"' in page
    assert '"layout": "freeform"' in page


def test_canvasagent_and_magicschool_setup_do_not_request_forge_styling():
    root = Path(REPO_ROOT)
    ai_authoring = root / "api" / "default_docs" / "AI Authoring"
    start_here = (ai_authoring / "START HERE - CanvasAgent.txt").read_text(encoding="utf-8")
    assert "Canvas Expert renders its presentation" in start_here
    setup_paths = (
        ai_authoring / "MagicSchool Toolkit" / "Assignment Author — SETUP.txt",
        ai_authoring / "MagicSchool Toolkit" / "Page Author — SETUP.txt",
        ai_authoring / "MagicSchool Toolkit" / "Quiz Author — SETUP.txt",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in setup_paths).casefold()
    assert "heading style" not in combined
    assert "describe style" not in combined


def test_every_envelope_tag_claimed_is_one_the_code_actually_reads(text):
    """Guards against the doc naming a tag the parsers do not accept."""
    claimed = set(re.findall(r"<([A-Z]+FORGE_JSON)>", text))
    assert claimed, "no envelope tags found in the instruction set"
    haystack = ""
    for root, _, files in os.walk(ai_ta.API_DIR):
        if "__pycache__" in root or os.sep + "tests" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8", errors="ignore") as f:
                    haystack += f.read()
    unknown = sorted(t for t in claimed if t not in haystack)
    assert not unknown, f"instruction set names tags no code reads: {unknown}"


def test_every_mcp_tool_named_is_a_real_tool(text):
    """The doc tells the assistant to call these by name, so they must exist."""
    from api.mcp_server import tools

    named = set(re.findall(r"\b((?:get|list|refresh|start|submit)_[a-z_]+)\b", text))
    assert named, "no tool names found in the instruction set"
    missing = sorted(n for n in named if not hasattr(tools, n))
    assert not missing, f"instruction set names tools that do not exist: {missing}"


def test_scoring_session_flow_is_assignment_type_neutral(text):
    lowered = text.lower()
    assert "prepare_scoring_session" in lowered
    assert "get_scoring_packet" in lowered
    assert "stage_scoring_results" in lowered
    assert "apply_staged_scoring_results" in lowered
    assert "never ask the teacher to choose a scoring transport" in lowered
    assert "loop through the teacher-selected exact assignment set" in lowered
    assert "per-assignment reconfirmation" in lowered
    assert "each safe packet and staged apply stays" in lowered
    assert "assignment-bounded; no queue" in lowered
    assert "selected exact assignments one at a time" not in lowered


def test_connected_guidance_describes_differentiated_delivery_ownership(text):
    lowered = text.lower()
    for phrase in (
        "public color tags",
        "same renderer-neutral family tail",
        "tier placement is manual",
        "shared bridge",
        "family-link repair",
        "canvas live",
        "teacher-owned canvas grade sync",
    ):
        assert phrase in lowered
    assert "confirm_sis_grade_bridge_passback" not in text
    assert "post_grades" not in text


def test_current_bridge_guidance_is_source_in_module_and_bridge_out():
    """Current authority docs must not teach the retired bridge-in-module rule."""
    root = Path(REPO_ROOT)
    authority = [
        root / "api" / "README.md",
        root / "api" / "webui" / "README.md",
        root / "api" / "default_docs" / "AI Authoring" / "START HERE - CanvasAgent.txt",
        root / "docs" / "guides" / "sis-grade-bridges.md",
        root / "docs" / "contracts" / "sis-grade-bridge-contract.md",
        root / "docs" / "reference" / "operation-ledger-module-map.md",
        root / "docs" / "reference" / "quiz-operation-design.md",
        root / "docs" / "reference" / "assignment-differentiation-design.md",
    ]
    stale_phrases = (
        "exactly one assignment-type module item points to the exact bridge id",
        "attach only the exact bridge id",
        "bridge-only module",
        "the bridge is attached to the selected module",
        "only the server-named `<family> - bridge` no-submission bridge is attached",
        "no tier is added to a module",
        "assignmentforge does not create module items",
        "assignmentforge never reads roster placement or creates or changes groups, overrides, modules",
    )
    for path in authority:
        lowered = path.read_text(encoding="utf-8").casefold()
        assert "source" in lowered and "module" in lowered, path
        assert "gradebook-only" in lowered or "bridge" in lowered, path
        for phrase in stale_phrases:
            assert phrase not in lowered, f"{phrase!r} remains in {path}"


def test_current_authority_has_no_retired_assignment_family_direction():
    """Current docs/code must not teach the retired content-only delivery path."""
    root = Path(REPO_ROOT)
    surfaces = [
        root / "api" / "README.md",
        root / "api" / "mcp_server" / "tools.py",
        root / "api" / "operation_ledger" / "executor.py",
        root / "api" / "sis_grade_bridge.py",
        root / "docs" / "README.md",
        root / "docs" / "mcp-server.md",
        root / "docs" / "guides" / "sis-grade-bridges.md",
        root / "docs" / "guides" / "scoring-sessions.md",
        root / "docs" / "guides" / "canvasexpert-agent-capabilities.md",
        root / "docs" / "contracts" / "sis-grade-bridge-contract.md",
        root / "docs" / "reference" / "assignment-differentiation-design.md",
        root / "docs" / "reference" / "course-expert-module-map.md",
        root / "docs" / "reference" / "operation-ledger-module-map.md",
        root / "docs" / "reference" / "quiz-operation-design.md",
        root / "docs" / "reference" / "authoring-contract-drift.md",
        root / "api" / "default_docs" / "AI Authoring" / AGENT_NAME,
        root / "api" / "default_docs" / "AI Authoring" / "Author an Assignment (AssignmentForge).txt",
    ]
    retired = (
        re.compile(r"content-only", re.IGNORECASE),
        re.compile(r"independent unpublished", re.IGNORECASE),
        re.compile(r"unpublished, unrestricted", re.IGNORECASE),
        re.compile(r"assignmentforge.{0,120}(?:no bridge|bridge-free)", re.IGNORECASE),
        re.compile(r"(?:the )?teacher\s+(?:assign|publish)", re.IGNORECASE),
        re.compile(r"(?:the )?teacher.{0,60}(?:students|groups|pods).{0,60}(?:assign|publish)", re.IGNORECASE),
    )
    for path in surfaces:
        normalized = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
        for pattern in retired:
            assert not pattern.search(normalized), f"{pattern.pattern!r} remains in {path}"


def test_family_contract_distinguishes_creation_and_final_source_safety():
    contract = (Path(REPO_ROOT) / "docs" / "contracts" / "sis-grade-bridge-contract.md").read_text(
        encoding="utf-8"
    )
    normalized = re.sub(r"\s+", " ", contract).casefold()
    assert "assignmentforge sources are whole-course visible" in normalized
    assert "created unpublished, then published only after its source shape is verified" in normalized
    assert "final source state is published, omitted from the final grade, and sis-disabled" in normalized
    assert "server-owned safety fields are unpublished" not in normalized


def test_mcp_server_instructions_cover_cross_course_scoring_discovery_and_held_work():
    from api.mcp_server import server

    lowered = server._SERVER_INSTRUCTIONS.lower()
    for phrase in (
        "discover_scoring_work",
        "every current course",
        "complete assignment and attention set",
        "wait for teacher direction",
        "selected exact",
        "held work",
        "evidence gaps are not empty",
    ):
        assert phrase in lowered
    assert "never ask the teacher to choose a scoring transport" in lowered
    assert "rows or another session" in lowered


def test_the_superseded_explainer_is_gone_from_the_shipped_defaults():
    stale = os.path.join(ai_ta.DEFAULT_AI_TA_DIR, "START HERE - Canvas Expert.txt")
    assert not os.path.exists(stale), (
        "the old explainer still ships, so a new install gets two files that "
        "disagree with each other"
    )


def test_a_retired_name_that_still_ships_cannot_churn():
    """Retiring a name that still ships is deliberate: it lets a corrected
    version seed back in on the same run.

    The invariant is narrower than "never both". What must never happen is the
    CURRENT shipped hash appearing in that name's retired set, because then
    every launch would delete the file and re-seed the very bytes that mark it
    for deletion, forever.
    """
    for name, retired_hashes in ai_ta.RETIRED_FILES.items():
        shipped = os.path.join(ai_ta.DEFAULT_AI_TA_DIR, name)
        if not os.path.exists(shipped):
            continue
        with open(shipped, "rb") as f:
            current = ai_ta._shipped_hash(f.read())
        assert current not in retired_hashes, (
            f"{name} would be deleted and re-seeded on every launch: its "
            "currently shipped bytes are listed as retired"
        )


REPO_ROOT = os.path.dirname(ai_ta.API_DIR)


def test_setup_appendix_exists_and_comes_first():
    """Someone emailed this file may not have installed CanvasExpert at all, so
    setup sits in the first appendix rather than the last."""
    with open(AGENT_PATH, encoding="utf-8") as f:
        body = f.read()
    assert "Appendix A. Getting CanvasExpert running" in body
    others = [body.index(f"Appendix {letter}.") for letter in "BCDEF"
              if f"Appendix {letter}." in body]
    assert others, "no other appendices found"
    assert body.index("Appendix A.") < min(others)


def test_every_file_the_setup_appendix_tells_them_to_click_exists(text):
    """A doc naming a launcher that was renamed sends teachers hunting."""
    for filename in ("Open Canvas Expert.bat", "Repair.bat"):
        assert filename in text, f"setup guidance never mentions {filename}"
        assert os.path.isfile(os.path.join(REPO_ROOT, filename)), (
            f"{filename} is named in the instructions but is not in the repo root"
        )


def test_the_port_matches_the_launcher(text):
    """The doc tells a teacher to type this address when the browser does not
    open on its own, so a drifted port sends them to a dead page."""
    with open(os.path.join(ai_ta.API_DIR, "qf_ui.py"), encoding="utf-8") as f:
        port = re.search(r"DEFAULT_PORT\s*=\s*(\d+)", f.read()).group(1)
    assert f"127.0.0.1:{port}" in text, (
        f"instructions do not name the real default port {port}"
    )


def test_the_python_floor_matches_the_launcher(text):
    """Both the batch file and the instructions state a minimum version."""
    with open(os.path.join(REPO_ROOT, "Open Canvas Expert.bat"), encoding="utf-8") as f:
        bat = f.read()
    wanted = re.search(r"Python (\d+\.\d+) or newer", bat)
    assert wanted, "launcher no longer states a Python version"
    assert f"Python {wanted.group(1)} or newer" in text, (
        f"launcher requires Python {wanted.group(1)} but the instructions disagree"
    )


def test_setup_instructions_match_user_scoped_python_installer(text):
    """The setup appendix must describe the launcher's current first-run path."""
    setup = text.split("Appendix A.", 1)[1].split("Appendix B.", 1)[0]
    assert "winget" in setup.lower()
    assert "install" in setup.lower()
    with open(os.path.join(REPO_ROOT, "Open Canvas Expert.bat"), encoding="utf-8") as f:
        bat = f.read()
    assert "winget install --id Python.Python.3.13" in bat
    assert "--scope user" in bat


def test_the_download_route_serves_it():
    """The AI Connections card links here, so the name must stay mapped."""
    from fastapi.testclient import TestClient

    from api.webui.server import app

    # Deliberately not `with TestClient(app)`. The context manager runs the
    # app's lifespan, and startup calls ai_ta.build_library against the real
    # workspace, so the test would seed and retire files in the developer's own
    # OneDrive folder. Plain construction skips lifespan and still routes.
    client = TestClient(app)
    r = client.get("/api/download-contract", params={"name": "CanvasAgent"})
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    assert AGENT_NAME in r.headers["content-disposition"].replace("%20", " ")
    assert r.text.lstrip().startswith("CanvasAgent")


def test_the_canvasagent_js_core_markers_match_the_file():
    """canvasagent.js slices the CORE block client-side using literal markers.

    Renaming the markers in the text file would break the "Copy the short
    version" button with no error anywhere, so pin the two together.
    """
    js_path = os.path.join(
        ai_ta.API_DIR, "webui", "static", "canvasagent.js"
    )
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    for marker in (CORE_BEGIN, CORE_END):
        assert f'"{marker}"' in js, (
            f"canvasagent.js does not use the marker {marker!r} that the "
            "instruction set actually contains"
        )


def test_the_canvasagent_page_offers_the_agent_in_advanced_setup():
    """Instructions remain available without competing with the health console."""
    page = os.path.join(
        ai_ta.API_DIR, "webui", "templates", "canvasagent.html"
    )
    with open(page, encoding="utf-8") as f:
        html = f.read()
    assert 'data-agent-copy="core"' in html
    assert 'data-agent-copy="full"' in html
    assert "name=CanvasAgent" in html
    assert "Advanced setup and instructions" in html


# --------------------------------------------------------------------------
# Retirement behaviour
# --------------------------------------------------------------------------

def _write(path, text, newline="\n"):
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def test_shipped_hash_ignores_line_endings(tmp_path):
    """A Windows checkout is CRLF while the committed blob is LF.

    Comparing raw bytes would never match, and the retirement would silently
    do nothing at all.
    """
    body = "one\ntwo\nthree\n"
    lf, crlf = tmp_path / "lf.txt", tmp_path / "crlf.txt"
    _write(lf, body, newline="\n")
    _write(crlf, body, newline="\r\n")
    assert lf.read_bytes() != crlf.read_bytes(), "fixture failed to differ"
    assert ai_ta._shipped_hash(lf.read_bytes()) == ai_ta._shipped_hash(crlf.read_bytes())


def test_retires_an_unmodified_copy(tmp_path):
    name, hashes = next(iter(ai_ta.RETIRED_FILES.items()))
    target = tmp_path / name
    # Reconstruct a body whose normalised hash is one we shipped by trusting the
    # recorded hash: use a real recorded value via monkey-free indirection.
    target.write_text("whatever", encoding="utf-8")
    known = ai_ta._shipped_hash(target.read_bytes())
    original = ai_ta.RETIRED_FILES
    try:
        ai_ta.RETIRED_FILES = {name: frozenset({known})}
        removed = ai_ta._retire_superseded(str(tmp_path))
    finally:
        ai_ta.RETIRED_FILES = original
    assert removed == [str(target)]
    assert not target.exists()


def test_leaves_a_teacher_edited_copy_alone(tmp_path):
    """The module promises never to clobber an edit. That must hold here too."""
    name = next(iter(ai_ta.RETIRED_FILES))
    target = tmp_path / name
    target.write_text("I rewrote this by hand and want it kept.", encoding="utf-8")
    removed = ai_ta._retire_superseded(str(tmp_path))
    assert removed == []
    assert target.exists()
    assert "by hand" in target.read_text(encoding="utf-8")


def test_retirement_is_silent_when_nothing_is_there(tmp_path):
    assert ai_ta._retire_superseded(str(tmp_path)) == []


def test_build_library_replaces_a_stale_copy_with_the_new_one(tmp_path):
    """End to end: the stale file goes and CanvasAgent arrives on one run."""
    stale_name = "START HERE - Canvas Expert.txt"
    stale = tmp_path / stale_name
    stale.write_text("stale", encoding="utf-8")
    known = ai_ta._shipped_hash(stale.read_bytes())
    original = ai_ta.RETIRED_FILES
    try:
        ai_ta.RETIRED_FILES = {stale_name: frozenset({known})}
        ai_ta.build_library(str(tmp_path))
    finally:
        ai_ta.RETIRED_FILES = original
    assert not stale.exists(), "stale explainer survived"
    assert (tmp_path / AGENT_NAME).exists(), "CanvasAgent did not seed"
