"""QuizForge file -> live Canvas New Quiz.

Pipeline:
  1. Read the .txt, extract JSON from the <QUIZFORGE_JSON> envelope.
  2. Merge rationales onto their items (item["_rationale"]).
  3. Inline STIMULUS HTML into each attached item (by explicit stimulus_id),
     then drop STIMULUS / STIMULUS_END.
  4. Create the quiz (unpublished), push each item via transform.build_item.

Usage:
  py qf_pusher.py "qf_materials/qf quiz examples/all_types_sampler.txt"
  py qf_pusher.py "<file>" --dry-run     # print payloads, no live calls
"""
import json
import os
import re
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api import codefmt, teks, transform

ENVELOPE = re.compile(r"<QUIZFORGE_JSON>(.*?)</QUIZFORGE_JSON>", re.DOTALL)
STATE_PATH = ".experiment_state.json"

# QuizForge defaults applied to every pushed quiz:
#  - shuffle answers (not questions, to keep stimulus-attached items together)
QUIZ_SETTINGS = {
    "shuffle_answers": True,
    "shuffle_questions": False,
}

TOTAL_POINTS = 100  # QuizForge requires a 100-point total

SETTING_KEYS = (
    "shuffle_answers", "shuffle_questions", "access_code",
    "allow_multiple_attempts", "score_to_keep", "allowed_attempts",
    "build_on_last_attempt", "attempt_cooldown", "has_time_limit",
    "time_limit_minutes", "calculator_type", "one_at_a_time",
    "allow_backtracking", "hide_results", "due_at", "unlock_at", "lock_at",
    "assignment_group_id", "assignment_group_name", "post_to_sis", "published",
    "module_id", "module_name",
)


def distribute_points(items, total=TOTAL_POINTS):
    """Per-item points. Respect explicit QF `points` if any item sets them;
    otherwise split `total` as evenly as possible (remainder on the last item)."""
    explicit = [it.get("points") for it in items]
    if any(p is not None for p in explicit):
        return [float(p) if p is not None else 0.0 for p in explicit]
    n = len(items)
    if n == 0:
        return []
    base = round(total / n, 2)
    pts = [base] * n
    pts[-1] = round(total - base * (n - 1), 2)  # absorb rounding drift
    return pts


