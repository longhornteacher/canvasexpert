"""Parser for the JSON 3.0 newspec format (sandbox, non-production)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from .models import ChoiceRationale, QuestionType, QuizPayload, RationalesEntry

TAG_OPEN = "<QUIZFORGE_JSON>"
TAG_CLOSE = "</QUIZFORGE_JSON>"
SUPPORTED_TYPES: Tuple[QuestionType, ...] = (
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
)


def extract_tagged_payload(text: str) -> str:
    """Extract the JSON payload between the newspec tags.

    Friendly chatter outside the tags is ignored; only the tagged JSON is returned.
    Raises ValueError when tags are missing or empty.
    """
    start = text.find(TAG_OPEN)
    end = text.find(TAG_CLOSE, start + len(TAG_OPEN)) if start != -1 else -1

    # Be forgiving when the closing tag is missing or mistyped (common LLM slip):
    # fall back to the next opening tag or the end of text so we can still parse.
    if start == -1:
        # Check if the raw text is valid JSON (support for manual .json files without tags)
        try:
            trimmed = text.strip()
            # Optimization: only attempt parse if it looks like an object
            if trimmed.startswith("{"):
                json.loads(trimmed)
                return trimmed
        except json.JSONDecodeError:
            pass
        raise ValueError("QUIZFORGE_JSON tags not found.")
    if end == -1 or end <= start:
        alt_end = text.find(TAG_OPEN, start + len(TAG_OPEN))
        end = alt_end if alt_end != -1 else len(text)

    payload = text[start + len(TAG_OPEN) : end].strip()
    if not payload:
        raise ValueError("Tagged JSON payload is empty.")
    return payload


def _sanitize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Apply lightweight validation/sanitization rules for a single item."""
    if "type" not in item:
        raise ValueError("Item missing required 'type' field.")
    qtype = item["type"]
    if qtype not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported item type '{qtype}'.")

    # Coerce prompt to string; LLMs occasionally emit null or a number.
    if not isinstance(item.get("prompt"), str):
        raw_prompt = item.get("prompt")
        item["prompt"] = "" if raw_prompt is None else str(raw_prompt)

    # STIMULUS/END are never scored or rationalized.
    if qtype in ("STIMULUS", "STIMULUS_END") and "points" in item:
        item = {k: v for k, v in item.items() if k != "points"}

    # Points are only honored if the teacher explicitly supplied them.
    # We accept the field when present but do not infer or inject defaults.
    if "metadata" not in item:
        item["metadata"] = {}
    if not isinstance(item.get("metadata"), dict):
        item["metadata"] = {}
    if "extensions" not in item["metadata"] or not isinstance(item["metadata"].get("extensions"), dict):
        item["metadata"]["extensions"] = {}

    # Rendering mode for student-facing strings.
    # Default is executable (enables rich HTML formatting for better readability).
    # Use "verbatim" only when string-reasoning tasks require exact character preservation.
    render_mode_raw = item.get("render_mode", "executable")
    render_mode = render_mode_raw.lower() if isinstance(render_mode_raw, str) else "executable"
    if render_mode not in {"verbatim", "executable"}:
        render_mode = "executable"
    item["render_mode"] = render_mode

    if qtype == "FITB":
        mode_raw = item.get("answer_mode", "open_entry")
        mode = mode_raw.lower() if isinstance(mode_raw, str) else "open_entry"
        if mode not in {"open_entry", "dropdown", "wordbank"}:
            mode = "open_entry"
        item["answer_mode"] = mode
        options_raw = item.get("options", [])
        item["options"] = [str(opt) for opt in options_raw] if isinstance(options_raw, list) else []

    if qtype == "STIMULUS":
        layout_raw = item.get("layout", "below")
        layout = layout_raw.lower() if isinstance(layout_raw, str) else "below"
        if layout not in {"below", "right"}:
            layout = "below"
        item["layout"] = layout

    # Tag experimental numerical modes
    if qtype == "NUMERICAL":
        evaluation = item.get("evaluation", {})
        if isinstance(evaluation, dict):
            mode = evaluation.get("mode", "exact")
            if mode != "exact":
                flags = item.get("experimental_flags") if isinstance(item.get("experimental_flags"), list) else []
                flags.append("numerical_non_exact_mode")
                item["experimental_flags"] = flags

    return item


