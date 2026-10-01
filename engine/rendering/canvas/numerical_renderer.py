"""QTI rendering helpers for numerical question type."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal

from engine.core.questions import NumericalAnswer, NumericalQuestion
from engine.utils.text_utils import rand8
from .html_formatter import html_mattext, htmlize_prompt


def build_numerical_item(question: NumericalQuestion, index: int) -> ET.Element:
    """Render a numerical question into a Canvas QTI <item> element."""
    item_ident = question.forced_ident or f"item_q{index:02d}_{rand8()}"
    item_title = f"Q{index:02d}"
    item = ET.Element("item", {"ident": item_ident, "title": item_title})

    itemmetadata = ET.SubElement(item, "itemmetadata")
    qtimetadata = ET.SubElement(itemmetadata, "qtimetadata")

    def meta_field(label: str, entry: str) -> None:
        field = ET.SubElement(qtimetadata, "qtimetadatafield")
        ET.SubElement(field, "fieldlabel").text = label
        ET.SubElement(field, "fieldentry").text = entry

    meta_field("question_type", "numerical_question")
    meta_field("calculator_type", "none")
    meta_field("points_possible", str(question.points))
    if question.parent_stimulus_ident:
        meta_field("parent_stimulus_item_ident", question.parent_stimulus_ident)

    presentation = ET.SubElement(item, "presentation")
    material = ET.SubElement(presentation, "material")
    material.append(html_mattext(htmlize_prompt(question.prompt, render_mode=getattr(question, "render_mode", "verbatim"))))

    response_str = ET.SubElement(presentation, "response_str", {"ident": "response1", "rcardinality": "Single"})
    render_fib = ET.SubElement(response_str, "render_fib", {"fibtype": "Decimal"})
    ET.SubElement(render_fib, "response_label", {"ident": "answer1"})

    _build_scoring(question.answer, item)
    return item


def _build_scoring(spec: NumericalAnswer, item: ET.Element) -> None:
    resprocessing = ET.SubElement(item, "resprocessing")
    outcomes = ET.SubElement(resprocessing, "outcomes")
    ET.SubElement(outcomes, "decvar", {"maxvalue": "100", "minvalue": "0", "varname": "SCORE", "vartype": "Decimal"})

    respcondition = ET.SubElement(resprocessing, "respcondition", {"continue": "No"})
    conditionvar = ET.SubElement(respcondition, "conditionvar")

    mode = spec.tolerance_mode
    if mode == "range":
        # Canvas requires varequal even for range-only questions
        # Use the midpoint of the range as the answer value if no answer specified
        if spec.answer is None:
            if spec.lower_bound is None or spec.upper_bound is None:
                raise ValueError("Range mode requires either an answer or computed bounds.")
            # Use midpoint as the answer for display purposes
            answer_val = (spec.lower_bound + spec.upper_bound) / 2
        else:
            answer_val = spec.answer
        
        # Canvas needs: <or><varequal>answer</varequal><and><bounds></and></or>
        or_elem = ET.SubElement(conditionvar, "or")
        varequal = ET.SubElement(or_elem, "varequal", {"respident": "response1"})
        varequal.text = _format_decimal(answer_val)
        
        and_elem = ET.SubElement(or_elem, "and")
        _append_bounds(and_elem, spec)
    else:
        attrs: dict[str, str] = {"respident": "response1"}
        if mode == "percent_margin":
            if spec.margin_value is None:
                raise ValueError("Percent margin mode requires a tolerance value.")
            attrs["margintype"] = "percent"
            attrs["margin"] = _format_decimal(spec.margin_value)
        elif mode == "absolute_margin":
            if spec.margin_value is None:
                raise ValueError("Absolute margin mode requires a tolerance value.")
            attrs["margintype"] = "absolute"
            attrs["margin"] = _format_decimal(spec.margin_value)
        elif mode == "significant_digits":
            if spec.precision_value is None:
                raise ValueError("Significant digits mode requires a precision value.")
            attrs["precisiontype"] = "significantDigits"
            attrs["precision"] = str(spec.precision_value)
        elif mode == "decimal_places":
            if spec.precision_value is None:
                raise ValueError("Decimal places mode requires a precision value.")
            attrs["precisiontype"] = "decimals"
            attrs["precision"] = str(spec.precision_value)

        if spec.answer is None:
            raise ValueError("Numerical question answer is required for non-range modes.")

        or_elem = ET.SubElement(conditionvar, "or")
        varequal = ET.SubElement(or_elem, "varequal", attrs)
        varequal.text = _format_decimal(spec.answer)

        and_elem = ET.SubElement(or_elem, "and")
        _append_bounds(and_elem, spec)

    ET.SubElement(respcondition, "setvar", {"action": "Set", "varname": "SCORE"}).text = "100"


def _append_bounds(parent: ET.Element, spec: NumericalAnswer) -> None:
    if spec.lower_bound is None or spec.upper_bound is None:
        raise ValueError("Numerical bounds must be computed before rendering.")

    lower_tag = "vargt" if spec.strict_lower else "vargte"
    lower = ET.SubElement(parent, lower_tag, {"respident": "response1"})
    lower.text = _format_decimal(spec.lower_bound)

    upper = ET.SubElement(parent, "varlte", {"respident": "response1"})
    upper.text = _format_decimal(spec.upper_bound)


def _format_decimal(value: Decimal) -> str:
    """
    Format a Decimal value for Canvas QTI.
    
    Canvas New Quizzes requires all numeric values in varequal elements
    to include a decimal point, even for integers and scientific notation.
    Examples: "4.0", "1.5E+6" not "4" or "1.5E6"
    """
    normalized = value.normalize()
    text = str(normalized)

    # CRITICAL: Canvas New Quizzes REQUIRES all numeric values in QTI to include a decimal point.
    # Even integers must be "4.0" not "4". This is non-standard but Canvas is strict.
    # See engine/tests/unit/test_qti_builder_fitb.py and api/README.md for Canvas quirks.

    # Handle scientific notation
    if "E" in text or "e" in text:
        # Canvas QTI: Scientific notation must have a decimal in the mantissa (e.g., "1.0E+3", not "1E+3").
        # If the mantissa is an integer, add ".0" to ensure compliance.
        # Ensure the mantissa has a decimal point
        if "E" in text:
            mantissa, exponent = text.split("E")
        else:
            mantissa, exponent = text.split("e")
            exponent = exponent.replace("e", "E")
        
        # Ensure mantissa has decimal point (Canvas requirement).
        # E.g., "2E+5" becomes "2.0E+5", not "2E5".
        if "." not in mantissa:
            mantissa += ".0"

        return f"{mantissa}E{exponent}"

    # For regular decimals (not scientific), strip trailing zeros but keep at least one decimal place.
    # "4.50" becomes "4.5", but "4.0" stays "4.0" (never becomes "4").
    if "." in text:
        # Don't strip trailing zeros - Canvas needs them
        # But we can strip excessive zeros while keeping at least one
        text = text.rstrip("0")
        if text.endswith("."):
            text += "0"
    else:
        # Integer without decimal - add .0
        text += ".0"
    
    if not text or text == ".0":
        return "0.0"
    
    return text