def load_qf(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    m = ENVELOPE.search(raw)
    payload = m.group(1).strip() if m else raw  # tolerate bare JSON too
    return json.loads(payload)


def prepare_items(data):
    """Return scored items in order, with rationales merged and stimulus inlined."""
    rationales = {r["item_id"]: r for r in data.get("rationales", [])}
    stim_html = {it.get("id"): it.get("prompt", "")
                 for it in data["items"] if it["type"] == "STIMULUS"}

    prepared = []
    for it in data["items"]:
        if it["type"] in ("STIMULUS", "STIMULUS_END"):
            continue
        it = dict(it)
        if it.get("type") == "FITB":
            tokens = re.findall(r"\[blank\d*\]", str(it.get("prompt") or ""))
            accept = it.get("accept")
            if len(tokens) <= 1 and isinstance(accept, list) and len(accept) == 1 and isinstance(accept[0], list):
                it["accept"] = accept[0]
        # inline stimulus by EXPLICIT id only (never implicit, to avoid
        # gluing a code block onto an unrelated generic question)
        sid = it.get("stimulus_id")
        if sid and sid in stim_html and it.get("prompt"):
            it["prompt"] = stim_html[sid] + it["prompt"]
        # VSCode-style highlighting for any code blocks (incl. inlined stimulus)
        if it.get("prompt"):
            it["prompt"] = codefmt.highlight_html_blocks(it["prompt"])
        # visible TEKS label on the question
        codes = teks.item_teks(it)
        if codes and it.get("prompt"):
            it["prompt"] = it["prompt"] + teks.label_html(codes)
        if it.get("id") in rationales:
            it["_rationale"] = rationales[it["id"]]
        prepared.append(it)
    return prepared


def _normalized_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("QF push settings must be an object")
    return {key: settings[key] for key in SETTING_KEYS if key in settings}


def _effective_quiz_settings(push_settings):
    quiz_settings = dict(QUIZ_SETTINGS)
    if "shuffle_answers" in push_settings:
        quiz_settings["shuffle_answers"] = bool(push_settings["shuffle_answers"])
    if "shuffle_questions" in push_settings:
        quiz_settings["shuffle_questions"] = bool(push_settings["shuffle_questions"])
    if push_settings.get("access_code"):
        quiz_settings["require_student_access_code"] = True
        quiz_settings["student_access_code"] = str(push_settings["access_code"]).strip()
    if push_settings.get("allow_multiple_attempts"):
        score = push_settings.get("score_to_keep", "highest")
        raw_attempts = push_settings.get("allowed_attempts", -1)
        attempt_count = -1 if str(raw_attempts) in ("-1", "unlimited") else int(raw_attempts)
        attempts = {
            "multiple_attempts_enabled": True,
            "score_to_keep": score if score in ("highest", "latest", "average", "first") else "highest",
            "build_on_last_attempt": bool(push_settings.get("build_on_last_attempt", False)),
        }
        if attempt_count > 0:
            attempts.update({"attempt_limit": True, "max_attempts": attempt_count})
        cooldown = int(push_settings.get("attempt_cooldown", 0) or 0)
        if cooldown > 0:
            attempts.update({"cooling_period": True, "cooling_period_seconds": cooldown * 60})
        quiz_settings["multiple_attempts"] = attempts
    if push_settings.get("has_time_limit"):
        minutes = int(push_settings.get("time_limit_minutes", 0))
        if minutes > 0:
            quiz_settings.update({
                "has_time_limit": True,
                "session_time_limit_in_seconds": minutes * 60,
            })
    if push_settings.get("calculator_type") in ("basic", "scientific"):
        quiz_settings["calculator_type"] = push_settings["calculator_type"]
    if push_settings.get("one_at_a_time"):
        quiz_settings.update({
            "one_at_a_time_type": "question",
            "allow_backtracking": bool(push_settings.get("allow_backtracking", True)),
        })
    if push_settings.get("hide_results"):
        quiz_settings["result_view_settings"] = {
            "result_view_restricted": True,
            "display_points_awarded": False,
            "display_points_possible": False,
            "display_items": False,
        }
    else:
        quiz_settings["result_view_settings"] = {
            "result_view_restricted": True,
            "display_points_awarded": True,
            "display_points_possible": True,
            "display_items": True,
            "display_item_response": True,
            "display_item_response_correctness": True,
            "display_item_response_qualifier": "after_last_attempt",
            "display_item_correct_answer": True,
            "display_item_feedback": True,
        }
    return quiz_settings


def build_push_plan(path, settings=None):
    """Build the deterministic, JSON-safe no-network QuizForge push plan."""
    push_settings = _normalized_settings(settings or {})
    data = load_qf(path)
    title = data.get("title", os.path.basename(path))
    prepared = prepare_items(data)
    points = distribute_points(prepared)
    items = []
    for index, (qf_item, point) in enumerate(zip(prepared, points), 1):
        payload = transform.build_item(qf_item, index)
        payload["item"]["points_possible"] = point
        items.append({
            "index": index,
            "source_item_id": qf_item.get("id"),
            "source_type": qf_item.get("type"),
            "payload": payload,
        })
    quiz_points = round(sum(points), 2) if points else 1
    assignment_keys = (
        "due_at", "unlock_at", "lock_at", "assignment_group_id",
        "assignment_group_name", "post_to_sis", "published",
        "allow_multiple_attempts", "allowed_attempts",
    )
    return {
        "version": 1,
        "title": title,
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        "source_path": str(path),
        "quiz_payload": {"quiz": {
            "title": title,
            "points_possible": quiz_points,
            "grading_type": "points",
            "quiz_settings": _effective_quiz_settings(push_settings),
        }},
        "items": items,
        "assignment_settings": {
            key: push_settings[key] for key in assignment_keys if key in push_settings
        },
        "module": {
            key: push_settings[key] for key in ("module_id", "module_name")
            if push_settings.get(key) not in (None, "")
        },
    }


def find_assignment_group_id(course_id, name):
    """Resolve a grading-category (assignment group) NAME to its id in THIS course.

    Lets one category choice target many courses where the underlying ids differ —
    the same by-name philosophy used for tier groups in multi-course pushes.
    """
    from api import canvas
    st, groups = canvas.get(
        canvas.core(f"/courses/{course_id}/assignment_groups"),
        params={"per_page": 100},
    )
    if st == 200 and isinstance(groups, list):
        for g in groups:
            if str(g.get("name", "")).strip().lower() == name.strip().lower():
                return g.get("id")
    return None


def patch_assignment(quiz_id, course_id, settings):
    """Patch the underlying Core REST assignment with dates / grading category.

    New Quiz assignment_id == quiz_id. Date strings must be ISO 8601 with
    timezone offset (Canvas rejects naive datetimes). Non-fatal on failure —
    the quiz already exists; we just log the warning.
    """
    from api import canvas
    payload = {}
    for key in ("due_at", "unlock_at", "lock_at"):
        val = settings.get(key)
        if val:
            payload[key] = val
    # Grading category: explicit id wins; otherwise resolve a NAME per course.
    ag_id = settings.get("assignment_group_id")
    if not ag_id and settings.get("assignment_group_name"):
        ag_id = find_assignment_group_id(course_id, settings["assignment_group_name"])
        if ag_id:
            print(f"  [settings] category '{settings['assignment_group_name']}' -> #{ag_id}")
        else:
            print(f"  [settings] category '{settings['assignment_group_name']}' "
                  f"not in this course — leaving default")
    if ag_id:
        payload["assignment_group_id"] = ag_id
    if "post_to_sis" in settings:
        payload["post_to_sis"] = bool(settings["post_to_sis"])
    if settings.get("published"):
        payload["published"] = True      # returns 400 for NQ — handled below
    # attempt count lives on the REST assignment, not quiz_settings
    if settings.get("allow_multiple_attempts"):
        raw = settings.get("allowed_attempts", -1)
        payload["allowed_attempts"] = -1 if str(raw) in ("-1", "unlimited") else int(raw)
    if not payload:
        return
    print(f"\n  [settings] patching assignment: {list(payload.keys())}")
    st, resp = canvas.put(
        canvas.core(f"/courses/{course_id}/assignments/{quiz_id}"),
        json={"assignment": payload},
    )
    if st in (200, 201):
        print("  [settings] OK")
    else:
        # Publishing NQ via API is a known Canvas limitation — non-fatal
        print(f"  [settings] HTTP {st} (non-fatal — some fields may need Canvas UI)")


def create_module(course_id, name):
    """Create a new Canvas module and return its id, or None on failure."""
    from api import canvas
    print(f"\n  [module] creating new module '{name}'…")
    st, resp = canvas.post(
        canvas.core(f"/courses/{course_id}/modules"),
        json={"module": {"name": name, "position": 1}},
    )
    if st in (200, 201):
        mod_id = resp.get("id")
        print(f"  [module] created id={mod_id}")
        return mod_id
    print(f"  [module] create failed HTTP {st} (non-fatal)")
    return None


def find_or_create_module(course_id, name):
    """Return the id of the module named `name`, creating it only if absent.

    Find-or-create keeps multi-course pushes from spawning duplicate modules
    when the same module name already exists in some of the target courses.
    """
    from api import canvas
    st, mods = canvas.get(
        canvas.core(f"/courses/{course_id}/modules"),
        params={"per_page": 100, "search_term": name},
    )
    if st == 200 and isinstance(mods, list):
        for m in mods:
            if str(m.get("name", "")).strip().lower() == name.strip().lower():
                print(f"  [module] using existing '{name}' id={m.get('id')}")
                return m.get("id")
    return create_module(course_id, name)


def add_to_module(quiz_id, course_id, module_id, title):
    """Add the quiz (as an Assignment item) to a Canvas Module.

    New Quizzes are assignments under the hood, so type='Assignment' and
    content_id=quiz_id is the correct payload. Non-fatal on failure.
    """
    from api import canvas
    print(f"\n  [module] adding '{title}' to module #{module_id}…")
    st, resp = canvas.post(
        canvas.core(f"/courses/{course_id}/modules/{module_id}/items"),
        json={"module_item": {"title": title, "type": "Assignment",
                               "content_id": quiz_id}},
    )
    if st in (200, 201):
        print(f"  [module] OK — item id={resp.get('id')}")
    else:
        print(f"  [module] HTTP {st} (non-fatal)")


# Canvas occasionally returns a transient gateway/throttle error on the
# New Quizzes item endpoint. Without a retry, a single 502 silently drops a
# question — the quiz publishes incomplete and points (already distributed for
# the full item count) no longer sum correctly. Retrying these codes turns
# "silently incomplete quiz" into "reliably complete quiz".
TRANSIENT_CODES = {429, 500, 502, 503, 504}


def _post_item_with_retry(canvas, course_id, quiz_id, payload, max_attempts=4):
    """POST one quiz item, retrying transient failures with exponential backoff.

    Returns (status, data, attempts). Permanent errors (e.g. a 4xx from a bad
    payload) are not retried — they fail fast on the first try.
    """
    import time
    delay = 1.0
    st, data = None, None
    for attempt in range(1, max_attempts + 1):
        st, data = canvas.post(
            canvas.quiz(f"/courses/{course_id}/quizzes/{quiz_id}/items"),
            json=payload)
        ok = st in (200, 201) and isinstance(data, dict) and "id" in data
        if ok or st not in TRANSIENT_CODES or attempt == max_attempts:
            return st, data, attempt
        print(f"     transient HTTP {st} — retry {attempt}/{max_attempts - 1} "
              f"in {delay:.0f}s")
        time.sleep(delay)
        delay *= 2
    return st, data, max_attempts


def push_file(path, dry_run=False):
    from api import canvas
    from api.canvas import COURSE_ID

    # Optional assignment settings injected by the web UI via env var.
    # Parsed here so they're visible in --dry-run output too.
    _settings_raw = os.environ.get("QF_PUSH_SETTINGS", "").strip()
    push_settings = json.loads(_settings_raw) if _settings_raw else {}

    data = load_qf(path)
    title = data.get("title", os.path.basename(path))
    items = prepare_items(data)
    plan = build_push_plan(path, push_settings)
    print(f"\n=== {os.path.basename(path)} -> '{title}'  ({len(items)} items) ===")
    teks.coverage_report(items)  # always: pure-local TEKS tracking

    payloads = [(item["source_type"], item["payload"]) for item in plan["items"]]
    pts = [item["payload"]["item"]["points_possible"] for item in plan["items"]]
    quiz_points = plan["quiz_payload"]["quiz"]["points_possible"]
    print(f"  points: {quiz_points} total across {len(pts)} items "
          f"({pts[0] if pts else 0} each)")

    if dry_run:
        for t, p in payloads:
            print(f"\n--- {t} ({p['item']['points_possible']} pts) ---")
            print(json.dumps(p, indent=2)[:1400])
        if push_settings:
            print(f"\n[dry-run] would apply settings: {json.dumps(push_settings, indent=2)}")
        return None

    status, q = canvas.post(
        canvas.quiz(f"/courses/{COURSE_ID}/quizzes"),
        json=plan["quiz_payload"],
    )
    if not q or "id" not in q:
        print("  !! quiz creation failed")
        return None
    quiz_id = q["id"]

    results = []
    for t, p in payloads:
        st, data_item, attempts = _post_item_with_retry(canvas, COURSE_ID, quiz_id, p)
        ok = st in (200, 201) and isinstance(data_item, dict) and "id" in data_item
        results.append((t, st, ok, attempts))
        time.sleep(0.4)

    print("\n  item results:")
    for t, st, ok, attempts in results:
        note = "" if attempts == 1 else f"  (after {attempts} tries)"
        print(f"    {'OK ' if ok else 'XX '} {t:14} HTTP {st}{note}")

    # Apply optional assignment settings (dates, grading category, publish, SIS)
    if push_settings:
        patch_assignment(quiz_id, COURSE_ID, push_settings)

    # Add to module if requested (existing module or create a new one)
    module_id = push_settings.get("module_id")
    if not module_id and push_settings.get("module_name"):
        module_id = find_or_create_module(COURSE_ID, push_settings["module_name"])
    if module_id:
        add_to_module(quiz_id, COURSE_ID, module_id, title)

    # record for cleanup; NQ assignment_id == quiz_id
    all_ok = all(ok for _, _, ok, _ in results)
    canvas_url = f"{canvas.BASE}/courses/{COURSE_ID}/assignments/{quiz_id}"
    rec = {"quiz_id": quiz_id, "assignment_id": quiz_id, "title": title,
           "canvas_url": canvas_url}
    _record(rec)
    print(f"\n  quiz id={quiz_id}  (assignment_id={quiz_id})")
    # Emit a machine-parseable success line the UI can extract
    print(f"  CANVAS_URL: {canvas_url}")
    if all_ok:
        print(f"  PUSH_OK: {title}")
    else:
        failed = sum(1 for _, _, ok, _ in results if not ok)
        print(f"  PUSH_WARN: {failed}/{len(results)} items failed — review in Canvas")
    return rec


def _record(rec):
    state = {"quizzes": [], "overrides": [], "files": [], "capability_matrix": {}}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
    state.setdefault("quizzes", []).append(rec)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = sys.argv[1]
    if "--plan-json" in sys.argv[2:]:
        try:
            raw = os.environ.get("QF_PUSH_SETTINGS", "").strip()
            settings = json.loads(raw) if raw else {}
            print(json.dumps(build_push_plan(path, settings), ensure_ascii=False,
                             separators=(",", ":")))
        except Exception as exc:
            print(f"plan error: {exc}", file=sys.stderr)
            raise SystemExit(2)
        return
    push_file(path, dry_run="--dry-run" in sys.argv[2:])


if __name__ == "__main__":
    main()
