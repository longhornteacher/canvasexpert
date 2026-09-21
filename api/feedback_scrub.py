"""Scrub engine for feedback tools — roster-aware real-name removal from student
writing content before it leaves the machine.

Built around a token map from `vault.all_real_identifiers()`, collision-checked
against protected (literary) names. Biases hard to over-correction: a false scrub
is fine; a leaked real name is not.

Pure stdlib; offline-testable.
"""
import re
import unicodedata

# Neutral placeholder for a real Canvas/SIS id found in free text. Ids carry no
# useful signal for feedback quality (unlike a name), so there's no "fake id"
# to substitute in; just remove it. Deliberately word-character-free: the scrub
# engine re-scans its own output, so a placeholder containing letters (e.g.
# "[id]") could be re-clobbered by a later shorter name/nickname rule whose
# token appears inside it. "[#]" has no word chars, so no \b-bounded rule can
# match within it.
ID_PLACEHOLDER = "[#]"
NEUTRAL_NAME_PLACEHOLDER = "⟨student-name-scrubbed⟩"

# Ids shorter than this are never real Canvas/SIS ids in practice; skipping
# them avoids pathological corruption from a stray 1-2 char match (e.g. a
# lone digit that happens to equal a truncated/blank id field).
_MIN_ID_SCRUB_LEN = 3

# Common English words that happen to look like names — collision hints only.
# These are never used to block scrubbing, just surfaced in find_collisions().
COMMON_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "over", "after",
    "all", "also", "am", "are", "as", "at", "be", "been", "being",
    "did", "do", "does", "done", "each", "few", "get", "got", "has",
    "had", "have", "her", "here", "hers", "him", "his", "how", "its",
    "just", "like", "may", "more", "most", "much", "my", "no", "not",
    "now", "once", "only", "other", "our", "out", "own", "per", "said",
    "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "too", "under", "very", "was", "way", "were",
    "what", "when", "where", "which", "while", "who", "will", "with",
    "would", "you", "your",
    # Common name-words that appear in writing
    "will", "rose", "summer", "may", "grace", "hope", "faith", "joy",
    "mark", "jack", "sam", "max", "leo", "ray", "roy", "earl",
    "page", "stone", "river", "lake", "forest", "brooke", "dale",
    "shelby", "sydney", "alexis", "morgan", "casey", "jordan", "taylor",
    "kelly", "skyler", "harley", "madison", "charlie",
    "king", "queen", "angel", "storm", "snow", "sunny", "olive",
}


def _tokenize(name: str) -> list[str]:
    """Split a full name into tokens (first, last, middle)."""
    return name.strip().split()


def _fold(s: str) -> str:
    """NFKD-normalize and drop combining marks, for matching only ('José' ->
    'Jose'). Never applied to output text or to a replacement string — only
    to the pattern source and the text being scanned, so an unaccented typing
    of an accented roster name is still caught."""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _fold_with_map(s: str) -> tuple[str, list[int]]:
    """Fold char-by-char; `index_map[i]` is the source index in `s` of
    `folded[i]`. Per-character (not whole-string) NFKD keeps the offset map
    trivial: each source character maps to zero or more folded characters,
    never interacting with its neighbors."""
    folded_chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(s):
        for fc in unicodedata.normalize("NFKD", ch):
            if not unicodedata.combining(fc):
                folded_chars.append(fc)
                index_map.append(i)
    return "".join(folded_chars), index_map


def find_token_matches(text: str, names: set[str] | list[str]) -> list[str]:
    """Return every name in `names` with at least one token appearing as a
    `\\b`-bounded, accent-folded, case-insensitive match in `text`.

    Token-level, not full-name-level: a scrub miss on a single token (e.g. a
    surname scrubbed by one rule while a given name typed unaccented slips
    past another) is still caught. Shared by `verify_clean` and the outbound
    safety scan (`feedback_safety.scan_payload`) so there is exactly one
    matcher at this granularity, not two that can drift apart.
    """
    folded_text = _fold(text).lower()
    survivors: list[str] = []
    for name in names:
        if not name:
            continue
        for token in name.split():
            if re.search(rf'\b{re.escape(_fold(token))}\b', folded_text, re.IGNORECASE):
                survivors.append(name)
                break
    return survivors


