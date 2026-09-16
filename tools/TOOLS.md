# Tool Registry

This is a conditional route card for optional developer helpers, not a runtime dependency.

| Tool | Status | Use when |
|---|---|---|
| `size-report` | available | File sizes are needed without reading source contents. |

Run the available size report from the repository root:

```powershell
py tools/size_report.py
```

Tool output should be compact and structured. Preserve source URLs, file paths, commands,
timestamps, and IDs only when they matter for review; keep credentials and student data
out of output. Use tools for retrieval, parsing, validation, summarization, and
normalization. Use reasoning for architecture, tradeoffs, review, and specifications.

Canvas-specific priority when a relevant tool becomes available:

- API documentation: `canvas-docs-scraper`
- Canvas objects and relationships: `canvas-api-inspector`
- unfamiliar local architecture: `repo-indexer`
- large test output: `test-failure-summarizer`
- large diffs: `change-risk-summarizer`

Tool use never widens the active task's file, data, or side-effect scope.
