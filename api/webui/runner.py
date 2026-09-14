"""Run the existing qf_pusher / validate_qf CLI scripts as
subprocesses, with per-request Canvas credentials injected via the
environment. canvas.py calls `load_dotenv()` (which never overrides vars
already present in the environment), so setting CANVAS_BASE / COURSE_ID /
CANVAS_TOKEN here makes the subprocess target exactly the chosen profile,
regardless of what's in api/.env.
"""
import os
import json
import re
import subprocess
import sys

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(extra):
    env = dict(os.environ)
    env.update(extra)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_capture(args, extra_env=None, timeout=120):
    """Blocking run; returns (returncode, combined_output)."""
    proc = subprocess.run(
        [sys.executable, "-u", *args],
        cwd=API_DIR,
        env=_env(extra_env or {}),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


# A path is redacted from its drive letter or leading separator through its
# last segment, spaces included: the teacher's workspace really is
# "...\CE Workspace\Quiz Inbox\...", and a rule that stopped at whitespace
# left those folder names in the message. Requiring at least two segments
# keeps ordinary text ("1/2 of the items") out of it. Over-redaction is the
# safe direction here; the reason survives either way.
_PATH_SEGMENT = r"[^\\/\r\n'\"]"
_PATHISH = re.compile(
    rf"(?:[A-Za-z]:[\\/]|[\\/])(?:{_PATH_SEGMENT}*[\\/])+{_PATH_SEGMENT}*"
)


def _planner_detail(stderr: bytes) -> str:
    """The planner's own last error line, bounded and path-free.

    A planner failure used to reach the caller as the bare words "planner
    failed", which is unactionable: the assistant reporting it to a teacher
    could not say whether the draft was malformed, the envelope was the wrong
    kind, or the subprocess never started. The planner's message answers that.
    Local filesystem paths are redacted on the way out for the same reason
    ``content_push._scrub_paths`` redacts them from a preview.
    """
    text = (stderr or b"").decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    detail = _PATHISH.sub("<path>", lines[-1])[:200]
    return f": {detail}" if detail else ""


def run_json_object(args, extra_env=None, timeout=120, max_output_bytes=2_000_000):
    """Run a local planner and return its one bounded JSON-object response.

    Canvas credentials are deliberately removed: operation preparation is a
    local transformation boundary, not a live-write subprocess.

    ``PYTHONIOENCODING`` is set to utf-8 because we decode the child's stdout
    as utf-8 below, and nothing was telling the child to produce it. On
    Windows a piped stdout encodes as cp1252, and the planners print their
    plan with ``ensure_ascii=False``, so a single character outside cp1252
    anywhere in a draft -- a checkmark, an arrow, a Greek letter, one of the
    math comparison signs -- raised UnicodeEncodeError inside the child and
    came back here as exit 2. Quiz content carries those constantly, in the
    per-choice rationales especially, so in practice this failed every quiz
    push that used one while assignments, pages, and rubrics kept working:
    their adapters build the payload in-process and never come through here.

    stdin is ``DEVNULL`` because the MCP server speaks JSON-RPC over its own
    stdin. No planner reads stdin today, so this closes a hazard rather than
    a live bug: a child that inherits that handle shares the transport.

    The timeout now matches ``run_capture``'s. This is margin, not a fix: a
    measured plan takes well under a second, and the encoding bug above was
    the whole of the observed failure. But the old 30s covered a cold
    interpreter spawn on a Windows venv under real-time virus scanning plus
    one planner run per variant, sequentially, for a differentiated push, and
    that was a thin margin for a pure local transformation to be judged on.
    """
    env = _env(extra_env or {})
    for key in ("CANVAS_TOKEN", "CANVAS_BASE", "COURSE_ID"):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, "-u", *args],
            cwd=API_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("planner timed out") from exc
    if len(proc.stdout) > max_output_bytes or len(proc.stderr) > max_output_bytes:
        raise ValueError("planner output exceeded limit")
    if proc.returncode != 0:
        raise ValueError(f"planner failed{_planner_detail(proc.stderr)}")
    try:
        text = proc.stdout.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("planner returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("planner response must be a JSON object")
    return parsed


def run_streaming(args, extra_env=None):
    """Yield stdout lines as they're produced, then a final '[exit N]' line."""
    proc = subprocess.Popen(
        [sys.executable, "-u", *args],
        cwd=API_DIR,
        env=_env(extra_env or {}),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        proc.stdout.close()
        code = proc.wait()
        yield f"[exit {code}]"
