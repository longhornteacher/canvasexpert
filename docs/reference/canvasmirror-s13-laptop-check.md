# S13 second-computer field check — CanvasMirror read portability

Status: historical checklist; bounded result recorded in
`s13-laptop-field-results.md`. Do not dispatch this as a current task. The new
`docs/handoffs/canvasmirror-performance-discovery.md` carries remaining checks
into its D06 slice. The following records the former check, read-only with
respect to Canvas: local index maintenance is expected, but do not perform Canvas
writes, operation retries, a reset, or an explicit Canvas refresh before the
first cross-device read. Do not edit repository files as part of this check.

## Preflight

1. Read `AGENTS.md`, the current brief's S12/S13 field result, and S13 in
   `docs/reference/canvasmirror-functional-read-path-slices.md`. Follow
   `docs/guides/continue-dev-on-another-computer.md` to clone or safely resync
   `dev` after the replaced S13 report history; preserve any local work and do
   not merge the replaced commits. Verify that `dev` matches `origin/dev` and
   the sanitized field-result commit is an ancestor of `HEAD`.
2. Confirm that the computer's Canvas Expert runtime actually uses this updated
   checkout; inspect the configured runtime source path rather than assuming a
   successful pull changed the running process. Confirm the same selected
   OneDrive workspace and matching six-character Identity Vault transfer-key
   fingerprint as the desktop. Never print the key or private paths.
3. Wait until OneDrive has synced and hydrated `_Shared/`, `CanvasMirror/`, and
   `_System/CanvasMirror Control/`. Check **Local workspace & privacy** for a
   shared-store conflict; stop student-data reads if it reports one. Do not open
   vault files or student originals directly.

## Read and restart

4. Restart the Canvas Expert runtime to load the updated code. Let its
   automatic local index maintenance finish. **Do not call `refresh_mirror` or
   use the console refresh button before the first reads.** If indexing is
   pending, observe status and allow the maintenance cycle to complete.
5. Through the local Canvas Expert MCP connection, choose one Current course
   and a sampled assignment with submission evidence. Use the existing
   discovery/read tools, including `get_roster`, `get_submissions`, and
   `get_assignment_evidence`. Check assignment context, submission coverage,
   age/revision, section labels, and any available attachment text or explicit
   attachment gap. Note `ok`, warnings, stage/error codes, aggregate counts,
   and elapsed times. Do not print individual rows, labels, IDs, names, or
   content in the report.
6. Restart the runtime once more and repeat the **same** reads. Confirm
   it becomes readable without activation, import, manual refresh, or a reset.
   If something fails, distinguish sync/hydration delay, update required,
   local index pending/failure, acquisition failure, and attachment gap. A
   local index repair may be appropriate; record it before repeating reads.

## Report back to the desktop chat

Return only: checked revision and runtime-revision match; workspace/key/sync/
conflict preflight pass or fail; first-read and post-restart pass or fail;
aggregate assignment/submission/section/attachment counts; coverage and
warning/error codes; timings; and whether any intervention was needed. State
whether there was an explicit Canvas refresh (expected: no). Keep private
student data, course and assignment identifiers, labels, paths, and row content
out of the report. This check covers CanvasMirror read portability only; the
guide's separate one-week scoring and work-handoff checks remain open.
