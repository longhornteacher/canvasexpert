"""Feedback tools result parsing, validation, rendering, and re-identification.

The model supplies structured fields only (explanation, glows, grows, fixes,
an optional exemplar per item). Canvas Expert renders those fields into the
one fixed plain-text layout -- see ``render_feedback_item``. No persona and
no AI identity or disclosure is ever invented here.
"""
import json
import re

from api.feedback_vault import Vault
from api.feedback_contract import CONTRACT_VERSION
from api.powergrader import writing_timeline

_AI_SIGNATURE_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-–—]\s*)?[\w .,'&-]{1,100}\s+"
    r"\((?:AI|AI teaching assistant)\)\.?\s*$"
)
_DRAFTED_BY_LINE_RE = re.compile(r"(?im)^[ \t]*Drafted by\b[^\n]*$")
_TRAILING_NAME_SIGNATURE_RE = re.compile(r"\n[ \t]*[-–—][ \t]*[A-Z][A-Za-z' .-]{0,40}[ \t]*$")

_CODE_FENCE_RE = re.compile(r"```[^\n]*\n?")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_MARKER_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_RULE_LINE_RE = re.compile(r"(?m)^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LIST_MARKER_RE = re.compile(r"^(?:[-*•]|\d+\.)[ \t]+")
_AMP_RE = re.compile(r"&amp;|&")


def flatten_text(value: object, *, list_item: bool = False,
                 strip_signatures: bool = True) -> str:
    """Strip markdown decoration and signatures from one model-supplied field.

    Applies to explanation, every glow/grow/fix, and an exemplar or
    disclosure. List items collapse to one line; explanation and exemplar
    text keeps its line breaks, with blank runs capped at one.

    ``strip_signatures`` drops a lingering "Drafted by ...", AI signature, or
    trailing "- Name" line that a model wrote despite the contract. The
    teacher's own requested disclosure text is exempt: it is legitimately a
    signature line, so callers rendering it pass ``strip_signatures=False``.
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CODE_FENCE_RE.sub("", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _HEADING_MARKER_RE.sub("", text)
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _RULE_LINE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    if strip_signatures:
        text = _DRAFTED_BY_LINE_RE.sub("", text)
        text = _AI_SIGNATURE_LINE_RE.sub("", text)
        text = _TRAILING_NAME_SIGNATURE_RE.sub("", text)
    text = _AMP_RE.sub(" and ", text)
    text = re.sub(r"[ \t]+", " ", text)

    if list_item:
        text = _LIST_MARKER_RE.sub("", text.strip())
        text = re.sub(r"\s*\n+\s*", " ", text)
        return text.strip()

    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extra_credit_applies(score, possible) -> bool:
    """Whether Extra credit Part 1/2 belongs on this row.

    Shown unless score and possible are both numeric and score meets or beats
    possible. A null score, or an unknown possible, always shows it.
    """
    if score is None or possible is None:
        return True
    if isinstance(score, bool) or isinstance(possible, bool):
        return True
    try:
        return not (float(score) >= float(possible))
    except (TypeError, ValueError):
        return True


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


def _score_line(score, possible) -> str | None:
    if score is None:
        return None
    formatted = _format_number(score)
    possible_formatted = _format_number(possible) if possible is not None else None
    if possible_formatted is None:
        return f"Score: {formatted}"
    return f"Score: {formatted}/{possible_formatted}"


_DIVIDER = "-" * 40


def render_feedback_item(result: dict, *, possible=None, exemplar: str = "",
                         correction: dict | None = None) -> str:
    """Render one SAFE result's structured fields into the fixed plain-text layout.

    ``possible`` is the item's max score from the SAFE bundle. ``correction``
    is the teacher-authored AssignmentForge correction for this item, when
    one exists; it wins as Extra credit Part 2 over the model's ``exemplar``.
    """
    score = result.get("score")
    explanation = flatten_text(result.get("explanation") or "")
    glows = [flatten_text(g, list_item=True) for g in (result.get("glows") or [])
             if str(g or "").strip()]
    grows = [flatten_text(g, list_item=True) for g in (result.get("grows") or [])
             if str(g or "").strip()]

    blocks = []
    score_line = _score_line(score, possible)
    if score_line:
        blocks.append(score_line)
    if explanation:
        blocks.append(explanation)
    if glows:
        blocks.append("Glows\n" + "\n".join(f"- {g}" for g in glows))
    if grows:
        blocks.append("Grows\n" + "\n".join(f"- {g}" for g in grows))

    if _extra_credit_applies(score, possible):
        fixes = [flatten_text(f, list_item=True) for f in (result.get("fixes") or [])
                 if str(f or "").strip()]
        if isinstance(correction, dict) and str(correction.get("answer") or "").strip():
            answer = flatten_text(correction.get("answer") or "")
            why = flatten_text(correction.get("why") or "")
            exemplar_text = f"{answer}\n\nWhy: {why}" if why else answer
        else:
            exemplar_text = flatten_text(exemplar or "")
        part1_lines = "\n".join(f"{i}. {fix}" for i, fix in enumerate(fixes, 1))
        blocks.append(
            f"{_DIVIDER}\n"
            "Extra credit Part 1: Fix these in a handwritten second draft\n"
            f"{part1_lines}"
        )
        blocks.append("Extra credit Part 2: Hand copy this exemplar\n" + exemplar_text)

    return "\n\n".join(blocks)


def _possible_by_key(bundle: dict | None) -> dict:
    possible = {}
    for s in (bundle or {}).get("students", []):
        for r in s.get("responses", []):
            possible[(s.get("pseudonym"), str(r.get("item_id", "")))] = r.get("possible")
    return possible


def render_results(results: list, *, bundle: dict | None = None,
                   exemplars: dict | None = None,
                   corrections_by_item: dict | None = None) -> list:
    """Render every validated SAFE result's fields into final feedback text.

    Returns new result dicts carrying a rendered ``feedback`` string built
    from ``render_feedback_item``. ``pseudonym``, ``item_id``, ``score``, and
    ``writing_process_observations`` pass through unchanged for ``reidentify``.
    """
    possible = _possible_by_key(bundle)
    exemplars = exemplars or {}
    corrections_by_item = corrections_by_item or {}
    rendered = []
    for r in results:
        row = dict(r)
        item_id = str(row.get("item_id", ""))
        key = (row.get("pseudonym"), item_id)
        row["feedback"] = render_feedback_item(
            row,
            possible=possible.get(key),
            exemplar=exemplars.get(item_id, ""),
            correction=corrections_by_item.get(item_id),
        )
        rendered.append(row)
    return rendered


def missing_exemplar_item_ids(results: list, *, bundle: dict | None = None,
                              exemplars: dict | None = None,
                              corrections_by_item: dict | None = None) -> list[str]:
    """Item ids that need a shared exemplar no correction or supplied exemplar covers.

    Checked only for rows where Extra credit would actually render (a row at
    or above full marks needs no exemplar). Returns sorted item ids only --
    no response content or student identity.
    """
    possible = _possible_by_key(bundle)
    exemplars = exemplars or {}
    corrections_by_item = corrections_by_item or {}
    missing: set[str] = set()
    for r in results or []:
        if not isinstance(r, dict):
            continue
        item_id = str(r.get("item_id", ""))
        if not item_id or item_id in missing:
            continue
        key = (r.get("pseudonym"), item_id)
        if not _extra_credit_applies(r.get("score"), possible.get(key)):
            continue
        if corrections_by_item.get(item_id):
            continue
        if str(exemplars.get(item_id) or "").strip():
            continue
        missing.add(item_id)
    return sorted(missing)


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
    # wrapper -- even though structured fields like `glows`/`grows` are
    # themselves lists and would otherwise confuse the wrapper heuristic below.
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


def validate_results(results, bundle: dict = None, vault: Vault = None,
                     *, contract_version: str = CONTRACT_VERSION) -> dict:
    """Validate scoring output against the Feedback Scoring Contract (v1).

    See docs/contracts/feedback-scoring-contract.md. Accepts either the wrapped
    object ({contract_version, results:[...]}) or a bare array. `bundle` and
    `vault` are optional cross-checks: with them we confirm every result maps to
    a real (pseudonym, item_id) the LLM was actually given, that scores fit the
    item's max, and (only when a bundle establishes `possible`) that `fixes` is
    present wherever Extra credit would render. Returns {ok, errors:[...],
    warnings:[...], n:int, fields:[...]}. `fields` names the distinct fields
    behind the errors. `ok` is False only on hard errors -- out-of-range scores
    and uncovered students are warnings, since the teacher reviews before any
    push. Exemplar coverage is a separate, later check: see
    `missing_exemplar_item_ids`.
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
    possible = _possible_by_key(bundle) if bundle else {}
    if bundle:
        for s in bundle.get("students", []):
            for r in s.get("responses", []):
                expected.add((s.get("pseudonym"), str(r.get("item_id", ""))))

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

        explanation = r.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            _error(where, "explanation", "'explanation' must be non-empty text")

        glows = r.get("glows")
        if not isinstance(glows, list) or not any(
            isinstance(g, str) and g.strip() for g in glows
        ):
            _error(where, "glows", "'glows' must be a list of at least one non-empty string")

        grows = r.get("grows")
        if not isinstance(grows, list) or not any(
            isinstance(g, str) and g.strip() for g in grows
        ):
            _error(where, "grows", "'grows' must be a list of at least one non-empty string")

        if "writing_process_observations" in r and not isinstance(
            r.get("writing_process_observations"), str
        ):
            _error(where, "writing_process_observations",
                   "'writing_process_observations' must be text")

        sc = r.get("score", None)
        if sc is not None and not isinstance(sc, (int, float)):
            _error(where, "score", "'score' must be a number or null")

        key = (ps, it)
        if key in seen:
            errors.append(f"{where}: duplicate result for {key}")
        seen.add(key)
        if vault is not None and ps and vault.reverse(ps) is None:
            errors.append(f"{where}: pseudonym '{ps}' is not in the vault")
        if bundle:
            if key not in expected:
                errors.append(f"{where}: {key} was not in the bundle the LLM scored")
            else:
                pmax = possible.get(key)
                if isinstance(sc, (int, float)) and isinstance(pmax, (int, float)) \
                        and not (0 <= sc <= pmax):
                    warnings.append(f"{where}: score {sc} is outside 0..{pmax}")
                if _extra_credit_applies(sc, pmax):
                    fixes = r.get("fixes")
                    if not isinstance(fixes, list) or not any(
                        isinstance(f, str) and f.strip() for f in fixes
                    ):
                        _error(where, "fixes",
                               "'fixes' must be a list of at least one non-empty string")

    if bundle:
        for key in sorted(expected - seen):
            warnings.append(f"no result for {key} (student left unscored)")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "n": len(results),
            "fields": sorted(fields)}


