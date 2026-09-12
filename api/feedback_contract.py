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
    if not template and policy == "ai_disclosure":
        template = "Drafted by {name} (AI), reviewed by your teacher."
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
        feedback_hint: str = "Brief rubric-based feedback.",
        include_signoff_in_feedback: bool = False,
        packet_digest: str = "",
) -> dict:
    """Return the shared scoring output contract and its JSON example."""
    persona = persona or {}
    signoff = persona_signoff(persona, ai_ta_name)
    feedback = feedback_hint.strip() or "Brief rubric-based feedback."
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
        "Quote briefly from the response to justify the score.",
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
    ]
    if signoff:
        rules.extend([
            f"End feedback with this persona signoff exactly once: {signoff}",
            "Do not invent a separate signature or disclosure beyond the selected persona.",
            "If `disclosure` is present, copy the persona signoff exactly.",
        ])
    else:
        rules.extend([
            "Do not invent a separate signature or disclosure beyond the selected persona.",
            "`disclosure` is optional metadata; leave it empty if the persona has no signoff.",
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


def build_contract_text(ai_ta_name: str = "your teaching assistant",
                        rubric_text: str = "",
                        persona: dict | None = None) -> str:
    """Instructions the teacher pastes into their LLM alongside the bundle.

    When `rubric_text` is provided it is inlined below so the file is self-contained
    (prompt context lives in the bundle; the rubric travels here) - no separate
    attach step. When omitted, the contract keeps its general "attach the rubric
    as Knowledge" wording; Scoring Sessions resolve their scoring basis privately.
    Extends the Essay Scorer skill: keyed batch output for automatic
    re-identification. Student-facing signoff is persona-controlled, not part of
    the scoring contract.
    """
    persona = persona or {}
    ai_ta_name = str(persona.get("name") or ai_ta_name or "your teaching assistant").strip()
    contract = scoring_output_contract(
        persona=persona,
        ai_ta_name=ai_ta_name,
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
    return f"""You are {ai_ta_name}, a teaching assistant helping a real teacher
score student writing and draft feedback. {rubric_clause}

You will receive a JSON bundle of pseudonymized responses. For EACH response return
one result object. {contract["format_instruction"]}

{sample}

Rules:
{contract["rules_text"]}{signoff_clause}{rubric_block}"""
