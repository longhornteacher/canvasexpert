"""AssignmentForge — parse, validate, and compose <ASSIGNMENTFORGE_JSON> payloads.

Pure functions only: no HTTP here. server.py owns the Canvas calls (placeholder
resolution, group lookup, assignment creation) so this module stays testable
without a token. Contract: default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt (v1.0-json).
"""
import html
import json
import re

ENVELOPE_RE = re.compile(
    r"<ASSIGNMENTFORGE_JSON>\s*(\{.*\})\s*</ASSIGNMENTFORGE_JSON>", re.S)

PLACEHOLDER_RE = re.compile(r"\{\{(file|page):([^}]+)\}\}")

ONLINE_TYPES = {"online_text_entry", "online_url", "online_upload",
                "media_recording", "student_annotation"}
ALL_TYPES = ONLINE_TYPES | {"none", "on_paper", "external_tool"}


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


def validate(d):
    problems = []
    if d.get("version") != "1.0-json":
        problems.append(f"version must be \"1.0-json\" (got {d.get('version')!r})")
    typ = d.get("type")
    if typ != "ASSIGNMENT":
        problems.append(f"type must be ASSIGNMENT (got {typ!r}) — pages use PageForge")
    if not str(d.get("title", "")).strip():
        problems.append("title is required")
    if not str(d.get("description", "")).strip():
        problems.append("description is required")
    pts = d.get("points")
    if pts is not None and (not isinstance(pts, (int, float)) or pts < 0):
        problems.append("points must be a number >= 0")

    sub = d.get("submission") or {}
    types = sub.get("types") or []
    bad = [t for t in types if t not in ALL_TYPES]
    if bad:
        problems.append(f"unknown submission types: {bad}")
    if "external_tool" in types:
        if len(types) > 1:
            problems.append("external_tool cannot combine with other submission types")
        if not str(sub.get("external_tool_url", "")).strip():
            problems.append("external_tool requires submission.external_tool_url")
    if "student_annotation" in types and not str(sub.get("annotatable_file", "")).strip():
        problems.append("student_annotation requires submission.annotatable_file")

    tiers = d.get("tiers") or []
    labels = set()
    for i, t in enumerate(tiers):
        if not str(t.get("label", "")).strip():
            problems.append(f"tier {i + 1}: label is required")
        if "group" in t:
            problems.append(f"tier {i + 1}: group is not permitted; the teacher assigns drafts in Canvas")
        lbl = str(t.get("label", "")).strip().lower()
        if lbl and lbl in labels:
            problems.append(f"tier label {t.get('label')!r} is duplicated")
        labels.add(lbl)
    _validate_supports(d.get("supports"), tiers, problems)
    _validate_corrections(d.get("corrections"), problems)
    return problems


def _text_list(value, path, problems):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip()
                                          for item in value):
        problems.append(f"{path} must be a list of non-empty strings")


def _validate_supports(value, tiers, problems):
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        problems.append("supports must be an object keyed by tier")
        return
    if not tiers and value:
        problems.append("supports requires tiers")
    for key, support in value.items():
        path = f"supports[{key!r}]"
        if not isinstance(key, str) or not key.strip():
            problems.append("supports keys must be non-empty strings")
            continue
        if not isinstance(support, dict):
            problems.append(f"{path} must be an object")
            continue
        unknown = sorted(set(support) - {"stem_frame", "scaffold", "verb_bank"})
        if unknown:
            problems.append(f"{path} has unknown fields: {unknown}")
        if "stem_frame" in support:
            _text_list(support["stem_frame"], f"{path}.stem_frame", problems)
        if "verb_bank" in support:
            _text_list(support["verb_bank"], f"{path}.verb_bank", problems)
        if "scaffold" in support and support["scaffold"] not in {"bullet", "outline"}:
            problems.append(f"{path}.scaffold must be 'bullet' or 'outline'")


