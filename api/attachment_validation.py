"""Validation shared by the AssignmentForge and PageForge envelopes."""

ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx", "png", "jpg", "jpeg"}


def validate_attachments(value, problems):
    if value is None:
        return
    if not isinstance(value, list):
        problems.append("attachments must be an array")
        return
    seen_files = set()
    seen_canvas = set()
    for index, item in enumerate(value):
        path = f"attachments[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{path} must be an object")
            continue
        unknown = sorted(set(item) - {"file", "canvas_file", "folder", "label"})
        if unknown:
            problems.append(f"{path} has unknown fields: {unknown}")
        source_keys = [key for key in ("file", "canvas_file") if key in item]
        label = item.get("label")
        if len(source_keys) != 1:
            problems.append(f"{path} must contain exactly one of file or canvas_file")
            continue
        source = source_keys[0]
        filename = item.get(source)
        if not isinstance(filename, str) or not filename.strip():
            problems.append(f"{path}.{source} must be non-empty text")
            continue
        if (filename != filename.strip() or filename.endswith(".") or filename in {".", ".."}
                or ".." in filename or any(ord(char) < 32 for char in filename)
                or any(char in filename for char in ("/", "\\", ":", "<", ">", '"', "|", "?", "*", "\x00"))
                or filename.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            problems.append(f"{path}.{source} must be a safe bare file name")
            continue
        if source == "canvas_file" and "folder" in item:
            folder = item["folder"]
            if (not isinstance(folder, str) or not folder.strip() or folder != folder.strip()
                    or ".." in folder or "\\" in folder or "\x00" in folder):
                problems.append(f"{path}.folder must be a safe Canvas folder path")
        elif source == "file" and "folder" in item:
            problems.append(f"{path}.folder is only valid with canvas_file")
        key = filename.casefold()
        seen = seen_files if source == "file" else seen_canvas
        duplicate_key = (key, str(item.get("folder") or "").casefold()) if source == "canvas_file" else key
        if duplicate_key in seen:
            problems.append(f"{path}.{source} is duplicated: {filename}")
        seen.add(duplicate_key)
        suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
        if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS:
            problems.append(f"{path}.{source} has a disallowed type: {filename}")
        if not isinstance(label, str) or not label.strip():
            problems.append(f"{path}.label must be non-empty text")
