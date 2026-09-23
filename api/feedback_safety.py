"""Outbound PII safety scan — the gate before anything goes to an external LLM.

Two layers, by design (Hanlon's Razor: assume accidents, make leaks impossible-by-
default and obvious, without nagging):

  HARD (blocks send): the payload carries an identity-bearing field (name, canvas_id,
  sis_id, section, ...), a known real id as a structural value, or a known real id
  (>= _MIN_ID_HARD_BLOCK_LEN chars) appearing as a word-bounded token inside free
  text (a student typing their own id number into an essay body). A correctly
  pseudonymized bundle has none of these; this catches a raw/un-pseudonymized
  payload reaching the send path at all. If hard violations exist, the UI shows
  RED and the send button stays disabled.

  SOFT (warns, review): a known roster name appears inside free-text (prompt/response).
  Kids write names in their writing; we can't auto-decide if it's a real person or a
  character, so we surface it for the teacher rather than blocking.

  Ids shorter than _MIN_ID_HARD_BLOCK_LEN found in free text are NOT hard-blocked
  here (a coincidental short number like a year would false-positive too often);
  they are instead proactively scrubbed to a placeholder before this scan ever
  runs (see feedback_scrub._MIN_ID_SCRUB_LEN, a lower floor of 3).

Pure stdlib; offline-testable. Verdict is GREEN only when there are zero hard violations.
"""

import re

from api import feedback_scrub

# Dict keys that must never appear in an outbound payload. Real Canvas
# identity (name, sortable_name, short_name, sis_id, canvas user id,
# section) must never leave the MCP gate (api/mcp_server/pseudonym.py's
# module docstring makes this promise); every alias Canvas/roster code uses
# for those fields is listed here so the promise is enforced structurally
# rather than by 45+ call sites' good behavior.
_FORBIDDEN_KEYS = {"name", "real_name", "canvas_id", "sis_id", "sisid",
                   "section", "sectionnames", "sectionids", "sectionsisids",
                   "user_id", "userid", "student_id", "sortable_name",
                   "short_name", "login_id", "email", "section_id"}
# Keys exempt from free-text scanning: known-structural, high-churn values
# (identifiers, digests, timestamps, small controlled vocabularies, and the
# vault-assigned fake name itself) with no free-text content to review.
#
# Every OTHER string value in the payload is scanned by default (Layer 2a/2b
# below) -- this is a denylist, not an allowlist, so a new free-text field
# added anywhere in the app is covered automatically instead of silently
# unscanned until someone remembers to list it here. An exempt key still
# gets Layer 1b (exact-value match against a known real id).
_STRUCTURAL_EXEMPT_KEYS = {
    # identifiers: an exact/token coincidental match against a real
    # Canvas/SIS id must not withhold an otherwise-legitimate read.
    "id", "course_id", "item_id",
    # digests/hashes: opaque values with no free-text content to review.
    "proposal_digest", "settings_digest",
    # timestamps: no free-text content; long digit runs carry the same
    # coincidental-id-match risk as an identifier.
    "synced_at", "due_at", "updated_at", "generated",
    # controlled-vocabulary/enum fields: a fixed, small set of known values
    # (e.g. "current"/"stale", "graded"/"submitted", "ready"). Deliberately
    # NOT including "source": the same key name is reused by
    # api/powergrader/context.py's materials[].source for a citation string
    # that isn't guaranteed to be equally narrow, and scanning an enum value
    # costs nothing (no realistic value token-matches a name).
    "state", "workflow_state", "status", "scope", "grain", "method",
    # the vault-assigned fake name itself: scanning it cannot catch a real
    # leak (generated specifically not to collide with a real name) and
    # only adds review noise.
    "pseudonym",
}

# `section` is normally an identity-bearing roster field, but the MCP
# freshness envelope uses that name for a small, fixed projection label. It
# is safe only at the exact metadata boundary and for these declared scopes.
_FRESHNESS_SECTIONS = frozenset({
    "assignment_groups", "assignments", "gradebook_snapshot", "groups",
    "modules", "pages", "roster", "scoring_discovery", "submissions",
})

