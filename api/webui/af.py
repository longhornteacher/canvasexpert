"""AssignmentForge — parse, validate, and compose <ASSIGNMENTFORGE_JSON> payloads.

Pure functions only: no HTTP here. server.py owns the Canvas calls (placeholder
resolution, group lookup, assignment creation) so this module stays testable
without a token. Contract: default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt (v1.0-json).
"""
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
    return problems


# Scaffolding is wrapped in a visually distinct support panel so students can
# tell base directions from tier supports at a glance.
_SCAFFOLD_WRAP = (
    '<div style="margin-top:18px;padding:12px 16px;'
    'border-left:4px solid #0b67c2;background:#eef5fc;border-radius:6px">'
    "{inner}</div>"
)


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
    return out


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