def build_replacement_map(vault_entries: list[dict],
                          protected: set[str]) -> list[tuple]:
    """Return [(compiled_regex, replacement)] for the WHOLE roster, sorted
    longest-token-first.

    Per student, map:
      - full real name -> full one-word pseudonym
      - each real-name token (first, middle, last) -> the SAME pseudonym
      - each nickname -> the SAME pseudonym
      - canvas_id, sis_id (when >= _MIN_ID_SCRUB_LEN chars) -> ID_PLACEHOLDER

    A token that is ALSO in `protected` is STILL scrubbed (roster identity wins
    over a literary match — privacy first). `protected` only shields words that
    are NOT roster tokens.

    The returned rules are ordered by pattern length descending (longest match
    first) so 'Jose Flores' -> 'Pikachu' beats the single-token rules.
    """
    rules: list[tuple[str, str]] = []  # (regex_string, replacement)

    protected_tokens = {
        _fold(str(value)).casefold()
        for value in (protected or set())
        if str(value or "").strip()
        for value in str(value).split()
    }

    for entry in vault_entries:
        real_name = entry.get("real_name", "").strip()
        pseudo = entry.get("pseudonym", "")
        nicknames = entry.get("nicknames", [])

        # Real ids (canvas_id, sis_id) -> neutral placeholder. This runs
        # BEFORE the name/nickname guard below so an entry with no name on
        # file (or no nicknames) still gets its id scrubbed out of free text
        # — a student's Canvas/SIS number typed into an essay body is just as
        # much a real identifier as their name.
        for id_field in ("canvas_id", "sis_id"):
            raw_id = str(entry.get(id_field, "") or "").strip()
            if len(raw_id) >= _MIN_ID_SCRUB_LEN:
                rules.append((re.escape(raw_id), ID_PLACEHOLDER))

        if not real_name and not nicknames:
            continue
        if not pseudo:
            # No pseudonym on file to substitute in -- nothing else can be
            # scrubbed safely for this entry (see verify_clean).
            continue

        # Full real name -> full pseudonym (e.g. "Jose Flores" -> "Pikachu").
        # Folded so an unaccented typing of an accented roster name ("Jose
        # Flores" for vault "José Flores") still matches — matching only;
        # the replacement text is unaffected.
        collision = any(
            _fold(token).casefold() in protected_tokens
            for token in _tokenize(real_name)
        )
        name_replacement = NEUTRAL_NAME_PLACEHOLDER if collision else pseudo
        rules.append((re.escape(_fold(real_name)), name_replacement))

        # Every individual token (first, middle, last) resolves to the SAME
        # complete pseudonym -- there is no separate first/last component to
        # map to under the one-word contract.
        for token in _tokenize(real_name):
            if token:
                replacement = (
                    NEUTRAL_NAME_PLACEHOLDER
                    if _fold(token).casefold() in protected_tokens else pseudo
                )
                rules.append((re.escape(_fold(token)), replacement))

        # Nicknames/aliases -> full pseudonym so every identity alias resolves
        # consistently to the student's existing pseudonym. Folded like every
        # other name token (2.2: nicknames carry the same accent-folding
        # guarantee as real_name).
        for nn in nicknames:
            if nn.strip():
                nickname = nn.strip()
                replacement = (
                    NEUTRAL_NAME_PLACEHOLDER
                    if _fold(nickname).casefold() in protected_tokens else pseudo
                )
                rules.append((re.escape(_fold(nickname)), replacement))

    # Sort by pattern length descending (longest first) so full-name rules beat
    # single-token rules
    rules.sort(key=lambda r: len(r[0]), reverse=True)

    # Compile regexes with word boundaries, case-insensitive
    compiled = []
    for pattern_str, replacement in rules:
        try:
            compiled.append((
                re.compile(rf'\b{pattern_str}\b', re.IGNORECASE),
                replacement,
            ))
        except re.error:
            continue

    return compiled


def _collision_spans(text: str, protected: set[str]) -> list[tuple[int, int]]:
    """Find roster/protected-token spans in original text coordinates."""
    folded, index_map = _fold_with_map(text)
    spans = []
    tokens = {
        _fold(str(value)).casefold()
        for value in (protected or set())
        if str(value or "").strip()
        for value in str(value).split()
    }
    for token in tokens:
        for match in re.finditer(rf"\b{re.escape(token)}\b", folded, re.IGNORECASE):
            start = index_map[match.start()]
            end = index_map[match.end() - 1] + 1
            spans.append((start, end))
    return sorted(set(spans))


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    patterns = (r'"[^"\r\n]*"', r"“[^”\r\n]*”", r"'[^'\r\n]*'")
    spans = []
    for pattern in patterns:
        spans.extend((match.start(), match.end()) for match in re.finditer(pattern, text))
    return sorted(spans)


