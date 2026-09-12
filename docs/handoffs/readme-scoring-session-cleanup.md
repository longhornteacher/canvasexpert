# README Scoring Session Cleanup

Status: READY — corrected after preflight

## Objective

Make every README-named file describe the shipped chat-first Scoring Session flow and
remove remaining teacher-facing PowerGrader UI, hosted-model, and scheduled Auto-Score
claims. Keep legacy internal names only where they accurately identify private code or
reference filenames.

## Acceptance criteria

1. The root `README.md` presents Scoring Sessions as one assignment-type-neutral MCP
   workflow: the agent requests a SAFE packet, asks the teacher when rubric/guidance or
   missing-score policy is needed, submits through guarded validation/write handling, and
   leaves Canvas Live as the only review/edit surface.
2. `docs/README.md` labels both legacy-named PowerGrader reference files as internal/private
   Scoring Session implementation maps, without suggesting that a PowerGrader UI exists.
3. `api/webui/README.md` no longer claims Assignment creation can schedule Auto-Score.
4. All 13 README-named files are audited. No README advertises PowerGrader, a local scoring
   queue, hosted/OpenRouter grading, scheduled scoring, or the retired assignment-type-specific
   public scoring tools. Accurate New Quiz documentation and explicit private legacy path or
   filename references remain.
5. Markdown remains concise, internally consistent, and free of trailing-whitespace errors.

## Non-goals

- No runtime, test, contract, guide, module-map, or non-README changes.
- No renaming of `api/powergrader/` or the two legacy-named reference files.
- No behavioral change and no broad test-suite run.

## Locked decisions and scope

- Allowed product files: `README.md`, `docs/README.md`, and `api/webui/README.md`.
- Audit-only inventory: every path returned by
  `rg --files | Where-Object { $_ -notmatch '^(?i:docs[\\/]handoffs[\\/])' -and $_ -match '(?i)(^|[\\/])[^\\/]*readme[^\\/]*$' }`.
- `api/README.md` already uses the intended framing; its `powergrader/` entry is an accurate
  legacy internal path and should not be erased merely to satisfy a word search.
- Canvas rubric takes precedence when present; CE rubric/guidance is fallback; the agent asks
  the teacher when required context or policy is missing.
- Do not imply that every result waits in Canvas Expert for teacher review. Results are drafts
  produced agent-to-user, then a guarded submit writes to Canvas; Canvas Live is the staging,
  review, and editing surface.

## Required references

- This brief.
- `README.md`, especially `What it does`.
- `docs/README.md`, especially `Useful starting references`.
- `api/webui/README.md`, especially `Create`, `Scoring Sessions`, and its route table.
- `api/README.md` lines describing Scoring Sessions, only as wording authority; do not edit it
  unless repository truth contradicts this brief, in which case stop RED.

## Preflight and stop conditions

- Confirm branch `dev`, a clean worktree except for this brief, and that all three stale
  passages still exist before editing.
- Stop RED if the README inventory differs materially, any runtime behavior contradicts the
  locked wording, or a non-README file must change.

## Verification gate

Run:

```powershell
$readmes = rg --files | Where-Object { $_ -notmatch '^(?i:docs[\\/]handoffs[\\/])' -and $_ -match '(?i)(^|[\\/])[^\\/]*readme[^\\/]*$' }
$readmes | Sort-Object
rg -n -i "power.?grader|open.?router|auto.?scor|grading queue|scoring queue|review queue|stage_scores|preview_(new_quiz|assignment)_scores|apply_(new_quiz|assignment)_scores|feedback-expert" -- $readmes
git diff --check
```

Review every reported match and record why any retained match is an accurate private legacy
path/reference. The gate passes only if the inventory contains 13 files, the three acceptance
edits are present, no prohibited teacher-facing claim remains, and `git diff --check` is clean.

## Execution result

**GREEN — README cleanup is complete.** No commit; branch remains `dev`. No live
Canvas access or write occurred. Changed product files are exactly `README.md`,
`docs/README.md`, and `api/webui/README.md`; this brief records the execution result.

Verification:

- Corrected README inventory command returned **13 files**; it explicitly excludes
  `docs/handoffs/` so the active brief is not counted as a README document.
- The named case-insensitive `rg` scan returned **10 matches**. Each was reviewed:
  `api/README.md` retains the accurate private `powergrader/` implementation path
  and explicitly says there is no hosted grader/local queue; `docs/README.md`
  labels both legacy-named maps internal/private Scoring Session implementation
  maps; `api/webui/README.md` uses the private reference filename and explicitly
  says no scoring page/queue/hosted grader exists; root `README.md` says no local
  scoring queue/hosted grader exists. No prohibited teacher-facing claim or
  retired public scoring tool remains in the 13-file inventory.
- `git diff --check`: exit **0**, no whitespace findings (Git emitted only
  informational Windows autocrlf line-ending warnings).

All acceptance criteria hold; no deviations or unresolved decisions remain.
