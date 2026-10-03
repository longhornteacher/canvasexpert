"""Operation-ledger adapters — one per registered kind."""

from .assignment import AssignmentAdapter
from .assignment_update import AssignmentUpdateAdapter
from .attempts_grant import AttemptsGrantAdapter
from .grade_adjustment import GradeAdjustmentAdapter
from .page import PageAdapter
from .quick_assignment import QuickAssignmentAdapter
from .quiz import QuizAdapter
from .sis_grade_bridge import SisGradeBridgeAdapter

__all__ = [
    "AssignmentAdapter", "AssignmentUpdateAdapter", "AttemptsGrantAdapter",
    "GradeAdjustmentAdapter",
    "PageAdapter",
    "QuickAssignmentAdapter", "QuizAdapter",
    "SisGradeBridgeAdapter",
]
