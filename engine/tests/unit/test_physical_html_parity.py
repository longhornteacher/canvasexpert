from __future__ import annotations

from pathlib import Path
import importlib.util

import pytest
from engine.core.answers import NumericalAnswer
from engine.core.questions import (
    CategorizationQuestion,
    CategoryMapping,
    EssayQuestion,
    FITBQuestion,
    MAQuestion,
    MCChoice,
    MCQuestion,
    MatchingPair,
    MatchingQuestion,
    NumericalQuestion,
    OrderingItem,
    OrderingQuestion,
    StimulusEnd,
    StimulusItem,
    TFQuestion,
)
from engine.core.quiz import Quiz
from engine.packagers.physical_handler import generate_physical_outputs
from engine.rendering.physical.html_renderer import render_html
from engine.rendering.physical.quiz_adapter import to_printdoc


def _coverage_quiz() -> Quiz:
    return Quiz(
        title="Physical Render Coverage",
        instructions="<p>Use the printed spaces to answer.</p>",
        questions=[
            StimulusItem(
                qtype="STIMULUS",
                prompt="<pre><code>for i in range(3):\n    print(i)</code></pre>",
                points=0,
                forced_ident="code_stim",
            ),
            MCQuestion(
                qtype="MC",
                prompt="<p>What does the loop change each time?</p>",
                points=5,
                choices=[
                    MCChoice("i", True),
                    MCChoice("range", False),
                    MCChoice("print", False),
                    MCChoice("3", False),
                ],
            ),
            StimulusEnd(qtype="STIMULUS_END", prompt="", points=0),
            MCQuestion(
                qtype="MC",
                prompt="<p>Which statement is deliberately long?</p>",
                points=5,
                choices=[
                    MCChoice("Short option", False),
                    MCChoice(
                        "This option is intentionally long enough to force the single-column path.",
                        True,
                    ),
                ],
            ),
            MAQuestion(
                qtype="MA",
                prompt="<p>Select the data types.</p>",
                points=5,
                choices=[
                    MCChoice("string", True),
                    MCChoice("integer", True),
                    MCChoice("loop", False),
                ],
            ),
            TFQuestion(qtype="TF", prompt="<p>Python uses indentation.</p>", points=5, answer_true=True),
            MatchingQuestion(
                qtype="MATCHING",
                prompt="<p>Match each term.</p>",
                points=5,
                pairs=[
                    MatchingPair(prompt="variable", answer="stores a value"),
                    MatchingPair(prompt="loop", answer="repeats work"),
                ],
            ),
            FITBQuestion(
                qtype="FITB",
                prompt="<p>A [blank] stores a value.</p>",
                points=5,
                variants=["variable"],
                blank_token="tok",
            ),
            NumericalQuestion(
                qtype="NUMERICAL",
                prompt="<p>What is 2 + 2?</p>",
                points=5,
                answer=NumericalAnswer(answer=4, lower_bound=4, upper_bound=4),
            ),
            OrderingQuestion(
                qtype="ORDERING",
                prompt="<p>Order the development steps.</p>",
                points=5,
                items=[
                    OrderingItem(text="Write code", ident="a"),
                    OrderingItem(text="Run code", ident="b"),
                ],
            ),
            CategorizationQuestion(
                qtype="CATEGORIZATION",
                prompt="<p>Sort each item.</p>",
                points=5,
                categories=["Keyword", "Operator"],
                items=[
                    CategoryMapping("if", "c1", "Keyword"),
                    CategoryMapping("+", "c2", "Operator"),
                ],
            ),
            StimulusItem(
                qtype="STIMULUS",
                prompt="```poetry\nFirst quiet line\nSecond bright line\nThird steady line\nFourth clear line\nFifth numbered line\n```",
                points=0,
                title="Classroom Poem",
                author="Fictional Author",
                forced_ident="poetry_stim",
            ),
            EssayQuestion(
                qtype="ESSAY",
                prompt="<p>Explain what the poem's fifth line does.</p>",
                points=10,
            ),
            StimulusEnd(qtype="STIMULUS_END", prompt="", points=0),
        ],
    )


