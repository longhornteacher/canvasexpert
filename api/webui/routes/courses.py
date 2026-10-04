"""Settings course listing and live group helpers used by roster services."""
import requests


from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.platform_services.canvas_client import canvas_get, canvas_headers

router = APIRouter(tags=["courses"])




@router.get("/api/courses")
def list_all_courses():
    """All courses the saved token can see (used by both settings and dashboard)."""
    data, err = canvas_get("/api/v1/courses", {"per_page": 100, "state[]": "available"})
    if err:
        return JSONResponse({"ok": False, "error": err})
    courses = [{"id": str(c["id"]), "name": c.get("name", f"course {c['id']}")}
               for c in data if "id" in c]
    courses.sort(key=lambda c: c["name"].lower())
    return JSONResponse({"ok": True, "courses": courses})


def fetch_group_category_groups(course_id: str, category_id: str) -> tuple[list[dict] | None, str | None]:
    """Fetch one group category's groups + memberships live from Canvas.

    Factored out of ``load_group_categories``'s whole-course loop so both the
    full course refresh and a single-category targeted reconciliation share
    one Canvas-shape-normalization path (id/name/student_ids/memberships per
    group). Returns ``(groups_out, None)`` on success, ``(None, err)`` on any
    non-200/transport failure — never raises.
    """
    hdrs, base = canvas_headers()
    if not hdrs:
        return None, "No token saved."

    def get(path, params=None):
        try:
            r = requests.get(f"{base}{path}", headers=hdrs,
                             params=params or {}, timeout=20)
            return (r.status_code, r.json() if r.status_code == 200 else None)
        except Exception:
            return (0, None)

    def memberships(group_id):
        """Return list of membership dicts for a group."""
        st, members = get(f"/api/v1/groups/{group_id}/memberships", {"per_page": 200})
        return members or []

    st, groups_raw = get(f"/api/v1/group_categories/{category_id}/groups", {"per_page": 100})
    if st != 200:
        return None, f"Canvas returned {st} for group_categories/{category_id}/groups."

    groups_out = []
    for grp in (groups_raw or []):
        grp_id = str(grp["id"])
        mems = memberships(grp_id)
        groups_out.append({
            "id":          grp_id,
            "name":        grp["name"],
            "student_ids": [m["user_id"] for m in mems],
            "memberships": mems,
        })
    return groups_out, None


def load_group_categories(course_id: str) -> tuple[list[dict], str | None, str]:
    """Return (categories, error, message) for a course's group sets.

    Canvas note (confirmed live 2026-06): teacher PATs may get 403 on every
    /group_categories endpoint (district permission), while
    /courses/:id/groups still returns the same groups with their
    group_category_id. So: try group_categories for proper set names, fall
    back to bucketing /courses/:id/groups by category id.

    V3: Also returns membership IDs for Canvas write operations.
    """
    hdrs, base = canvas_headers()
    if not hdrs:
        return [], "No token saved.", ""

    def get(path, params=None):
        try:
            r = requests.get(f"{base}{path}", headers=hdrs,
                             params=params or {}, timeout=20)
            return (r.status_code, r.json() if r.status_code == 200 else None)
        except Exception as e:
            return (0, None)

    def memberships(group_id):
        """Return list of membership dicts for a group."""
        st, members = get(f"/api/v1/groups/{group_id}/memberships", {"per_page": 200})
        return members or []

    # Preferred path: real group sets with names.
    st, cats = get(f"/api/v1/courses/{course_id}/group_categories", {"per_page": 50})
    if st == 200 and cats:
        result = []
        for cat in cats:
            # Status intentionally ignored here (unchanged from prior
            # behavior): a per-category fetch failure degrades to an empty
            # groups list for that one category rather than failing the
            # whole-course load.
            groups_out, _err = fetch_group_category_groups(course_id, cat["id"])
            result.append({"category_id":   str(cat["id"]),
                           "category_name": cat["name"],
                           "groups":        groups_out or []})
        return result, None, ""

    # Fallback: course groups bucketed by category id (category names 403-gated).
    st2, groups_raw = get(f"/api/v1/courses/{course_id}/groups", {"per_page": 100})
    if st2 != 200:
        return [], (f"Canvas returned {st or st2} for course groups "
                    f"(group_categories: {st}; groups: {st2})."), ""

    if not groups_raw:
        return [], None, "No group sets found in this course."

    buckets = {}
    for grp in groups_raw:
        buckets.setdefault(str(grp.get("group_category_id") or "0"), []).append(grp)
    result = []
    for i, (cat_id, grps) in enumerate(sorted(buckets.items()), start=1):
        result.append({
            "category_id":   cat_id,
            "category_name": "Group set" if len(buckets) == 1 else f"Group set {i}",
            "groups": [{
                "id":          str(grp["id"]),
                "name":        grp["name"],
                "student_ids": [m["user_id"] for m in (memberships(grp["id"]) or [])],
                "memberships": memberships(grp["id"]),
            } for grp in grps],
        })
    return result, None, ""
