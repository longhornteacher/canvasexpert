"""Feedback result parsing, validation, and private identity projection."""
import json
import re

from api.feedback_vault import IdentityVault
from api.feedback_contract import CONTRACT_VERSION

def _format_number(value) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return str(number)


def _draft_score_prefix(score, possible) -> str | None:
    if score is None:
        return None
    formatted = _format_number(score)
    possible_formatted = _format_number(possible) if possible is not None else None
    if possible_formatted is None:
        return f"Draft score: {formatted}"
    return f"Draft score: {formatted}/{possible_formatted}"


def _possible_by_key(bundle: dict | None) -> dict:
    possible = {}
    for s in (bundle or {}).get("students", []):
        for r in s.get("responses", []):
            possible[(s.get("pseudonym"), str(r.get("item_id", "")))] = r.get("possible")
    return possible


def render_results(results: list, *, bundle: dict | None = None,
                   grade_mode: str = "post_score") -> list:
    """Preserve authored feedback, adding only the explicit feedback-only score prefix."""
    possible = _possible_by_key(bundle)
    rendered = []
    for r in results:
        row = dict(r)
        item_id = str(row.get("item_id", ""))
        key = (row.get("pseudonym"), item_id)
        authored = row.get("feedback", "")
        row["authored_feedback"] = authored
        row["possible"] = possible.get(key)
        if grade_mode == "feedback_only" and row.get("score") is not None:
            prefix = _draft_score_prefix(row.get("score"), row["possible"])
            row["feedback"] = prefix if authored == "" else f"{prefix}\n\n{authored}"
        rendered.append(row)
    return rendered


def item_feedback_for_mode(item: dict, grade_mode: str) -> str:
    """Render a saved authored item from structure, never by rewriting its text."""
    feedback = item.get("feedback", "")
    if grade_mode != "feedback_only" or item.get("score") is None:
        return feedback
    prefix = _draft_score_prefix(item.get("score"), item.get("possible"))
    return prefix if feedback == "" else f"{prefix}\n\n{feedback}"


def _top_level_json_blocks(text: str):
    """Yield (start, end, value) for each decodable JSON value in `text`, left
    to right. A value's own nested arrays/objects are not reported again as
    separate blocks, since their span is already consumed by the outer one.
    """
    decoder = json.JSONDecoder()
    consumed_to = 0
    for match in re.finditer(r"[\[{]", text):
        start = match.start()
        if start < consumed_to:
            continue
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        yield start, end, value
        consumed_to = end


def _first_json_block(text: str):
    """Return the JSON results embedded in prose or one or more code fences.

    A model reply is not always one clean JSON value: it may lead with a
    sentence of prose that itself decodes as a small JSON object, or wrap
    each student in its own fenced object instead of one shared array.
    Collect every top-level decodable value instead of stopping at whichever
    comes first, then choose:

    - if any value is a list, the widest one wins (ties broken by the later
      one), since a wrapping array is almost certainly the real results and
      a short leading note must not shadow it;
    - otherwise, if two or more values look like a single result (each
      carries a `pseudonym`), treat the reply as one fenced object per
      student and combine them into a list;
    - otherwise, fall back to the widest decodable value, on the theory that
      the biggest block is more likely to be substance than an aside.
    """
    blocks = list(_top_level_json_blocks(text))
    if not blocks:
        raise ValueError("the model reply did not contain valid JSON results")

    def widest(candidates):
        return max(candidates, key=lambda block: (block[1] - block[0], block[0]))

    lists_found = [block for block in blocks if isinstance(block[2], list)]
    if lists_found:
        return widest(lists_found)[2]

    dicts_found = [block[2] for block in blocks if isinstance(block[2], dict)]
    result_like = [d for d in dicts_found if isinstance(d.get("pseudonym"), str) and d.get("pseudonym")]
    if len(result_like) > 1:
        return result_like
    if result_like:
        return result_like[0]

    return widest(blocks)[2]


def _unwrap_dict_results(data: dict) -> list:
    """Pull a results list out of a wrapper object.

    `results` is the documented key, but models substitute close synonyms
    (`scores`, `students`, ...). Accept any single list-valued key; when more
    than one qualifies, prefer whichever one's first element looks like a
    result object rather than guessing. A bare object with no wrapper at
    all, carrying its own `pseudonym`, is one result rather than none.
    """
    results = data.get("results")
    if isinstance(results, list):
        return results

    # A dict carrying its own `pseudonym` is one bare result object, not a
    # wrapper whose list-valued optional fields should be considered results.
    if isinstance(data.get("pseudonym"), str) and data.get("pseudonym"):
        return [data]

    list_valued = [v for v in data.values() if isinstance(v, list)]
    if len(list_valued) == 1:
        return list_valued[0]
    if len(list_valued) > 1:
        for value in list_valued:
            first = value[0] if value else None
            if isinstance(first, dict) and first.get("pseudonym"):
                return value
        return []  # several lists, none look like results: don't guess

    return []


