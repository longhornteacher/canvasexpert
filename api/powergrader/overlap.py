"""Shared-wording evidence between SAFE responses.

Pure and deterministic: no I/O, no model, no detector score. Two responses to the
same item share wording when both contain the same run of words that the
assignment materials and the item prompt did not hand them. The scoring agent
reads the counts and short samples as evidence and weighs them itself; a match
here is something to look at, never a verdict.
"""
import re
from collections import defaultdict

_WORD = re.compile(r"[^\W_]+")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    """Lowercased alphanumeric words with their character spans in ``text``."""
    return [(m.group().lower(), m.start(), m.end()) for m in _WORD.finditer(text)]


def _shingles(words: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]


def _samples(entry: dict, positions: set[int], limit: int, width: int) -> list[str]:
    """The longest shared runs in ``entry``'s own text, each capped at ``width`` words."""
    runs: list[list[int]] = []
    for position in sorted(positions):
        if runs and position == runs[-1][-1] + 1:
            runs[-1].append(position)
        else:
            runs.append([position])
    longest = sorted(runs, key=lambda run: (-len(run), run[0]))[:max(0, limit)]
    tokens, text = entry["tokens"], entry["text"]
    return [
        " ".join(text[tokens[run[0]][1]:tokens[run[:max(1, width)][-1]][2]].split())
        for run in sorted(longest, key=lambda run: run[0])
    ]


def find_overlaps(
    responses: list[dict],
    *,
    shared_text: str = "",
    n: int = 8,
    min_shared_words: int = 25,
    max_pairs: int = 5,
    max_samples: int = 2,
    sample_words: int = 30,
) -> dict[str, list[dict]]:
    """Find wording that SAFE responses to the same item share.

    ``responses`` are ``{pseudonym, item_id, text}`` built from scrubbed text.
    Every ``n``-word run that also appears in ``shared_text`` (directions, rubric,
    shared materials, teacher context) is ignored, so quoting the prompt counts
    for nothing. Only responses with the same ``item_id`` and different
    pseudonyms are compared.

    For each response, ``shared_words`` counts its own word positions covered by a
    run it shares with another response; an entry is reported for that response
    once the count reaches ``min_shared_words``, so each side of a pair carries
    its own count and share. Returns ``{pseudonym: [entry, ...]}`` with entries
    ``{with, item_id, shared_words, share, samples}``, largest first, at most
    ``max_pairs`` per pseudonym. Samples are passages of the response's own text.
    """
    excluded = set(_shingles([word for word, _, _ in _tokens(str(shared_text or ""))], n))
    by_item: dict[str, list[dict]] = defaultdict(list)
    for response in responses or []:
        pseudonym = str(response.get("pseudonym") or "")
        text = str(response.get("text") or "")
        tokens = _tokens(text)
        if not pseudonym or len(tokens) < n:
            continue
        starts: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for start, shingle in enumerate(_shingles([word for word, _, _ in tokens], n)):
            if shingle not in excluded:
                starts[shingle].append(start)
        by_item[str(response.get("item_id") or "")].append(
            {"pseudonym": pseudonym, "text": text, "tokens": tokens, "starts": starts})

    found: dict[str, list[dict]] = defaultdict(list)
    for item_id, entries in by_item.items():
        holders: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        for entry in entries:
            for shingle in entry["starts"]:
                holders[shingle].append(entry)
        for entry in entries:
            covered: dict[str, set[int]] = defaultdict(set)
            for shingle, starts in entry["starts"].items():
                for other in holders[shingle]:
                    if other["pseudonym"] == entry["pseudonym"]:
                        continue
                    for start in starts:
                        covered[other["pseudonym"]].update(range(start, start + n))
            for other, positions in covered.items():
                if len(positions) < min_shared_words:
                    continue
                found[entry["pseudonym"]].append({
                    "with": other,
                    "item_id": item_id,
                    "shared_words": len(positions),
                    "share": round(len(positions) / len(entry["tokens"]), 2),
                    "samples": _samples(entry, positions, max_samples, sample_words),
                })
    return {
        pseudonym: sorted(
            rows, key=lambda row: (-row["shared_words"], row["with"], row["item_id"])
        )[:max(0, max_pairs)]
        for pseudonym, rows in sorted(found.items())
    }


def attach_overlap_evidence(bundle: dict, *, basis_text: str = "") -> None:
    """Recompute overlap evidence over a whole SAFE bundle, in place.

    Shared text is everything every student was handed: the scoring basis the
    caller supplies, the bundle's shared context and materials, and each item
    prompt. Earlier overlap evidence is dropped first, so a refreshed bundle is
    compared as a whole and a row that no longer overlaps loses its entry. Only
    rows with overlaps carry ``evidence``; other evidence keys are left alone.
    """
    students = [s for s in bundle.get("students") or [] if isinstance(s, dict)]
    shared = [str(basis_text or "")]
    context = bundle.get("shared_context")
    if isinstance(context, dict):
        shared.append(str(context.get("assignment_description") or ""))
        shared.extend(
            str(material.get("text") or "")
            for material in context.get("materials") or []
            if isinstance(material, dict)
        )
    responses = []
    for student in students:
        for response in student.get("responses") or []:
            if not isinstance(response, dict):
                continue
            shared.append(str(response.get("prompt") or ""))
            evidence = response.get("evidence")
            if isinstance(evidence, dict):
                evidence.pop("overlap", None)
                if not evidence:
                    response.pop("evidence")
            responses.append({
                "pseudonym": student.get("pseudonym"),
                "item_id": response.get("item_id"),
                "text": response.get("response"),
            })
    found = find_overlaps(responses, shared_text="\n\n".join(shared))
    for student in students:
        entries = found.get(str(student.get("pseudonym") or ""))
        if not entries:
            continue
        for response in student.get("responses") or []:
            if not isinstance(response, dict):
                continue
            rows = [entry for entry in entries
                    if entry["item_id"] == str(response.get("item_id") or "")]
            if rows:
                response.setdefault("evidence", {})["overlap"] = rows
