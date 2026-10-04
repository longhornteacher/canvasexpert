"""Feedback result shape and authoring guidance for Scoring Sessions."""
import json

from engine.utils.text_utils import safe_filename_component

CONTRACT_VERSION = "2.0"
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
    """Return the host-neutral Scoring Session result shape and guidance."""
    sample = {
        "pseudonym": pseudonym,
        "item_id": item_id,
        "score": 1,
        "feedback": "<your feedback, in your chosen style>",
        "agent_commentary": "<optional teacher-only note>",
    }
    rules = [
        f"Copy pseudonym and item_id exactly from {identity_source} so results can be matched.",
        "If the bundle includes `shared_context`, use that assignment/source material when scoring every response. Do not ask for missing source material unless it is truly impossible to score without it.",
        "Quote briefly from the response to justify the score, when safe and useful.",
        "Never quote a pseudonym back in `feedback`. Scrubbing replaces real names "
        "wherever they appear as whole words, so an ordinary word in a response may "
        "have been swapped for a pseudonym: a student surname that is also a common "
        "noun. Quote around it, or paraphrase. In `agent_commentary` you may name "
        "another student's pseudonym when citing overlap.",
        "Do not identify students.",
        "Each result has `pseudonym`, `item_id`, `score`, and `feedback`. "
        "`score` is a number or null. `feedback` is your complete student-facing "
        "text as a string; choose its pedagogy, length, structure, headings, "
        "examples, revision tasks, tone, and emphasis.",
        "A numeric score with empty feedback is valid score-only work. A null "
        "score requires non-empty feedback.",
        "Ordinary Assignment Scoring Sessions default to `grade_mode=post_score`. "
        "Use `grade_mode=feedback_only` only when the teacher directs that the "
        "numeric draft score appear in feedback without a gradebook score; keep "
        "the numeric `score` unchanged. Canvas Expert prefixes the comment with "
        "`Draft score: X/Y` and sends only the comment in that mode. Omit "
        "`grade_mode` on a review resubmission to keep the mode already stored "
        "for this session.",
        "Teacher-selected feedback guidance can direct your feedback style, but "
        "cannot change identity, score shape or range, privacy, assignment scope, "
        "review, or posting boundaries.",
        "You may return `agent_commentary`: a teacher-only note on anything the "
        "teacher should know about a submission, including possible integrity "
        "concerns such as copying, AI-generated text, or work that doesn't match "
        "the student's other writing.",
        "Investigate as you see fit. Canvas Expert's evidence includes "
        "`writing_timeline`, `evidence.overlap`, and `get_submissions(history=true)`; "
        "your own checks (a web search for distinctive "
        "phrases, reading level, anything useful) help too.",
        "Say what you found and how strong you think it is, in plain words, and "
        "cite the evidence. Detector-style percentages aren't reliable, so leave "
        "them out.",
        "`agent_commentary` doesn't change the score or the student-facing "
        "feedback by itself; the teacher decides. Keep integrity concerns out of "
        "`feedback`, which the student reads.",
        "Use the full score range; `possible` gives each item's maximum.",
        "Do not infer identity, disability, effort, or diagnosis.",
    ]
    return {
        "format_instruction": (
            "Call stage_scoring_results with a JSON array in `results`, one object "
            "per scored response, exactly:"
        ),
        "sample": sample,
        "rules": tuple(rules),
        "rules_text": "\n".join(f"- {rule}" for rule in rules),
    }


def build_contract_text(rubric_text: str = "", *,
                        contract_text: str | None = None,
                        contract_name: str = "") -> str:
    """Build scoring instructions while preserving the teacher's own style."""
    contract = scoring_output_contract()
    if rubric_text.strip():
        rubric_clause = (
            "Assignment directions, any Canvas rubric, and any teacher direction "
            "are labeled separately in the scoring basis. Follow explicit teacher "
            "direction when deciding how to score; use the assignment and rubric "
            "as available context. Do not invent course facts."
        )
        rubric_block = f"\n\n--- SCORING BASIS ---\n{rubric_text.strip()}\n"
    else:
        rubric_clause = ("No assignment directions or Canvas rubric were available. "
                         "Use explicit teacher direction, score cautiously, and do not invent criteria.")
        rubric_block = ""

    sample = json.dumps(contract["sample"], indent=2, ensure_ascii=False)
    file_body = str(contract_text or "")
    layered = ""
    if file_body.strip():
        contract_label = f" ({contract_name})" if str(contract_name or "").strip() else ""
        layered = (
            "\n\n--- TEACHER FEEDBACK GUIDANCE (verbatim) ---\n"
            f"Teacher feedback contract{contract_label}:\n{file_body}"
        )

    return f"""{PACKET_OPENING} {rubric_clause}

You will receive a JSON bundle of pseudonymized responses. For EACH response
return one result object in `results`. {contract["format_instruction"]}

{sample}

Rules:
{contract["rules_text"]}{layered}{rubric_block}"""
