"""Lightweight data structures for the JSON 3.0 newspec sandbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# Supported question types in newspec JSON 3.0
QuestionType = Literal[
    "STIMULUS",
    "STIMULUS_END",
    "MC",
    "MA",
    "TF",
    "MATCHING",
    "FITB",
    "ESSAY",
    "FILEUPLOAD",
    "ORDERING",
    "CATEGORIZATION",
    "NUMERICAL",
]


@dataclass
class ChoiceRationale:
    """Per-choice rationale entry for a single answer option."""

    id: str
    correct: bool
    rationale: str


@dataclass
class RationalesEntry:
    """Rationale aligned to a scored item.

    Two shapes:
    - MC/MA: ``choices`` holds one :class:`ChoiceRationale` per answer option,
      covering the correct answer and every distractor.
    - All other scorable types: ``text`` holds a single explanation (for ESSAY /
      FILEUPLOAD this is "what a strong response looks like").

    Exactly one of ``choices`` / ``text`` is populated.
    """

    item_id: str
    choices: List[ChoiceRationale] = field(default_factory=list)
    text: Optional[str] = None


@dataclass
class QuizPayload:
    """Parsed quiz payload for the newspec JSON format."""

    version: str
    title: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    items: List[Dict[str, Any]] = field(default_factory=list)
    rationales: List[RationalesEntry] = field(default_factory=list)
    instructions: Optional[str] = None
    total_points: Any = None


@dataclass
class PackagedQuiz:
    """Lightweight packaged quiz for downstream consumers inside the sandbox."""

    version: str
    title: Optional[str]
    metadata: Dict[str, Any]
    items: List[Dict[str, Any]]
    rationales: List[RationalesEntry]
    experimental: List[Dict[str, Any]] = field(default_factory=list)
    instructions: Optional[str] = None
    total_points: Any = None