def parse_results(text: str) -> list:
    """Parse the LLM's result JSON: an array, an object wrapping the results
    under `results` (or a close synonym), or a single bare result object.

    Models routinely wrap the JSON in a markdown code fence, lead with a
    sentence of prose, or reshape the wrapper entirely; tolerate all of that
    rather than quietly returning zero results for the batch.
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("the model reply was empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _first_json_block(raw)
    if isinstance(data, dict):
        data = _unwrap_dict_results(data)
    return data if isinstance(data, list) else []


def validate_results(results, bundle: dict = None, vault: IdentityVault = None,
                     *, contract_version: str = CONTRACT_VERSION) -> dict:
    """Validate scoring output against the Feedback Scoring Contract (v2).

    See docs/contracts/feedback-scoring-contract.md. Accepts either the wrapped
    object ({contract_version, results:[...]}) or a bare array. `bundle` and
    `vault` are optional cross-checks: with them we confirm every result maps to
    a real (pseudonym, item_id) the LLM was actually given and scores fit the
    item's max. Returns {ok, errors:[...],
    warnings:[...], n:int, fields:[...]}. `fields` names the distinct fields
    behind the errors. `ok` is False only on hard errors -- out-of-range scores
    and uncovered students are warnings, since the teacher reviews before any
    push.
    """
    errors, warnings = [], []
    fields: set[str] = set()

    def _error(where: str, field: str, message: str) -> None:
        errors.append(f"{where}: {message}")
        fields.add(field)

    if isinstance(results, dict):
        ver = str(results.get("contract_version", "") or "")
        if ver and ver.split(".")[0] != str(contract_version).split(".")[0]:
            warnings.append(f"contract_version {ver} != expected {contract_version}")
        results = results.get("results", [])
    if not isinstance(results, list):
        return {"ok": False, "errors": ["top-level results is not a list/array"],
                "warnings": [], "n": 0, "fields": []}

    expected = set()
    held_keys = set()
    possible = _possible_by_key(bundle) if bundle else {}
    if bundle:
        for s in bundle.get("students", []):
            for r in s.get("responses", []):
                key = (s.get("pseudonym"), str(r.get("item_id", "")))
                expected.add(key)
                # A required-file hold is structural: readable partial text must
                # not let a held item be scored as complete.
                if r.get("_held"):
                    held_keys.add(key)

    seen = set()
    for i, r in enumerate(results):
        where = f"results[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where}: not an object")
            continue
        ps, it = r.get("pseudonym"), str(r.get("item_id", ""))
        if not ps or not isinstance(ps, str):
            _error(where, "pseudonym", "missing/invalid 'pseudonym'")
        if not it:
            _error(where, "item_id", "missing 'item_id'")

        if "score" not in r:
            _error(where, "score", "missing 'score'")

        feedback = r.get("feedback")
        if not isinstance(feedback, str):
            _error(where, "feedback", "'feedback' must be text")

        if "agent_commentary" in r and not isinstance(
            r.get("agent_commentary"), str
        ):
            _error(where, "agent_commentary",
                   "'agent_commentary' must be text")

        if "insincere" in r and not isinstance(r.get("insincere"), bool):
            _error(where, "insincere", "'insincere' must be a boolean")

        if "late_days" in r:
            late_days = r.get("late_days")
            if (not isinstance(late_days, int) or isinstance(late_days, bool)
                    or not (0 <= late_days <= 60)):
                _error(where, "late_days", "'late_days' must be an integer from 0 to 60")

        sc = r.get("score")
        if sc is not None and (not isinstance(sc, (int, float)) or isinstance(sc, bool)):
            _error(where, "score", "'score' must be a number or null")
        if sc is None and isinstance(feedback, str) and not feedback.strip():
            _error(where, "feedback", "'feedback' must be non-empty when 'score' is null")

        key = (ps, it)
        if key in seen:
            errors.append(f"{where}: duplicate result for {key}")
        seen.add(key)
        if key in held_keys:
            _error(where, "item_id",
                   f"{key} is held for incomplete required evidence and cannot be scored")
        if vault is not None and ps and vault.reverse(ps) is None:
            _error(where, "pseudonym", f"pseudonym '{ps}' is not in the vault")
        if bundle:
            if key not in expected:
                known = {p for p, _item in expected}
                _error(where, "pseudonym" if ps not in known else "item_id",
                       f"{key} was not in the bundle the LLM scored")
            else:
                pmax = possible.get(key)
                if isinstance(sc, (int, float)) and isinstance(pmax, (int, float)) \
                        and not (0 <= sc <= pmax):
                    warnings.append(f"{where}: score {sc} is outside 0..{pmax}")

    # A confirmed-insincere mark and a confirmed late-day count are per
    # student, not per item: every item row for one pseudonym must agree, or
    # the review has no single answer to stamp onto the session.
    grading_by_pseudonym: dict[str, set] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        ps = r.get("pseudonym")
        if not ps or not isinstance(ps, str):
            continue
        grading_by_pseudonym.setdefault(ps, set()).add(
            (bool(r.get("insincere", False)), r.get("late_days", None))
        )
    for ps, values in grading_by_pseudonym.items():
        if len(values) > 1:
            errors.append(
                f"pseudonym {ps}: 'insincere' and 'late_days' must agree across "
                "every item row for one student"
            )
            fields.add("insincere")
            fields.add("late_days")

    if bundle:
        for key in sorted(expected - seen):
            warnings.append(f"no result for {key} (student left unscored)")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "n": len(results),
            "fields": sorted(fields)}


def reidentify(results: list, vault: IdentityVault) -> list:
    """Map pseudonymous results to private ids without editing authored feedback."""
    out = []
    for r in results:
        who = vault.reverse(r.get("pseudonym", ""))
        agent_commentary = r.get("agent_commentary", "")
        if not isinstance(agent_commentary, str):
            agent_commentary = ""
        out.append({
            "resolved":  who is not None,
            "real_name": (who or {}).get("real_name", ""),
            "canvas_id": (who or {}).get("canvas_id", ""),
            "sis_id":    (who or {}).get("sis_id", ""),
            "item_id":   r.get("item_id", ""),
            "score":     r.get("score"),
            "feedback":  r.get("feedback", ""),
            "authored_feedback": r.get("authored_feedback", r.get("feedback", "")),
            "possible": r.get("possible"),
            "agent_commentary": agent_commentary,
        })
    return out


def merge_rows_by_uid(rows: list) -> dict:
    """Combine per-item reidentified rows into one draft per student.

    Multi-item work (a New Quiz with an essay item and an upload item, for
    example) produces one result per (pseudonym, item_id), but a PowerGrader
    session holds one AI draft per student. Item drafts must be merged --
    a plain ``{canvas_id: row}`` dict silently keeps only the last item, and
    each item renders fully under a header ``Item {n} of {m}``.

    Returns ``{canvas_id: row}`` with unresolved rows excluded.
    """
    grouped: dict[str, list] = {}
    for row in rows:
        if not row.get("resolved"):
            continue
        grouped.setdefault(str(row.get("canvas_id") or ""), []).append(row)

    merged: dict[str, dict] = {}
    for uid, items in grouped.items():
        if len(items) == 1:
            base = dict(items[0])
        else:
            sections = [
                f"Item {index} of {len(items)}\n{item.get('feedback', '')}"
                for index, item in enumerate(items, 1)
            ]
            scores = [item.get("score") for item in items]
            total = (
                sum(scores)
                if scores and all(isinstance(s, (int, float)) for s in scores)
                else None
            )
            base = {
                **items[0],
                "item_id": ",".join(str(item.get("item_id") or "") for item in items),
                "score": total,
                "feedback": "\n\n".join(sections),
                "agent_commentary": "\n\n".join(
                    str(item.get("agent_commentary") or "").strip()
                    for item in items
                    if str(item.get("agent_commentary") or "").strip()
                ),
            }
        merged[uid] = base
    return merged


def item_rows_by_uid(rows: list) -> dict[str, list[dict]]:
    """Keep validated AI drafts item-local for New Quiz review.

    ``merge_rows_by_uid`` remains the compatibility summary used by ordinary
    PowerGrader feedback.  This companion deliberately keeps the same
    reidentified/validated rows rather than introducing another scoring shape.
    """
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if not row.get("resolved") or not row.get("item_id"):
            continue
        grouped.setdefault(str(row.get("canvas_id") or ""), []).append({
            "item_id": str(row.get("item_id")),
            "score": row.get("score"),
            "feedback": row.get("authored_feedback", row.get("feedback", "")),
            "possible": row.get("possible"),
            "feedback_is_authored": True,
        })
    return grouped
