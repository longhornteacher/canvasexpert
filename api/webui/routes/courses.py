"""Course and group API routes for Canvas Expert.

One APIRouter; 3 routes for listing courses, course detail, and group sets.

Routes: GET /api/courses
        GET /api/course-detail
        GET /api/groups
"""
import requests

from api import course_catalog, operational_log
from api.mirror import read_service
from api.mirror import store as mirror_store

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.platform_services.canvas_client import canvas_get, canvas_get_all, canvas_headers

router = APIRouter(tags=["courses"])


GROUPS_MAX_AGE_HOURS = read_service.GROUPS_MAX_AGE_HOURS


def _local_students(course_id: str):
    """(students, name_by_id) from a current roster mirror, or None.

    None signals the existing live users fallback. ``name_by_id`` also feeds
    the groups live fallback below so a stale/missing groups scope never
    forces an extra live users call just because roster is already local.
    """
    document = mirror_store.read_roster(course_id)
    if not isinstance(document, dict) or document.get("state") != "current":
        return None
    raw_students = document.get("students")
    if not isinstance(raw_students, dict):
        return None
    try:
        students = []
        name_by_id = {}
        for student_id, student in raw_students.items():
            name = student.get("name", "")
            students.append({
                "name":          name,
                "sortable_name": student.get("sortable_name") or name,
            })
            name_by_id[str(student_id)] = name or f"user {student_id}"
    except AttributeError:
        return None
    students.sort(key=lambda s: s["sortable_name"].lower())
    return students, name_by_id


def _local_group_sets(course_id: str):
    """Group sets from a strictly current groups snapshot, or None.

    Member names are resolved via the roster mirror's students directly —
    independent of whatever the roster/students scope above decided — so a
    stale roster never forces this scope live and vice versa. None signals
    the existing per-membership live fallback.
    """
    document = mirror_store.read_groups(course_id)
    if not mirror_store.groups_are_current(document, max_age_hours=GROUPS_MAX_AGE_HOURS):
        return None
    roster_document = mirror_store.read_roster(course_id)
    roster_students = roster_document.get("students") if isinstance(roster_document, dict) else None
    name_by_id = {}
    if isinstance(roster_students, dict):
        for student_id, student in roster_students.items():
            if isinstance(student, dict):
                name_by_id[str(student_id)] = student.get("name") or f"user {student_id}"
    try:
        categories = mirror_store.groups_for_roster(document)
        group_sets = [
            {
                "name": category["category_name"],
                "groups": [
                    {
                        "name": group["name"],
                        "members": sorted(
                            name_by_id.get(str(member_id), f"user {member_id}")
                            for member_id in group["student_ids"]
                        ),
                    }
                    for group in category["groups"]
                ],
            }
            for category in categories
        ]
    except (KeyError, TypeError):
        return None
    return group_sets


def _local_assignments(course_id: str):
    """Assignments from the Course Catalog when its scope is exactly
    current, or None to signal the existing live assignments fallback.

    ``html_url`` is a transiently-computed display value reconstructed at
    read time from ``canvas_headers()`` and the catalog id — never a
    persisted field (precedented by the catalog's derived-but-not-persisted
    ``assignment_ids``/``quiz_ids`` HTTP projection fields).
    """
    read_result = course_catalog.read_catalog(course_id)
    document = read_result.get("catalog") if isinstance(read_result, dict) else None
    if not isinstance(document, dict):
        return None
    scope = document.get("assignments")
    if not isinstance(scope, dict) or scope.get("state") != "current":
        return None
    records = scope.get("records")
    if not isinstance(records, dict):
        return None
    _, base = canvas_headers()
    try:
        assignments = [
            {
                "id":        str(record.get("id") or assignment_id),
                "name":      record.get("name", ""),
                "due_at":    (record.get("due_at") or "")[:10],
                "points":    record.get("points_possible"),
                "published": record.get("published", False),
                "html_url":  f"{base}/courses/{course_id}/assignments/{record.get('id') or assignment_id}",
            }
            for assignment_id, record in records.items()
        ]
    except AttributeError:
        return None
    return assignments