def _parse_choice_rationales(raw_choices: Any) -> Optional[List[ChoiceRationale]]:
    """Parse a raw choices list into ChoiceRationale objects.

    Auto-fix: a choice missing a usable ``id`` is assigned a letter by position
    (A, B, C, ...) so authors/LLMs that omit ids don't lose their rationales.
    Returns None if the input is not a valid non-empty list.
    """
    if not isinstance(raw_choices, list) or not raw_choices:
        return None
    result: List[ChoiceRationale] = []
    for idx, choice in enumerate(raw_choices):
        if not isinstance(choice, dict):
            logger.warning("Rationale choice is not an object; skipped: %r", choice)
            continue
        choice_id = choice.get("id")
        if not isinstance(choice_id, str) or not choice_id.strip():
            choice_id = chr(65 + idx) if idx < 26 else str(idx + 1)
            logger.info("Rationale choice missing 'id'; auto-assigned '%s' by position.", choice_id)
        correct = choice.get("correct")
        rationale_text = choice.get("rationale")
        if not isinstance(correct, bool):
            logger.warning("Rationale choice '%s' has non-boolean 'correct' field; skipped", choice_id)
            continue
        if not isinstance(rationale_text, str):
            logger.warning("Rationale choice '%s' missing string 'rationale'; skipped", choice_id)
            continue
        result.append(ChoiceRationale(id=choice_id, correct=correct, rationale=rationale_text))
    return result if result else None


def _parse_rationales(raw: Any) -> List[RationalesEntry]:
    """Convert raw rationales list into RationalesEntry objects, skipping invalid shapes.

    Each entry must contain a ``choices`` array where each element has ``id``,
    ``correct``, and ``rationale`` fields. Entries without a valid ``choices``
    array are skipped with a logged warning (so callers can surface what dropped).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Rationales must be a list when provided.")
    entries: List[RationalesEntry] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("Rationale entry skipped (not an object): %r", entry)
            continue
        item_id = entry.get("item_id")
        if not isinstance(item_id, str):
            logger.warning("Rationale entry skipped: missing or non-string 'item_id'.")
            continue

        # Per-choice form (MC/MA): a 'choices' array.
        if "choices" in entry:
            parsed_choices = _parse_choice_rationales(entry.get("choices"))
            if parsed_choices is None:
                logger.warning(
                    "Rationale for '%s' skipped: 'choices' present but no valid "
                    "{id, correct, rationale} entries.", item_id,
                )
                continue
            entries.append(RationalesEntry(item_id=item_id, choices=parsed_choices))
            continue

        # Single-rationale form (TF/FITB/MATCHING/ORDERING/NUMERICAL/ESSAY/FILEUPLOAD).
        single = entry.get("rationale")
        if isinstance(single, str) and single.strip():
            entries.append(RationalesEntry(item_id=item_id, text=single.strip()))
            continue

        logger.warning(
            "Rationale for '%s' skipped: needs either a 'choices' array (MC/MA) "
            "or a non-empty 'rationale' string (other types).", item_id,
        )

    return entries


def parse_news_json(text: str) -> QuizPayload:
    """Parse raw LLM output into a structured QuizPayload for the newspec JSON format."""
    payload_text = extract_tagged_payload(text)
    data = json.loads(payload_text)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object.")

    version_raw = data.get("version")
    if version_raw is None:
        logger.warning("Top-level 'version' field missing; defaulting to 'unknown'")
        version = "unknown"
    else:
        version = str(version_raw)

    title = data.get("title")
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("'metadata' must be an object when present.")
    if "extensions" not in metadata or not isinstance(metadata.get("extensions"), dict):
        metadata["extensions"] = {}

    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        raise ValueError("'items' must be a list.")
    if not items_raw:
        logger.warning("'items' list is empty; quiz will have no questions")

    sanitized_items: List[Dict[str, Any]] = []
    for _idx, _raw_item in enumerate(items_raw):
        if not isinstance(_raw_item, dict):
            logger.warning("Item at index %d is not an object; skipped", _idx)
            continue
        try:
            sanitized_items.append(_sanitize_item(dict(_raw_item)))
        except ValueError as _e:
            logger.warning("Item at index %d skipped: %s", _idx, _e)

    # Rationale list is optional; stimuli/structural markers should not be rationalized by the LLM.
    rationales_raw = data.get("rationales")
    rationales = _parse_rationales(rationales_raw)

    instructions_raw = data.get("instructions")
    instructions = instructions_raw if isinstance(instructions_raw, str) and instructions_raw.strip() else None

    return QuizPayload(
        version=version,
        title=title if isinstance(title, str) else None,
        metadata=metadata,
        items=sanitized_items,
        rationales=rationales,
        instructions=instructions,
        total_points=data.get("total_points"),
    )
