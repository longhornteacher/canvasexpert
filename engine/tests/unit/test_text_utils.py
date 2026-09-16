"""Unit tests for the shared filename sanitizer.

safe_filename_component is the one canonical implementation; workspace.py,
folder_creator.py, ai_ta.py, feedback_contract.py, and portfolio.py all
delegate to it instead of each reimplementing their own rules.
"""

import unittest

from engine.utils.text_utils import safe_filename_component


class TestSafeFilenameComponent(unittest.TestCase):
    def test_preserves_spaces_and_ordinary_punctuation(self):
        self.assertEqual(
            safe_filename_component("Unit 3 (Ch. 4-6) Quiz"),
            "Unit 3 (Ch. 4-6) Quiz",
        )

    def test_replaces_windows_illegal_characters(self):
        self.assertEqual(
            safe_filename_component('Essay: "Draft" <v2>'),
            "Essay_ _Draft_ _v2_",
        )

    def test_collapses_whitespace_and_trims_trailing_dots(self):
        self.assertEqual(safe_filename_component("  Quiz   Title.  "), "Quiz Title")

    def test_empty_input_uses_fallback(self):
        self.assertEqual(safe_filename_component("   ", fallback="_unnamed"), "_unnamed")
        self.assertEqual(safe_filename_component("", fallback=""), "")

    def test_guards_reserved_windows_device_names(self):
        self.assertEqual(safe_filename_component("CON"), "CON_")
        self.assertEqual(safe_filename_component("con.txt"), "con.txt_")
        self.assertEqual(safe_filename_component("Constitution"), "Constitution")

    def test_truncates_to_max_len(self):
        result = safe_filename_component("x" * 200, max_len=10)
        self.assertEqual(result, "x" * 10)


def test_folder_creator_preserves_spaces():
    """folder_creator.sanitize_filename delegates to the shared helper."""
    from engine.packaging.folder_creator import sanitize_filename

    assert sanitize_filename("Chapter 5 Quiz") == "Chapter 5 Quiz"
    assert sanitize_filename("") == ""


if __name__ == "__main__":
    unittest.main()
