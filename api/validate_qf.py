"""Validate QuizForge_Base-compliant fixtures.

Doubles as the first stage of the pusher: extract the JSON from the
<QUIZFORGE_JSON> envelope, parse it, and sanity-check QF compliance
(per-item rationales, required fields, type coverage, and rationale depth).

Run: py validate_qf.py
"""
import glob
import json
import os
import re

FOLDER = os.path.join("qf_materials", "qf quiz examples")

ALL_TYPES = {"STIMULUS", "STIMULUS_END", "MC", "MA", "TF", "MATCHING", "FITB",
             "ORDERING", "CATEGORIZATION", "NUMERICAL"}
WRITING_TYPES = {"ESSAY", "FILEUPLOAD"}
SCORED_SINGLE_RATIONALE = {"TF", "FITB", "MATCHING", "ORDERING", "NUMERICAL",
                            "CATEGORIZATION"}
PER_CHOICE_RATIONALE = {"MC", "MA"}
NO_RATIONALE = {"STIMULUS", "STIMULUS_END"}

ENVELOPE = re.compile(r"<QUIZFORGE_JSON>(.*?)</QUIZFORGE_JSON>", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")
_GENERIC_PHRASES = ("this is incorrect", "this is correct", "this is wrong", "not correct")
_TEACHER_PHRASES = ("ask the teacher", "ask your teacher", "see the teacher", "see your teacher")


def extract_json(text):
    m = ENVELOPE.search(text)
    return m.group(1).strip() if m else None


def _load(path):
    """Read ``path``, extract the envelope, and parse the JSON exactly once.

    Returns ``(name, data, problems)``. ``data`` is ``None`` and ``problems``
    is non-empty when the file has no envelope or the JSON does not parse.
    Both ``validate()`` and callers of ``advise()`` use this helper so the
    read-and-parse logic exists in exactly one place.
    """
    name = os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    payload = extract_json(raw)
    if payload is None:
        return name, None, [f"{name}: no <QUIZFORGE_JSON> envelope found"]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        return name, None, [f"{name}: INVALID JSON - {e}"]
    return name, data, []


def validate(path, seen_types):
    name, data, problems = _load(path)
    if data is None:
        return problems

    items = data.get("items", [])
    # An envelope with no items parses cleanly and satisfies every per-item rule
    # by having nothing to check, so without this it reports as valid and the
    # Create page offers to push an empty quiz to Canvas.
    if not items:
        problems.append(f"{name}: no questions in this draft (items is empty)")

    rationale_by_id = {}
    for r in data.get("rationales", []):
        if isinstance(r, dict) and r.get("item_id") is not None:
            rationale_by_id[r["item_id"]] = r

    for idx, it in enumerate(items, 1):
        t = it.get("type")
        seen_types.add(t)
        if t in WRITING_TYPES:
            problems.append(
                f"{name}: {t} item {it.get('id')!r} cannot be pushed as a Canvas New Quiz. "
                "Author each writing portion as a separate AssignmentForge assignment "
                "worth 100 points (for example, a matching ' - ECR' assignment)."
            )
            continue
        if t not in ALL_TYPES:
            problems.append(f"{name}: unknown type {t!r}")
            continue

        iid = it.get("id")

        # MC exactly one correct; MA at least two correct
        if t == "MC":
            n = sum(1 for c in it.get("choices", []) if c.get("correct"))
            if n != 1:
                problems.append(f"{name}: MC {iid!r} has {n} correct (need 1)")
            problems.extend(_check_letter_choice_ids(name, iid, it))
        if t == "MA":
            n = sum(1 for c in it.get("choices", []) if c.get("correct"))
            if n < 2:
                problems.append(f"{name}: MA {iid!r} has {n} correct (need >=2)")
            problems.extend(_check_letter_choice_ids(name, iid, it))

        if t == "FITB":
            problems.extend(_check_fitb_shape(name, iid, idx, it))

        if t not in PER_CHOICE_RATIONALE and t not in SCORED_SINGLE_RATIONALE:
            continue  # STIMULUS / STIMULUS_END carry no rationale

        if not iid:
            problems.append(
                f"{name}: item #{idx} ({t}) has no \"id\", so its rationale "
                f"cannot be matched to it. Give it a unique id."
            )
            continue

        entry = rationale_by_id.get(iid)
        if entry is None:
            if t in PER_CHOICE_RATIONALE:
                problems.append(
                    f"{name}: {t} item {iid!r} has no rationales entry. Add one "
                    f"with a \"choices\" array explaining the correct answer and "
                    f"every distractor."
                )
            else:
                problems.append(
                    f"{name}: {t} item {iid!r} has no rationales entry. Add one "
                    f"explaining why the correct answer is correct."
                )
            continue

        if t in PER_CHOICE_RATIONALE:
            problems.extend(_check_per_choice(name, t, iid, it, entry))
        else:
            problems.extend(_check_single(name, t, iid, entry))

    title = data.get("title", "(untitled)")
    grp = data.get("metadata", {}).get("variant_group", "-")
    label = data.get("metadata", {}).get("variant_label", "-")
    type_counts = {}
    for it in items:
        type_counts[it["type"]] = type_counts.get(it["type"], 0) + 1
    summary = ", ".join(f"{k}x{v}" for k, v in sorted(type_counts.items()))
    # This header prints before the caller reports `problems`, so calling every
    # draft OK here contradicts the rejection that follows a moment later.
    print(f"  {'OK  ' if not problems else 'BAD '}{name}")
    print(f"       title: {title}")
    print(f"       group: {grp}  |  variant: {label}")
    print(f"       items: {summary}")
    return problems


def _check_fitb_shape(name, iid, index, item):
    """Reject FITB shapes the planner cannot turn into a valid Canvas item."""
    problems = []
    prompt = str(item.get("prompt") or "")
    blanks = re.findall(r"\[blank\d*\]", prompt)
    accept = item.get("accept")
    mode = str(item.get("answer_mode", "open_entry") or "open_entry").lower()
    label = f"{name}: FITB item {iid!r}"
    if len(blanks) > 3:
        problems.append(f"{label} has {len(blanks)} blanks; use at most 3 linked blanks.")
    if len(blanks) > 1:
        if mode != "open_entry":
            problems.append(f"{label} multi-blank answer_mode {mode!r} is unsupported; use open_entry.")
        if not isinstance(accept, list) or len(accept) != len(blanks) or not all(isinstance(group, list) and group for group in accept):
            problems.append(f"{label} multi-blank accept must be one non-empty array per blank.")
        return problems

    if isinstance(accept, list) and len(accept) == 1 and isinstance(accept[0], list):
        # The parser accepts this equivalent single-blank form; the pusher
        # normalizes it before building the Canvas payload.
        accept = accept[0]
    if not isinstance(accept, list) or not accept or any(isinstance(value, list) for value in accept):
        problems.append(f"{label} accept must be a non-empty flat array for a single blank.")
    if mode in {"wordbank", "dropdown"}:
        options = item.get("options")
        if not isinstance(options, list) or not options:
            problems.append(f"{label} {mode} requires a non-empty options array.")
        elif isinstance(accept, list):
            option_values = {str(value).strip().casefold() for value in options}
            if not any(str(value).strip().casefold() in option_values for value in accept if not isinstance(value, list)):
                problems.append(f"{label} {mode} accept answer must appear in options.")
    return problems


def _check_letter_choice_ids(name, iid, item):
    ids = [choice.get("id") for choice in item.get("choices", [])
           if isinstance(choice, dict)]
    if not ids or not all(isinstance(value, str) and len(value) == 1 and value.isupper()
                          for value in ids):
        return []
    expected = [chr(ord("A") + offset) for offset in range(len(ids))]
    if ids != expected:
        return [
            f"{name}: {item.get('type')} item {iid!r} choice ids must be contiguous "
            f"letters starting at A (expected {', '.join(expected)}; found {', '.join(ids)})."
        ]
    return []


def _check_per_choice(name, t, iid, item, entry):
    """Hard-fail depth checks for one MC/MA rationale entry against its item.

    Names the question (the item), names what is missing or mismatched, and
    says what to add -- the entry does not get to pass by being merely present.
    """
    rchoices = entry.get("choices")
    if not rchoices:
        return [
            f"{name}: {t} item {iid!r} has no per-choice rationales. Add a "
            f"\"choices\" array to its rationale explaining the correct answer "
            f"and every distractor."
        ]

    item_choices = item.get("choices") or []
    n_item = len(item_choices)
    if len(rchoices) != n_item:
        return [
            f"{name}: {t} item {iid!r} has {n_item} answer choices but its "
            f"rationale lists {len(rchoices)}. Every choice needs its own "
            f"explanation."
        ]

    item_choice_ids = {c.get("id") for c in item_choices}
    problems = []
    empty = []
    for rc in rchoices:
        rc = rc or {}
        cid = rc.get("id")
        if cid not in item_choice_ids:
            problems.append(
                f"{name}: {t} item {iid!r} rationale choice id {cid!r} does not "
                f"match any choice on this item. Use one of this item's own "
                f"choice ids: {sorted(str(x) for x in item_choice_ids)}."
            )
        if not str(rc.get("rationale", "")).strip():
            empty.append(cid if cid is not None else "?")
    if empty:
        problems.append(
            f"{name}: {t} item {iid!r} rationale text is empty for choice(s) "
            f"{', '.join(map(str, empty))}. Each choice needs a specific explanation."
        )
    return problems


def _check_single(name, t, iid, entry):
    """Hard-fail depth check for one single-rationale entry.

    Covers TF/FITB/MATCHING/ORDERING/NUMERICAL/CATEGORIZATION.
    """
    text = str(entry.get("rationale", ""))
    if text.strip():
        return []
    return [
        f"{name}: {t} item {iid!r} rationale is empty. Add an explanation of "
        f"why the correct answer is correct."
    ]


def _strip_html(text):
    return _HTML_TAG.sub("", text or "")


def _sentence_count(text):
    stripped = _strip_html(text).strip()
    if not stripped:
        return 0
    parts = [p for p in _SENTENCE_END.split(stripped) if p.strip()]
    return len(parts)


def _word_count(text):
    return len(_strip_html(text).split())


def _advise_text(label, text, advisories):
    """Append style suggestions for one auto-graded rationale string.

    Heuristic and never blocking (decision D4): sentence/word counting
    misfires on things like "e.g.", "Dr.", and "3.14", which is exactly why
    these are advisories and not hard fails.
    """
    plain = _strip_html(text)
    lower = plain.lower()

    n_sentences = _sentence_count(text)
    if n_sentences != 2:
        advisories.append(
            f"{label}: reads as {n_sentences} sentence(s), not the usual two "
            f"(a concept sentence, then a sentence tying it to this choice)."
        )

    n_words = _word_count(text)
    if not (15 <= n_words <= 40):
        advisories.append(f"{label}: {n_words} word(s), outside the usual 15 to 40 word range.")

    for phrase in _GENERIC_PHRASES:
        if phrase in lower:
            advisories.append(
                f"{label}: rationale text reads as generic (\"{phrase}\"); "
                f"consider explaining why this specific choice is right or wrong."
            )
            break

    for phrase in _TEACHER_PHRASES:
        if phrase in lower:
            advisories.append(
                f"{label}: rationale tells the student to ask or see the "
                f"teacher instead of explaining the concept."
            )
            break


def advise(data):
    """Return style suggestions for auto-graded rationales. Never blocks a push.

    Writing items are rejected by ``validate`` and never reach this advisory path.
    """
    advisories = []
    if not data:
        return advisories

    rationale_by_id = {}
    for r in data.get("rationales", []):
        if isinstance(r, dict) and r.get("item_id") is not None:
            rationale_by_id[r["item_id"]] = r

    for it in data.get("items", []):
        t = it.get("type")
        iid = it.get("id")
        entry = rationale_by_id.get(iid) if iid else None
        if not entry:
            continue  # validate() already reports a missing entry as a hard fail

        if t in PER_CHOICE_RATIONALE:
            for rc in entry.get("choices") or []:
                rc = rc or {}
                text = str(rc.get("rationale", ""))
                if not text.strip():
                    continue  # validate() already reports empty choice text
                _advise_text(f"item {iid!r} choice {rc.get('id')!r}", text, advisories)
        elif t in SCORED_SINGLE_RATIONALE:
            text = str(entry.get("rationale", ""))
            if not text.strip():
                continue  # validate() already reports an empty rationale
            _advise_text(f"item {iid!r}", text, advisories)

    return advisories


def main():
    excluded_prefixes = ("af_", "nq_pull_", "pf_")
    paths = sorted(
        path
        for path in glob.glob(os.path.join(FOLDER, "*.txt"))
        if not os.path.basename(path).startswith(excluded_prefixes)
    )
    if not paths:
        print(f"No .txt fixtures found in {FOLDER}")
        return
    print(f"Validating {len(paths)} fixture(s) in {FOLDER}\n")
    seen_types = set()
    all_problems = []
    all_advisories = []
    for p in paths:
        all_problems += validate(p, seen_types)
        _, data, _ = _load(p)
        if data is not None:
            all_advisories += advise(data)
        print()

    print("=" * 60)
    missing = ALL_TYPES - seen_types
    print(f"Type coverage: {len(seen_types)}/{len(ALL_TYPES)} types present")
    if missing:
        print(f"  MISSING types across all fixtures: {sorted(missing)}")
    else:
        print("  All 10 live QuizForge types are represented across the set.")
    print()
    if all_problems:
        print(f"COMPLIANCE ISSUES ({len(all_problems)}):")
        for p in all_problems:
            print(f"  - {p}")
    else:
        print("No compliance issues found.")
    print()
    if all_advisories:
        print(f"ADVISORIES ({len(all_advisories)}):")
        for a in all_advisories:
            print(f"  ! {a}")
    else:
        print("No advisories.")


if __name__ == "__main__":
    main()
