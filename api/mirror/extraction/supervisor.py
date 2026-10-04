"""Run a format adapter in a killable worker process with a hard timeout.

Thread cancellation cannot bound a stuck native parser, so heavy extraction runs
in a supervised subprocess. The worker reads the file bytes itself and returns a
serialized result; the parent never trusts unvalidated output.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from .schema import ExtractionError, result_from_dict, result_to_dict, validate_result

DEFAULT_TIMEOUT = 300.0
MAX_TIMEOUT = 300.0


def run_adapter(adapter_name: str, path: str | Path, *, timeout: float = DEFAULT_TIMEOUT,
                python_executable: str | None = None):
    """Extract one file in a supervised process; typed failure on timeout/crash."""
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > MAX_TIMEOUT:
        raise ExtractionError("invalid_timeout")
    request = {"adapter": adapter_name, "path": str(path)}
    try:
        completed = subprocess.run(
            [python_executable or sys.executable, "-m", __name__],
            input=json.dumps(request), text=True, encoding="utf-8",
            capture_output=True, timeout=timeout,
            cwd=Path(__file__).resolve().parents[3],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        raise ExtractionError("timeout") from None
    except OSError:
        raise ExtractionError("missing_dependency") from None
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise ExtractionError("recognition_failed") from None
    if isinstance(payload, dict) and "error" in payload:
        raise ExtractionError(payload["error"])
    if completed.returncode:
        raise ExtractionError("recognition_failed")
    return result_from_dict(payload)


def _worker(request: dict) -> dict:
    from .registry import load_adapter
    try:
        adapter = load_adapter(request["adapter"])
        data = Path(request["path"]).read_bytes()
        result = validate_result(adapter(data, filename=request["path"]))
        return result_to_dict(result)
    except ExtractionError as exc:
        return {"error": exc.code}
    except Exception as exc:
        return {"error": "missing_dependency" if isinstance(exc, ImportError)
                else "recognition_failed"}


if __name__ == "__main__":
    print(json.dumps(_worker(json.load(sys.stdin)), ensure_ascii=False))
