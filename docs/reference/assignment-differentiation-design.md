# AssignmentForge differentiation design

**Decision date:** 2026-09-15

**Status:** Accepted content-only design.

## Boundary

AssignmentForge tiers identify authored pedagogical variants only. `Support`, `Core`,
`Accelerate`, and `Extend` are canonical labels used to select Settings-backed public
title tags. They do not identify a Canvas group, student, pod, membership, or placement.

The teacher owns all Canvas assignment decisions after delivery: assigning each draft to
students or groups, editing it, and publishing it. AssignmentForge never reads roster
placement or creates or changes groups, overrides, modules, bridges, families, or SIS
registrations.

## Payload and preparation

`api/webui/af.py::tier_payloads` returns content identity (`label`, title, description) only.
The adapter resolves public tags and appends ` - <tag>` to each source title. Tiered payloads
retain the ordinary assignment fields supplied by the teacher: points, dates, submission
settings, grading category, `post_to_sis`, and ordinary final-grade semantics.

Preparation captures only exact same-title assignment collisions for the authored source
titles. It does not read group sets, groups, memberships, enrollments, or student IDs.
`assignment_group_name` remains the ordinary Canvas grading-category option and may resolve
through the existing assignment-group lookup.

## Canvas delivery

Each tier is one independent real Canvas assignment object. It is created unpublished,
with `only_visible_to_overrides=false`, and with no assignment override. No tier is added
to a module. The created object preserves its authored description and ordinary assignment
fields, including points, dates, submission settings, grading category, SIS setting, and
final-grade setting. The teacher assigns and publishes the drafts in Canvas.

Write-ahead checkpoints, exact returned IDs, uncertain-send handling, retry, and reconcile
remain unchanged. Retry resumes only from exact verified assignment IDs and never guesses
from titles.

## Review and result

Reviews and assistant results expose each variant label, public-tagged title, Canvas
assignment ID, and Canvas HTML URL when supplied. They include the explicit teacher action:
assign students/groups and publish the drafts in Canvas. AssignmentForge projections contain
no group names, counts, member or student IDs, bridge, family, or module fields.

## Non-goals

- QuizForge differentiated delivery remains group-restricted and bridge-based.
- DataForge grouping, roster tools, grade projection, and SIS bridge semantics are unchanged.
- AssignmentForge does not create module items, differentiated bridge/family tails, or
  automatic grade synchronization.
- Ordinary whole-class AssignmentForge delivery remains unchanged.
