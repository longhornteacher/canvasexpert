"""Student-facing submission descriptions for Canvas assignment types."""

SUBMISSION_WORDING = {
    "online_text_entry": "Type your answer in Canvas",
    "online_upload": "Upload a file",
    "online_url": "Submit a link",
    "media_recording": "Record audio or video",
    "on_paper": "Hand in on paper",
    "none": "Nothing to submit",
    "external_tool": "Complete it in the linked tool",
}


def submission_wording(submission: dict | None) -> str:
    """Describe a validated submission block, including tracked Word writing."""
    submission = submission or {}
    types = submission.get("types") or ["online_text_entry"]
    extensions = [str(ext).lstrip(".") for ext in (submission.get("allowed_extensions") or [])]
    if types == ["online_upload"] and extensions == ["docx"]:
        return "Upload your Word document"
    words = []
    for item in types:
        word = SUBMISSION_WORDING.get(item)
        if item == "online_upload" and extensions:
            extension_text = " or ".join(f".{ext}" for ext in extensions)
            word = f"Upload a {extension_text} file"
        if word and word not in words:
            words.append(word)
    return " or ".join(words)
