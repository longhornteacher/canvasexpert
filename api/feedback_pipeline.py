"""Compatibility facade for the feedback tools pipeline.

This module keeps the historical `feedback_pipeline` import path stable while the
implementation lives in smaller helper modules.
"""

from api.feedback_contract import CONTRACT_VERSION, REVIEW_NOTE, safe, build_contract_text
from api.feedback_artifacts import (
    _shared_context_blob,
    _scrub_bundle,
    pseudonymize,
    pseudonymize_submissions,
)
from api.feedback_results import (
        _AI_SIGNATURE_LINE_RE,
        flatten_text,
        item_rows_by_uid,
        merge_rows_by_uid,
        missing_exemplar_item_ids,
        parse_results,
        reidentify,
        render_feedback_item,
        render_results,
        validate_results,
)

__all__ = [
    "CONTRACT_VERSION",
    "REVIEW_NOTE",
    "safe",
    "_shared_context_blob",
    "_scrub_bundle",
    "_AI_SIGNATURE_LINE_RE",
    "build_contract_text",
    "flatten_text",
    "item_rows_by_uid",
    "merge_rows_by_uid",
    "missing_exemplar_item_ids",
    "parse_results",
    "pseudonymize",
    "pseudonymize_submissions",
    "reidentify",
    "render_feedback_item",
    "render_results",
    "validate_results",
]