def scrub_text_with_protected_spans(
    text: str, replacement_map: list[tuple], protected: set[str], *,
    quoted_only: bool = False,
) -> str:
    """Scrub roster identifiers while preserving colliding literary tokens.

    Source/assignment text may preserve every exact protected token. Student
    responses preserve one only when it occurs inside an explicit quote; all
    other collisions use the neutral marker instead of a pseudonym.
    """
    text = str(text or "")
    collisions = _collision_spans(text, protected)
    if quoted_only:
        quotes = _quoted_spans(text)
        collisions = [
            span for span in collisions
            if any(start <= span[0] and span[1] <= end for start, end in quotes)
        ]
    if not collisions:
        return scrub_text(text, replacement_map)
    output = []
    cursor = 0
    for start, end in collisions:
        if start < cursor:
            continue
        output.append(scrub_text(text[cursor:start], replacement_map))
        output.append(text[start:end])
        cursor = end
    output.append(scrub_text(text[cursor:], replacement_map))
    return "".join(output)


def scrub_text(text: str, replacement_map: list[tuple]) -> str:
    """Apply the replacement map. Word-boundary, case-insensitive;
    possessives fall out naturally (\\bJose\\b matches in 'Jose's' -> 'Pikachu's').
    Longest patterns first so 'Jose Flores'->'Pikachu' beats single-token rules.

    Patterns are compiled from accent-folded name tokens (see `_fold`), so
    matching runs against a folded copy of the text-so-far; each match's span
    is then mapped back to the original (unfolded) coordinates before
    splicing in the replacement. Everything outside a matched span is
    carried over byte-for-byte — folding is for matching only, never for
    output, so an unrelated accented word elsewhere in the text is never
    altered.
    """
    result = text
    for regex, replacement in replacement_map:
        folded, index_map = _fold_with_map(result)
        matches = list(regex.finditer(folded))
        if not matches:
            continue
        pieces: list[str] = []
        last_end = 0
        for m in matches:
            start = index_map[m.start()]
            end = index_map[m.end() - 1] + 1 if m.end() > m.start() else start
            if start < last_end:
                continue  # defensive: overlapping match, keep the earlier one
            pieces.append(result[last_end:start])
            pieces.append(replacement)
            last_end = end
        pieces.append(result[last_end:])
        result = "".join(pieces)
    return result


def find_collisions(vault_entries: list[dict],
                    protected: set[str]) -> dict:
    """Return a dict of collision categories for the UI to surface:
    {
      'literary': [...real names that match a protected literary name...],
      'dup_first': [...first names shared by 2+ students...],
      'common_word': [...nickname/name tokens <=2 chars or in COMMON_WORDS...],
    }
    None of these block anything — they are informational only.
    """
    literary: list[str] = []
    dup_first: list[str] = []
    common_word: list[str] = []

    first_names: dict[str, list[str]] = {}
    protected_lower = {p.lower() for p in protected}

    for entry in vault_entries:
        real_name = entry.get("real_name", "").strip()
        tokens = _tokenize(real_name)
        nicknames = entry.get("nicknames", [])

        # Literary collision: real name (or any token) matches a protected name
        if real_name and real_name.lower() in protected_lower:
            literary.append(real_name)
        for token in tokens:
            if token.lower() in protected_lower and real_name not in literary:
                literary.append(f"{real_name} ('{token}')")
                break

        # Dup first names
        if tokens:
            fn = tokens[0].lower()
            first_names.setdefault(fn, []).append(real_name)

        # Common word tokens
        all_tokens = tokens + nicknames
        for t in all_tokens:
            t_lower = t.lower().strip(".'\"")
            if len(t_lower) <= 2 and t_lower not in ("i", "a"):
                common_word.append(f"'{t}' (from '{real_name}')")
            elif t_lower in COMMON_WORDS:
                common_word.append(f"'{t}' (from '{real_name}')")

    # Find duplicate first names
    for fn, names in first_names.items():
        if len(names) >= 2:
            dup_first.append(f"{', '.join(names)}")

    return {
        "literary": sorted(set(literary)),
        "dup_first": sorted(set(dup_first)),
        "common_word": sorted(set(common_word)),
    }


def protected_proper_nouns(text: str) -> set[str]:
    """Extract a conservative source-derived proper-noun allowlist."""
    result = set()
    for match in re.finditer(r"\b[A-Z][A-Za-z][A-Za-z'’-]*\b", str(text or "")):
        token = match.group(0)
        if token.casefold() not in COMMON_WORDS:
            result.add(token.casefold())
    return result


def verify_clean(text: str, vault) -> list[str]:
    """Re-scan scrubbed text for any surviving real identifier
    (vault.all_real_identifiers() names). Returns survivors.
    With over-correction this should be empty; a non-empty result is a BUG
    to log, never a user task."""
    names, _ids = vault.all_real_identifiers()
    return find_token_matches(text, names)
