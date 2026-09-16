"""Privacy helpers for local Scoring Session artifacts."""

import os

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
