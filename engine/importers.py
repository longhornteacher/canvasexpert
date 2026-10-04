"""Importer for the canonical QuizForge JSON 3.0 spec."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import List, Optional, Protocol

from engine.core.answers import NumericalAnswer
from engine.core.questions import (
    CategorizationQuestion,
    CategoryMapping,
    EssayQuestion,
    FITBQuestion,
    FileUploadQuestion,
    MAQuestion,
    MCChoice,
    MCQuestion,
    MatchingPair,
    MatchingQuestion,
    NumericalQuestion,
    OrderingItem,
    OrderingQuestion,
    Question,
    StimulusEnd,
    StimulusItem,
    TFQuestion,
)
from engine.core.quiz import Quiz
from engine.spec_engine import packager as news_packager
from engine.spec_engine import parser as news_parser
from engine.spec_engine import parser as spec_parser
from engine.utils.json_lint import lint_json_syntax, repair_json
from engine.validation.authored_points import authored_point_problems

logger = logging.getLogger(__name__)


@dataclass
class ImportedQuiz:
    quiz: Quiz
    raw: str


class Importer(Protocol):
    def import_quiz(self, raw_spec: str) -> ImportedQuiz:
        ...


class JsonImportError(Exception):
    """Raised when JSON 3.0 import fails (no text fallback)."""

    def __init__(
        self,
        message: str,
        line: int | None = None,
        column: int | None = None,
        char: int | None = None,
        lint_errors: list[str] | None = None,
    ):
        super().__init__(message)
        self.line = line
        self.column = column
        self.char = char
        self.lint_errors = lint_errors or []


class JsonImporter:
    """Importer for the JSON 3.0 spec using the spec_engine sandbox."""

    def import_quiz(self, raw_spec: str) -> ImportedQuiz:
        logger.debug("JSON spec mode active (QUIZFORGE_SPEC_MODE=json)")
        lint_target = raw_spec

        # Preflight: ensure tags exist and capture payload for linting
        try:
            lint_target = spec_parser.extract_tagged_payload(raw_spec)
        except Exception as e:
            lint_errors = lint_json_syntax(raw_spec, max_errors=5)
            message = f"Invalid JSON wrapper: {e}"
            if lint_errors:
                lint_errors = lint_errors[:5]
            raise JsonImportError(message, lint_errors=lint_errors) from e

        payload = None
        try:
            payload = news_parser.parse_news_json(raw_spec)
        except json.JSONDecodeError as e:
            lint_errors = lint_json_syntax(lint_target, max_errors=5)
            # Attempt auto-repair (trailing commas, JS comments, single-quoted strings)
            repaired = repair_json(lint_target)
            if repaired != lint_target:
                try:
                    wrapped = f"{spec_parser.TAG_OPEN}\n{repaired}\n{spec_parser.TAG_CLOSE}"
                    payload = news_parser.parse_news_json(wrapped)
                    logger.info("JSON auto-repaired (trailing commas / comments / quote style fixed)")
                except Exception:
                    pass
            if payload is None:
                message = (
                    "Invalid JSON payload: the JSON could not be parsed. "
                    f"The parser stopped at line {e.lineno}, column {e.colno} with '{e.msg}', "
                    "which usually means a missing comma or an unescaped double quote inside a string. "
                    "Fix the JSON syntax around that spot so it becomes valid."
                )
                raise JsonImportError(message, line=e.lineno, column=e.colno, char=e.pos, lint_errors=lint_errors) from e
        except ValueError as e:
            # parser-level structural errors (missing fields, bad types)
            lint_errors = lint_json_syntax(lint_target, max_errors=5)
            raise JsonImportError(f"JSON import failed: {e}", lint_errors=lint_errors) from e
        except Exception as e:
            raise JsonImportError(f"JSON import failed: {e}") from e
        point_problems = authored_point_problems(payload.items, payload.total_points)
        if point_problems:
            raise JsonImportError(f"QuizForge point validation failed: {point_problems[0]}")
        logger.debug("Parsed JSON payload version=%s", payload.version)

        packaged = news_packager.package_quiz(payload, context="default")

        experimental_seen = any(item for item in getattr(packaged, "experimental", []) or [])
        if experimental_seen:
            logger.warning("Experimental features present in JSON payload (non-exact numerical modes)")

        quiz = _packaged_to_domain(packaged)
        debug_mode = os.getenv("QUIZFORGE_DEBUG_JSON", "0") == "1"
        if debug_mode:
            logger.debug("Parsed JSON domain model:\n%s", repr(quiz))

        # Preserve raw plus experimental flags info for debugging
        raw_with_flags = raw_spec
        if experimental_seen:
            raw_with_flags += f"\n\n# experimental_flags: {[item.get('experimental_flags', []) for item in getattr(packaged, 'items', []) if item.get('experimental_flags')]}"

        return ImportedQuiz(quiz=quiz, raw=raw_with_flags)


def import_quiz_from_llm(raw_output: str) -> ImportedQuiz:
    """Import the canonical QuizForge JSON 3.0 format."""
    return JsonImporter().import_quiz(raw_output)


def _packaged_to_domain(packaged) -> Quiz:
    """Convert PackagedQuiz (newspec) into the legacy domain Quiz object."""
    questions: List[Question] = []
    current_stimulus_id: Optional[str] = None

    def _render_mode(item: dict) -> str:
        raw = item.get("render_mode", "executable")
        mode = raw.lower() if isinstance(raw, str) else "executable"
        return mode if mode in {"verbatim", "executable"} else "executable"

    def _points(item: dict) -> float:
        return float(item["points"]) if "points" in item else 0.0

    for item in packaged.items:
        qtype = item.get("type")
        prompt = item.get("prompt", "")
        stim_id = item.get("stimulus_id")
        forced_ident = item.get("id")
        render_mode = _render_mode(item)

        if qtype == "STIMULUS":
            forced_ident = forced_ident or f"stim_{uuid.uuid4().hex[:8]}"
            layout_raw = item.get("layout", "below")
            layout = str(layout_raw).lower() if isinstance(layout_raw, str) else "below"
            if layout not in {"below", "right"}:
                layout = "below"
            stim_title = str(item.get("title", "") or "")
            stim_author = str(item.get("author", "") or "")
            q = StimulusItem(qtype="STIMULUS", prompt=prompt, render_mode=render_mode, points=0.0, points_set=False, forced_ident=forced_ident, layout=layout, title=stim_title, author=stim_author)
            current_stimulus_id = forced_ident
            questions.append(q)
            continue

        if qtype == "STIMULUS_END":
            q = StimulusEnd(qtype="STIMULUS_END", prompt=prompt, render_mode=render_mode, points=0.0, points_set=False)
            current_stimulus_id = None
            questions.append(q)
            continue

        parent_stimulus = stim_id or current_stimulus_id
        pts = _points(item)
        pts_set = "points" in item

        if qtype == "MC":
            choices = [MCChoice(text=c["text"], correct=bool(c.get("correct"))) for c in item.get("choices", [])]
            q = MCQuestion(qtype="MC", prompt=prompt, render_mode=render_mode, points=pts, points_set=pts_set, choices=choices, parent_stimulus_ident=parent_stimulus, forced_ident=forced_ident)
        elif qtype == "MA":
            choices = [MCChoice(text=c["text"], correct=bool(c.get("correct"))) for c in item.get("choices", [])]
            q = MAQuestion(qtype="MA", prompt=prompt, render_mode=render_mode, points=pts, points_set=pts_set, choices=choices, parent_stimulus_ident=parent_stimulus, forced_ident=forced_ident)
        elif qtype == "TF":
            answer_raw = item.get("answer")
            q = TFQuestion(
                qtype="TF",
                prompt=prompt,
                render_mode=render_mode,
                points=pts,
                points_set=pts_set,
                answer_true=_parse_tf_answer(answer_raw),
                parent_stimulus_ident=parent_stimulus,
                forced_ident=forced_ident,
            )
        elif qtype == "MATCHING":
            pairs = [MatchingPair(prompt=p["left"], answer=p["right"]) for p in item.get("pairs", [])]
            q = MatchingQuestion(qtype="MATCHING", prompt=prompt, render_mode=render_mode, points=pts, points_set=pts_set, pairs=pairs, parent_stimulus_ident=parent_stimulus, forced_ident=forced_ident)
        elif qtype == "FITB":
            accept_list = item.get("accept", []) or []
            variants: List[str] = []
            variants_per_blank: List[List[str]] = []

            if isinstance(accept_list, list):
                # Multi-blank: array of arrays with one entry per blank
                if accept_list and all(isinstance(group, list) for group in accept_list) and len(accept_list) > 1:
                    variants_per_blank = [[str(v) for v in group] for group in accept_list]
                    for group in variants_per_blank:
                        variants.extend(group)
                else:
                    # Single-blank: accept either ["a", "b"] or [["a", "b"]] shapes
                    for variant_group in accept_list:
                        if isinstance(variant_group, list):
                            variants.extend([str(v) for v in variant_group])
                        elif variant_group is not None:
                            variants.append(str(variant_group))
            answer_mode_raw = item.get("answer_mode", "open_entry")
            answer_mode = str(answer_mode_raw).lower() if isinstance(answer_mode_raw, str) else "open_entry"
            options_raw = item.get("options", [])
            options: List[str] = [str(o) for o in options_raw] if isinstance(options_raw, list) else []
            blank_token = uuid.uuid4().hex
            blank_tokens: List[str] = []
            prompt_with_token = prompt
            if variants_per_blank:
                num_blanks = len(variants_per_blank)
                # Find existing blank markers in order; fallback to numbered [blank1], [blank2], etc.
                import re
                markers = re.findall(r"\[blank\d*\]", prompt) or []
                for idx in range(num_blanks):
                    token = uuid.uuid4().hex
                    display_token = f"[{token}]"
                    blank_tokens.append(token)
                    if idx < len(markers):
                        prompt_with_token = prompt_with_token.replace(markers[idx], display_token, 1)
                    else:
                        prompt_with_token += f" [ {display_token} ]"
            else:
                display_token = f"[{blank_token}]"
                prompt_with_token = prompt.replace("[blank]", display_token)
                if prompt_with_token == prompt:
                    prompt_with_token = f"{prompt} [{display_token}]"
            q = FITBQuestion(
                qtype="FITB",
                prompt=prompt_with_token,
                render_mode=render_mode,
                points=pts,
                points_set=pts_set,
                variants=variants,
                blank_token=blank_token,
                answer_mode=answer_mode,
                options=options,
                variants_per_blank=variants_per_blank,
                blank_tokens=blank_tokens,
                parent_stimulus_ident=parent_stimulus,
                forced_ident=forced_ident,
            )
        elif qtype == "ESSAY":
            q = EssayQuestion(qtype="ESSAY", prompt=prompt, render_mode=render_mode, points=pts, points_set=pts_set, parent_stimulus_ident=parent_stimulus, forced_ident=forced_ident)
        elif qtype == "FILEUPLOAD":
            q = FileUploadQuestion(qtype="FILEUPLOAD", prompt=prompt, render_mode=render_mode, points=pts, points_set=pts_set, parent_stimulus_ident=parent_stimulus, forced_ident=forced_ident)
        elif qtype == "ORDERING":
            items = [OrderingItem(text=text, ident=str(uuid.uuid4())) for text in item.get("items", [])]
            q = OrderingQuestion(
                qtype="ORDERING",
                prompt=prompt,
                render_mode=render_mode,
                points=pts,
                points_set=pts_set,
                items=items,
                header=item.get("header"),
                parent_stimulus_ident=parent_stimulus,
                forced_ident=forced_ident,
            )
        elif qtype == "CATEGORIZATION":
            categories = item.get("categories", [])
            category_idents = {cat: str(uuid.uuid4()) for cat in categories}
            cat_items: List[CategoryMapping] = []
            for entry in item.get("items", []):
                label = entry.get("label")
                cat = entry.get("category")
                item_ident = str(uuid.uuid4())
                cat_items.append(CategoryMapping(item_text=str(label), item_ident=item_ident, category_name=str(cat)))
            distractors = item.get("distractors", []) or []
            distractor_idents = {d: str(uuid.uuid4()) for d in distractors}
            q = CategorizationQuestion(
                qtype="CATEGORIZATION",
                prompt=prompt,
                render_mode=render_mode,
                points=pts,
                points_set=pts_set,
                categories=categories,
                category_idents=category_idents,
                items=cat_items,
                distractors=list(distractors),
                distractor_idents=distractor_idents,
                parent_stimulus_ident=parent_stimulus,
                forced_ident=forced_ident,
            )
        elif qtype == "NUMERICAL":
            q = _convert_numerical(item, pts, pts_set, parent_stimulus, forced_ident, render_mode=render_mode)
        else:
            logger.warning("Unsupported item type '%s' encountered; skipping", qtype)
            continue

        questions.append(q)

    # Build item_id → {choice_id: choice_text} so rationale entries can be
    # matched by choice text later (choice order may be shuffled by balance_answers).
    item_choice_text: dict = {}
    for _item in packaged.items:
        _iid = _item.get("id")
        if _iid:
            item_choice_text[_iid] = {
                _c.get("id"): _c.get("text", "") for _c in _item.get("choices", [])
            }

    rationale_entries = []
    for r in packaged.rationales:
        if r.choices:
            choice_texts = item_choice_text.get(r.item_id, {})
            rationale_entries.append({
                "item_id": r.item_id,
                "choices": [
                    {
                        "id": c.id,
                        "correct": c.correct,
                        "rationale": c.rationale,
                        "text": choice_texts.get(c.id, ""),
                    }
                    for c in r.choices
                ],
            })
        elif getattr(r, "text", None):
            rationale_entries.append({"item_id": r.item_id, "rationale": r.text})

    title = packaged.title or "Untitled Quiz"
    instructions = getattr(packaged, 'instructions', None) or ""
    return Quiz(title=title, questions=questions, rationales=rationale_entries, instructions=instructions)


def _convert_numerical(item: dict, pts: float, pts_set: bool, parent_stimulus: Optional[str], forced_ident: Optional[str], *, render_mode: str = "verbatim") -> NumericalQuestion:
    evaluation = item.get("evaluation", {}) or {}
    mode = evaluation.get("mode", "exact")
    answer_raw = item.get("answer")
    answer = _safe_decimal(answer_raw)

    margin = _safe_decimal(evaluation.get("value")) if "value" in evaluation else None
    range_min = _safe_decimal(evaluation.get("min")) if "min" in evaluation else None
    range_max = _safe_decimal(evaluation.get("max")) if "max" in evaluation else None
    precision = None
    if "value" in evaluation and mode in ("significant_digits", "decimal_places"):
        try:
            precision = int(evaluation.get("value"))
        except (TypeError, ValueError):
            precision = None

    lower, upper, strict_lower = _bounds_for_numerical(answer, mode, margin, range_min, range_max, precision)
    answer_spec = NumericalAnswer(
        answer=answer,
        tolerance_mode=mode,
        margin_value=margin,
        range_min=range_min,
        range_max=range_max,
        precision_value=precision,
        lower_bound=lower,
        upper_bound=upper,
        strict_lower=strict_lower,
    )
    return NumericalQuestion(
        qtype="NUMERICAL",
        prompt=item.get("prompt", ""),
        render_mode=render_mode,
        points=pts,
        points_set=pts_set,
        answer=answer_spec,
        parent_stimulus_ident=parent_stimulus,
        forced_ident=forced_ident,
    )


def _bounds_for_numerical(answer, mode, margin, range_min, range_max, precision):
    """Reuse text parser logic to keep bounds consistent."""
    try:
        lower, upper, strict_lower = _resolve_numerical_bounds(
            answer=answer,
            mode=mode,
            margin=margin,
            range_min=range_min,
            range_max=range_max,
            precision=precision,
        )
        return lower, upper, strict_lower
    except Exception:
        # Fallback to exact bounds when inputs are insufficient.
        if answer is None:
            answer = Decimal("0")
        return answer, answer, False


def _resolve_numerical_bounds(*, answer, mode, margin, range_min, range_max, precision):
    """Compute Canvas's accepted numerical bounds from a QuizForge item."""
    if mode == "exact":
        if answer is None:
            raise ValueError("Numerical question requires an Answer value.")
        return answer, answer, False
    if mode == "percent_margin":
        if answer is None or margin is None:
            raise ValueError("Percent margin requires Answer and Tolerance.")
        offset = answer.copy_abs() * (abs(margin) / Decimal("100"))
        return answer - offset, answer + offset, False
    if mode == "absolute_margin":
        if answer is None or margin is None:
            raise ValueError("Absolute margin requires Answer and Tolerance.")
        offset = abs(margin)
        return answer - offset, answer + offset, False
    if mode == "range":
        if range_min is None or range_max is None:
            raise ValueError("Range requires minimum and maximum.")
        return range_min, range_max, False
    if mode in {"significant_digits", "decimal_places"}:
        if answer is None or precision is None:
            raise ValueError("Precision mode requires Answer and Precision.")
        if precision < 0:
            raise ValueError("Precision must be non-negative.")
        if mode == "significant_digits":
            numeric = float(answer)
            exponent = math.floor(math.log10(abs(numeric))) if numeric else 0
            offset = (
                Decimal("0.5") * Decimal(10) ** (exponent - precision + 1)
                if numeric else Decimal("0.5")
            )
        else:
            offset = Decimal("0.5") * Decimal(10) ** (-precision)
        return answer - offset, answer + offset, True
    raise ValueError(f"Unsupported tolerance mode '{mode}'.")


def _safe_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_tf_answer(value) -> bool:
    """Parse TF answer from raw JSON field."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "t", "1", "yes"}:
            return True
        if lowered in {"false", "f", "0", "no"}:
            return False
    # Default to False if unclear, but avoid flipping True -> False
    return bool(value)
