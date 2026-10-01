"""Parser for plain-text quiz outline format.

Converts the LLM-generated TXT format into Quiz domain models.
Performs minimal validation - just enough to parse successfully.
Full validation happens in the validation layer.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

from ..core.questions import (
    CategorizationQuestion,
    CategoryMapping,
    EssayQuestion,
    FITBQuestion,
    FileUploadQuestion,
    MAQuestion,
    MCChoice,
    MCQuestion,
    NumericalQuestion,
    MatchingPair,
    MatchingQuestion,
    OrderingItem,
    OrderingQuestion,
    Question,
    StimulusEnd,
    StimulusItem,
    TFQuestion,
)
from ..core.answers import NumericalAnswer
from ..core.quiz import Quiz
from ..utils.text_utils import rand8, sanitize_text


class TextOutlineParser:
    """Concrete parser for the TXT authoring format produced by the LLM."""
    
    def _split_quiz_and_rationales(self, text: str) -> Tuple[str, List[str]]:
        """Split input into quiz section and rationales section.
        
        Returns:
            Tuple of (quiz_text, rationales_list)
        """
        # Find rationales delimiter
        if '===RATIONALES===' not in text:
            return text, []
        
        parts = text.split('===RATIONALES===')
        quiz_text = parts[0]
        rationales_text = parts[1] if len(parts) > 1 else ""
        
        # Parse rationales (split on --- and extract Q#: content)
        rationales = []
        if rationales_text.strip():
            blocks = [b.strip() for b in rationales_text.split('---') if b.strip()]
            for block in blocks:
                # Skip the first empty block after ===RATIONALES===
                if not block or block.isspace():
                    continue
                # Extract content after "Q#:" 
                if ':' in block:
                    # Format is "Q1: explanation text"
                    parts = block.split(':', 1)
                    rationale = parts[1].strip()
                    rationales.append(rationale)
        
        return quiz_text, rationales
    
    def parse_file(self, filepath: str, title_override: Optional[str] = None) -> Quiz:
        """Parse a quiz from a file path.

        Args:
            filepath: File path to a .txt quiz outline
            title_override: Optional title to override quiz title from file

        Returns:
            Quiz: Parsed Quiz object from the file
        Raises:
            ValueError: If the file cannot be parsed or contains no questions
        """
        with open(filepath, "r", encoding="utf-8") as handle:
            content = handle.read()
        return self.parse_text(content, title_override=title_override, filename=os.path.basename(filepath))

    def parse_text(self, content: str, *, title_override: Optional[str] = None, filename: Optional[str] = None) -> Quiz:
        """Parse a quiz from text content (web-compatible, no file I/O).

        Args:
            content: Raw quiz text (plain-text outline format)
            title_override: Optional title to override quiz title
            filename: Used for default title if no title in content

        Returns:
            Quiz: Parsed quiz object with title and questions list

        Raises:
            ValueError: If quiz content is invalid (no questions, bad format, etc.)
        """
        # Split quiz and rationales first
        quiz_text, rationales = self._split_quiz_and_rationales(content)
        
        lines = [line.rstrip("\n") for line in quiz_text.split("\n")]

        title: Optional[str] = None
        target_total_points: Optional[float] = None
        blocks: List[List[str]] = []
        current_block: List[str] = []

        for raw in lines:
            line = raw.strip("\ufeff")
            if line.strip() == "---":
                if current_block:
                    blocks.append(current_block)
                    current_block = []
                continue

            if not blocks and not current_block:
                lowered = line.lower()
                if lowered.startswith("title:"):
                    title = line.split(":", 1)[1].strip()
                    continue
                if lowered.startswith("totalpoints:"):
                    try:
                        target_total_points = float(line.split(":", 1)[1].strip())
                    except ValueError as exc:
                        raise ValueError("TotalPoints must be a finite, nonnegative number.") from exc
                    if not math.isfinite(target_total_points) or target_total_points < 0:
                        raise ValueError("TotalPoints must be a finite, nonnegative number.")
                    continue
                if lowered.startswith("keeppoints:"):
                    # Retained as a recognized legacy header; authored values are never
                    # rescaled based on this flag.
                    continue

            current_block.append(raw)

        if current_block:
            blocks.append(current_block)

        if not title:
            if filename:
                title = os.path.splitext(filename)[0]
            else:
                title = "Untitled Quiz"
        if title_override:
            title = title_override

        questions: List[Question] = []
        current_stimulus_ident: Optional[str] = None

        for block in blocks:
            parsed = self._parse_block(block)
            if not parsed:
                continue
            if isinstance(parsed, StimulusEnd):
                current_stimulus_ident = None
                continue
            if isinstance(parsed, StimulusItem):
                parsed.points = 0.0
                parsed.forced_ident = parsed.forced_ident or f"stim_{rand8()}"
                current_stimulus_ident = parsed.forced_ident
            else:
                if current_stimulus_ident:
                    parsed.parent_stimulus_ident = current_stimulus_ident
            questions.append(parsed)

        if not questions:
            raise ValueError("No questions parsed. Ensure the file contains at least one '---' block.")

        scorable = [q for q in questions if not isinstance(q, (StimulusItem, StimulusEnd))]
        for index, question in enumerate(scorable, 1):
            try:
                points = float(question.points)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"Scored question {question.forced_ident or index} needs explicit finite, nonnegative Points."
                ) from exc
            if (not question.points_set or not math.isfinite(points) or points < 0):
                raise ValueError(
                    f"Scored question {question.forced_ident or index} needs explicit finite, nonnegative Points."
                )
        if target_total_points is not None:
            actual_total = sum(float(q.points) for q in scorable)
            if not math.isclose(actual_total, target_total_points, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(
                    f"TotalPoints ({target_total_points:g}) must equal authored question points ({actual_total:g})."
                )

        return Quiz(title=title, questions=questions, rationales=rationales)

    def _parse_block(self, lines: List[str]) -> Optional[Question]:
        """Parse a single quiz block (text between ------ delimiters) into a Question.

        This is the core parsing method. It processes Type, Prompt, Choices, Answer, Points, etc.

        Args:
            lines: Raw text lines from a single block

        Returns:
            Question: A parsed Question subclass (MCQuestion, TFQuestion, etc.), or None if empty block
            StimulusEnd: Special marker if block is STIMULUS_END

        Raises:
            ValueError: If Type is unsupported, required fields missing, or invalid format
        """
        qtype: Optional[str] = None
        points: Optional[float] = None
        points_explicit = False
        prompt_lines: List[str] = []
        choices: List[MCChoice] = []
        tf_answer: Optional[bool] = None
        pairs: List[MatchingPair] = []
        variants: List[str] = []
        ordering_items: List[str] = []
        ordering_header: Optional[str] = None
        categories: List[str] = []
        category_items: List[tuple[str, str]] = []  # (item_text, category_name)
        distractors: List[str] = []

        numerical_answer_value: Optional[Decimal] = None
        numerical_mode: str = "exact"
        numerical_margin_value: Optional[Decimal] = None
        numerical_range_min: Optional[Decimal] = None
        numerical_range_max: Optional[Decimal] = None
        numerical_precision_value: Optional[int] = None

        in_prompt = False
        in_choices = False
        in_pairs = False
        in_accept = False
        in_ordering_items = False
        in_categories = False
        in_category_items = False
        in_distractors = False

        for raw in lines:
            line = raw.strip()
            if not line:
                if in_prompt:
                    prompt_lines.append("")
                continue

            lowered = line.lower()
            if lowered.startswith("type:"):
                qtype = line.split(":", 1)[1].strip().upper()
                in_prompt = False
                in_choices = False
                in_pairs = False
                in_accept = False
                in_ordering_items = False
                in_categories = False
                in_category_items = False
                in_distractors = False
                continue
            if lowered.startswith("points:"):
                try:
                    points = float(line.split(":", 1)[1].strip())
                except ValueError:
                    points = 10.0
                points_explicit = True
                continue
            if lowered.startswith("prompt:"):
                in_prompt = True
                in_choices = False
                in_pairs = False
                in_accept = False
                in_ordering_items = False
                in_categories = False
                in_category_items = False
                in_distractors = False
                prompt_part = line.split(":", 1)[1].strip()
                if prompt_part:
                    prompt_lines.append(prompt_part)
                continue
            if lowered.startswith("header:"):
                ordering_header = line.split(":", 1)[1].strip()
                continue
            if lowered.startswith("items:"):
                if qtype == "ORDERING":
                    in_ordering_items = True
                    in_prompt = False
                    in_choices = False
                    in_pairs = False
                    in_accept = False
                    in_categories = False
                    in_distractors = False
                elif qtype == "CATEGORIZATION":
                    in_category_items = True
                    in_prompt = False
                    in_choices = False
                    in_pairs = False
                    in_accept = False
                    in_ordering_items = False
                    in_categories = False
                    in_distractors = False
                continue
            if lowered.startswith("categories:"):
                in_categories = True
                in_prompt = False
                in_choices = False
                in_pairs = False
                in_accept = False
                in_ordering_items = False
                in_category_items = False
                in_distractors = False
                continue
            if lowered.startswith("distractors:"):
                in_distractors = True
                in_prompt = False
                in_choices = False
                in_pairs = False
                in_accept = False
                in_ordering_items = False
                in_categories = False
                in_category_items = False
                continue
            if lowered.startswith("choices:"):
                in_choices = True
                in_prompt = False
                in_pairs = False
                in_accept = False
                in_ordering_items = False
                in_categories = False
                in_category_items = False
                in_distractors = False
                continue
            if lowered.startswith("pairs:"):
                in_pairs = True
                in_prompt = False
                in_choices = False
                in_accept = False
                in_ordering_items = False
                in_categories = False
                in_category_items = False
                in_distractors = False
                continue
            if lowered.startswith("accept:") or lowered.startswith("answers:"):
                in_accept = True
                in_prompt = False
                in_choices = False
                in_pairs = False
                in_ordering_items = False
                in_categories = False
                in_category_items = False
                in_distractors = False
                continue
            if lowered.startswith("answer:"):
                if qtype == "TF":
                    value = line.split(":", 1)[1].strip().lower()
                    if value in ("true", "t", "1", "yes"):
                        tf_answer = True
                    elif value in ("false", "f", "0", "no"):
                        tf_answer = False
                    else:
                        raise ValueError(f"Invalid TF Answer value: {value}")
                elif qtype == "NUMERICAL":
                    answer_raw = line.split(":", 1)[1].strip()
                    numerical_answer_value = self._parse_decimal_value(answer_raw)
                else:
                    raise ValueError(f"Unexpected Answer field for type {qtype}.")
                continue

            if lowered.startswith("tolerance:"):
                if qtype != "NUMERICAL":
                    raise ValueError("Tolerance field is only valid for NUMERICAL questions.")
                if numerical_mode in {"range", "significant_digits", "decimal_places"}:
                    raise ValueError("Numerical question cannot mix tolerance with range or precision settings.")
                tol_raw = line.split(":", 1)[1].strip()
                tol_mode, tol_value = self._parse_numerical_tolerance(tol_raw)
                numerical_mode = tol_mode
                numerical_margin_value = tol_value
                numerical_precision_value = None
                numerical_range_min = None
                numerical_range_max = None
                in_prompt = False
                continue

            if lowered.startswith("precision:"):
                if qtype != "NUMERICAL":
                    raise ValueError("Precision field is only valid for NUMERICAL questions.")
                if numerical_mode in {"percent_margin", "absolute_margin", "range"}:
                    raise ValueError("Numerical question cannot mix precision with tolerance or range settings.")
                prec_raw = line.split(":", 1)[1].strip()
                prec_mode, prec_value = self._parse_numerical_precision(prec_raw)
                numerical_mode = prec_mode
                numerical_precision_value = prec_value
                numerical_margin_value = None
                numerical_range_min = None
                numerical_range_max = None
                in_prompt = False
                continue

            if lowered.startswith("range:"):
                if qtype != "NUMERICAL":
                    raise ValueError("Range field is only valid for NUMERICAL questions.")
                if numerical_mode != "exact":
                    raise ValueError("Numerical question cannot combine range with other tolerance settings.")
                range_raw = line.split(":", 1)[1].strip()
                range_min, range_max = self._parse_numerical_range(range_raw)
                numerical_mode = "range"
                numerical_range_min = range_min
                numerical_range_max = range_max
                numerical_margin_value = None
                numerical_precision_value = None
                in_prompt = False
                continue

            if in_prompt:
                prompt_lines.append(raw)
                continue

            if in_choices:
                match = re.match(r"^-\s*\[(x|X|\s)\]\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid choice line. Use '- [x] text' or '- [ ] text'.")
                is_correct = match.group(1).strip().lower() == "x"
                text = match.group(2).strip()
                choices.append(MCChoice(text=text, correct=is_correct))
                continue

            if in_pairs:
                match = re.match(r"^-\s*(.+?)\s*=>\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid pair line. Use '- Term => Answer'.")
                term = sanitize_text(match.group(1).strip())
                answer = sanitize_text(match.group(2).strip())
                pairs.append(MatchingPair(prompt=term, answer=answer))
                continue

            if in_accept:
                match = re.match(r"^-\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid Accept line. Use '- variant'.")
                variants.append(sanitize_text(match.group(1).strip()))
                continue

            if in_ordering_items:
                # Parse numbered items: "1. Item text"
                match = re.match(r"^\d+\.\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid ordering item line. Use '1. Item text'.")
                ordering_items.append(sanitize_text(match.group(1).strip()))
                continue

            if in_categories:
                # Parse category names: "- Category Name"
                match = re.match(r"^-\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid category line. Use '- Category Name'.")
                categories.append(sanitize_text(match.group(1).strip()))
                continue

            if in_category_items:
                # Parse items with mapping: "- Item => Category"
                match = re.match(r"^-\s*(.+?)\s*=>\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid categorization item line. Use '- Item => Category'.")
                item_text = sanitize_text(match.group(1).strip())
                category_name = sanitize_text(match.group(2).strip())
                category_items.append((item_text, category_name))
                continue

            if in_distractors:
                # Parse distractor items: "- Distractor text"
                match = re.match(r"^-\s*(.+)$", line)
                if not match:
                    raise ValueError("Invalid distractor line. Use '- Distractor text'.")
                distractors.append(sanitize_text(match.group(1).strip()))
                continue

        prompt = sanitize_text("\n".join(prompt_lines).strip())
        points_value = points if points is not None else 10.0

        if not qtype:
            return None

        if qtype in {"ENDSTIMULUS", "END_STIMULUS", "STIMULUS_END", "UNLINK", "DETACH"}:
            return StimulusEnd(qtype="STIMULUS_END", prompt="", points=0.0)
        if qtype == "MC":
            if len(choices) < 2 or len(choices) > 7:
                raise ValueError("MC questions must have 2-7 choices.")
            if sum(1 for c in choices if c.correct) != 1:
                raise ValueError("MC questions require exactly one correct choice.")
            for choice in choices:
                choice.text = sanitize_text(choice.text)
            return MCQuestion(qtype="MC", prompt=prompt, points=points_value, points_set=points_explicit, choices=choices)
        if qtype == "TF":
            if tf_answer is None:
                raise ValueError("TF question missing 'Answer: true|false'.")
            return TFQuestion(qtype="TF", prompt=prompt, points=points_value, points_set=points_explicit, answer_true=tf_answer)
        if qtype == "MA":
            if len(choices) < 2:
                raise ValueError("MA questions require at least two choices.")
            if sum(1 for c in choices if c.correct) < 1:
                raise ValueError("MA questions require at least one correct choice.")
            for choice in choices:
                choice.text = sanitize_text(choice.text)
            return MAQuestion(qtype="MA", prompt=prompt, points=points_value, points_set=points_explicit, choices=choices)
        if qtype == "NUMERICAL":
            lower_bound, upper_bound, strict_lower = self._resolve_numerical_bounds(
                answer=numerical_answer_value,
                mode=numerical_mode,
                margin=numerical_margin_value,
                range_min=numerical_range_min,
                range_max=numerical_range_max,
                precision=numerical_precision_value,
            )
            answer_spec = NumericalAnswer(
                answer=numerical_answer_value,
                tolerance_mode=numerical_mode,
                margin_value=numerical_margin_value,
                range_min=numerical_range_min,
                range_max=numerical_range_max,
                precision_value=numerical_precision_value,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                strict_lower=strict_lower,
            )
            question = NumericalQuestion(qtype="NUMERICAL", prompt=prompt, points=points_value, points_set=points_explicit, answer=answer_spec)
            errors = question.validate()
            if errors:
                raise ValueError("Numerical question invalid: " + "; ".join(errors))
            return question
        if qtype == "MATCHING":
            if len(pairs) < 2:
                raise ValueError("Matching questions require at least two pairs.")
            return MatchingQuestion(qtype="MATCHING", prompt=prompt, points=points_value, points_set=points_explicit, pairs=pairs)
        if qtype == "FITB":
            if not variants:
                raise ValueError("FITB questions require an Accept: list.")
            token = uuid.uuid4().hex
            display_token = f"[{token}]"
            replaced = prompt.replace("[blank]", display_token)
            if replaced == prompt:
                replaced = prompt + f" [ {display_token} ]"
            return FITBQuestion(qtype="FITB", prompt=replaced, points=points_value, points_set=points_explicit, variants=variants, blank_token=token)
        if qtype == "ESSAY":
            return EssayQuestion(qtype="ESSAY", prompt=prompt, points=points_value, points_set=points_explicit)
        if qtype == "FILEUPLOAD":
            return FileUploadQuestion(qtype="FILEUPLOAD", prompt=prompt, points=points_value, points_set=points_explicit)
        if qtype == "STIMULUS":
            return StimulusItem(qtype="STIMULUS", prompt=prompt, points=0.0, points_set=False, forced_ident=f"stim_{rand8()}")
        if qtype == "ORDERING":
            if len(ordering_items) < 2:
                raise ValueError("ORDERING questions require at least 2 items.")
            items_with_idents = [
                OrderingItem(text=item_text, ident=str(uuid.uuid4()))
                for item_text in ordering_items
            ]
            return OrderingQuestion(
                qtype="ORDERING",
                prompt=prompt,
                points=points_value,
                points_set=points_explicit,
                items=items_with_idents,
                header=ordering_header
            )
        if qtype == "CATEGORIZATION":
            if len(categories) < 2:
                raise ValueError("CATEGORIZATION questions require at least 2 categories.")
            if len(category_items) < 1:
                raise ValueError("CATEGORIZATION questions require at least 1 item.")
            # Generate UUIDs for categories
            category_idents = {cat: str(uuid.uuid4()) for cat in categories}
            # Generate UUIDs for items
            all_item_texts = list(set(item_text for item_text, _ in category_items))
            distractor_set = set(distractors)
            item_idents = {item: str(uuid.uuid4()) for item in all_item_texts}
            distractor_idents = {dist: str(uuid.uuid4()) for dist in distractor_set}
            # Create mappings
            mappings = [
                CategoryMapping(
                    item_text=item_text,
                    item_ident=item_idents[item_text],
                    category_name=cat_name
                )
                for item_text, cat_name in category_items
            ]
            return CategorizationQuestion(
                qtype="CATEGORIZATION",
                prompt=prompt,
                points=points_value,
                points_set=points_explicit,
                categories=categories,
                category_idents=category_idents,
                items=mappings,
                distractors=list(distractor_set),
                distractor_idents=distractor_idents
            )
        if qtype == "STIMULUS_END":
            return StimulusEnd(qtype="STIMULUS_END", prompt="", points=0.0, points_set=False)

        raise ValueError(f"Unsupported Type: {qtype}.")

    @staticmethod
    def _parse_decimal_value(raw: str) -> Decimal:
        """Parse a string into a Decimal value for numerical questions.

        Args:
            raw: String representation of a number (e.g., "4.5", "1e-3", "3,000")

        Returns:
            Decimal: Parsed value

        Raises:
            ValueError: If string cannot be converted to Decimal
        """
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError("Numerical field requires a value.")
        cleaned = cleaned.replace(",", "")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Unable to parse numeric value '{raw}'.") from exc

    @staticmethod
    def _parse_numerical_tolerance(raw: str) -> tuple[str, Decimal]:
        """Parse tolerance specification from text (e.g., "5%" or "0.5").

        Returns mode and value:
        - "percent_margin": "5%" → ("percent_margin", 5.0)
        - "absolute_margin": "0.5" or "±0.5" → ("absolute_margin", 0.5)

        Args:
            raw: Tolerance text (e.g., "10%", "2.5", "+/- 1.0")

        Returns:
            tuple: (tolerance_mode, tolerance_value as Decimal)

        Raises:
            ValueError: If format is invalid or value is negative
        """
        normalized = raw.strip()
        if not normalized:
            raise ValueError("Tolerance value cannot be empty.")

        lowered = normalized.lower().replace("percentage", "%").replace("percent", "%")
        percent_match = re.search(r"([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*%", lowered)
        if percent_match:
            value = TextOutlineParser._parse_decimal_value(percent_match.group(1))
            return "percent_margin", abs(value)

        cleaned = normalized.replace("±", "").replace("+/-", "").replace("−", "-").strip()
        value = TextOutlineParser._parse_decimal_value(cleaned)
        return "absolute_margin", abs(value)

    @staticmethod
    def _parse_numerical_range(raw: str) -> tuple[Decimal, Decimal]:
        """Parse a range specification (e.g., "5 to 10").

        Args:
            raw: Range text (e.g., "5 to 10", "1-20")

        Returns:
            tuple: (min_value, max_value) as Decimals

        Raises:
            ValueError: If format is invalid or values are missing
        """
        normalized = raw.strip()
        if not normalized:
            raise ValueError("Range value cannot be empty.")
        normalized = normalized.replace("–", "-").replace("—", "-")
        match = re.match(
            r"^\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*(?:to|-)\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*$",
            normalized,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError("Range must use 'min to max' format (e.g., '5 to 10').")
        minimum = TextOutlineParser._parse_decimal_value(match.group(1))
        maximum = TextOutlineParser._parse_decimal_value(match.group(2))
        if minimum >= maximum:
            raise ValueError("Range minimum must be less than maximum.")
        return minimum, maximum

    @staticmethod
    def _parse_numerical_precision(raw: str) -> tuple[str, int]:
        """Parse precision specification for numerical questions.

        Supports:
        - Significant digits: "3 sig figs" → ("significant_digits", 3)
        - Decimal places: "2 decimal places" → ("decimal_places", 2)

        Args:
            raw: Precision text (e.g., "3 significant digits", "2 dp", "5 sig fig")

        Returns:
            tuple: (precision_mode, precision_value as int)

        Raises:
            ValueError: If format is invalid or value is negative
        """
        normalized = raw.strip().lower()
        if not normalized:
            raise ValueError("Precision value cannot be empty.")
        normalized = normalized.replace("sig figs", "significant digits").replace("sig fig", "significant digit")
        normalized = normalized.replace("sig.", "significant").replace("dp", " decimal places")
        digits_match = re.search(r"(-?\d+)", normalized)
        if not digits_match:
            raise ValueError("Precision must include a numeric value.")
        precision_value = int(digits_match.group(1))
        if precision_value < 0:
            raise ValueError("Precision value must be non-negative.")
        if "significant" in normalized or "sig" in normalized:
            return "significant_digits", precision_value
        if "decimal" in normalized or "place" in normalized:
            return "decimal_places", precision_value
        raise ValueError("Precision must specify significant digits or decimal places.")

    @staticmethod
    def _resolve_numerical_bounds(
        *,
        answer: Optional[Decimal],
        mode: str,
        margin: Optional[Decimal],
        range_min: Optional[Decimal],
        range_max: Optional[Decimal],
        precision: Optional[int],
    ) -> tuple[Decimal, Decimal, bool]:
        """Compute lower and upper bounds for a numerical answer based on tolerance/precision/range.

        This is the core logic for numerical grading. Given an answer and a tolerance mode,
        compute the inclusive bounds [lower_bound, upper_bound] that Canvas will accept.

        Supported modes:
        - "exact": Answer must match exactly (no tolerance)
        - "percent_margin": Answer ± X% (e.g., answer=10, margin=5% → [9.5, 10.5])
        - "absolute_margin": Answer ± X (e.g., answer=10, margin=2 → [8, 12])
        - "range": Accept any value in [range_min, range_max]
        - "significant_digits": Bounds based on sig figs rounding
        - "decimal_places": Bounds based on decimal place rounding

        Args:
            answer: The correct numerical answer
            mode: Tolerance mode (one of the above)
            margin: For percent/absolute margin modes
            range_min, range_max: For range mode
            precision: For sig digits/decimal places modes

        Returns:
            tuple: (lower_bound, upper_bound, strict_lower_bound_flag)
            strict_lower_bound_flag: True if lower bound should be exclusive (>), False for inclusive (>=)

        Raises:
            ValueError: If required parameters are missing or invalid for the mode
        """
        # CRITICAL: These bounds must match Canvas QTI's grading logic exactly.
        # If bounds are too loose or strict, Canvas will mark correct answers as wrong.
        # See engine/tests/unit/test_text_parser.py and api/README.md for Canvas quirks.
        if mode == "exact":
            if answer is None:
                raise ValueError("Numerical question requires an Answer value.")
            return answer, answer, False
        if mode == "percent_margin":
            if answer is None:
                raise ValueError("Percent margin mode requires an Answer value.")
            if margin is None:
                raise ValueError("Percent margin mode requires a Tolerance value.")
            offset = answer.copy_abs() * (abs(margin) / Decimal("100"))
            return answer - offset, answer + offset, False
        if mode == "absolute_margin":
            if answer is None:
                raise ValueError("Absolute margin mode requires an Answer value.")
            if margin is None:
                raise ValueError("Absolute margin mode requires a Tolerance value.")
            offset = abs(margin)
            return answer - offset, answer + offset, False
        if mode == "range":
            if range_min is None or range_max is None:
                raise ValueError("Range mode requires both minimum and maximum values.")
            return range_min, range_max, False
        if mode == "significant_digits":
            if answer is None or precision is None:
                raise ValueError("Significant digits mode requires Answer and Precision values.")
            lower, upper = TextOutlineParser._compute_significant_digit_bounds(answer, precision)
            return lower, upper, True
        if mode == "decimal_places":
            if answer is None or precision is None:
                raise ValueError("Decimal places mode requires Answer and Precision values.")
            lower, upper = TextOutlineParser._compute_decimal_place_bounds(answer, precision)
            return lower, upper, True
        raise ValueError(f"Unsupported tolerance mode '{mode}'.")

    @staticmethod
    def _compute_significant_digit_bounds(answer: Decimal, sig_digits: int) -> tuple[Decimal, Decimal]:
        if sig_digits < 0:
            raise ValueError("Significant digits must be non-negative.")
        numeric = float(answer)
        if numeric == 0.0:
            offset = Decimal("0.5")
        else:
            exponent = math.floor(math.log10(abs(numeric)))
            offset_exponent = exponent - sig_digits + 1
            offset = Decimal("0.5") * (Decimal(10) ** offset_exponent)
        lower = answer - offset
        upper = answer + offset
        return lower, upper
    @staticmethod
    def _compute_decimal_place_bounds(answer: Decimal, decimal_places: int) -> tuple[Decimal, Decimal]:
        if decimal_places < 0:
            raise ValueError("Decimal places must be non-negative.")
        offset = Decimal("0.5") * (Decimal(10) ** (-decimal_places))
        lower = answer - offset
        upper = answer + offset
        return lower, upper
