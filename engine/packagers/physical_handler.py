"""Physical quiz output handler.

Converts validated Quiz objects into printable DOCX/PDF files.
Performs NO validation - trusts input is perfect.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Dict

from engine.core.quiz import Quiz
from engine.packaging.folder_creator import sanitize_filename
from engine.rendering.physical.styles.default_styles import (
    LOG_ANSWER_CHOICE_STATS,
    LOG_POINT_VALUE_DISTRIBUTION,
    LOG_QUESTION_LENGTH_STATS,
    LOG_STIMULUS_USAGE,
    MC_TWO_COLUMN_THRESHOLD,
)


def generate_physical_outputs(quiz: Quiz, output_folder: str) -> Dict[str, str]:
    """Generate live physical outputs via the shared HTML render substrate.

    The back-compat DOCX keys keep their original names:
    ``quiz_path`` and ``key_path`` point to editable DOCX files. PDF files are
    additive through ``quiz_pdf_path`` and ``key_pdf_path``.
    """

    from engine.rendering.physical.emit_docx import html_to_docx
    from engine.rendering.physical.emit_pdf import html_to_pdf
    from engine.rendering.physical.html_renderer import default_css_path, render_html
    from engine.rendering.physical.quiz_adapter import to_printdoc
    from engine.rendering.physical.reference_doc import build_reference_docx

    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)

    base = sanitize_filename(quiz.title) or "Untitled_Quiz"
    quiz_docx = out / f"{base}.docx"
    quiz_pdf = out / f"{base}.pdf"
    key_docx = out / f"{base}_KEY.docx"
    key_pdf = out / f"{base}_KEY.pdf"
    log_path = out / "physical_validation.log"
    log_path.write_text("", encoding="utf-8")

    printdoc = to_printdoc(quiz)
    quiz_html = render_html(printdoc, variant="quiz")
    key_html = render_html(printdoc, variant="key")

    with tempfile.TemporaryDirectory(prefix="ce-printdoc-") as tmp:
        reference_docx = build_reference_docx(str(Path(tmp) / "reference.docx"))
        _try_emit(
            lambda: html_to_docx(quiz_html, reference_docx, str(quiz_docx)),
            "quiz docx",
            log_path,
        )
        _try_emit(
            lambda: html_to_docx(key_html, reference_docx, str(key_docx)),
            "key docx",
            log_path,
        )

    css_path = default_css_path()
    _try_emit(lambda: html_to_pdf(quiz_html, css_path, str(quiz_pdf)), "quiz pdf", log_path)
    _try_emit(lambda: html_to_pdf(key_html, css_path, str(key_pdf)), "key pdf", log_path)

    rationale_path = _create_rationale_sheet(quiz, output_folder)
    _log_validation_stats(quiz, str(log_path))

    return {
        "quiz_path": str(quiz_docx) if quiz_docx.exists() else "",
        "quiz_pdf_path": str(quiz_pdf) if quiz_pdf.exists() else "",
        "key_path": str(key_docx) if key_docx.exists() else "",
        "key_pdf_path": str(key_pdf) if key_pdf.exists() else "",
        "rationale_path": rationale_path,
        "log_path": str(log_path),
    }


def _try_emit(fn: Callable[[], str], label: str, log_path: Path) -> None:
    try:
        fn()
    except RuntimeError as exc:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"PHYSICAL RENDER WARNING [{label}]: {exc}\n")


def _create_rationale_sheet(quiz: Quiz, output_folder: str) -> str:
    """Generate per-choice rationale/corrections sheet DOCX via CorrectionDocRenderer."""

    from engine.core.questions import MAQuestion, MCQuestion, StimulusEnd, StimulusItem
    from engine.rendering.correction_doc import CorrectionDocRenderer
    from engine.spec_engine.models import ChoiceRationale, PackagedQuiz, RationalesEntry

    choice_ids = "ABCDEFGHIJ"

    items: list = []
    for question in quiz.questions:
        if isinstance(question, (StimulusItem, StimulusEnd)):
            continue
        item_dict: dict = {
            "type": question.qtype,
            "id": getattr(question, "forced_ident", None) or question.qtype,
            "prompt": question.prompt,
        }
        if isinstance(question, (MCQuestion, MAQuestion)):
            item_dict["choices"] = [
                {"id": choice_ids[i], "text": c.text, "correct": c.correct}
                for i, c in enumerate(question.choices)
            ]
        items.append(item_dict)

    item_choices_by_id = {
        d["id"]: d.get("choices", [])
        for d in items
        if d.get("type") in ("MC", "MA")
    }

    rationales: list = []
    for rationale in quiz.rationales or []:
        if not isinstance(rationale, dict) or not rationale.get("item_id"):
            continue
        item_id = rationale["item_id"]

        if rationale.get("rationale") and not rationale.get("choices"):
            rationales.append(RationalesEntry(item_id=item_id, text=rationale["rationale"]))
            continue

        if not rationale.get("choices"):
            continue
        current_choices = item_choices_by_id.get(item_id, [])
        if not current_choices:
            continue

        text_to_stored = {}
        for choice in rationale["choices"]:
            key = choice.get("text", "").strip().lower()
            if key:
                text_to_stored[key] = choice

        new_choices: list = []
        for current in current_choices:
            current_text = current.get("text", "").strip().lower()
            stored = text_to_stored.get(current_text)
            if stored:
                new_choices.append(
                    ChoiceRationale(
                        id=current["id"],
                        correct=bool(current.get("correct")),
                        rationale=stored["rationale"],
                    )
                )

        if new_choices:
            rationales.append(RationalesEntry(item_id=item_id, choices=new_choices))

    packaged = PackagedQuiz(
        version="3.0-json",
        title=quiz.title,
        metadata={},
        items=items,
        rationales=rationales,
        instructions=getattr(quiz, "instructions", "") or "",
    )

    renderer = CorrectionDocRenderer()
    docx_bytes = renderer.render_docx(packaged)

    output_path = Path(output_folder) / f"{sanitize_filename(quiz.title)}_RATIONALE.docx"
    output_path.write_bytes(docx_bytes)
    return str(output_path)


def _log_validation_stats(quiz: Quiz, log_path: str) -> None:
    """Write validation statistics to the physical render log."""

    with open(log_path, "a", encoding="utf-8") as log:
        log.write("\n" + "=" * 60 + "\n")
        log.write("PHYSICAL QUIZ VALIDATION STATISTICS\n")
        log.write("=" * 60 + "\n\n")

        if LOG_ANSWER_CHOICE_STATS:
            _log_answer_choice_analysis(quiz, log)

        if LOG_QUESTION_LENGTH_STATS:
            _log_question_length_analysis(quiz, log)

        if LOG_POINT_VALUE_DISTRIBUTION:
            _log_point_distribution(quiz, log)

        if LOG_STIMULUS_USAGE:
            _log_stimulus_analysis(quiz, log)


def _log_answer_choice_analysis(quiz: Quiz, log) -> None:
    log.write("ANSWER CHOICE STATISTICS\n")
    log.write("-" * 40 + "\n")

    from engine.core.questions import MCQuestion

    mc_questions = [q for q in quiz.questions if isinstance(q, MCQuestion)]
    if not mc_questions:
        log.write("No multiple choice questions found.\n\n")
        return

    all_choice_lengths = []
    for question in mc_questions:
        for choice in question.choices:
            all_choice_lengths.append(len(choice.text))

    log.write(f"Total MC questions: {len(mc_questions)}\n")
    log.write(f"Average choice length: {sum(all_choice_lengths) / len(all_choice_lengths):.1f} chars\n")
    log.write(f"Min choice length: {min(all_choice_lengths)} chars\n")
    log.write(f"Max choice length: {max(all_choice_lengths)} chars\n")

    correct_positions = []
    for question in mc_questions:
        for idx, choice in enumerate(question.choices):
            if choice.correct:
                correct_positions.append(idx)
                break

    log.write("\nCorrect answer distribution:\n")
    for position in range(max(correct_positions) + 1):
        count = correct_positions.count(position)
        letter = chr(65 + position)
        log.write(f"  {letter}: {count} ({count / len(mc_questions) * 100:.1f}%)\n")

    two_col_eligible = 0
    for question in mc_questions:
        choice_lengths = [len(choice.text) for choice in question.choices]
        if all(length < MC_TWO_COLUMN_THRESHOLD for length in choice_lengths):
            two_col_eligible += 1
    log.write(f"\nQuestions eligible for 2-column layout: {two_col_eligible}/{len(mc_questions)}\n\n")


def _log_question_length_analysis(quiz: Quiz, log) -> None:
    log.write("QUESTION LENGTH STATISTICS\n")
    log.write("-" * 40 + "\n")

    question_lengths = [len(q.prompt) for q in quiz.questions]
    if not question_lengths:
        log.write("No questions found.\n\n")
        return

    log.write(f"Total questions: {len(quiz.questions)}\n")
    log.write(f"Average question length: {sum(question_lengths) / len(question_lengths):.1f} chars\n")
    log.write(f"Min question length: {min(question_lengths)} chars\n")
    log.write(f"Max question length: {max(question_lengths)} chars\n")

    long_threshold = 200
    long_questions = [
        (idx + 1, question)
        for idx, question in enumerate(quiz.questions)
        if len(question.prompt) > long_threshold
    ]

    if long_questions:
        log.write(f"\nQuestions over {long_threshold} chars (may need formatting review):\n")
        for question_number, question in long_questions:
            log.write(f"  Q{question_number}: {len(question.prompt)} chars\n")

    log.write("\n")


def _log_point_distribution(quiz: Quiz, log) -> None:
    log.write("POINT VALUE DISTRIBUTION\n")
    log.write("-" * 40 + "\n")

    from engine.core.questions import StimulusEnd, StimulusItem

    scorable_questions = [
        q for q in quiz.questions if not isinstance(q, (StimulusItem, StimulusEnd))
    ]
    point_values = [q.points for q in scorable_questions]
    total_points = sum(point_values)

    log.write(f"Total points: {total_points}\n")
    if not scorable_questions:
        log.write("No scorable questions found.\n\n")
        return

    log.write(f"Average points per question: {total_points / len(scorable_questions):.2f}\n")

    unique_values = sorted(set(point_values))
    log.write("\nPoint value breakdown:\n")
    for value in unique_values:
        count = point_values.count(value)
        log.write(f"  {value} pt: {count} questions ({count / len(scorable_questions) * 100:.1f}%)\n")

    log.write("\n")


def _log_stimulus_analysis(quiz: Quiz, log) -> None:
    log.write("STIMULUS USAGE STATISTICS\n")
    log.write("-" * 40 + "\n")

    from engine.core.questions import StimulusEnd, StimulusItem

    questions_with_stimuli = []
    stimulus_groups = {}
    current_stimulus = None

    for idx, question in enumerate(quiz.questions):
        if isinstance(question, StimulusItem):
            current_stimulus = question.prompt
            stimulus_groups.setdefault(current_stimulus, [])
        elif isinstance(question, StimulusEnd):
            current_stimulus = None
        elif current_stimulus:
            questions_with_stimuli.append(question)
            stimulus_groups[current_stimulus].append(idx + 1)

    log.write(f"Questions with stimuli: {len(questions_with_stimuli)}/{len(quiz.scorable_questions())}\n")

    if questions_with_stimuli:
        stimulus_lengths = [len(stimulus) for stimulus in stimulus_groups.keys()]
        log.write(f"Average stimulus length: {sum(stimulus_lengths) / len(stimulus_lengths):.1f} chars\n")
        log.write(f"Unique stimuli: {len(stimulus_groups)}\n")
        log.write(f"Questions per stimulus (avg): {len(questions_with_stimuli) / len(stimulus_groups):.1f}\n")

    log.write("\n")
