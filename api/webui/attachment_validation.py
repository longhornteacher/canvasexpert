"""Validation shared by the AssignmentForge and PageForge envelopes."""

ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx", "png", "jpg", "jpeg"}


def validate_attachments(value, problems):
    if value is None:
        return
    if not isinstance(value, list):
        problems.append("attachments must be an array")
        return
    seen = set()
    for index, item in enumerate(value):
        path = f"attachments[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{path} must be an object")
            continue
        unknown = sorted(set(item) - {"file", "label"})
        if unknown:
            problems.append(f"{path} has unknown fields: {unknown}")
        filename = item.get("file")
        label = item.get("label")
        if not isinstance(filename, str) or not filename.strip():
            problems.append(f"{path}.file must be non-empty text")
            continue
        if (filename != filename.strip() or filename in {".", ".."} or ".." in filename
                or "/" in filename or "\\" in filename or ":" in filename or "\x00" in filename):
            problems.append(f"{path}.file must be a bare file name")
            continue
        key = filename.casefold()
        if key in seen:
            problems.append(f"{path}.file is duplicated case-insensitively: {filename}")
        seen.add(key)
        suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
        if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS:
            problems.append(f"{path}.file has a disallowed type: {filename}")
        if not isinstance(label, str) or not label.strip():
            problems.append(f"{path}.label must be non-empty text")
