"""Point validation fixer; authored values are never normalized."""

from typing import List, Tuple
from ...core.quiz import Quiz


class PointNormalizer:
    """Keep explicit quiz points and report rows that need teacher values."""
    
    def normalize(self, quiz: Quiz, target_total: float = 100.0) -> Tuple[Quiz, List[str]]:
        """Preserve authored values and identify missing values without filling them."""
        messages: List[str] = []
        scorable = quiz.scorable_questions()
        missing = [q for q in scorable if not q.points_set]
        if missing:
            messages.append(
                f"{len(missing)} scored question(s) need teacher-authored points."
            )
        return quiz, messages
