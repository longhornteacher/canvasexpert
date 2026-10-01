"""Canvas quiz metadata helper."""

from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape


def build_assessment_meta_xml(title: str, total_points: float, guid: str, instructions: str = "") -> str:
    description_content = f"<p>{xml_escape(instructions)}</p>" if instructions else ""
    return (
        "<?xml version=\"1.0\"?>\n"
        f"<quiz xmlns=\"http://canvas.instructure.com/xsd/cccv1p0\" xmlns:xsi=\"http://canvas.instructure.com/xsd/cccv1p0 https://canvas.instructure.com/xsd/cccv1p0.xsd\" identifier=\"{guid}\">\n"
                f"  <title>{xml_escape(title)}</title>\n"
        f"  <description>{description_content}</description>\n"
        "  <shuffle_questions>false</shuffle_questions>\n"
        "  <shuffle_answers>true</shuffle_answers>\n"
        "  <calculator_type>none</calculator_type>\n"
        "  <scoring_policy>keep_highest</scoring_policy>\n"
        "  <allowed_attempts>1</allowed_attempts>\n"
        f"  <points_possible>{total_points}</points_possible>\n"
        "</quiz>\n"
    )
