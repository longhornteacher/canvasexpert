"""Machine-local operation-ledger primitives."""

from .receipts import create_receipt, list_receipts, get_receipt, new_receipt
from . import models, operations, batches, registry, executor, claims, recovery
from .adapters import (
    AssignmentAdapter, AssignmentUpdateAdapter, PageAdapter, QuickAssignmentAdapter,
    GradeAdjustmentAdapter, QuizAdapter, SisGradeBridgeAdapter,
)

__all__ = [
    "create_receipt", "list_receipts", "get_receipt", "new_receipt",
    "models", "operations", "batches", "registry", "executor", "claims",
    "recovery",
    "AssignmentAdapter", "AssignmentUpdateAdapter", "PageAdapter",
    "QuickAssignmentAdapter", "GradeAdjustmentAdapter", "QuizAdapter",
    "SisGradeBridgeAdapter",
]

registry.register(AssignmentAdapter())
registry.register(AssignmentUpdateAdapter())
registry.register(PageAdapter())
registry.register(QuickAssignmentAdapter())
registry.register(GradeAdjustmentAdapter())
registry.register(QuizAdapter())
registry.register(SisGradeBridgeAdapter())
