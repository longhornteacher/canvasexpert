"""QTI item metadata helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from engine.core.questions import (
    CategorizationQuestion,
    EssayQuestion,
    FITBQuestion,
    FileUploadQuestion,
    MAQuestion,
    MCQuestion,
    MatchingQuestion,
    OrderingQuestion,
    Question,
    StimulusItem,
    TFQuestion,
)


def question_type_for(question: Question) -> str:
    """Map a question object to the Canvas QTI question_type metadata value."""
    if isinstance(question, StimulusItem):
        return "text_only_question"
    if isinstance(question, MCQuestion):
        return "multiple_choice_question"
    if isinstance(question, TFQuestion):
        return "true_false_question"
    if isinstance(question, MAQuestion):
        return "multiple_answers_question"
    if isinstance(question, MatchingQuestion):
        return "matching_question"
    if isinstance(question, EssayQuestion):
        return "essay_question"
    if isinstance(question, FileUploadQuestion):
        return "file_upload_question"
    if isinstance(question, FITBQuestion):
        return "fill_in_multiple_blanks_question"
    if isinstance(question, OrderingQuestion):
        return "ordering_question"
    if isinstance(question, CategorizationQuestion):
        return "categorization_question"
    return question.qtype


def add_item_metadata(item: ET.Element, question: Question) -> None:
    """Attach Canvas metadata fields to an item element."""
    itemmetadata = ET.SubElement(item, "itemmetadata")
    qtimetadata = ET.SubElement(itemmetadata, "qtimetadata")

    _add_field(qtimetadata, "question_type", question_type_for(question))

    if isinstance(question, StimulusItem):
        _add_field(qtimetadata, "points_possible", "0.0")
    else:
        _add_field(qtimetadata, "points_possible", str(question.points))

    if _uses_calculator_none(question):
        _add_field(qtimetadata, "calculator_type", "none")

    if question.parent_stimulus_ident:
        _add_field(qtimetadata, "parent_stimulus_item_ident", question.parent_stimulus_ident)


def _uses_calculator_none(question: Question) -> bool:
    return isinstance(
        question,
        (
            MCQuestion,
            TFQuestion,
            MAQuestion,
            MatchingQuestion,
            EssayQuestion,
            FileUploadQuestion,
            FITBQuestion,
            OrderingQuestion,
            CategorizationQuestion,
        ),
    )


def _add_field(parent: ET.Element, label: str, entry: str) -> None:
    field = ET.SubElement(parent, "qtimetadatafield")
    ET.SubElement(field, "fieldlabel").text = label
    ET.SubElement(field, "fieldentry").text = entry