def test_render_html_contains_quiz_content_and_key_rows():
    printdoc = to_printdoc(_coverage_quiz())
    quiz_html = render_html(printdoc, variant="quiz")
    key_html = render_html(printdoc, variant="key")

    expected_quiz_bits = [
        "Use the printed spaces to answer.",
        "for i in range(3):",
        "print(i)",
        "What does the loop change each time?",
        "This option is intentionally long enough",
        "Select the data types.",
        "A. True",
        "stores a value",
        "variable",
        "__________________",
        "What is 2 + 2?",
        "Write code",
        "Categories:",
        "Keyword",
        "Fifth numbered line",
        "Classroom Poem",
        "Fictional Author",
        "Explain what the poem",
    ]
    for text in expected_quiz_bits:
        assert text in quiz_html

    assert 'class="choice-table two-column"' in quiz_html
    assert '<ol class="choices" type="A">' in quiz_html

    expected_key_bits = [
        "Question #",
        "Correct Answer",
        "Total Points:",
        "<td>1</td>",
        "<td>A</td>",
        "<td>2</td>",
        "<td>B</td>",
        "<td>A, B</td>",
        "<td>True</td>",
        "variable -&gt; stores a value",
        "variable",
        "<td>4</td>",
        "See rubric",
        "55",
    ]
    for text in expected_key_bits:
        assert text in key_html


def test_generate_physical_outputs_uses_new_paths_with_mocked_emitters(monkeypatch, tmp_path):
    import engine.rendering.physical.emit_docx as emit_docx
    import engine.rendering.physical.emit_pdf as emit_pdf

    def fake_docx(html: str, reference_docx: str, out_path: str) -> str:
        Path(out_path).write_bytes(b"docx")
        return out_path

    def fake_pdf(html: str, out_path: str) -> str:
        Path(out_path).write_bytes(b"%PDF")
        return out_path

    monkeypatch.setattr(emit_docx, "html_to_docx", fake_docx)
    monkeypatch.setattr(emit_pdf, "html_to_pdf", fake_pdf)

    results = generate_physical_outputs(_coverage_quiz(), str(tmp_path))

    assert set(results) == {
        "quiz_path",
        "quiz_pdf_path",
        "key_path",
        "key_pdf_path",
        "rationale_path",
        "log_path",
    }
    for key in results:
        assert results[key]
        assert Path(results[key]).exists()

    assert results["quiz_path"].endswith(".docx")
    assert results["quiz_pdf_path"].endswith(".pdf")
    assert results["key_path"].endswith("_KEY.docx")
    assert results["key_pdf_path"].endswith("_KEY.pdf")


def test_generate_physical_outputs_logs_missing_engine_warnings(monkeypatch, tmp_path):
    import engine.rendering.physical.emit_docx as emit_docx
    import engine.rendering.physical.emit_pdf as emit_pdf

    def missing_docx(html: str, reference_docx: str, out_path: str) -> str:
        raise RuntimeError("Pandoc unavailable")

    def missing_pdf(html: str, out_path: str) -> str:
        raise RuntimeError("Microsoft Edge unavailable")

    monkeypatch.setattr(emit_docx, "html_to_docx", missing_docx)
    monkeypatch.setattr(emit_pdf, "html_to_pdf", missing_pdf)

    results = generate_physical_outputs(_coverage_quiz(), str(tmp_path))

    assert results["quiz_path"] == ""
    assert results["quiz_pdf_path"] == ""
    assert results["key_path"] == ""
    assert results["key_pdf_path"] == ""
    assert Path(results["rationale_path"]).exists()

    log_text = Path(results["log_path"]).read_text(encoding="utf-8")
    assert "PHYSICAL RENDER WARNING [quiz docx]: Pandoc unavailable" in log_text
    assert "PHYSICAL RENDER WARNING [quiz pdf]: Microsoft Edge unavailable" in log_text
    assert "PHYSICAL QUIZ VALIDATION STATISTICS" in log_text


def test_generate_physical_outputs_native_smoke_when_provisioned(tmp_path):
    if importlib.util.find_spec("pypandoc") is None:
        pytest.skip("pypandoc/pypandoc-binary is not installed")
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright is not installed")

    import pypandoc
    from engine.rendering.physical.emit_pdf import edge_executable_path

    try:
        pypandoc.get_pandoc_path()
    except OSError:
        pytest.skip("Pandoc binary is not available")

    if edge_executable_path() is None:
        pytest.skip("Microsoft Edge is not installed")

    results = generate_physical_outputs(_coverage_quiz(), str(tmp_path))

    for key in ("quiz_path", "quiz_pdf_path", "key_path", "key_pdf_path"):
        path = Path(results[key])
        assert path.exists()
        assert path.stat().st_size > 0
