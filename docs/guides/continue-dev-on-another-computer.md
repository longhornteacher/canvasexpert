# Continue Canvas Expert pilot work on another computer

The current pilot and S13 field check are on GitHub branch `dev`. GitHub opens
`main` by default; `main` is the stable branch and does not contain the current
S12/S13 work. Use the repository's Git remote:

```text
https://github.com/longhornteacher/canvasexpert.git
```

Do not use the in-app updater for this pilot checkout. Its configured repository
does not match the Git remote. A Download ZIP copy has no Git history and cannot
`git pull`; use a Git checkout for work that must stay in sync.

## Fresh Git checkout

In a new, empty parent directory:

```powershell
git clone --branch dev https://github.com/longhornteacher/canvasexpert.git
cd canvasexpert
git status --short --branch
```

Confirm the status says `dev...origin/dev` with no changed files. Configure the
Canvas Expert launcher/runtime to use this checkout, then restart it. A Git
checkout update does not change an already-running process.

## Existing Git checkout

First inspect local work and the configured remote:

```powershell
git status --short --branch
git remote -v
git fetch origin
```

The remote must be the URL above. Preserve uncommitted changes and independent
local commits; stop and resolve those before changing the branch. If `dev` does
not exist locally, run `git switch --track origin/dev`. Otherwise run
`git switch dev` and inspect:

```powershell
git log --oneline origin/dev..dev
git rev-list --left-right --count dev...origin/dev
```

If there are no local-only commits, update with `git pull --ff-only origin dev`.
An older computer may instead have the superseded S13 report commits from the
recent privacy history replacement. In that case a fast-forward pull will fail.
If the worktree is clean and the only local-only commits are those replaced
report commits, use `git reset --hard origin/dev`. If there is other local work,
stop and preserve it; do not merge the replaced commits into `dev`.

Verify the selected checkout matches GitHub:

```powershell
git status --short --branch
git rev-list --left-right --count dev...origin/dev
git merge-base --is-ancestor aa49123 HEAD
```

The count should be `0 0`, the working tree should be clean, and the ancestor
check should exit successfully. Confirm the running Canvas Expert process uses
this checkout, then restart it. Before claiming the repository is current for
development, also compare `dev...origin/main` as required by `AGENTS.md`.

## Shared teacher workspace and remaining S13 check

GitHub carries source and instructions, not the Canvas token, Identity Vault
transfer key, or teacher workspace. Follow
[`more-than-one-computer.md`](more-than-one-computer.md) for the same OneDrive
workspace, matching vault-key fingerprint, sync/hydration, and local index
startup. Do not commit or print private student data or district configuration.

For the remaining read-path field check, give the computer's Codex agent
[`canvasmirror-s13-laptop-check.md`](../reference/canvasmirror-s13-laptop-check.md).
The [sanitized result so far](../reference/s13-laptop-field-results.md) lists the
specific direct reads still needed. Keep S13 YELLOW until that evidence passes.
