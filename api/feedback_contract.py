"""Feedback tools contract and prompt helpers.

These helpers define the shared scoring instructions and feedback contract used
by the feedback pipeline and SAFE Scoring Session packet builders. The base
shape (fields, minimums, layout) is product-owned: no persona, teacher
contract file, or conversational guidance can replace it. A selected contract
file layers on top here, under one heading, to adjust judgment, tone, and
emphasis. Conversational scoring guidance layers separately, onto the scoring
basis, as the existing TEACHER DIRECTIVE block built in scoring_preparation.py;
it is not repeated here.
"""
import json

from engine.utils.text_utils import safe_filename_component

CONTRACT_VERSION = "1.0"
REVIEW_NOTE = ("Pseudonymized for privacy. Review the response text for any "
                 "self-identifying details (names, places) before sending to an LLM.")

PACKET_OPENING = (
    "You are scoring student work and drafting feedback that the teacher "
    "will send in their own voice."
)


def safe(name, max_len=80):
    return safe_filename_component(name, max_len=max_len, fallback="quiz")


def scoring_output_contract(
        *,
        identity_source: str = "the bundle",
        pseudonym: str = "<copy>",
        item_id: str = "<copy>",
) -> dict:
    """Return the shared scoring output contract and its JSON example.

    The model supplies structured fields only; Canvas Expert renders them
    into the one fixed plain-text layout. No persona exists here, and no AI
    identity or disclosure is invented.
    """
    sample = {
        "pseudonym": pseudonym,
        "item_id": item_id,
        "score": 1,
        "explanation": "<1-3 sentences explaining the score>",
        "glows": ["<specific strength>", "<specific strength>"],
        "grows": ["<specific area to improve>"],
        "fixes": ["<concrete fix, doable by hand in a second draft>",
                  "<concrete fix, doable by hand in a second draft>"],
        "writing_process_observations": "<optional teacher-only observation>",
    }
    exemplars_sample = {"<item_id>": "<model answer to the prompt, about 150 words at most>"}
    rules = [
        f"Copy pseudonym and item_id exactly from {identity_source} so results can be matched.",
        "If the bundle includes `shared_context`, use that assignment/source material when scoring every response. Do not ask for missing source material unless it is truly impossible to score without it.",
        "Quote briefly from the response to justify the score, when safe and useful.",
        "Never quote a pseudonym back in any field. Scrubbing replaces real names "
        "wherever they appear as whole words, so an ordinary word in a response may "
        "have been swapped for a pseudonym: a student surname that is also a common "
        "noun. Quote around it, or paraphrase.",
        "Do not identify students.",
        "`score` may be a number or null for comment-only feedback.",
        "`explanation` must be 1-3 sentences explaining the score.",
        "`glows` must list 2-3 specific strengths.",
        "`grows` must list 1-2 specific areas to improve.",
        "`fixes` must list 2-4 concrete changes a student could make by hand in a second draft.",
        "Do not name yourself, sign off, or mention AI, a teaching assistant, or a "
        "disclosure. Canvas Expert adds any disclosure the teacher asked for.",
        "When a response includes `writing_timeline`, you may return `writing_process_observations` as an observational, teacher-only string.",
        "It must never be an integrity conclusion, probability, or penalty recommendation.",
        "It must not change the score or any student-facing field.",
        "Use the full score range; `possible` gives each item's maximum.",
        "When a response includes `oral_reading`, use only the supplied passage, transcript, metrics, uncertainty, and candidate differences.",
        "Do not infer pronunciation, expression, prosody, identity, disability, effort, intent, cheating, or diagnosis; low ASR confidence is not a reading error.",
        "For each item_id where any student scored below full marks or received a "
        "null score, also provide one exemplar in `exemplars`: a short model answer "
        "to the prompt (about 150 words at most) a student could hand copy. Write "
        "one exemplar per item_id, shared by every student.",
    ]
    return {
        "format_instruction": (
            "Call stage_scoring_results with a JSON array in `results`, one object "
            "per scored response, exactly:"
        ),
        "sample": sample,
        "exemplars_sample": exemplars_sample,
        "rules": tuple(rules),
        "rules_text": "\n".join(f"- {rule}" for rule in rules),
    }


def build_contract_text(rubric_text: str = "",
                        *,
                        contract_text: str | None = None,
                        contract_name: str = "") -> str:
    """Instructions the teacher's AI client follows to score and draft feedback.

    ``rubric_text``, when provided, is inlined below so the file is
    self-contained (prompt context lives in the bundle; the rubric travels
    here). ``contract_text`` is an optional teacher-authored feedback contract
    file selected for this assignment, included verbatim under one layered
    heading; it cannot replace the base fields, minimums, or rendered layout
    above. Conversational scoring guidance is not a second copy here: it
    already layers onto the scoring basis, in ``rubric_text``, as the
    existing TEACHER DIRECTIVE block (scoring_preparation.py).
    """
    contract = scoring_output_contract()
    if rubric_text.strip():
        rubric_clause = ("The scoring rubric is included at the bottom of this file, "
                         "score strictly by it, do not invent criteria.")
        rubric_block = f"\n\n--- RUBRIC (score strictly by this) ---\n{rubric_text.strip()}\n"
    else:
        rubric_clause = ("No scoring rubric was provided, use the available assignment "
                         "context, score cautiously, and do not invent criteria.")
        rubric_block = ""

    sample = json.dumps(contract["sample"], indent=2, ensure_ascii=False)
    exemplars_sample = json.dumps(contract["exemplars_sample"], indent=2, ensure_ascii=False)

    file_body = str(contract_text or "")
    layered = ""
    if file_body.strip():
        contract_label = f" ({contract_name})" if str(contract_name or "").strip() else ""
        layered = (
            "\n\n--- TEACHER GUIDANCE (layered: adjusts judgment, tone, and emphasis; "
            "it cannot change the fields or layout) ---\n"
            f"Teacher feedback contract{contract_label} (verbatim):\n{file_body}"
        )

    return f"""{PACKET_OPENING} {rubric_clause}

You will receive a JSON bundle of pseudonymized responses. For EACH response
return one result object in `results`. {contract["format_instruction"]}

{sample}

For every item_id where any student scored below full marks or received a
null score, also call with `exemplars`, one entry per such item_id, shared by
every student:

{exemplars_sample}

Rules:
{contract["rules_text"]}{layered}{rubric_block}"""
