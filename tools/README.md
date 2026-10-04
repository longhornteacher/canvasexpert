# Tools

This directory holds small, optional developer helpers. They are not part of the
Canvas Expert runtime or launcher.

## Available Helpers

```powershell
py tools/size_report.py
```

Reports `.py` and `.js` files at or above 300 lines, marking 500+ line files as
large. The report is advisory and exits successfully; it is meant to guide small
refactor slices, not block urgent fixes.

```powershell
py tools/canvasmirror_release_benchmark.py --self-check --output <path outside the repo and workspace>
```

Read-only CanvasMirror release harness. `--self-check` runs a synthetic
disposable-cache check; `--live-readonly` measures the configured courses
against real Canvas using GET requests only. See `docs/mirror.md`.
