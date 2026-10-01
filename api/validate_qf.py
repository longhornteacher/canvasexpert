"""Validate QuizForge authored quiz envelopes.

Doubles as the first stage of the pusher: extract the JSON from the
<QUIZFORGE_JSON> envelope, parse it, and sanity-check supported quiz structure,
including the shape of any optional feedback supplied by the author.

Run: py validate_qf.py
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engine.validation.authored_points import authored_point_problems

FOLDER = os.path.join("qf_materials", "qf quiz examples")

ALL_TYPES = {"STIMULUS", "STIMULUS_END", "MC", "MA", "TF", "MATCHING", "FITB",
             "ORDERING", "CATEGORIZATION", "NUMERICAL"}
WRITING_TYPES = {"ESSAY", "FILEUPLOAD"}
# Declared target: absent means New Quizzes. Classic Quizzes are the only engine
# that can hold writing items and Differentiated Hub supports.
QUIZ_ENGINES = ("new", "classic")
CLASSIC_UNSUPPORTED_TYPES = {"ORDERING", "CATEGORIZATION"}
CLASSIC_UNSUPPORTED_NUMERIC_MODES = {"percent_margin", "decimal_places"}
SCORED_SINGLE_RATIONALE = {"TF", "FITB", "MATCHING", "ORDERING", "NUMERICAL",
                            "CATEGORIZATION"}
PER_CHOICE_RATIONALE = {"MC", "MA"}

ENVELOPE = re.compile(r"<QUIZFORGE_JSON>(.*?)</QUIZFORGE_JSON>", re.DOTALL)


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

    classic = data.get("quiz_engine", "new") == "classic"
    problems.extend(f"{name}: {problem}" for problem in engine_problems(data))

    for idx, it in enumerate(items, 1):
        t = it.get("type")
        seen_types.add(t)
        if t in WRITING_TYPES and classic:
            continue  # engine_problems owns the classic writing rules
        if t in WRITING_TYPES:
            problems.append(
                f"{name}: {t} item {it.get('id')!r} cannot be pushed as a Canvas New Quiz. "
                "Author each writing portion as a separate AssignmentForge assignment "
                "with teacher-chosen points (for example, a matching ' - ECR' assignment)."
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


def engine_problems(data):
    """One-sentence refusals for the declared ``quiz_engine``.

    Shared by ``validate`` (staging) and the planner (preview) so both agree.
    A New Quiz file yields only the engine-value and Hub-key refusals; its
    writing refusal stays where it always was.
    """
    if not isinstance(data, dict):
        return []
    engine = data.get("quiz_engine", "new")
    if engine not in QUIZ_ENGINES:
        return [f'quiz_engine must be "new" or "classic" (got {engine!r}).']
    problems = []
    problems.extend(authored_point_problems(data.get("items") or [], data.get("total_points")))
    problems.extend(_rationale_problems(data))
    if engine != "classic":
        problems.extend(f'{key} is only allowed when quiz_engine is "classic".'
                        for key in ("differentiation", "tiers") if key in data)
        return problems
    items = [it for it in data.get("items") or [] if isinstance(it, dict)]
    rationale_ids = {r.get("item_id") for r in data.get("rationales") or []
                     if isinstance(r, dict)}
    for it in items:
        problems.extend(classic_item_problems(it, rationale_ids))
    problems.extend(_classic_points_problems(items))
    problems.extend(_classic_hub_problems(data))
    return problems


def _rationale_problems(data):
    """Validate optional authored feedback without prescribing its coverage or style."""
    entries = data.get("rationales", [])
    if not isinstance(entries, list):
        return ['"rationales" must be an array when supplied.']
    items = data.get("items") or []
    items_by_id = {}
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        if not isinstance(item["id"], str):
            continue
        items_by_id.setdefault(item["id"], []).append(item)

    problems = []
    seen_items = set()
    for index, entry in enumerate(entries, 1):
        label = f"rationales entry #{index}"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be an object.")
            continue
        item_id = entry.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            problems.append(f"{label} needs a non-empty string item_id.")
            continue
        if item_id in seen_items:
            problems.append(f"{label} duplicates item_id {item_id!r}.")
            continue
        seen_items.add(item_id)
        matches = items_by_id.get(item_id, [])
        if len(matches) != 1:
            problems.append(f"{label} item_id {item_id!r} must match exactly one quiz item.")
            continue
        item = matches[0]
        item_type = item.get("type")
        if item_type not in PER_CHOICE_RATIONALE and item_type not in SCORED_SINGLE_RATIONALE:
            problems.append(f"{label} item_id {item_id!r} does not support rationale feedback.")
            continue
        if item_type in PER_CHOICE_RATIONALE:
            if "rationale" in entry:
                problems.append(f"{label} for {item_type} item {item_id!r} cannot include a single rationale field.")
            choices = entry.get("choices")
            if not isinstance(choices, list) or not choices:
                problems.append(f"{label} for {item_type} item {item_id!r} needs a choices array.")
                continue
            item_choices = item.get("choices")
            item_choice_ids = [c.get("id") for c in item_choices
                               if isinstance(c, dict) and isinstance(c.get("id"), str)] \
                if isinstance(item_choices, list) else []
            valid_ids = set(item_choice_ids)
            seen_choices = set()
            for choice_index, choice in enumerate(choices, 1):
                choice_label = f"{label} choice #{choice_index}"
                if not isinstance(choice, dict):
                    problems.append(f"{choice_label} must be an object.")
                    continue
                choice_id = choice.get("id")
                if not isinstance(choice_id, str) or not choice_id:
                    problems.append(f"{choice_label} needs a non-empty string id.")
                    continue
                if item_choice_ids.count(choice_id) != 1:
                    problems.append(
                        f"{choice_label} id {choice_id!r} does not match a choice on item {item_id!r}."
                    )
                elif choice_id in seen_choices:
                    problems.append(f"{choice_label} duplicates choice id {choice_id!r}.")
                else:
                    seen_choices.add(choice_id)
                text = choice.get("rationale")
                if not isinstance(text, str) or not text.strip():
                    problems.append(f"{choice_label} needs non-empty rationale text.")
        else:
            if "choices" in entry:
                problems.append(f"{label} for item {item_id!r} must use a single rationale string.")
            text = entry.get("rationale")
            if not isinstance(text, str) or not text.strip():
                problems.append(f"{label} for item {item_id!r} needs non-empty rationale text.")
    return problems


def classic_item_problems(item, rationale_ids):
    t = item.get("type")
    label = f"{t} item {item.get('id')!r}"
    if t in CLASSIC_UNSUPPORTED_TYPES:
        return [f"{label} cannot be a Classic Quiz question; use MATCHING or MC instead."]
    if t in WRITING_TYPES:
        problems = []
        if not str(item.get("prompt") or "").strip():
            problems.append(f"{label} needs a prompt.")
        if item.get("id") is not None and item.get("id") in rationale_ids:
            problems.append(f"{label} takes no rationale; remove its rationales entry.")
        return problems
    if t == "FITB":
        mode = str(item.get("answer_mode", "open_entry") or "open_entry").lower()
        if mode == "wordbank":
            return [f"{label} cannot use a word bank in a Classic Quiz; use answer_mode dropdown."]
        if item.get("fuzzy_match"):
            return [f"{label} cannot use fuzzy_match in a Classic Quiz."]
        if item.get("case_sensitive") is True:
            return [f"{label} cannot be case_sensitive in a Classic Quiz."]
        if mode == "dropdown" and not re.search(r"\[blank\d*\]", str(item.get("prompt") or "")):
            return [f"{label} needs a [blank] token in its prompt to hold the dropdown."]
    if t == "NUMERICAL":
        evaluation = item.get("evaluation")
        mode = str((evaluation.get("mode") if isinstance(evaluation, dict) else None) or "exact")
        if mode in CLASSIC_UNSUPPORTED_NUMERIC_MODES:
            return [f"{label} cannot use {mode} in a Classic Quiz; use exact, absolute_margin, range, or significant_digits."]
    return []


def _classic_points_problems(items):
    return []


def _classic_hub_problems(data):
    style, tiers = data.get("differentiation"), data.get("tiers")
    if "differentiation" not in data and "tiers" not in data:
        return []
    if style == "bridge":
        return ['differentiation "bridge" is not available in QuizForge; use "hub" for tier supports.']
    if style != "hub":
        return ['differentiation must be "hub" when a QuizForge file carries tiers.']
    if "tiers" not in data:
        return ['differentiation "hub" requires tiers.']
    from api.webui import af  # lazy: keeps the CLI validator free of app imports

    problems = []
    af._validate_tiers(tiers, problems, style="hub")
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


def advise(data):
    """Retain the staging API without imposing automatic feedback-style rules."""
    return []


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
