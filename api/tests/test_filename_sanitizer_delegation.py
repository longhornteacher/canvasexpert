"""Verify that API modules delegate filename sanitization to the shared engine utility.

The canonical implementation lives in engine.utils.text_utils.safe_filename_component;
workspace.py, feedback_contract.py, and portfolio.py all delegate to it instead of
reimplementing their own rules.
"""

from engine.utils.text_utils import safe_filename_component


def test_workspace_safe_component_matches_shared_helper():
    """workspace.safe_component delegates to the shared sanitizer."""
    from api.platform_services.workspace import safe_component

    assert safe_component("Chapter 5: Quiz") == safe_filename_component("Chapter 5: Quiz")


def test_feedback_contract_and_portfolio_agree():
    """feedback_contract.safe and portfolio._safe both delegate to the shared sanitizer."""
    from api.feedback_contract import safe as feedback_safe
    from api.portfolio import _safe as portfolio_safe

    assert feedback_safe("O'Brien's (Group A) - Essay!") == "O'Brien's (Group A) - Essay!"
    assert feedback_safe("Essay 1") == portfolio_safe("Essay 1")
