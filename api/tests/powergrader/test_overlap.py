"""Shared-wording evidence laws: what counts as a match, and what never does."""
import pytest

from api.powergrader.overlap import attach_overlap_evidence, find_overlaps


def _run(tag: str, count: int) -> str:
    """``count`` distinct words that no other tag shares."""
    return " ".join(f"{tag}{i}" for i in range(count))


def _row(pseudonym: str, text: str, item_id: str = "1") -> dict:
    return {"pseudonym": pseudonym, "item_id": item_id, "text": text}


def test_wording_quoted_from_the_prompt_never_counts():
    """LAW: a run that also appears in the shared text is excluded, however long."""
    quoted = _run("prompt", 40)
    responses = [
        _row("Learner A", f"{_run('a', 3)} {quoted} {_run('aend', 3)}"),
        _row("Learner B", f"{_run('b', 3)} {quoted} {_run('bend', 3)}"),
    ]

    assert find_overlaps(responses, shared_text=f"Directions. {quoted}") == {}
    assert set(find_overlaps(responses)) == {"Learner A", "Learner B"}


@pytest.mark.parametrize("shared_words, reported", [(24, False), (25, True)])
def test_a_pair_is_reported_at_25_shared_words_and_not_at_24(shared_words, reported):
    """LAW: the threshold is inclusive, counted in word positions, case and punctuation aside."""
    shared = _run("shared", shared_words)
    loud = ", ".join(shared.upper().split())
    responses = [
        _row("Learner A", f"{_run('a', 5)} {shared} {_run('aend', 5)}"),
        _row("Learner B", f"{_run('b', 5)} {loud}. {_run('bend', 5)}"),
    ]

    found = find_overlaps(responses)

    if not reported:
        assert found == {}
        return
    assert [entry["shared_words"] for entry in found["Learner A"]] == [25]
    assert [entry["shared_words"] for entry in found["Learner B"]] == [25]
    entry = found["Learner A"][0]
    assert entry["with"] == "Learner B" and entry["item_id"] == "1"
    assert entry["share"] == round(25 / 35, 2)


def test_only_different_students_on_the_same_item_are_compared():
    """LAW: another item, or the same student twice, never produces a pair."""
    text = _run("same", 40)

    assert find_overlaps([_row("Learner A", text, "1"), _row("Learner B", text, "2")]) == {}
    assert find_overlaps([_row("Learner A", text, "1"), _row("Learner A", text, "1")]) == {}


def test_each_side_is_capped_and_samples_come_from_its_own_text():
    """LAW: at most max_pairs entries, largest first; at most 2 samples of at most 30 words."""
    pieces = [_run(f"seg{k}x", 30 + k) for k in range(7)]
    source = _row("Learner A", " ".join(f"{piece} gap{k}" for k, piece in enumerate(pieces)))
    others = [_row(f"Learner {k}", f"{_run(f'lead{k}x', 3)} {piece}") for k, piece in enumerate(pieces)]

    entries = find_overlaps([source, *others])["Learner A"]

    assert [entry["with"] for entry in entries] == [f"Learner {k}" for k in (6, 5, 4, 3, 2)]
    assert [entry["shared_words"] for entry in entries] == [36, 35, 34, 33, 32]
    assert all(len(entry["samples"]) == 1 for entry in entries)
    sample = entries[0]["samples"][0]
    assert len(sample.split()) == 30 and sample.startswith("seg6x0 seg6x1")

    first_run, second_run, third_run = (_run(tag, 10) for tag in ("x", "y", "z"))
    three_runs = f"{first_run} u1 {second_run} u2 {third_run}"
    pair = find_overlaps([_row("Learner A", three_runs), _row("Learner B", f"{third_run} v1 {first_run} v2 {second_run}")])
    assert pair["Learner A"][0]["shared_words"] == 30
    assert pair["Learner A"][0]["samples"] == [first_run, second_run]


def test_attaching_evidence_recomputes_the_whole_bundle_and_uses_every_shared_source():
    """CONTRACT: evidence lands only on rows with overlaps, replaces stale entries, and skips handed-out text."""
    handed_out = _run("handout", 30)
    copied = _run("copied", 30)

    def student(pseudonym, text, prompt=""):
        return {"pseudonym": pseudonym, "responses": [
            {"item_id": "1", "prompt": prompt, "response": text}]}

    bundle = {
        "shared_context": {"assignment_description": "Read it.", "materials": [{"text": handed_out}]},
        "students": [
            student("Learner A", f"{_run('a', 4)} {copied} amid {handed_out}"),
            student("Learner B", f"{_run('b', 4)} {copied} bmid {handed_out}"),
            student("Learner C", f"{_run('c', 4)} {_run('prompted', 30)}", prompt=_run("prompted", 30)),
            student("Learner D", f"{_run('d', 4)} {_run('prompted', 30)}", prompt=_run("prompted", 30)),
        ],
    }
    bundle["students"][2]["responses"][0]["evidence"] = {
        "overlap": [{"with": "Learner Z"}], "other": "kept"}

    attach_overlap_evidence(bundle, basis_text="Rubric text.")
    attach_overlap_evidence(bundle, basis_text="Rubric text.")

    rows = {s["pseudonym"]: s["responses"][0] for s in bundle["students"]}
    assert [e["with"] for e in rows["Learner A"]["evidence"]["overlap"]] == ["Learner B"]
    assert [e["with"] for e in rows["Learner B"]["evidence"]["overlap"]] == ["Learner A"]
    assert rows["Learner A"]["evidence"]["overlap"][0]["shared_words"] == 30
    assert rows["Learner C"]["evidence"] == {"other": "kept"}
    assert "evidence" not in rows["Learner D"]
