"""Read-only CanvasMirror release harness.

``--live-readonly`` performs configured Canvas release measurements using only
core GET-backed owners and fresh isolated machine-cache roots. ``--self-check``
is the synthetic disposable-cache and aggregate-output safety check.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _is_within(candidate: Path, parent: Path | None) -> bool:
    if parent is None:
        return False
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_output_path(output: str | Path, *, workspace_root: str | Path | None = None) -> Path:
    """Require a caller-owned aggregate JSON path outside repo and workspace."""
    path = Path(output).expanduser().resolve()
    if _is_within(path, REPO_ROOT) or _is_within(path, Path(workspace_root) if workspace_root else None):
        raise ValueError("benchmark output must be outside the repository and configured workspace")
    if path.suffix.lower() != ".json":
        raise ValueError("benchmark output must be a .json file")
    return path


def _temporary_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="canvasexpert-mirror-release-"))
    if _is_within(root, REPO_ROOT):
        raise ValueError("temporary benchmark root cannot be under the repository")
    return root


def _in_cache_root(root: Path, operation):
    """Run one operation with its machine-local cache redirected to a disposable root."""
    from api import runtime_paths
    with patch.object(runtime_paths, "local_cache_dir", return_value=Path(root) / "cache"):
        return operation()


def validate_profile(profile: dict) -> None:
    """Release comparisons are fixed to one current and two concluded courses."""
    if profile != {"current_courses": 1, "concluded_courses": 2}:
        raise ValueError("release profile must contain exactly 1 current and 2 concluded courses")


def synthetic_self_check(output: str | Path, *, workspace_root: str | Path | None = None) -> dict:
    """Exercise disposable machine-cache isolation without contacting Canvas or config."""
    output_path = validate_output_path(output, workspace_root=workspace_root)
    root = _temporary_root()
    try:
        from api.mirror import store
        def write_and_verify():
            store.write_groups("synthetic", [], root=str(root), attempted_at="2026-01-01T00:00:00Z")
            projection = Path(store.groups_path("synthetic", root=str(root)))
            if not projection.is_file() or not _is_within(projection, root):
                raise RuntimeError("temporary machine-cache proof failed")
        _in_cache_root(root, write_and_verify)
        result = {
            "kind": "canvasmirror_release_harness",
            "classification": "synthetic_self_check",
            "profile": {"current_courses": 1, "concluded_courses": 2},
            "runs": {"cold": 3, "warm": 3, "focused": 0},
            "aggregate": {"logical_requests": 0, "physical_requests": 0, "bytes": 0,
                          "retries": 0, "status_classes": {}},
            "temporary_root_verified": True,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return result
    finally:
        if root.exists() and not _is_within(root, REPO_ROOT):
            shutil.rmtree(root)


class ProfileRefusal(ValueError):
    """Stable, aggregate-only reason for an unusable configured course profile."""


def _metrics(fn):
    from api.platform_services.canvas_client import canvas_get_telemetry, telemetry_snapshot
    started = time.monotonic()
    with canvas_get_telemetry("course.refresh", "manual") as telemetry:
        result = fn()
    metrics = telemetry_snapshot(telemetry)
    metrics["wall_ms"] = max(0, int((time.monotonic() - started) * 1000))
    return result, metrics


def _summary(samples: list[dict]) -> dict:
    if not samples:
        return {"run_count": 0, "median_wall_ms": 0, "worst_wall_ms": 0,
                "median_logical_requests": 0, "worst_logical_requests": 0,
                "median_physical_requests": 0, "worst_physical_requests": 0,
                "median_bytes": 0, "worst_bytes": 0, "median_retries": 0,
                "worst_retries": 0, "status_classes": {}}
    result = {"run_count": len(samples)}
    for source, label in (("wall_ms", "wall_ms"), ("logical_requests", "logical_requests"),
                          ("physical_requests", "physical_requests"), ("bytes", "bytes"),
                          ("retries", "retries")):
        values = [int(sample.get(source, 0)) for sample in samples]
        result[f"median_{label}"] = int(statistics.median(values))
        result[f"worst_{label}"] = max(values)
    classes: dict[str, int] = {}
    for sample in samples:
        for key, count in sample.get("status_classes", {}).items():
            classes[key] = classes.get(key, 0) + int(count)
    result["status_classes"] = classes
    return result


def _safe_temp_cleanup(root: Path) -> None:
    # Delete only a verified disposable root: it must exist, live under the
    # system temp dir, and never under the repository. The positive
    # "under system temp" assertion (not just "not under repo") keeps cleanup
    # resolving to exact disposable roots per the release deletion rule.
    temp_dir = Path(tempfile.gettempdir()).resolve()
    if root.exists() and _is_within(root, temp_dir) and not _is_within(root, REPO_ROOT):
        shutil.rmtree(root)


def run_live_readonly(courses: list[dict], *, canvas_get, canvas_get_all, canvas_get_all_complete,
                      context_refresh=None, full_pass=None, delta_pass=None, focused_refresh=None,
                      temp_root_factory=_temporary_root, focused_assignment_id: str = "") -> dict:
    """Execute the release profile with only existing core GET-backed read owners.

    IDs and private records stay inside local variables; the returned structure is
    aggregate metrics only. Dependency injection makes the full orchestration
    testable without Canvas.
    """
    from api.mirror import course_context, store, sync
    context_refresh = context_refresh or course_context.refresh_course_context
    full_pass = full_pass or sync.full_pass
    delta_pass = delta_pass or sync.delta_pass
    focused_refresh = focused_refresh or sync.sync_assignment_submissions
    candidates = [{"id": str(item.get("id") or "")} for item in courses if str(item.get("id") or "")]
    validation_root = temp_root_factory()
    try:
        lifecycles = []
        for course in candidates:
            outcome, _metrics_ignored = _in_cache_root(validation_root, lambda course=course: _metrics(
                lambda: context_refresh(
                    course["id"], canvas_get=canvas_get, canvas_get_all=canvas_get_all,
                    root=str(validation_root))))
            lifecycles.append((course, outcome.get("lifecycle") if outcome.get("state") == "current" else "unknown"))
        current = [course for course, lifecycle in lifecycles if lifecycle == "current"]
        concluded = [course for course, lifecycle in lifecycles if lifecycle == "concluded"]
        if len(current) != 1 or len(concluded) != 2:
            raise ProfileRefusal("profile_mismatch")
    finally:
        _safe_temp_cleanup(validation_root)

    cold_samples, warm_samples, focused_samples = [], [], []
    for _run_index in range(3):
        root = temp_root_factory()
        try:
            def cold():
                outcomes = []
                for course in current + concluded:
                    outcomes.append(full_pass(course["id"], canvas_get_all=canvas_get_all,
                                              canvas_get_all_complete=canvas_get_all_complete, root=str(root),
                                              skip_new_quiz_metadata=course in concluded))
                return outcomes
            cold_outcomes, cold_metric = _in_cache_root(root, lambda: _metrics(cold))
            if not all(item.get("ok") for item in cold_outcomes):
                cold_metric["status_classes"]["sync_failed"] = 1
            cold_samples.append(cold_metric)

            warm_outcome, warm_metric = _in_cache_root(root, lambda: _metrics(lambda: delta_pass(
                current[0]["id"], canvas_get_all=canvas_get_all,
                canvas_get_all_complete=canvas_get_all_complete, root=str(root))))
            if not warm_outcome.get("ok"):
                warm_metric["status_classes"]["sync_failed"] = 1
            warm_samples.append(warm_metric)

            if focused_assignment_id:
                projection = _in_cache_root(root, lambda: store.read_assignments(
                    current[0]["id"], root=str(root))) or {}
                if focused_assignment_id in (projection.get("assignments") or {}):
                    focused_outcome, focused_metric = _in_cache_root(root, lambda: _metrics(
                        lambda: focused_refresh(
                            current[0]["id"], focused_assignment_id, canvas_get_all=canvas_get_all,
                            root=str(root))))
                    if not focused_outcome.get("ok"):
                        focused_metric["status_classes"]["sync_failed"] = 1
                    focused_samples.append(focused_metric)
        finally:
            _safe_temp_cleanup(root)
    return {"kind": "canvasmirror_release_harness", "classification": "live_readonly",
            "profile": {"current_courses": 1, "concluded_courses": 2},
            "cold": _summary(cold_samples), "warm": _summary(warm_samples),
            "focused": _summary(focused_samples), "temporary_roots_verified": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only CanvasMirror release harness")
    parser.add_argument("--output", required=True, help="aggregate JSON output outside repo/workspace")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-check", action="store_true", help="run only the synthetic disposable-cache check")
    mode.add_argument("--live-readonly", action="store_true", help="run configured read-only release profile")
    args = parser.parse_args(argv)
    try:
        from api.platform_services import config, workspace
        output = validate_output_path(args.output, workspace_root=workspace.workspace_root())
        if args.self_check:
            result = synthetic_self_check(output, workspace_root=workspace.workspace_root())
        else:
            from api.platform_services.canvas_client import canvas_get, canvas_get_all, canvas_get_all_complete
            focused = os.environ.get("CANVAS_EXPERT_RELEASE_FOCUSED_ASSIGNMENT_ID", "").strip()
            try:
                result = run_live_readonly(config.active_courses(), canvas_get=canvas_get,
                                           canvas_get_all=canvas_get_all,
                                           canvas_get_all_complete=canvas_get_all_complete,
                                           focused_assignment_id=focused)
            except ProfileRefusal:
                result = {"kind": "canvasmirror_release_harness", "classification": "refused",
                          "reason": "profile_mismatch"}
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                print(json.dumps({"classification": "refused", "reason": "profile_mismatch"}))
                return 2
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        print(json.dumps({"classification": result["classification"]}))
    except (OSError, ValueError, RuntimeError):
        # Never reflect a path, URL, response body, credential, or supplied ID.
        print("release harness refused", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
