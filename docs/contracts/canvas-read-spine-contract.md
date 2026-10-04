# Canvas read spine v1 contract

`api.mirror.read_service` is the runtime-only, local-projection read boundary for routine
display and offline reads. It does not perform Canvas HTTP, queue a refresh, or authorize a
write. Projection files and the Course Catalog retain their own strict persisted schemas.

## Intents

The contract names `local_display`, `refresh_if_stale`, `focused_current`,
`authoritative_live`, `explicit_diagnostic`, and `offline`. Version 1 implements only
`local_display` and `offline`, both as disk-only reads. The remaining intents belong to
future coordinator, command-owner, or diagnostic work; asking this service for one raises
`ValueError` rather than silently calling Canvas.

## Scopes and envelope

The exact v1 scopes are `private.roster`, `private.groups`, `private.assignments`,
`private.submissions`, `private.submission_comments`, `catalog.assignments`,
`catalog.modules`, `catalog.assignment_groups`, and `catalog.pages`. Every result contains
exactly these runtime fields:

```text
course_id, scope, state, capability, source, last_success_at, last_attempt_at,
canvas_observed_at, retry_after, generation, mirror_revision, snapshot_id,
refresh_state, error_code, records
```

`records` is a copied list from one physical projection only. No scope joins roster,
groups, assignments, submissions, or Catalog records; `private.submission_comments`
reuses the submission records and takes its freshness only from the comment sidecar.
`state` is the projection's stored state, optionally changed from `current` to `stale`
when the caller supplies an exceeded age limit; last-good records remain available in that
case. Private assignment and submission freshness comes from the full/delta sync-pass
records. Once the mirror's refresh lifecycle has recorded a revision, roster, groups,
assignment, and submission scopes report its `mirror_revision`, `snapshot_id`, and
`refresh_state`, and a refresh that is not `synced` replaces `state`; otherwise those
identity fields are empty or zero. `capability` is `supported`
after a successful local projection and `unknown` otherwise. `source` is `mirror`,
`catalog`, or `none`; `canvas_observed_at` and `retry_after` are empty in v1 because these
projections do not persist them. `generation` is an opaque runtime value derived from the
scope identity/version and last-success metadata.

Catalog scopes adapt only validated `course_catalog.read_catalog()` output. They never add
envelope fields to Catalog files or relax the Catalog's forbidden-field/unknown-key rules.
Private scopes likewise expose existing private records only. Local output never proves a
target exists for a command: all write preflight, execution, verification, and receipts
remain with their live owners.
