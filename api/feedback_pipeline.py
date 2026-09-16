"""Compatibility facade for the feedback tools pipeline.

This module keeps the historical `feedback_pipeline` import path stable while the
implementation lives in smaller helper modules.
"""

from api.feedback_contract import CONTRACT_VERSION, REVIEW_NOTE, safe, build_contract_text, persona_signoff
from api.feedback_artifacts import (
    _shared_context_blob,
    _scrub_bundle,
    pseudonymize,
    pseudonymize_submissions,
)
from api.feedback_results import (
        _AI_SIGNATURE_LINE_RE,
        _DISCLOSURE_NAME_RE,
        _SECTION_LABELS,
        _format_feedback_linebreaks,
        _remove_persona_signature,
        _remove_phrase,
        item_rows_by_uid,
        merge_rows_by_uid,
        normalize_ai_feedback,
        parse_results,
        reidentify,
        reidentified_csv,
        validate_results,
)

__all__ = [
    "CONTRACT_VERSION",
    "REVIEW_NOTE",
    "safe",
    "_shared_context_blob",
    "_scrub_bundle",
    "_AI_SIGNATURE_LINE_RE",
    "_DISCLOSURE_NAME_RE",
    "_SECTION_LABELS",
    "_format_feedback_linebreaks",
    "_remove_persona_signature",
    "_remove_phrase",
    "build_contract_text",
    "item_rows_by_uid",
    "merge_rows_by_uid",
    "normalize_ai_feedback",
    "parse_results",
    "persona_signoff",
    "pseudonymize",
    "pseudonymize_submissions",
    "reidentify",
    "reidentified_csv",
    "validate_results",
]
