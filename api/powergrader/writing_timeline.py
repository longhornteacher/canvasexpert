"""Local OOXML Writing Timeline parsing and privacy-safe projection helpers.

Raw Office metadata belongs only to private PowerGrader evidence.  The SAFE
projection produced here is deliberately reconstructed from a small whitelist;
it never copies arbitrary report fields.
"""

from __future__ import annotations

import copy
import io
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DC_NS = "http://purl.org/dc/elements/1.1/"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
APP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

_W_INS = f"{{{W_NS}}}ins"
_W_DEL = f"{{{W_NS}}}del"
_W_TEXT = f"{{{W_NS}}}t"
_W_DEL_TEXT = f"{{{W_NS}}}delText"
_W_TAB = f"{{{W_NS}}}tab"
_W_BREAK = {f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}
_ALLOWED_AUTHOR_CATEGORIES = {
    "submission_author",
    "other_roster_author",
    "unrecognized_author_present",
}
_TRACKING_BOOLEAN_KEYS = (
    "trail_present",
    "track_revisions_present",
    "tracking_protection_present",
    "tracking_protection_enforced",
    "tracking_lock_present",
)
_AGGREGATE_KEYS = (
    "available",
    "valid",
    "block_count",
    "insertion_count",
    "deletion_count",
    *_TRACKING_BOOLEAN_KEYS,
)
_PROPERTY_NUMBER_KEYS = ("total_time_minutes", "revision")
_PROPERTY_AUTHOR_KEYS = ("creator_category", "last_modified_by_category")


