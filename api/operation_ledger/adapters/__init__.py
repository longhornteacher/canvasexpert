"""Operation-ledger adapters — one per registered kind."""

from .assignment import AssignmentAdapter
from .assignment_update import AssignmentUpdateAdapter
from .grade_adjustment import GradeAdjustmentAdapter
from .missing_fill import MissingFillAdapter
from .page import PageAdapter
from .quick_assignment import QuickAssignmentAdapter
from .quiz import QuizAdapter
from .sis_grade_bridge import SisGradeBridgeAdapter

__all__ = [
    "AssignmentAdapter", "AssignmentUpdateAdapter", "GradeAdjustmentAdapter",
    "MissingFillAdapter", "PageAdapter",
    "QuickAssignmentAdapter", "QuizAdapter",
    "SisGradeBridgeAdapter",
]
