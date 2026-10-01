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
        item_rows_by_uid,
        item_feedback_for_mode,
        merge_rows_by_uid,
        parse_results,
        reidentify,
        render_results,
        validate_results,
)

__all__ = [
    "CONTRACT_VERSION",
    "REVIEW_NOTE",
    "safe",
    "_shared_context_blob",
    "_scrub_bundle",
    "build_contract_text",
    "item_rows_by_uid",
    "item_feedback_for_mode",
    "merge_rows_by_uid",
    "parse_results",
    "pseudonymize",
    "pseudonymize_submissions",
    "reidentify",
    "render_results",
    "validate_results",
]
