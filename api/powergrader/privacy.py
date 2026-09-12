"""Privacy helpers for local Scoring Session artifacts.

Functions for building privacy step records and writing private audit files.
"""

import json
import os
from datetime import datetime

from api import feedback_pipeline as fp
from api.platform_services import workspace


def privacy_step(step_id: str, label: str, status: str,
                 detail: str = "", **extra) -> dict:
    item = {"id": step_id, "label": label, "status": status, "detail": detail}
    item.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return item


def feedback_artifact_dirs(
    *, course_name: str = "", course_id: str = "",
    assignment_name: str = "", assignment_id: str = "",
    mode: str = "scoring-session", run_timestamp: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the SAFE (``For AI/``) and PRIVATE (``Student Work/Grading Keys/``)
    homes for one Scoring Session.

    The *reserve* value accounts for the deepest PowerGrader child layout
    (packet folder, batch folder, batch file name) so the root SAFE and PRIVATE
    directories stay short enough for all teacher-visible children.
    """
    workspace.ensure_workspace()
    if course_id and assignment_id:
        # Reserve ~60 chars for the deepest child: "Packet-<hash>/Batches/Batch-99/03-work.md"
        # where <hash> is 8 chars, batch index is 2 digits.
        pg_child_reserve = 60
        safe = workspace.ai_run_folder(
            course_name or course_id, course_id,
            assignment_name or assignment_id, assignment_id,
            mode or "assisted", run_timestamp=run_timestamp,
            reserve=pg_child_reserve,
        )
        private = workspace.grading_keys_assignment_folder(
            course_name or course_id, course_id,
            assignment_name or assignment_id, assignment_id,
            reserve=pg_child_reserve,
        )
        return safe, private
    return workspace.for_ai_root(), workspace.grading_keys_root()


def write_privacy_audit_file(
    private_folder: str,
    assignment_name: str,
    session_id: str,
    course_id: str,
    assignment_id: str,
    model_id: str,
    privacy_steps: list[dict],
    privacy_artifacts: dict,
) -> str | None:
    try:
        os.makedirs(workspace.extended_path(private_folder), exist_ok=True)
        audit_root = workspace.audits_dir() or private_folder
        os.makedirs(workspace.extended_path(audit_root), exist_ok=True)
        path = os.path.join(
            audit_root,
            f"{fp.safe(assignment_name)}__powergrader-privacy-audit-{fp.safe(session_id)}.json",
        )
        with open(workspace.extended_path(path), "w", encoding="utf-8") as f:
            json.dump({
                "session_id": session_id,
                "course_id": course_id,
                "assignment_id": assignment_id,
                "assignment_name": assignment_name,
                "model_id": model_id,
                "created": datetime.now().isoformat(timespec="seconds"),
                "privacy_steps": privacy_steps,
                "privacy_artifacts": privacy_artifacts,
            }, f, indent=2, ensure_ascii=False)
        return path
    except Exception:
        return None
