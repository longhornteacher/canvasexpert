"""PageForge 2.0 parser and envelope validation."""
import json
import re

from engine.rendering.forge.author_html import validate_author_html
from .attachment_validation import validate_attachments

ENVELOPE_RE = re.compile(
    r"<PAGEFORGE_JSON>\s*(\{.*\})\s*</PAGEFORGE_JSON>", re.S)


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
        return None, ["no <PAGEFORGE_JSON> … </PAGEFORGE_JSON> envelope found"]
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


def _text_array(value, path, problems):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        problems.append(f"{path} must be an array of non-empty strings")


def _sections(value, problems):
    if not isinstance(value, list):
        problems.append("sections must be an array")
        return
    for index, item in enumerate(value):
        path = f"sections[{index}]"
        if not _object(item, path, problems):
            continue
        _unknown_fields(item, {"heading", "html", "kind"}, path, problems)
        kind = item.get("kind", "section")
        if kind not in {"section", "callout", "collapsed"}:
            problems.append(f"{path}.kind must be section, callout, or collapsed")
        if kind != "callout":
            _required_text(item.get("heading"), f"{path}.heading", problems)
        elif "heading" in item:
            _optional_text(item["heading"], f"{path}.heading", problems)
        html = item.get("html")
        if not isinstance(html, str) or not html.strip():
            problems.append(f"{path}.html must be non-empty HTML text")
        else:
            problems.extend(validate_author_html(html, field_path=f"{path}.html", page=True))


def _extras(value, problems):
    if not isinstance(value, list):
        problems.append("extras must be an array")
        return
    for index, item in enumerate(value):
        path = f"extras[{index}]"
        if not _object(item, path, problems):
            continue
        _unknown_fields(item, {"summary", "html"}, path, problems)
        _required_text(item.get("summary"), f"{path}.summary", problems)
        html = item.get("html")
        if not isinstance(html, str) or not html.strip():
            problems.append(f"{path}.html must be non-empty HTML text")
        else:
            problems.extend(validate_author_html(html, field_path=f"{path}.html", page=True))


def _unit_info(value, problems):
    if value is None:
        return
    if not _object(value, "unit_info", problems):
        return
    _unknown_fields(value, {"unit", "teks", "subject", "grade"}, "unit_info", problems)
    for field in ("unit", "subject", "grade"):
        if field in value:
            _optional_text(value[field], f"unit_info.{field}", problems)
    if "teks" in value:
        _text_array(value["teks"], "unit_info.teks", problems)


def validate(d):
    problems = []
    if not isinstance(d, dict):
        return ["payload must be a JSON object"]
    if d.get("version") != "2.0-json":
        if d.get("version") == "1.0-json":
            problems.append('version "1.0-json" is no longer supported; re-fetch the PageForge authoring contract')
        else:
            problems.append(f'version must be "2.0-json" (got {d.get("version")!r})')
    if d.get("type") != "PAGE":
        problems.append(f"type must be PAGE (got {d.get('type')!r})")
    layout = d.get("layout", "standard")
    if layout not in {"standard", "freeform"}:
        problems.append("layout must be standard or freeform")
    allowed = {"version", "type", "title", "layout", "overview", "sections", "extras", "unit_info", "metadata", "attachments"}
    if layout == "freeform":
        allowed |= {"body", "banner"}
    _unknown_fields(d, allowed, "payload", problems)
    _required_text(d.get("title"), "title", problems)

    if layout == "standard":
        if "body" in d or "banner" in d:
            problems.append("standard layout does not allow body or banner")
        has_overview = isinstance(d.get("overview"), str) and bool(d["overview"].strip())
        sections = d.get("sections")
        has_sections = isinstance(sections, list) and bool(sections)
        if not has_overview and not has_sections:
            problems.append("standard layout requires overview or at least one section")
        if "overview" in d:
            overview = d["overview"]
            if not isinstance(overview, str) or not overview.strip():
                problems.append("overview must be non-empty HTML text")
            else:
                problems.extend(validate_author_html(overview, field_path="overview", page=True))
        if "sections" in d:
            _sections(d["sections"], problems)
    else:
        if "overview" in d:
            problems.append("freeform layout forbids overview")
        if "sections" in d:
            problems.append("freeform layout forbids sections")
        body = d.get("body")
        if not isinstance(body, str) or not body.strip():
            problems.append("body is required for freeform layout")
        else:
            problems.extend(validate_author_html(
                body, field_path="body", page=True, freeform=True,
            ))
        if "banner" in d and not isinstance(d["banner"], bool):
            problems.append("banner must be true or false")

    if "extras" in d:
        _extras(d["extras"], problems)
    _unit_info(d.get("unit_info"), problems)
    validate_attachments(d.get("attachments"), problems)
    return problems
