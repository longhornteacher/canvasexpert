"""Feedback tools configuration — AI Authoring persona library and feedback patterns (synced).

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
import json
import os

from .. import workspace
from . import _io as _io_mod

AI_TA_PERSONA_DEFAULT = {
    "name": "",
    "personality": "",
    "signoff_policy": "none",
    "signoff_text": "",
}

DEFAULT_AI_DISCLOSURE_SIGNOFF = "Drafted by {name} (AI), reviewed by your teacher."

BUILTIN_PERSONAS = [
    {"id": "sage", "name": "Sage",
     "personality": "A calm, thoughtful mentor. Warm and patient; names what's working before what to fix; precise without being cold.",
     "signoff_policy": "none", "signoff_text": ""},
    {"id": "pip", "name": "Pip",
     "personality": "Upbeat and energetic; plain language, short punchy sentences. Built for reluctant readers — high warmth, low jargon.",
     "signoff_policy": "none", "signoff_text": ""},
    {"id": "coach_vale", "name": "Coach Vale",
     "personality": "Direct and action-oriented; frames feedback as 'your next rep.' Concrete, motivating, no fluff.",
     "signoff_policy": "none", "signoff_text": ""},
]

BUILTIN_PERSONAS_BY_ID = {p["id"]: p for p in BUILTIN_PERSONAS}

FEEDBACK_PATTERNS_DEFAULT = [
    {"id": "basic", "name": "Glows & Grows (Basic)",
     "score_from_rubric": True,
     "glows": {"min": 2, "max": 3}, "grows": {"min": 1, "max": 2},
     "strategy_sentences": {"min": 2, "max": 3}, "sign_with_persona": True},
]


def _persona_folder() -> str | None:
    ai_ta_dir = workspace.library_folder("AI Authoring")
    if not ai_ta_dir:
        return None
    return os.path.join(ai_ta_dir, "Personas")


def get_persona_folder() -> str | None:
    folder = _persona_folder()
    if folder:
        os.makedirs(folder, exist_ok=True)
        _seed_persona_folder_once(folder)
    return folder


def _safe_persona_filename(persona: dict) -> str:
    import re
    stem = str(persona.get("name") or persona.get("id") or "Persona").strip()
    stem = re.sub(r"[^\w\- ]+", "", stem)
    stem = re.sub(r"\s+", " ", stem).strip() or "Persona"
    return f"{stem}.json"


def _seed_persona_folder_once(folder: str):
    marker = os.path.join(folder, ".personas_seeded")
    if os.path.exists(marker):
        return
    has_personas = any(name.lower().endswith(".json") for name in os.listdir(folder)) if os.path.isdir(folder) else False
    if not has_personas:
        for p in BUILTIN_PERSONAS:
            path = os.path.join(folder, _safe_persona_filename(p))
            if os.path.exists(path):
                continue
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"builtin": True, **p}, f, indent=2)
    with open(marker, "w", encoding="utf-8") as f:
        f.write("Canvas Expert seeded the starter personas here once.\n")


def _list_file_personas() -> list[dict]:
    folder = get_persona_folder()
    if not folder or not os.path.isdir(folder):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        persona_id = str(data.get("id") or os.path.splitext(name)[0]).strip()
        display_name = str(data.get("name") or persona_id).strip()
        personality = str(data.get("personality") or "").strip()
        builtin_defaults = BUILTIN_PERSONAS_BY_ID.get(persona_id) if data.get("builtin") else None
        signoff_policy = str(data.get("signoff_policy") if data.get("signoff_policy") is not None else (builtin_defaults or {}).get("signoff_policy", "none")).strip()
        signoff_text = str(data.get("signoff_text") if data.get("signoff_text") is not None else (builtin_defaults or {}).get("signoff_text", "")).strip()
        if not persona_id or not display_name:
            continue
        if persona_id in seen:
            continue
        seen.add(persona_id)
        out.append({"id": persona_id, "name": display_name, "personality": personality,
                    "signoff_policy": signoff_policy, "signoff_text": signoff_text,
                    "builtin": bool(data.get("builtin", False)), "source": "file", "path": path})
    return out


def list_personas() -> list[dict]:
    folder_enabled = bool(_persona_folder())
    file_personas = _list_file_personas()
    builtins = file_personas if folder_enabled else [{"builtin": True, **p} for p in BUILTIN_PERSONAS]
    ids = {p["id"] for p in builtins}
    custom_raw = _io_mod._synced_state().get("custom_personas", [])
    custom = [{"id": c.get("id", ""), "name": c.get("name", ""),
               "personality": c.get("personality", ""),
               "signoff_policy": c.get("signoff_policy", "none"),
               "signoff_text": c.get("signoff_text", ""),
               "builtin": False, "source": "settings"}
              for c in custom_raw if c.get("name") and c.get("id") not in ids]
    return builtins + custom


def get_persona(persona_id: str = "") -> dict:
    for p in list_personas():
        if p["id"] == (persona_id or "sage"):
            return {"name": p["name"], "personality": p["personality"],
                    "signoff_policy": p.get("signoff_policy", "none"),
                    "signoff_text": p.get("signoff_text", "")}
    return dict(AI_TA_PERSONA_DEFAULT)


def save_custom_persona(persona_id: str, name: str, personality: str):
    if any(p["id"] == persona_id for p in BUILTIN_PERSONAS):
        return
    def mutate(state):
        custom = state.setdefault("custom_personas", [])
        value = {"id": persona_id, "name": name.strip(), "personality": personality.strip(),
                 "signoff_policy": "none", "signoff_text": ""}
        for i, persona in enumerate(custom):
            if persona.get("id") == persona_id:
                custom[i] = value
                return
        custom.append(value)

    _io_mod._modify_synced(mutate)


def remove_custom_persona(persona_id: str):
    if any(p["id"] == persona_id for p in BUILTIN_PERSONAS):
        return
    _io_mod._modify_synced(
        lambda state: state.__setitem__(
            "custom_personas",
            [p for p in state.get("custom_personas", []) if p.get("id") != persona_id],
        ) or state
    )


def get_ai_ta_persona() -> dict:
    saved = _io_mod._synced_state().get("ai_ta_persona", {})
    return {"name": str(saved.get("name", "")).strip(),
            "personality": str(saved.get("personality", "")).strip()}


def set_ai_ta_persona(name: str, personality: str = ""):
    _io_mod._modify_synced(
        lambda state: state.__setitem__(
            "ai_ta_persona",
            {"name": (name or "").strip(), "personality": (personality or "").strip()},
        ) or state
    )


def list_feedback_patterns() -> list[dict]:
    return list(_io_mod._synced_state().get("feedback_patterns", FEEDBACK_PATTERNS_DEFAULT))


def get_feedback_pattern(pattern_id: str = "") -> dict:
    """Return the named feedback pattern, defaulting to Glows & Grows (`basic`)."""
    wanted = str(pattern_id or "").strip() or "basic"
    catalog = list_feedback_patterns() or list(FEEDBACK_PATTERNS_DEFAULT)
    for pattern in catalog:
        if str(pattern.get("id") or "") == wanted:
            return dict(pattern)
    for pattern in catalog:
        if str(pattern.get("id") or "") == "basic":
            return dict(pattern)
    return dict(FEEDBACK_PATTERNS_DEFAULT[0])


def set_feedback_patterns(patterns: list[dict]):
    _io_mod._modify_synced(
        lambda state: state.__setitem__("feedback_patterns", patterns) or state
    )