def reidentify(results: list, vault: Vault) -> list:
    """Map pseudonymous, already-rendered results back to real students.

    Unknown pseudonyms are marked `resolved: False` rather than dropped.
    `feedback` must already be the final rendered text (see `render_results`);
    this function performs no further feedback formatting or disclosure
    handling -- that is `merge_rows_by_uid`'s job, once per student.
    """
    out = []
    for r in results:
        who = vault.reverse(r.get("pseudonym", ""))
        writing_observation = r.get("writing_process_observations", "")
        if not isinstance(writing_observation, str):
            writing_observation = ""
        # The contract forbids integrity conclusions here; enforce it rather than
        # trusting the prompt.  This is the single funnel where model results
        # become teacher-facing rows, so the guard belongs here.
        writing_observation = writing_timeline.sanitize_process_observation(writing_observation)
        out.append({
            "resolved":  who is not None,
            "real_name": (who or {}).get("real_name", ""),
            "canvas_id": (who or {}).get("canvas_id", ""),
            "sis_id":    (who or {}).get("sis_id", ""),
            "item_id":   r.get("item_id", ""),
            "score":     r.get("score"),
            "feedback":  str(r.get("feedback") or ""),
            "writing_process_observations": writing_observation,
        })
    return out


def merge_rows_by_uid(rows: list, *, disclosure: str = "") -> dict:
    """Combine per-item reidentified rows into one draft per student.

    Multi-item work (a New Quiz with an essay item and an upload item, for
    example) produces one result per (pseudonym, item_id), but a PowerGrader
    session holds one AI draft per student. Item drafts must be merged --
    a plain ``{canvas_id: row}`` dict silently keeps only the last item, and
    each item renders fully under a header ``Item {n} of {m}``.

    ``disclosure`` is appended once, as the final line after a blank line, to
    every student's combined feedback (single item or multi-item alike) --
    only when the teacher asked for one this session. Never by default.
    Returns ``{canvas_id: row}`` with unresolved rows excluded.
    """
    grouped: dict[str, list] = {}
    for row in rows:
        if not row.get("resolved"):
            continue
        grouped.setdefault(str(row.get("canvas_id") or ""), []).append(row)

    disclosure_text = (
        flatten_text(disclosure, strip_signatures=False)
        if str(disclosure or "").strip() else ""
    )

    merged: dict[str, dict] = {}
    for uid, items in grouped.items():
        if len(items) == 1:
            base = dict(items[0])
        else:
            sections = [
                f"Item {index} of {len(items)}\n{str(item.get('feedback') or '')}".strip()
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
                "writing_process_observations": "\n\n".join(
                    str(item.get("writing_process_observations") or "").strip()
                    for item in items
                    if str(item.get("writing_process_observations") or "").strip()
                ),
            }
        if disclosure_text:
            base["feedback"] = f"{str(base.get('feedback') or '')}\n\n{disclosure_text}".strip()
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
            "feedback": row.get("feedback") or "",
        })
    return grouped
