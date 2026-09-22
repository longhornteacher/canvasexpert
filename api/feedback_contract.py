"""Feedback tools contract and prompt helpers.

These helpers define the shared scoring instructions and feedback contract used
by the feedback pipeline and SAFE Scoring Session packet builders.
"""
import json
import os

from api.nq_report import constructed_responses, html_to_text, parse_student_analysis_file
from api.feedback_vault import Vault
from engine.utils.text_utils import safe_filename_component

CONTRACT_VERSION = "1.0"
REVIEW_NOTE = ("Pseudonymized for privacy. Review the response text for any "
                 "self-identifying details (names, places) before sending to an LLM.")


def _default_contract_body() -> str:
    """Read the repository seed for legacy/offline packet paths.

    Assignment-scoped Scoring Sessions pass the teacher's workspace copy
    explicitly. This fallback keeps the older private packet builders useful
    without maintaining a second Python copy of the teacher-authored shape.
    """
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "default_docs", "Feedback Contracts", "Glows & Grows (Basic).md",
    )
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return "Use a clear, supportive, evidence-based feedback shape."
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if closing is not None:
            return "".join(lines[closing + 1:])
    return text


def _default_feedback_hint() -> str:
    """Describe the seeded contract without duplicating its editable rules."""
    for line in _default_contract_body().splitlines():
        value = line.strip()
        if value:
            heading = value.lstrip("#").strip()
            return f"Follow the teacher-authored feedback contract: {heading}."
    return "Follow the seeded teacher-authored feedback contract."


def safe(name, max_len=80):
    return safe_filename_component(name, max_len=max_len, fallback="quiz")


def persona_signoff(persona: dict | None = None,
                    ai_ta_name: str = "your teaching assistant") -> str:
    """Return the persona's student-visible signoff, if that persona wants one."""
    persona = persona or {}
    name = str(persona.get("name") or ai_ta_name or "your teaching assistant").strip()
    policy = str(persona.get("signoff_policy") or "").strip().lower()
    if not policy:
        return ""
    if policy in {"none", "off", "no_signoff"}:
        return ""
    template = str(persona.get("signoff_text") or "").strip()
    if not template:
        return ""
    return template.replace("{name}", name)


def scoring_output_contract(
        *,
        persona: dict | None = None,
        ai_ta_name: str = "your teaching assistant",
        identity_source: str = "the bundle",
        pseudonym: str = "<copy>",
        item_id: str = "<copy>",
        feedback_hint: str = "",
        teacher_contract: str | None = None,
        include_signoff_in_feedback: bool = False,
        packet_digest: str = "",
) -> dict:
    """Return the shared scoring output contract and its JSON example."""
    persona = persona or {}
    signoff = persona_signoff(persona, ai_ta_name)
    feedback = feedback_hint.strip() or (
        "Follow the selected teacher-authored feedback contract exactly."
        if teacher_contract is not None
        else _default_feedback_hint()
    )
    if signoff and include_signoff_in_feedback:
        feedback = f"{feedback} End with the persona signoff exactly once. {signoff}"
    sample = {
        "pseudonym": pseudonym,
        "item_id": item_id,
        "score": 1,
        "feedback": feedback,
        "writing_process_observations": "<optional teacher-only observation>",
    }
    rules = [
        f"Copy pseudonym and item_id exactly from {identity_source} so results can be matched.",
        "If the bundle includes `shared_context`, use that assignment/source material when scoring every response. Do not ask for missing source material unless it is truly impossible to score without it.",
        *([] if teacher_contract is not None else [
            "Quote briefly from the response to justify the score.",
        ]),
        "Never quote a pseudonym back in `feedback`. Scrubbing replaces real names "
        "wherever they appear as whole words, so an ordinary word in a response may "
        "have been swapped for a pseudonym: a student surname that is also a common "
        "noun. Quote around it, or paraphrase.",
        "Do not identify students.",
        "`score` may be a number or null for comment-only feedback.",
        "`feedback` must be non-empty.",
        "When a response includes `writing_timeline`, you may return `writing_process_observations` as an observational, teacher-only string.",
        "It must never be an integrity conclusion, probability, or penalty recommendation.",
        "It must not change the score or the student-facing `feedback`.",
        "Use the full score range; `possible` gives each item's maximum.",
        "When a response includes `oral_reading`, use only the supplied passage, transcript, metrics, uncertainty, and candidate differences.",
        "Do not infer pronunciation, expression, prosody, identity, disability, effort, intent, cheating, or diagnosis; low ASR confidence is not a reading error.",
        f"Write student-facing `feedback` in this shape: {feedback}",
    ]
    if signoff:
        rules.extend([
            f"End feedback with this persona signoff exactly once: {signoff}",
            "Do not invent a separate signature or disclosure beyond the selected persona.",
            "If `disclosure` is present, copy the persona signoff exactly.",
        ])
    else:
        rules.extend([
            "Do not add an AI disclosure, 'Drafted by', autofeedback banner, or similar label unless the teacher asked for one.",
            "Do not invent a separate signature or disclosure.",
            "`disclosure` is optional metadata; leave it empty unless the teacher asked for a signoff.",
        ])
    if signoff:
        sample["disclosure"] = signoff
    format_instruction = (
        "Return only valid JSON. Return a JSON array only, with one object per scored "
        "student. Each element must be exactly:"
    )
    envelope = None
    if packet_digest:
        format_instruction = (
            "Return only valid JSON. Return one top-level object with this exact "
            "`packet_digest` and a `results` array of ordinary result objects:"
        )
        envelope = {"packet_digest": packet_digest, "results": [sample]}
    return {
        "format_instruction": format_instruction,
        "sample": sample,
        "envelope": envelope,
        "rules": tuple(rules),
        "rules_text": "\n".join(f"- {rule}" for rule in rules),
        "signoff": signoff,
    }


