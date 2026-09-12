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


# Ceiling for one page of packet JSON. Over this the page is refused rather
# than trimmed. Silently dropping responses would let a teacher believe a
# whole class was scored when part of it never reached the model.
_TOKEN_BUDGET = 25_000


class PacketTooLarge(Exception):
    """One page projects over the token budget. Carries a workable retry."""


def _canonical_digest(value: dict) -> str:
    """SHA-256 over a key-sorted, separator-normalised JSON rendering."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def packet_digest(session_id, safe_bundle: dict) -> str:
    """Identity of one session's bundle content.

    ``submit_scoring_results`` recomputes this and refuses to write scores once
    it has moved, so a re-run between retrieval and submission cannot be scored blind.
    Both sides call this helper so the two digests cannot drift apart.
    """
    return _canonical_digest({
        "bundle": safe_bundle,
        "session_id": str(session_id or ""),
    })


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

    Paging walks *responses*, not students: a multi-item quiz gives one row per
    student per item, so ``offset``/``limit``/``total``/``next_offset`` all
    count rows. ``students_total`` carries the distinct-student count for
    anyone who wants it, and is deliberately not what paging is measured in.

    Returns a dict with:
    - packet_digest: bundle identity, required by ``submit_scoring_results``
    - items: list of {item_id, prompt, possible}, deduplicated by item_id
    - students: list of {pseudonym, item_id, text} for this page
    - total: scorable rows in the whole bundle
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

    Raises PacketTooLarge when the page projects over the token budget.
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
            text = (response.get("response") or "").strip()
            oral_text = feedback_artifacts.oral_reading_text(response.get("oral_reading"))
            text = "\n\n".join(part for part in (text, oral_text) if part)
            if not text:
                held += 1
                if pseudonym not in held_pseudonyms:
                    held_pseudonyms.append(pseudonym)
                continue
            scorable.append({
                "pseudonym": pseudonym,
                "item_id": item_id,
                "text": text,
            })

    total = len(scorable)
    offset = max(0, int(offset or 0))
    limit = max(1, int(limit or 1))
    page = scorable[offset:offset + limit]
    next_offset = offset + limit if (offset + limit) < total else None

    result = {
        "ok": True,
        "packet_digest": packet_digest(session.get("session_id"), safe_bundle),
        "items": sorted(items_by_id.values(), key=lambda i: i["item_id"]),
        "students": page,
        "total": total,
        "students_total": len({row["pseudonym"] for row in scorable}),
        "session_student_count": session_student_count,
        "bundle_student_count": len(bundle_pseudonyms),
        "excluded_student_count": max(0, session_student_count - len(bundle_pseudonyms)),
        "students_without_responses": len(bundle_pseudonyms - responding_pseudonyms),
        "returned": len(page),
        "held": held,
        "held_pseudonyms": held_pseudonyms,
        "included_context": include_context,
    }

    if include_context:
        result["contract"] = feedback_contract.build_contract_text(
            ai_ta_name=str((persona or {}).get("name") or "your teaching assistant"),
            rubric_text=rubric_text,
            persona=persona,
        )
        shared = safe_bundle.get("shared_context")
        if shared:
            result["shared_context"] = {
                "assignment_description": shared.get("assignment_description", ""),
                "materials": shared.get("materials", []),
            }

    if next_offset is not None:
        result["next_offset"] = next_offset

    # Measured on the assembled page, so the guard covers what actually goes
    # out rather than a stand-in shape.
    estimated = source_materials.estimate_text_tokens(json.dumps(result))
    if estimated > _TOKEN_BUDGET:
        if limit <= 1:
            raise PacketTooLarge(
                f"One response at offset {offset} projects to {estimated:,} tokens, over the "
                f"{_TOKEN_BUDGET:,} limit. Retry with include_context=false, or score this "
                "response in the PowerGrader queue instead."
            )
        # Scale the page down by how far over it landed, so the retry is a
        # size that fits rather than a blind decrement.
        raise PacketTooLarge(
            f"This page projects to {estimated:,} tokens, over the {_TOKEN_BUDGET:,} limit. "
            f"Retry with limit={max(1, (limit * _TOKEN_BUDGET) // estimated)}."
        )
    result["estimated_tokens"] = estimated

    return result
