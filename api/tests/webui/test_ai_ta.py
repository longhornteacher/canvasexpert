from api.webui import ai_ta


def test_build_library_writes_expected_files(tmp_path):
    target = tmp_path / "AI Authoring"

    first = ai_ta.build_library(target)
    names = sorted(p.name for p in target.iterdir() if p.is_file())

    assert len(names) >= 6
    assert "START HERE - CanvasAgent.txt" in names
    assert "START HERE - Canvas Expert.txt" not in names
    assert "Author a Quiz (QuizForge).txt" in names
    assert "Author an Assignment (AssignmentForge).txt" in names
    assert "Author a Page (PageForge).txt" in names
    assert "About This Folder.txt" in names
    assert not any("Rubric" in name for name in names)

    for path in target.iterdir():
        if path.is_dir():
            continue
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        assert "{KIND}" not in text
        assert "{TAG}" not in text

    quiz_text = (target / "Author a Quiz (QuizForge).txt").read_text(encoding="utf-8")
    assert "PASTE THIS WHOLE FILE" not in quiz_text
    assert quiz_text.lstrip().startswith("# QuizForge")
    assert "STIMULUS is for actual content students must reference" in quiz_text

    sentinel = target / "START HERE - CanvasAgent.txt"
    sentinel.write_text(sentinel.read_text(encoding="utf-8") + "\nSENTINEL\n", encoding="utf-8")

    second = ai_ta.build_library(target)
    names2 = sorted(p.name for p in target.iterdir() if p.is_file())

    assert names2 == names
    assert "SENTINEL" in sentinel.read_text(encoding="utf-8")


def test_toolkit_subfolder_created(tmp_path):
    target = tmp_path / "AI Authoring"
    ai_ta.build_library(target)

    toolkit = target / "MagicSchool Toolkit"
    assert toolkit.is_dir(), "MagicSchool Toolkit subfolder should be created"

    toolkit_names = {p.name for p in toolkit.iterdir()}
    for tool in ["Quiz Author", "Assignment Author", "Page Author"]:
        assert f"{tool} — SETUP.txt" in toolkit_names, f"Missing SETUP for {tool}"
    assert not any("Rubric" in name for name in toolkit_names)
    for tool in ["Quiz Author", "Assignment Author", "Page Author"]:
        assert f"{tool} — INSTRUCTIONS.txt" not in toolkit_names

    assert not any("KNOWLEDGE" in n for n in toolkit_names)

    quiz_setup = (toolkit / "Quiz Author — SETUP.txt").read_text(encoding="utf-8")
    assert "../Author a Quiz (QuizForge).txt" in quiz_setup
    assert "../Reference/QuizForge_example_quiz.txt" in quiz_setup
    assert "../Reference/QF_MOD_ELA_Question_Design.md" in quiz_setup


def test_build_library_seeds_repo_default_docs_first(tmp_path, monkeypatch):
    default_ai_ta = tmp_path / "default_docs" / "AI Authoring"
    default_toolkit = default_ai_ta / "MagicSchool Toolkit"
    default_toolkit.mkdir(parents=True)

    (default_ai_ta / "START HERE - CanvasAgent.txt").write_text(
        "repo start here\n", encoding="utf-8"
    )
    (default_toolkit / "Essay Scorer — INSTRUCTIONS.txt").write_text(
        "repo toolkit instructions\n", encoding="utf-8"
    )

    monkeypatch.setattr(ai_ta, "DEFAULT_AI_TA_DIR", str(default_ai_ta))

    target = tmp_path / "seeded"
    ai_ta.build_library(target)

    assert (target / "START HERE - CanvasAgent.txt").read_text(encoding="utf-8") == "repo start here\n"
    assert (target / "MagicSchool Toolkit" / "Essay Scorer — INSTRUCTIONS.txt").read_text(encoding="utf-8") == "repo toolkit instructions\n"
