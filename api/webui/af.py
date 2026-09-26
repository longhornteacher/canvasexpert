"""AssignmentForge 2.0 parser and envelope validation.

Pure functions only: Canvas payload assembly and delivery belong to the
operation adapter. Student-facing fragments are checked by the offline Forge
renderer allowlist.
"""
import json
import math
import re

from engine.rendering.forge.author_html import validate_author_html
from .attachment_validation import validate_attachments

ENVELOPE_RE = re.compile(
    r"<ASSIGNMENTFORGE_JSON>\s*(\{.*\})\s*</ASSIGNMENTFORGE_JSON>", re.S)

ONLINE_TYPES = {"online_text_entry", "online_url", "online_upload",
                "media_recording"}
ALL_TYPES = ONLINE_TYPES | {"none", "on_paper", "external_tool"}
TIER_LABELS = {"support", "core", "accelerate"}


def parse_file(path):
    """Read a file and return (data, problems). data is None when unusable."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return None, [f"cannot read file: {e}"]
    except UnicodeDecodeError:
        return None, [
            "this file is not text (it looks like a Word document, PDF, or other "
            "binary file): ask your AI chat for a .md, .json, or .txt file, or paste "
            "the JSON directly"
        ]
    return parse(text)


def parse(text):
    m = ENVELOPE_RE.search(text)
    if not m:
        return None, ["no <ASSIGNMENTFORGE_JSON> … </ASSIGNMENTFORGE_JSON> envelope found"]
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return None, [f"invalid JSON inside envelope: {e}"]
    return data, validate(data)


def _object(value, path, problems):
    if not isinstance(value, dict):
        problems.append(f"{path} must be an object")
        return False
    return True


def _unknown_fields(value, allowed, path, problems):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        problems.append(f"{path} has unknown fields: {unknown}")


def _required_text(value, path, problems):
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{path} is required and must be non-empty text")


def _optional_text(value, path, problems):
    if not isinstance(value, str):
        problems.append(f"{path} must be text")


def _is_nonnegative_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value >= 0
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _html(value, path, problems):
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{path} must be non-empty HTML text")
        return
    problems.extend(validate_author_html(value, field_path=path))


def _text_array(value, path, problems):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        problems.append(f"{path} must be an array of non-empty strings")


def _validate_supports(value, path, problems):
    if value is None:
        return
    if not _object(value, path, problems):
        return
    _unknown_fields(value, {"sentence_frames", "word_bank", "html"}, path, problems)
    has_content = False
    for field in ("sentence_frames", "word_bank"):
        if field in value:
            _text_array(value[field], f"{path}.{field}", problems)
            has_content |= isinstance(value[field], list) and bool(value[field])
    if "html" in value:
        html_value = value["html"]
        if not isinstance(html_value, str):
            problems.append(f"{path}.html must be text")
        elif html_value.strip():
            problems.extend(validate_author_html(html_value, field_path=f"{path}.html"))
            has_content = True
    if not has_content:
        problems.append(f"{path} must contain sentence_frames, word_bank, or html")


def _validate_correction(value, path, problems):
    if not _object(value, path, problems):
        return
    _unknown_fields(value, {"answer", "why"}, path, problems)
    for field in ("answer", "why"):
        _required_text(value.get(field), f"{path}.{field}", problems)


def _validate_corrections(value, problems):
    if value is None:
        return
    if not _object(value, "corrections", problems):
        return
    for part_id, entry in value.items():
        path = f"corrections[{part_id!r}]"
        _required_text(part_id, "corrections item_id", problems)
        if not _object(entry, path, problems):
            continue
        _unknown_fields(entry, {"shared", "by_tier"}, path, problems)
        shared, by_tier = entry.get("shared"), entry.get("by_tier")
        if (shared is None) == (by_tier is None):
            problems.append(f"{path} must set exactly one of shared or by_tier")
        elif shared is not None:
            _validate_correction(shared, f"{path}.shared", problems)
        elif not isinstance(by_tier, dict) or not by_tier:
            problems.append(f"{path}.by_tier must be a non-empty object")
        else:
            for label, correction in by_tier.items():
                _required_text(label, f"{path}.by_tier label", problems)
                _validate_correction(correction, f"{path}.by_tier[{label!r}]", problems)


def _validate_rubric(value, points, problems):
    if value is None:
        return
    if not _object(value, "rubric", problems):
        return
    _unknown_fields(value, {"criteria"}, "rubric", problems)
    criteria = value.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        problems.append("rubric.criteria must be a non-empty array")
        return
    total = 0
    total_is_number = True
    for index, criterion in enumerate(criteria):
        path = f"rubric.criteria[{index}]"
        if not _object(criterion, path, problems):
            total_is_number = False
            continue
        _unknown_fields(criterion, {"name", "points", "description", "levels"}, path, problems)
        _required_text(criterion.get("name"), f"{path}.name", problems)
        criterion_points = criterion.get("points")
        if not _is_nonnegative_number(criterion_points):
            problems.append(f"{path}.points must be a number >= 0")
            total_is_number = False
        else:
            total += criterion_points
        if "description" in criterion:
            _optional_text(criterion["description"], f"{path}.description", problems)
        if "levels" in criterion:
            levels = criterion["levels"]
            if not isinstance(levels, list) or not levels:
                problems.append(f"{path}.levels must be a non-empty array")
            else:
                for level_index, level in enumerate(levels):
                    level_path = f"{path}.levels[{level_index}]"
                    if not _object(level, level_path, problems):
                        continue
                    _unknown_fields(level, {"label", "points", "description"}, level_path, problems)
                    _required_text(level.get("label"), f"{level_path}.label", problems)
                    lp = level.get("points")
                    if not _is_nonnegative_number(lp):
                        problems.append(f"{level_path}.points must be a number >= 0")
                    if "description" in level:
                        _optional_text(level["description"], f"{level_path}.description", problems)
    if total_is_number and isinstance(points, (int, float)) and not isinstance(points, bool) and total != points:
        problems.append(f"rubric criterion points must sum to points ({points})")


def _validate_submission(value, problems):
    if value is None:
        return
    if not _object(value, "submission", problems):
        return
    _unknown_fields(value, {"types", "allowed_extensions", "external_tool_url", "annotatable_file"}, "submission", problems)
    if "annotatable_file" in value:
        problems.append("submission.annotatable_file is not supported")
    types = value.get("types", ["online_text_entry"])
    if not isinstance(types, list) or not types:
        problems.append("submission.types must be a non-empty array")
        return
    bad = [item for item in types if not isinstance(item, str) or item not in ALL_TYPES]
    if bad:
        problems.append(f"submission.types has unsupported values: {bad}")
    if "external_tool" in types:
        if len(types) > 1:
            problems.append("submission.types: external_tool cannot combine with other types")
        _required_text(value.get("external_tool_url"), "submission.external_tool_url", problems)
    if "student_annotation" in types:
        problems.append("submission.types: student_annotation is not supported")
    if "allowed_extensions" in value:
        exts = value["allowed_extensions"]
        if not isinstance(exts, list) or any(not isinstance(ext, str) or not ext.strip() for ext in exts):
            problems.append("submission.allowed_extensions must be an array of non-empty strings")


def _validate_directions(value, path, problems):
    if not isinstance(value, list) or not value:
        problems.append(f"{path} must be a non-empty array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _object(item, item_path, problems):
            continue
        _unknown_fields(item, {"html", "response", "lines"}, item_path, problems)
        _html(item.get("html"), f"{item_path}.html", problems)
        response = item.get("response")
        if response not in {"none", "short", "long"}:
            problems.append(f"{item_path}.response must be none, short, or long")
        if "lines" in item:
            lines = item["lines"]
            if response != "short":
                problems.append(f"{item_path}.lines is only valid with response short")
            if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 12:
                problems.append(f"{item_path}.lines must be an integer from 1 to 12")


def _validate_sections(value, path, problems, kinds):
    if value is None:
        return
    if not isinstance(value, list):
        problems.append(f"{path} must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _object(item, item_path, problems):
            continue
        _unknown_fields(item, {"heading", "html", "kind"}, item_path, problems)
        _html(item.get("html"), f"{item_path}.html", problems)
        kind = item.get("kind", "section")
        if kind not in kinds:
            problems.append(f"{item_path}.kind must be one of {', '.join(sorted(kinds))}")
        if kind != "callout":
            _required_text(item.get("heading"), f"{item_path}.heading", problems)
        elif "heading" in item:
            _optional_text(item["heading"], f"{item_path}.heading", problems)


def _validate_extras(value, path, problems):
    if value is None:
        return
    if not isinstance(value, list):
        problems.append(f"{path} must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _object(item, item_path, problems):
            continue
        _unknown_fields(item, {"summary", "html"}, item_path, problems)
        _required_text(item.get("summary"), f"{item_path}.summary", problems)
        _html(item.get("html"), f"{item_path}.html", problems)


def _validate_tiers(value, problems, style=None):
    if value is None:
        return
    minimum = 1 if style == "hub" else 2
    if not isinstance(value, list) or len(value) < minimum:
        problems.append(f"tiers must be an array with at least {minimum} tier{'s' if minimum != 1 else ''}")
        return
    labels = set()
    for index, tier in enumerate(value):
        path = f"tiers[{index}]"
        if not _object(tier, path, problems):
            continue
        allowed = {"label", "supports"} if style == "hub" else {"label", "overview", "directions", "supports"}
        _unknown_fields(tier, allowed, path, problems)
        label = tier.get("label")
        if not isinstance(label, str) or label.strip().casefold() not in TIER_LABELS:
            problems.append(f"{path}.label must be Support, Core, or Accelerate")
        elif label.strip().casefold() in labels:
            problems.append(f"{path}.label is duplicated")
        else:
            labels.add(label.strip().casefold())
        if style == "hub" and ("overview" in tier or "directions" in tier):
            problems.append(f"{path}: hub instructions belong on the hub")
        if style == "hub" and tier.get("supports") is None:
            problems.append(f"{path}.supports is required for hub tiers")
        if "overview" in tier:
            _html(tier["overview"], f"{path}.overview", problems)
        if "directions" in tier:
            _validate_directions(tier["directions"], f"{path}.directions", problems)
        if "supports" in tier:
            _validate_supports(tier["supports"], f"{path}.supports", problems)


def _validate_unit_info(value, path, problems):
    if value is None:
        return
    if not _object(value, path, problems):
        return
    _unknown_fields(value, {"unit", "teks", "subject", "grade"}, path, problems)
    for field in ("unit", "subject", "grade"):
        if field in value:
            _optional_text(value[field], f"{path}.{field}", problems)
    if "teks" in value:
        _text_array(value["teks"], f"{path}.teks", problems)


def validate(d):
    problems = []
    if not isinstance(d, dict):
        return ["payload must be a JSON object"]
    if d.get("version") != "2.0-json":
        if d.get("version") == "1.0-json":
            problems.append('version "1.0-json" is no longer supported; re-fetch the AssignmentForge authoring contract')
        else:
            problems.append(f'version must be "2.0-json" (got {d.get("version")!r})')
    if d.get("type") != "ASSIGNMENT":
        problems.append(f"type must be ASSIGNMENT (got {d.get('type')!r}) — pages use PageForge")
    _unknown_fields(d, {"version", "type", "title", "points", "submission", "overview", "directions", "sections", "rubric", "supports", "extras", "unit_info", "tiers", "differentiation", "corrections", "metadata", "attachments"}, "payload", problems)
    style = d.get("differentiation")
    if "differentiation" in d and style not in {"bridge", "hub"}:
        problems.append('differentiation must be "bridge" or "hub"')
    if d.get("tiers") is not None and style not in {"bridge", "hub"}:
        problems.append("Ask the teacher which differentiation style to use, then re-fetch the AssignmentForge authoring contract.")
    if d.get("tiers") is None and "differentiation" in d:
        problems.append('differentiation is only valid when "tiers" is present')
    _required_text(d.get("title"), "title", problems)
    points = d.get("points")
    if not _is_nonnegative_number(points):
        problems.append("points is required and must be a number >= 0")
    _validate_submission(d.get("submission"), problems)
    _html(d.get("overview"), "overview", problems)
    _validate_directions(d.get("directions"), "directions", problems)
    _validate_sections(d.get("sections"), "sections", problems, {"section", "callout"})
    _validate_rubric(d.get("rubric"), points, problems)
    _validate_supports(d.get("supports"), "supports", problems)
    _validate_extras(d.get("extras"), "extras", problems)
    _validate_unit_info(d.get("unit_info"), "unit_info", problems)
    _validate_tiers(d.get("tiers"), problems, style=style)
    _validate_corrections(d.get("corrections"), problems)
    validate_attachments(d.get("attachments"), problems)
    return problems


def submission_fields(d):
    """Canvas REST assignment fields derived from the submission block."""
    sub = d.get("submission") or {}
    types = sub.get("types") or ["online_text_entry"]
    fields = {"submission_types": types}
    exts = sub.get("allowed_extensions") or []
    if exts and "online_upload" in types:
        fields["allowed_extensions"] = [str(e).lstrip(".") for e in exts]
    if "external_tool" in types:
        fields["external_tool_tag_attributes"] = {
            "url": str(sub.get("external_tool_url", "")).strip(),
            "new_tab": True,
        }
    return fields