def _local_modules(course_id: str):
    """Modules from the Course Catalog when its scope is exactly
    current, or None to signal the existing live modules fallback.
    """
    module_scope = read_service.catalog_modules(course_id)
    if not isinstance(module_scope, dict) or module_scope.get("state") != "current":
        return None
    records = module_scope.get("records")
    if not isinstance(records, list):
        return None
    try:
        modules = [
            {
                "id":          str(module.get("id") or ""),
                "name":        module.get("name", ""),
                "items_count": len(module.get("items") or []),
                "published":   True,
            }
            for module in records
            if isinstance(module, dict) and module.get("id") is not None
        ]
    except (TypeError, AttributeError):
        return None
    return modules


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


@router.get("/api/course-detail")
def course_detail(course_id: str):
    """Everything the Course Info page shows: roster, group sets with member
    names, modules, and upcoming assignments. Students, group sets, and
    assignments are served from the local read spine (roster/groups mirror,
    Course Catalog) when their scope is current/fresh; each scope
    independently falls back to its existing live Canvas call when its
    projection is missing, stale, incomplete, or malformed — one stale scope
    never forces the others live. Modules are served from the local Course Catalog when current."""
    local_students = _local_students(course_id)
    if local_students is not None:
        students, name_by_id = local_students
    else:
        live_students, err = canvas_get_all(
            f"/api/v1/courses/{course_id}/users",
            {"enrollment_type[]": "student", "per_page": 100})
        if err:
            return JSONResponse({"ok": False, "error": err})
        name_by_id = {str(s["id"]): s.get("name", f"user {s['id']}") for s in live_students}
        students = sorted(
            ({"name":          s.get("name", ""),
              "sortable_name": s.get("sortable_name", s.get("name", ""))}
             for s in live_students),
            key=lambda s: s["sortable_name"].lower())

    group_sets = _local_group_sets(course_id)
    if group_sets is None:
        cats, _ = canvas_get_all(
            f"/api/v1/courses/{course_id}/group_categories", {"per_page": 50})
        group_sets = []
        for cat in cats or []:
            groups, _ = canvas_get_all(
                f"/api/v1/group_categories/{cat['id']}/groups", {"per_page": 100})
            out = []
            for g in groups or []:
                members, _ = canvas_get_all(
                    f"/api/v1/groups/{g['id']}/memberships", {"per_page": 200})
                out.append({
                    "name":    g["name"],
                    "members": sorted(name_by_id.get(str(m["user_id"]), f"user {m['user_id']}")
                                      for m in (members or [])),
                })
            group_sets.append({"name": cat["name"], "groups": out})

    modules = _local_modules(course_id)
    if modules is None:
        modules, _ = canvas_get_all(
            f"/api/v1/courses/{course_id}/modules", {"per_page": 100})

    assignments = _local_assignments(course_id)
    if assignments is None:
        live_assignments, _ = canvas_get_all(
            f"/api/v1/courses/{course_id}/assignments", {"per_page": 100})
        assignments = [{"id":        str(a["id"]),
                        "name":      a.get("name", ""),
                        "due_at":    (a.get("due_at") or "")[:10],
                        "points":    a.get("points_possible"),
                        "published": a.get("published", False),
                        "html_url":  a.get("html_url", "")}
                       for a in (live_assignments or [])]

    return JSONResponse({
        "ok": True,
        "students": students,
        "group_sets": group_sets,
        "modules": [{"id":          str(m["id"]),
                     "name":        m.get("name", ""),
                     "items_count": m.get("items_count", 0),
                     "published":   m.get("published", True)}
                    for m in (modules or [])],
        "assignments": assignments,
    })




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
