"""Scoring packet projection for PowerGrader sessions.

Projects one session's SAFE bundle into a single scorable page. Kept out of
the MCP tool layer so the paging, held-response, and budget arithmetic stays
testable without an MCP client.

Rows leave here as lists of dicts, never as {"columns", "rows"} tables. The
outbound safety scan walks dict keys, so tabulating here would hide every
student response from it: the tool layer gates first, then tabulates.
"""
import hashlib
import json

from api import feedback_artifacts, feedback_contract
from api.webui import source_materials


# Ceiling for one page of packet JSON. Response rows are segmented and pages
# are packed to this ceiling; the ceiling itself remains a safety envelope.
_TOKEN_BUDGET = 25_000
# Leave room for page-level counts and segment metadata after a response is
# sized against the stable page-zero envelope.
_SEGMENT_RESERVE = 512


class PacketTooLarge(Exception):
    """A required packet envelope or one response segment cannot fit."""


def _canonical_digest(value: dict) -> str:
    """SHA-256 over a key-sorted, separator-normalised JSON rendering."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def packet_digest(root_session_id, safe_bundle: dict, *, assignment_run_id="",
                  course_id="", assignment_id="") -> str:
    """Identity of one root session's active assignment bundle.

    ``submit_scoring_results`` recomputes this and refuses to write scores once
    it has moved, so a re-run between retrieval and submission cannot be scored blind.
    The root, private assignment run, exact coordinates, and SAFE content all
    participate so a replay from an earlier queue item fails closed.
    """
    return _canonical_digest({
        "bundle": safe_bundle,
        "root_session_id": str(root_session_id or ""),
        "assignment_run_id": str(assignment_run_id or root_session_id or ""),
        "course_id": str(course_id or ""),
        "assignment_id": str(assignment_id or ""),
    })


def _shared_context_projection(shared: dict | None) -> dict | None:
    """Return the public, student-free shape of optional shared context."""
    if not isinstance(shared, dict):
        return None
    return {
        "assignment_description": shared.get("assignment_description", "") or "",
        "materials": list(shared.get("materials") or []),
    }


def _packet_shape(*, digest: str, items: list[dict], students: list[dict],
                  total: int, source_response_total: int,
                  session_student_count: int, bundle_student_count: int,
                  students_without_responses: int, held: int,
                  held_pseudonyms: list[str], include_context: bool,
                  contract: str = "", shared_context: dict | None = None,
                  context_notice: dict | None = None,
                  next_offset: int | None = None) -> dict:
    """Assemble one unestimated packet projection for sizing and output."""
    result = {
        "ok": True,
        "packet_digest": digest,
        "items": items,
        "students": students,
        # ``total`` and paging are projected segment rows. The source count
        # makes the one-response/one-result-key distinction explicit.
        "total": total,
        "segment_total": total,
        "source_response_total": source_response_total,
        "students_total": len({row["pseudonym"] for row in students}) if students else 0,
        "session_student_count": session_student_count,
        "bundle_student_count": bundle_student_count,
        "excluded_student_count": max(0, session_student_count - bundle_student_count),
        "students_without_responses": students_without_responses,
        "returned": len(students),
        "held": held,
        "held_pseudonyms": held_pseudonyms,
        "included_context": include_context,
    }
    if include_context:
        result["contract"] = contract
        if shared_context is not None:
            result["shared_context"] = shared_context
        if context_notice is not None:
            result["shared_context_compaction"] = context_notice
    if next_offset is not None:
        result["next_offset"] = next_offset
    return result


def _estimated(result: dict) -> int:
    return source_materials.estimate_text_tokens(json.dumps(result))


def _compact_shared_context(*, shared: dict | None, base_factory) -> tuple[dict | None, dict | None]:
    """Fit optional shared materials while retaining an explicit omission marker.

    The contract and rubric basis are outside this helper and remain intact.
    Materials are omitted in stable input order when the unbounded projection is
    too large; the assignment description is then deterministically shortened
    to the largest prefix that fits the packet envelope.
    """
    projected = _shared_context_projection(shared)
    # The factory includes a minimal response row when the bundle has scorable
    # work. Reserve space for the actual segment/page envelope as well, so a
    # description that only fits an empty packet cannot deadlock page zero.
    # If the required envelope itself consumes more than that nominal reserve,
    # use its measured size as the floor rather than making a valid small row
    # impossible to return.
    if projected is None:
        return None, None
    minimum_envelope = _estimated(base_factory({
        "assignment_description": "", "materials": []
    }, None))
    # One reserve is consumed by the eventual response row and page counts;
    # the second keeps the context sizing probe from landing exactly on the
    # boundary where segment metadata can push a one-row page over the limit.
    context_budget = max(
        minimum_envelope, _TOKEN_BUDGET - (2 * _SEGMENT_RESERVE)
    )
    if _estimated(base_factory(projected, None)) <= context_budget:
        return projected, None

    materials = projected.get("materials") or []
    omitted = []
    for material in materials:
        if isinstance(material, dict):
            omitted.append(str(material.get("title") or material.get("name") or "shared material"))
        else:
            omitted.append("shared material")
    notice = {
        "code": "shared_context_compacted",
        "message": "Optional shared assignment context was compacted for packet transport; scoring contract and basis remain complete.",
        "omitted_materials": omitted,
        "assignment_description_truncated": False,
    }
    compacted = {
        "assignment_description": projected.get("assignment_description", ""),
        "materials": [],
        "notice": notice,
    }
    if _estimated(base_factory(compacted, notice)) <= context_budget:
        return compacted, notice

    description = str(compacted["assignment_description"] or "")
    lo, hi, best = 0, len(description), ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = dict(compacted)
        candidate["assignment_description"] = description[:mid]
        if mid < len(description):
            notice_candidate = dict(notice, assignment_description_truncated=True)
            candidate["notice"] = notice_candidate
        else:
            notice_candidate = notice
        if _estimated(base_factory(candidate, notice_candidate)) <= context_budget:
            best = candidate["assignment_description"]
            lo = mid + 1
        else:
            hi = mid - 1
    compacted["assignment_description"] = best
    notice["assignment_description_truncated"] = len(best) < len(description)
    compacted["notice"] = notice
    if _estimated(base_factory(compacted, notice)) > _TOKEN_BUDGET:
        # The required envelope itself is too large. Do not hide that fact by
        # dropping the contract or basis.
        raise PacketTooLarge(
            f"The required scoring contract and basis project over the {_TOKEN_BUDGET:,} token limit."
        )
    return compacted, notice


def _split_response(text: str, *, fits) -> list[str]:
    """Split one response at deterministic Unicode-safe boundaries."""
    if fits(text, 1, 1):
        return [text]

    def split_for_count(count: int) -> list[str]:
        chunks: list[str] = []
        start = 0
        index = 1
        while start < len(text):
            remaining = text[start:]
            if fits(remaining, index, count):
                chunks.append(remaining)
                break
            lo, hi, best = 1, len(remaining), 0
            while lo <= hi:
                mid = (lo + hi) // 2
                if fits(remaining[:mid], index, count):
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if not best:
                raise PacketTooLarge(
                    f"A response segment cannot fit within the {_TOKEN_BUDGET:,} token limit."
                )
            # Prefer a paragraph, line, or word boundary without ever losing
            # characters. The hard split remains the deterministic fallback.
            paragraph = remaining.rfind("\n\n", 0, best)
            line = remaining.rfind("\n", 0, best)
            word = remaining.rfind(" ", 0, best)
            boundary = max(paragraph, line, word)
            if boundary > max(0, best // 2):
                width = 2 if boundary == paragraph else 1
                if boundary + width <= best:
                    best = boundary + width
            chunks.append(remaining[:best])
            start += best
            index += 1
        return chunks

    count = 1
    chunks = split_for_count(count)
    for _ in range(8):
        new_count = len(chunks)
        updated = split_for_count(new_count)
        if len(updated) == new_count and updated == chunks:
            return updated
        chunks = updated
    return chunks


def build_packet(
    session: dict,
    safe_bundle: dict,
    *,
    offset: int = 0,
    limit: int = 10,
    include_context: bool = True,
    rubric_text: str = "",
    persona: dict | None = None,
) -> dict:
    """Project one page of scorable responses out of a SAFE bundle.

    Paging walks projected response segments, not students: a multi-item quiz
    gives one row per student per item, and an oversized response may give
    several complete ordered segments. ``offset``/``limit``/``total``/
    ``segment_total``/``next_offset`` count projected rows;
    ``source_response_total`` counts original scorable responses and
    ``students_total`` carries the distinct-student count separately.

    Returns a dict with:
    - packet_digest: bundle identity, required by ``submit_scoring_results``
    - items: list of {item_id, prompt, possible}, deduplicated by item_id
    - students: list of {pseudonym, item_id, text, segment_index, segment_count}
      for this page
    - total/segment_total: projected segment rows in the whole bundle
    - source_response_total: original scorable responses before segmentation
    - students_total: distinct students holding at least one scorable row
    - session_student_count: distinct students in the private session
    - bundle_student_count: distinct pseudonyms in the SAFE bundle
    - excluded_student_count: nonnegative session-minus-bundle count gap
    - students_without_responses: bundle students with no response rows
    - returned: rows in this page
    - next_offset: offset of the next page, absent on the final page
    - held: responses with no scorable text (media-only or empty)
    - held_pseudonyms: distinct pseudonyms holding at least one held response
    - included_context: whether contract and shared context were included
    - estimated_tokens: projected token count for this page

    A response is split into complete ordered segments when it cannot fit as
    one projected row. Pages then pack as many complete segments as fit under
    the requested limit and token ceiling.
    """
    students = safe_bundle.get("students") or []
    session_student_count = len({
        str(student["user_id"]) for student in session.get("students") or []
        if student.get("user_id") is not None
    })
    bundle_pseudonyms = {student.get("pseudonym", "") for student in students}
    responding_pseudonyms = {
        student.get("pseudonym", "") for student in students if student.get("responses")
    }

    # One scorable row per (student, response carrying text). Held rows are
    # the ones no model can score: media-only or empty submissions. Each
    # response is classified exactly once, so held is a response count that
    # never exceeds the responses actually present.
    scorable: list[dict] = []
    items_by_id: dict[str, dict] = {}
    held = 0
    held_pseudonyms: list[str] = []

    for student in students:
        pseudonym = student.get("pseudonym", "")
        for response in student.get("responses") or []:
            item_id = str(response.get("item_id") or "")
            if item_id not in items_by_id:
                items_by_id[item_id] = {
                    "item_id": item_id,
                    "prompt": response.get("prompt", ""),
                    "possible": response.get("possible"),
                }
            raw_text = response.get("response") or ""
            text = str(raw_text)
            oral_text = feedback_artifacts.oral_reading_text(response.get("oral_reading"))
            text = "\n\n".join(part for part in (text, oral_text) if part)
            if not text.strip():
                held += 1
                if pseudonym not in held_pseudonyms:
                    held_pseudonyms.append(pseudonym)
                continue
            scorable.append({
                "pseudonym": pseudonym,
                "item_id": item_id,
                "text": text,
            })

    source_response_total = len(scorable)
    digest = packet_digest(
        session.get("parent_scoring_session_id") or session.get("session_id"),
        safe_bundle,
        assignment_run_id=session.get("session_id"),
        course_id=session.get("course_id"),
        assignment_id=session.get("assignment_id"),
    )
    items = sorted(items_by_id.values(), key=lambda i: i["item_id"])
    bundle_student_count = len(bundle_pseudonyms)
    students_without_responses = len(bundle_pseudonyms - responding_pseudonyms)

    contract = feedback_contract.build_contract_text(
        ai_ta_name=str((persona or {}).get("name") or "your teaching assistant"),
        rubric_text=rubric_text,
        persona=persona,
    )

    # Calculate context using the complete page-zero envelope. This also makes
    # segmentation independent of whether later pages ask to omit context.
    shared = _shared_context_projection(safe_bundle.get("shared_context"))
    sizing_students = []
    sizing_total = 0
    if source_response_total:
        sample = max(
            scorable,
            key=lambda row: len(str(row["pseudonym"])) + len(str(row["item_id"])),
        )
        sizing_students = [{
            # Size the required envelope with real identity/key values from
            # the SAFE projection, rather than short placeholders that could
            # under-budget page metadata.
            "pseudonym": sample["pseudonym"],
            "item_id": sample["item_id"],
            "text": "x",
            "segment_index": 1,
            "segment_count": 1,
        }]
        sizing_total = 1

    def base_for_context(context_value, notice):
        return _packet_shape(
            digest=digest, items=items, students=sizing_students,
            total=sizing_total,
            source_response_total=source_response_total,
            session_student_count=session_student_count,
            bundle_student_count=bundle_student_count,
            students_without_responses=students_without_responses,
            held=held, held_pseudonyms=held_pseudonyms,
            include_context=True, contract=contract,
            shared_context=context_value, context_notice=notice,
        )

    sized_shared, context_notice = _compact_shared_context(
        shared=shared, base_factory=base_for_context
    ) if shared is not None else (None, None)
    sized_minimum = _estimated(base_for_context(sized_shared, context_notice))
    row_budget = min(
        _TOKEN_BUDGET,
        max(_TOKEN_BUDGET - _SEGMENT_RESERVE, sized_minimum),
    )

    def row_fits(text_value, segment_index, segment_count, pseudonym="pseudonym", item_id="item_id"):
        row = {
            "pseudonym": pseudonym,
            "item_id": item_id,
            "text": text_value,
            "segment_index": segment_index,
            "segment_count": segment_count,
        }
        return _estimated(_packet_shape(
            digest=digest, items=items, students=[row], total=1,
            source_response_total=source_response_total,
            session_student_count=session_student_count,
            bundle_student_count=bundle_student_count,
            students_without_responses=students_without_responses,
            held=held, held_pseudonyms=held_pseudonyms,
            include_context=True, contract=contract,
            shared_context=sized_shared, context_notice=context_notice,
        )) <= row_budget

    projected: list[dict] = []
    for original in scorable:
        segments = _split_response(
            original["text"],
            fits=lambda value, index, count: row_fits(
                value, index, count, original["pseudonym"], original["item_id"]
            ),
        )
        count = len(segments)
        for index, segment in enumerate(segments, start=1):
            projected.append({
                "pseudonym": original["pseudonym"],
                "item_id": original["item_id"],
                "text": segment,
                "segment_index": index,
                "segment_count": count,
            })

    segment_total = len(projected)
    offset = max(0, int(offset or 0))
    limit = max(1, int(limit or 1))
    page: list[dict] = []
    for row in projected[offset:offset + limit]:
        candidate = page + [row]
        candidate_next = offset + len(candidate) if offset + len(candidate) < segment_total else None
        candidate_context = sized_shared if include_context else None
        result = _packet_shape(
            digest=digest, items=items, students=candidate,
            total=segment_total, source_response_total=source_response_total,
            session_student_count=session_student_count,
            bundle_student_count=bundle_student_count,
            students_without_responses=students_without_responses,
            held=held, held_pseudonyms=held_pseudonyms,
            include_context=include_context, contract=contract,
            shared_context=candidate_context,
            context_notice=context_notice if include_context else None,
            next_offset=candidate_next,
        )
        if _estimated(result) > _TOKEN_BUDGET:
            if not page:
                raise PacketTooLarge(
                    f"A response segment at offset {offset} cannot fit within the "
                    f"{_TOKEN_BUDGET:,} token limit."
                )
            break
        page.append(row)

    next_offset = offset + len(page) if offset + len(page) < segment_total else None
    result = _packet_shape(
        digest=digest, items=items, students=page, total=segment_total,
        source_response_total=source_response_total,
        session_student_count=session_student_count,
        bundle_student_count=bundle_student_count,
        students_without_responses=students_without_responses,
        held=held, held_pseudonyms=held_pseudonyms,
        include_context=include_context, contract=contract,
        shared_context=sized_shared if include_context else None,
        context_notice=context_notice if include_context else None,
        next_offset=next_offset,
    )
    estimated = _estimated(result)
    if estimated > _TOKEN_BUDGET:
        raise PacketTooLarge(
            f"A response segment at offset {offset} cannot fit within the "
            f"{_TOKEN_BUDGET:,} token limit."
        )
    result["students_total"] = len({row["pseudonym"] for row in projected})
    result["estimated_tokens"] = estimated

    return result