def _normalized_values(value, *, extension: bool = False) -> set[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    normalized = set()
    for item in values:
        text = str(item or "").strip().lower()
        if extension:
            text = text[1:] if text.startswith(".") else text
        if text:
            normalized.add(text)
    return normalized


def is_tracked_assignment(assignment: dict | None) -> bool:
    """Return True only for Canvas's exact DOCX-only online-upload shape."""
    assignment = assignment if isinstance(assignment, dict) else {}
    return (
        _normalized_values(assignment.get("submission_types")) == {"online_upload"}
        and _normalized_values(assignment.get("allowed_extensions"), extension=True) == {"docx"}
    )


def _observation(part: str, status: str, reason: str) -> dict:
    return {"part": part, "status": status, "reason": reason}


def unavailable_report(reason: str = "local_file_unavailable") -> dict:
    """Build a non-crashing private report for evidence that cannot be parsed."""
    return {
        "status": "unavailable",
        "trail_present": None,
        "track_revisions_present": None,
        "tracking_protection_present": None,
        "tracking_protection_enforced": None,
        "tracking_lock_present": None,
        "properties": {
            "creator": None,
            "last_modified_by": None,
            "total_time_minutes": None,
            "revision": None,
        },
        "blocks": [],
        "largest_insertions": [],
        "observations": [_observation("package", "unavailable", reason)],
    }


def _invalid_report(reason: str, *, part: str = "package") -> dict:
    report = unavailable_report(reason)
    report["status"] = "invalid"
    report["observations"] = [_observation(part, "invalid", reason)]
    return report


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _attribute(element: ET.Element, local_name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == local_name:
            text = str(value or "").strip()
            return text or None
    return None


def _read_xml(
    archive: zipfile.ZipFile,
    part: str,
    observations: list[dict],
    *,
    required: bool = False,
) -> ET.Element | None:
    try:
        payload = archive.read(part)
    except KeyError:
        observations.append(_observation(part, "unavailable", "missing_part"))
        return None
    except (OSError, RuntimeError, zipfile.BadZipFile):
        observations.append(_observation(part, "invalid", "unreadable_part"))
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        observations.append(_observation(part, "invalid", "malformed_xml"))
        return None
    if required and _local_name(root.tag) != "document":
        observations.append(_observation(part, "invalid", "unsupported_shape"))
        return None
    return root


CENTRAL_TZ_NAME = "America/Chicago"
_central_zone = None


def central_timezone():
    """Return the America/Chicago zone, or None if no tz database is available.

    Every timeline timestamp is Central, end to end -- parsed report, SAFE
    projection, and teacher UI alike.  This is not cosmetic: a 6:04pm-9:48pm
    writing session normalized to UTC reads as "23:04Z to 02:48Z", which looks
    like overnight work.  A feature whose whole purpose is to avoid implying
    things about a student must not hand the teacher a misleading clock.

    None means "convert through the machine's own clock instead" -- see
    `_normalize_timestamp`.  Never UTC: silently reverting to UTC would restore
    exactly the misreading this exists to prevent.
    """
    global _central_zone
    if _central_zone is None:
        try:
            _central_zone = ZoneInfo(CENTRAL_TZ_NAME)
        except Exception:
            _central_zone = False  # cached "unavailable"; retrying per call is pointless
    return _central_zone or None


def _normalize_timestamp(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Word writes w:date in UTC.  A bare timestamp is read as UTC rather than
        # guessed at, so the Central conversion below stays correct.
        parsed = parsed.replace(tzinfo=timezone.utc)
    zone = central_timezone()
    # Argument-less astimezone() asks the OS for the offset at *that instant*, so the
    # no-tzdata path still tracks DST.  Converting through a captured
    # `datetime.now().astimezone().tzinfo` would not: that is a frozen offset, and it
    # would put every timestamp in the opposite DST season an hour out.
    central = parsed.astimezone(zone) if zone is not None else parsed.astimezone()
    return central.replace(microsecond=0).isoformat()


def _block_text(element: ET.Element) -> str:
    chunks: list[str] = []
    for node in element.iter():
        if node.tag in {_W_TEXT, _W_DEL_TEXT}:
            chunks.append(node.text or "")
        elif node.tag == _W_TAB:
            chunks.append("\t")
        elif node.tag in _W_BREAK:
            chunks.append("\n")
    return "".join(chunks)


def _block(element: ET.Element, *, part: str, order: int) -> dict:
    text = _block_text(element)
    raw_timestamp = _attribute(element, "date")
    return {
        "type": "insertion" if element.tag == _W_INS else "deletion",
        "author": _attribute(element, "author"),
        "timestamp": _normalize_timestamp(raw_timestamp),
        "raw_timestamp": raw_timestamp,
        "character_count": len(text),
        "word_count": len(re.findall(r"\b[\w\u2019'-]+\b", text, flags=re.UNICODE)),
        "story_part": part,
        "document_order": order,
    }


def _story_parts(names: list[str]) -> list[str]:
    """Return main-document-first Word story XML parts in stable order."""
    candidates = []
    for name in names:
        path = PurePosixPath(name)
        base = path.name.lower()
        if path.parent.as_posix() != "word" or path.suffix.lower() != ".xml":
            continue
        if (
            base == "document.xml"
            or base.startswith("header")
            or base.startswith("footer")
            or base in {"footnotes.xml", "endnotes.xml", "comments.xml"}
        ):
            candidates.append(name)
    return sorted(set(candidates), key=lambda value: (value != "word/document.xml", value))


def _integer_property(
    root: ET.Element | None,
    local_name: str,
    part: str,
    observations: list[dict],
) -> int | None:
    if root is None:
        return None
    node = next((item for item in root.iter() if _local_name(item.tag) == local_name), None)
    if node is None or not str(node.text or "").strip():
        return None
    try:
        value = int(str(node.text).strip())
    except ValueError:
        observations.append(_observation(part, "invalid", f"malformed_{local_name.lower()}"))
        return None
    if value < 0:
        observations.append(_observation(part, "invalid", f"malformed_{local_name.lower()}"))
        return None
    return value


def parse_docx(data: bytes) -> dict:
    """Parse a DOCX timeline without exposing visible document text."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        return _invalid_report("malformed_zip")

    observations: list[dict] = []
    try:
        names = archive.namelist()
        document = _read_xml(archive, "word/document.xml", observations, required=True)
        if document is None:
            status = "invalid" if any(
                item["status"] == "invalid" for item in observations
            ) else "unavailable"
            report = _invalid_report(
                observations[-1]["reason"] if observations else "missing_document",
                part="word/document.xml",
            )
            report["status"] = status
            report["observations"] = observations or [
                _observation("word/document.xml", status, "missing_document")
            ]
            return report

        settings = _read_xml(archive, "word/settings.xml", observations)
        core = _read_xml(archive, "docProps/core.xml", observations)
        app = _read_xml(archive, "docProps/app.xml", observations)

        track_revisions = False
        protection_present = False
        protection_enforced = False
        if settings is not None:
            track_revisions = any(node.tag == f"{{{W_NS}}}trackRevisions" for node in settings.iter())
            protections = [
                node for node in settings.iter()
                if node.tag == f"{{{W_NS}}}documentProtection"
                and str(_attribute(node, "edit") or "").lower() == "trackedchanges"
            ]
            protection_present = bool(protections)
            protection_enforced = any(
                str(_attribute(node, "enforcement") or "").lower() in {"1", "true", "on"}
                for node in protections
            )

        creator = None
        last_modified = None
        if core is not None:
            creator_node = core.find(f".//{{{DC_NS}}}creator")
            modified_node = core.find(f".//{{{CP_NS}}}lastModifiedBy")
            creator = str(creator_node.text or "").strip() or None if creator_node is not None else None
            last_modified = (
                str(modified_node.text or "").strip() or None
                if modified_node is not None else None
            )

        blocks: list[dict] = []
        roots = {"word/document.xml": document}
        order = 0
        for part in _story_parts(names):
            root = roots.get(part)
            if root is None:
                root = _read_xml(archive, part, observations)
            if root is None:
                continue
            for element in root.iter():
                if element.tag not in {_W_INS, _W_DEL}:
                    continue
                blocks.append(_block(element, part=part, order=order))
                order += 1

        largest = sorted(
            (
                copy.deepcopy(item)
                for item in blocks
                if item["type"] == "insertion" and item["character_count"] > 0
            ),
            key=lambda item: (-item["character_count"], item["document_order"]),
        )[:3]
        return {
            "status": "available",
            "trail_present": bool(blocks),
            "track_revisions_present": track_revisions,
            "tracking_protection_present": protection_present,
            "tracking_protection_enforced": protection_enforced,
            "tracking_lock_present": protection_present and protection_enforced,
            "properties": {
                "creator": creator,
                "last_modified_by": last_modified,
                "total_time_minutes": _integer_property(
                    app, "TotalTime", "docProps/app.xml", observations
                ),
                "revision": _integer_property(
                    app, "Revision", "docProps/app.xml", observations
                ),
            },
            "blocks": blocks,
            "largest_insertions": largest,
            "observations": observations,
        }
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return _invalid_report("unreadable_package")
    finally:
        archive.close()


def _normalize_author(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split()).casefold()


def _roster_aliases(record: dict) -> set[str]:
    aliases = set()
    user = record.get("user") if isinstance(record.get("user"), dict) else {}
    values = [
        record.get("real_name"),
        record.get("name"),
        user.get("name"),
        user.get("sortable_name"),
    ]
    values.extend(record.get("nicknames") or [])
    for value in values:
        normalized = _normalize_author(value)
        # Single tokens are deliberately never trusted as identity matches.
        if normalized and len(normalized.split()) >= 2:
            aliases.add(normalized)
    return aliases


def roster_records(submissions: list[dict]) -> list[dict]:
    records: dict[str, dict] = {}
    for submission in submissions or []:
        if not isinstance(submission, dict):
            continue
        user = submission.get("user") if isinstance(submission.get("user"), dict) else {}
        canvas_id = str(submission.get("user_id") or user.get("id") or "")
        if not canvas_id:
            continue
        records[canvas_id] = {
            "canvas_id": canvas_id,
            "user": {
                "name": user.get("name"),
                "sortable_name": user.get("sortable_name"),
            },
        }
    return list(records.values())


def _author_category(
    raw_author: str | None,
    *,
    submission_canvas_id: str,
    alias_owners: dict[str, set[str]],
) -> str | None:
    normalized = _normalize_author(raw_author)
    if not normalized:
        return None
    owners = alias_owners.get(normalized, set())
    if len(owners) != 1:
        return "unrecognized_author_present"
    owner = next(iter(owners))
    return "submission_author" if owner == str(submission_canvas_id) else "other_roster_author"


def categorize_authors(
    report: dict,
    *,
    submission_canvas_id: str,
    roster: list[dict],
) -> dict:
    """Add non-identifying match categories while retaining private raw values."""
    categorized = copy.deepcopy(report)
    alias_owners: dict[str, set[str]] = {}
    for record in roster or []:
        canvas_id = str(record.get("canvas_id") or "")
        if not canvas_id:
            continue
        for alias in _roster_aliases(record):
            alias_owners.setdefault(alias, set()).add(canvas_id)

    properties = categorized.get("properties")
    if isinstance(properties, dict):
        properties["creator_category"] = _author_category(
            properties.get("creator"),
            submission_canvas_id=submission_canvas_id,
            alias_owners=alias_owners,
        )
        properties["last_modified_by_category"] = _author_category(
            properties.get("last_modified_by"),
            submission_canvas_id=submission_canvas_id,
            alias_owners=alias_owners,
        )
    for key in ("blocks", "largest_insertions"):
        for block in categorized.get(key) or []:
            if isinstance(block, dict):
                block["author_category"] = _author_category(
                    block.get("author"),
                    submission_canvas_id=submission_canvas_id,
                    alias_owners=alias_owners,
                )
    return categorized


def _safe_block(block: dict) -> dict:
    safe = {
        "type": "insertion" if block.get("type") == "insertion" else "deletion",
        "character_count": max(0, int(block.get("character_count") or 0)),
        "word_count": max(0, int(block.get("word_count") or 0)),
    }
    timestamp = _normalize_timestamp(block.get("timestamp"))
    if timestamp:
        safe["timestamp"] = timestamp
    category = block.get("author_category")
    if category in _ALLOWED_AUTHOR_CATEGORIES:
        safe["author_category"] = category
    return safe


def _safe_properties(properties: dict | None) -> dict:
    if not isinstance(properties, dict):
        return {}
    safe_properties = {}
    for key in _PROPERTY_NUMBER_KEYS:
        value = properties.get(key)
        if isinstance(value, int) and value >= 0:
            safe_properties[key] = value
    for key in _PROPERTY_AUTHOR_KEYS:
        value = properties.get(key)
        if value in _ALLOWED_AUTHOR_CATEGORIES:
            safe_properties[key] = value
    return safe_properties


def safe_projection(report: dict | None) -> dict | None:
    """Rebuild the outbound projection from a strict value whitelist."""
    if not isinstance(report, dict):
        return None
    observations = report.get("observations") or []
    projection = {
        "available": report.get("status") == "available",
        "valid": not any(
            isinstance(item, dict) and item.get("status") == "invalid"
            for item in observations
        ),
        "block_count": len(report.get("blocks") or []),
        "insertion_count": sum(
            1 for item in report.get("blocks") or []
            if isinstance(item, dict) and item.get("type") == "insertion"
        ),
        "deletion_count": sum(
            1 for item in report.get("blocks") or []
            if isinstance(item, dict) and item.get("type") == "deletion"
        ),
    }
    for key in _TRACKING_BOOLEAN_KEYS:
        if isinstance(report.get(key), bool):
            projection[key] = report[key]

    safe_properties = _safe_properties(report.get("properties"))
    if safe_properties:
        projection["properties"] = safe_properties

    # The per-block array is deliberately NOT projected.  Counts above already
    # carry the volume signal, nothing downstream reads it, and a heavily tracked
    # DOCX inflates it without bound: a 75 KB upload measured 60,000 blocks and
    # 8.6 MB of outbound JSON, billed against the teacher's own AI key.
    projection["largest_insertions"] = [
        _safe_block(item) for item in report.get("largest_insertions") or []
        if isinstance(item, dict) and item.get("type") == "insertion"
        and int(item.get("character_count") or 0) > 0
    ][:3]
    return projection


def aggregate_summary(projection: dict | None) -> dict | None:
    """Return the aggregate-only portion of a SAFE timeline projection."""
    if not isinstance(projection, dict):
        return None
    summary = {}
    for key in _AGGREGATE_KEYS:
        value = projection.get(key)
        if key in _TRACKING_BOOLEAN_KEYS or key in {"available", "valid"}:
            if isinstance(value, bool):
                summary[key] = value
        elif isinstance(value, int) and value >= 0:
            summary[key] = value
    if "available" not in summary:
        return None
    safe_properties = _safe_properties(projection.get("properties"))
    if safe_properties:
        summary["properties"] = safe_properties
    return summary


_SUMMARY_FIELD_LABELS = (
    ("available", "Available"),
    ("valid", "Valid"),
    ("block_count", "Block count"),
    ("insertion_count", "Insertion count"),
    ("deletion_count", "Deletion count"),
    ("trail_present", "Revision trail present"),
    ("track_revisions_present", "Track revisions present"),
    ("tracking_protection_present", "Tracking protection present"),
    ("tracking_protection_enforced", "Tracking protection enforced"),
    ("tracking_lock_present", "Tracking lock present"),
)
_SUMMARY_PROPERTY_LABELS = (
    ("total_time_minutes", "Total time minutes"),
    ("revision", "Revision"),
    ("creator_category", "Creator category"),
    ("last_modified_by_category", "Last modified by category"),
)


def aggregate_summary_lines(
    projection: dict | None,
    *,
    title: str = "Writing Timeline Summary",
) -> list[str]:
    """Render a readable, aggregate-only summary for a teacher-facing packet."""
    summary = aggregate_summary(projection)
    if not summary or not summary.get("available"):
        return []
    lines = [f"### {title}"]
    for key, label in _SUMMARY_FIELD_LABELS:
        if key not in summary:
            continue
        value = summary[key]
        if isinstance(value, bool):
            value = "yes" if value else "no"
        lines.append(f"{label}: {value}")
    properties = summary.get("properties") or {}
    for key, label in _SUMMARY_PROPERTY_LABELS:
        if key in properties:
            lines.append(f"{label}: {properties[key]}")
    return lines


OBSERVATION_WITHHELD_NOTICE = (
    "Observation withheld: the model returned an integrity conclusion, which this "
    "feature does not report. The timeline facts above are unchanged."
)

_INTEGRITY_CONCLUSION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Named integrity verdicts.
        r"\bcheat\w*\b",
        r"\bplagiari\w*\b",
        r"\bacademic (dis)?(honesty|integrity)\b",
        r"\bmisconduct\b",
        r"\bghost.?writ\w*\b",
        r"\bsuspicio\w*\b",
        r"\bsuspect\w*\b",
        r"\b(honor|honour) code\b",
        # Attribution to a generator or to another person.
        r"\bai.?(generated|written|authored)\b",
        r"\b(generated|written|authored) by (an? )?(ai|llm|bot|chatbot|machine|chatgpt|copilot|gemini)\b",
        r"\b(?:used|asked|copied from|generated with)\s+(?:chatgpt|copilot|gemini|ai|llm|chatbot|an? ai|an? llm|a chatbot)\b",
        r"\bsomeone else (wrote|authored|typed|did)\b",
        r"\bnot (the )?(real |actual )?author\b",
        r"\bdid ?n[o']?t (write|author)\b",
        # Hedged authorship claims, the contract forbids probability, not just verdicts.
        r"\b(likely|probably|possibly|may have|might have|appears? to have|"
        r"seems? to have|evidently|clearly)\b[^.!?]{0,60}\b(wrote|written|authored?|"
        r"copied|outside help|another person|used ai)\b",
        # Penalty or escalation recommendations.
        r"\bpenal\w*\b",
        r"\bdisciplin\w*\s+action\b",
        r"\b(give|assign|award)\w* (them |the student )?a? ?zero\b",
        r"\bscore of zero\b",
        r"\b(report|refer|escalate) (this |them |the student )?to\b",
        r"\b(?:academic[- ]integrity|integrity) referral\b",
        r"\bshould be investigated\b",
    )
)

# These are bounded evidence-limit clauses, rather than integrity conclusions.
# Their subject, negative predicate, and complement are deliberately finite so
# arbitrary accusations cannot be absorbed by a disclaimer.
_EVIDENCE_LIMITATION_CLAUSE = re.compile(
    r"^(?:(?:(?:the|these|this)\s+)?(?:timestamps?|timeline|revision\s+trail|"
    r"history|revision\s+history|records?|metadata|evidence)(?:\s+alone)?\s+"
    r"(?:do not|does not|cannot|can't|doesn't)\s+"
    r"(?:establish|prove|show|demonstrate|confirm|indicate|determine|justify|support)\s+"
    r"(?:cheating|plagiarism|authorship|academic\s+dishonesty|AI\s+use|whether\s+AI\s+was\s+used|"
    r"a\s+penalty|disciplinary\s+action)(?:\s+(?:from|in)\s+(?:(?:the|these|this)\s+)?"
    r"(?:timestamps?|records?|history|metadata|evidence))?"
    r"|there\s+(?:is|are)\s+no\s+evidence\s+of\s+"
    r"(?:cheating|plagiarism|authorship|academic\s+dishonesty|AI\s+use|whether\s+AI\s+was\s+used|"
    r"a\s+penalty|disciplinary\s+action)(?:\s+(?:from|in)\s+(?:(?:the|these|this)\s+)?"
    r"(?:timestamps?|records?|history|metadata|evidence))?)$",
    re.IGNORECASE,
)


def _observation_clauses(text: str) -> list[str]:
    """Return bounded clauses without changing the original observation."""
    return [part.strip(" \t\r\n,;.?!") for part in re.split(
        r"[.!?;\r\n]+|\s+(?:but|however|yet|nevertheless|although|and)\s+",
        text,
        flags=re.IGNORECASE,
    ) if part.strip(" \t\r\n,;.?!")]


def sanitize_process_observation(value) -> str:
    """Blank a teacher-only observation that reads as an integrity conclusion.

    The scoring contract forbids unsupported integrity conclusions in prose, but
    a prompt rule is not an enforcement boundary. Explicit evidence-limit
    clauses are retained only when they match the bounded grammar; accusations
    elsewhere still fail closed.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    for clause in _observation_clauses(text):
        if _EVIDENCE_LIMITATION_CLAUSE.fullmatch(clause):
            continue
        for pattern in _INTEGRITY_CONCLUSION_PATTERNS:
            if pattern.search(clause):
                return OBSERVATION_WITHHELD_NOTICE
    return text