# A real id found as a `\b`-bounded token inside free text is only a HARD
# block at this length or longer. Below it, a coincidental short number (a
# year, a count, a page number a student typed in an essay) must not
# withhold an otherwise-legitimate read — those are scrub-only (see
# feedback_scrub._MIN_ID_SCRUB_LEN, which still catches them proactively).
_MIN_ID_HARD_BLOCK_LEN = 5


def _walk(obj, path="", key=None):
    """Yield (path, key, value) for every dict key in a nested structure,
    and for every scalar item inside a list (using the enclosing dict key,
    since a bare list item has no key of its own -- a list of plain strings
    would otherwise be entirely invisible to the scan: the list itself is
    the only value ever yielded for it, and it is not a str)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (path, k, v)
            yield from _walk(v, f"{path}.{k}", key=k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                yield from _walk(v, f"{path}[{i}]", key=key)
            else:
                yield (f"{path}[{i}]", key, v)


def scan_payload(payload, vault) -> dict:
    """Scan an outbound payload against the vault.
    Returns {green: bool, hard: [str], soft: [dict]}."""
    names, ids = vault.all_real_identifiers()
    hard, soft = [], []

    # Precompiled once (not per field): known ids long enough to hard-block
    # when found as a `\b`-bounded token inside free text. Paired with the
    # original id string so a hit still yields "real id '<value>' present at
    # ..." — the exact message _sanitize_violation()/_REAL_ID_VIOLATION in
    # api/mcp_server/pseudonym.py expects, so the raw value still gets
    # stripped before reaching an MCP client.
    hard_id_patterns = [
        (real_id, re.compile(rf"\b{re.escape(real_id)}\b"))
        for real_id in ids if len(real_id) >= _MIN_ID_HARD_BLOCK_LEN
    ]

    for path, key, value in _walk(payload):
        kl = (key or "").lower()
        if (kl == "section" and path in {"freshness", ".freshness"}
                and isinstance(value, str) and value in _FRESHNESS_SECTIONS):
            continue
        # Layer 1a: forbidden identity-bearing keys with a non-empty value.
        if kl in _FORBIDDEN_KEYS and value not in (None, "", [], {}):
            hard.append(f"identity field '{key}' present at {path or 'root'}")
            continue
        if not isinstance(value, str) or not value:
            continue
        # Layer 1b: a known real id as an exact structural value -> hard.
        # Unconditional (runs for exempt and scanned fields alike): unlike
        # Layer 2b below it has no length floor, so it is the only layer
        # that still catches a SHORT real id landing as an exact value
        # under an arbitrary key.
        if value in ids:
            hard.append(f"real id '{value}' present at {path}.{key}")
        if kl not in _STRUCTURAL_EXEMPT_KEYS:
            # Layer 2a: known roster name inside free text -> soft flag.
            # Token-level (not full-name-substring): mirrors the scrub
            # engine's own granularity via the same accent-folded matcher,
            # so a single-token scrub miss is caught here too, not only a
            # full-name miss. This is the default for any string value not
            # explicitly exempted above.
            for original in feedback_scrub.find_token_matches(value, names):
                soft.append({"where": f"{path}.{key}", "name": original})
            # Layer 2b: known real id (>= _MIN_ID_HARD_BLOCK_LEN chars) as a
            # word-bounded token inside free text -> hard block. Shorter ids
            # are scrub-only (Layer A in feedback_scrub) and never land here
            # as a hard violation, so a coincidental short number never
            # withholds a legitimate read.
            for real_id, pattern in hard_id_patterns:
                if pattern.search(value):
                    hard.append(f"real id '{real_id}' present at {path}.{key}")

    return {"green": not hard, "hard": hard, "soft": soft}


def assert_scrubbed(payload, vault) -> dict:
    """Post-scrub safety receipt: scan_payload + return it. Intended to run AFTER
    scrubbing has been applied. 'green' must be True and 'soft' should be [];
    a non-empty 'soft' is logged as a scrub miss (bug), not surfaced to the user.
    Does not modify the payload or call save()."""
    verdict = scan_payload(payload, vault)
    # HARD must always be empty after scrubbing — this catches pipeline bugs.
    # SOFT should ideally be empty; non-empty means the scrub engine missed
    # a real identifier, which should be logged.
    return verdict