def _validate_correction(value, path, problems):
    if not isinstance(value, dict):
        problems.append(f"{path} must be an object")
        return
    unknown = sorted(set(value) - {"answer", "why"})
    if unknown:
        problems.append(f"{path} has unknown fields: {unknown}")
    for field in ("answer", "why"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            problems.append(f"{path}.{field} must be non-empty text")


def _validate_corrections(value, problems):
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        problems.append("corrections must be an object keyed by packet item_id")
        return
    for part_id, entry in value.items():
        path = f"corrections[{part_id!r}]"
        if not isinstance(part_id, str) or not part_id.strip():
            problems.append("corrections keys must be non-empty strings")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{path} must be an object")
            continue
        unknown = sorted(set(entry) - {"shared", "by_tier"})
        if unknown:
            problems.append(f"{path} has unknown fields: {unknown}")
        shared = entry.get("shared")
        by_tier = entry.get("by_tier")
        shared_set = shared is not None
        by_tier_set = by_tier is not None
        if shared_set == by_tier_set:
            problems.append(f"{path} must set exactly one of shared or by_tier")
            continue
        if shared_set:
            _validate_correction(shared, f"{path}.shared", problems)
            continue
        if not isinstance(by_tier, dict) or not by_tier:
            problems.append(f"{path}.by_tier must be a non-empty object")
            continue
        for tier, correction in by_tier.items():
            if not isinstance(tier, str) or not tier.strip():
                problems.append(f"{path}.by_tier keys must be non-empty strings")
                continue
            _validate_correction(correction, f"{path}.by_tier[{tier!r}]", problems)


# Scaffolding is wrapped in a visually distinct support panel so students can
# tell base directions from tier supports at a glance.
_SCAFFOLD_WRAP = (
    '<div style="margin-top:18px;padding:12px 16px;'
    'border-left:4px solid #0b67c2;background:#eef5fc;border-radius:6px">'
    "{inner}</div>"
)

_SUPPORT_WRAP = (
    '<div style="border:1px solid #c9c9c9;border-radius:8px;'
    'padding:10px 14px;background:#f6f6f6;margin:10px 0;">{inner}</div>'
)


def _support_for(supports, label, public_tag=""):
    if not isinstance(supports, dict):
        return None, ""
    for candidate in (public_tag, label):
        wanted = str(candidate or "").strip().casefold()
        if not wanted:
            continue
        for key, value in supports.items():
            if str(key).strip().casefold() == wanted:
                return value if isinstance(value, dict) else None, str(key).strip()
    return None, ""


def _support_html(support, support_key=""):
    if not isinstance(support, dict):
        return ""
    stem_frame = [str(item).strip() for item in support.get("stem_frame") or []
                  if str(item).strip()]
    verb_bank = [str(item).strip() for item in support.get("verb_bank") or []
                 if str(item).strip()]
    if not stem_frame and not verb_bank:
        return ""

    def escaped(value):
        return html.escape(value, quote=True)

    if stem_frame:
        inner = "<strong>Complete the sentence:</strong><ul>" + "".join(
            f"<li>{escaped(item)}</li>" for item in stem_frame
        ) + "</ul>"
    elif support.get("scaffold") in {"bullet", "outline"}:
        inner = "<strong>Word bank:</strong> " + ", ".join(
            escaped(item) for item in verb_bank
        )
    else:
        key = str(support_key or "").casefold()
        heading = ("<strong>Push your analysis with verbs like:</strong> "
                   if key in {"blue", "accelerate", "extend"}
                   else "<strong>Word bank:</strong> ")
        inner = heading + ", ".join(escaped(item) for item in verb_bank)
    return _SUPPORT_WRAP.format(inner=inner)


def add_supports(tier_rows, supports, resolved_tiers=None):
    """Append authored support blocks without changing the base scaffold path."""
    resolved_tiers = resolved_tiers or []
    out = []
    for index, row in enumerate(tier_rows or []):
        current = dict(row)
        resolved = resolved_tiers[index] if index < len(resolved_tiers) else {}
        support, support_key = _support_for(
            supports, current.get("label"), resolved.get("tag"),
        )
        block = _support_html(support, support_key)
        if block and block not in (current.get("description") or ""):
            current["description"] = f"{current.get('description') or ''}{block}"
        out.append(current)
    return out


def tier_payloads(d):
    """Compose the per-push payload list.

    Returns [{label, title, description}]; a single entry with label None when
    the payload has no tiers (whole-class).
    """
    title = str(d.get("title", "")).strip()
    base = str(d.get("description", ""))
    tiers = d.get("tiers") or []
    if not tiers:
        return [{"label": None, "title": title, "description": base}]
    out = []
    for t in tiers:
        desc = str(t.get("description") or base)
        scaffold = str(t.get("scaffolding") or "").strip()
        if scaffold:
            desc += _SCAFFOLD_WRAP.format(inner=scaffold)
        out.append({
            "label": str(t["label"]).strip(),
            "title": title,
            "description": desc,
        })
    return add_supports(out, d.get("supports"), [
        {"tier": row.get("label"), "tag": ""} for row in tiers
    ])


def submission_fields(d):
    """Canvas REST assignment fields derived from the submission block.

    annotatable_file is returned by NAME under a private key; server.py
    resolves it to a course file id per course.
    """
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
    if "student_annotation" in types:
        fields["_annotatable_file_name"] = str(sub.get("annotatable_file", "")).strip()
    return fields