def build_contract_text(ai_ta_name: str = "",
                        rubric_text: str = "",
                        persona: dict | None = None,
                        teacher_contract: str | None = None,
                        *,
                        contract_text: str | None = None,
                        contract_name: str = "") -> str:
    """Instructions the teacher pastes into their LLM alongside the bundle.

    When `rubric_text` is provided it is inlined below so the file is self-contained
    (prompt context lives in the bundle; the rubric travels here) - no separate
    attach step. When omitted, the contract keeps its general "attach the rubric
    as Knowledge" wording; Scoring Sessions resolve their scoring basis privately.
    ``teacher_contract`` is the body selected for an assignment-scoped session
    and is included verbatim. When omitted, the repository seed is loaded for
    older offline packet paths. Transport/privacy rules remain product-owned;
    judgment and feedback shape live in the selected teacher body.
    """
    if contract_text is not None:
        teacher_contract = contract_text
    legacy_default = teacher_contract is None
    teacher_body = _default_contract_body() if legacy_default else str(teacher_contract)
    persona = persona or {}
    ai_ta_name = str(persona.get("name") or ai_ta_name or "").strip()
    contract = scoring_output_contract(
        persona=persona,
        ai_ta_name=ai_ta_name,
        teacher_contract=None if legacy_default else teacher_body,
        identity_source="the bundle",
        pseudonym="<copy>",
        item_id="<copy>",
    )
    signoff = contract["signoff"]
    signoff_clause = ""
    if signoff:
        signoff_clause = (
            "\n\nThis persona uses this student-visible signoff. End each `feedback` "
            f"value with it exactly once:\n{signoff}"
        )
    if rubric_text.strip():
        rubric_clause = ("The scoring rubric is included at the bottom of this file "
                         ", score strictly by it, do not invent criteria.")
        rubric_block = f"\n\n--- RUBRIC (score strictly by this) ---\n{rubric_text.strip()}\n"
    else:
        rubric_clause = ("No scoring rubric was provided, use the available assignment "
                         "context, score cautiously, and do not invent criteria.")
        rubric_block = ""
    sample = json.dumps(contract["sample"], indent=2, ensure_ascii=False)
    opening = (
        f"You are {ai_ta_name}, a teaching assistant helping a real teacher"
        if ai_ta_name else "You are a teaching assistant helping a real teacher"
    )
    contract_label = f" ({contract_name})" if str(contract_name or "").strip() else ""
    return f"""{opening}
score student writing and draft feedback. {rubric_clause}

You will receive a JSON bundle of pseudonymized responses. For EACH response return
one result object. {contract["format_instruction"]}

{sample}

Rules:
{contract["rules_text"]}{signoff_clause}

--- TEACHER FEEDBACK CONTRACT{contract_label} (verbatim) ---
{teacher_body}{rubric_block}"""
