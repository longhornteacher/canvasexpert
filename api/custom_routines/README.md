# Custom Routines

Custom routines run inside CanvasExpert's local runtime. The retained control console
configures and displays them; they are not a hosted agent surface or a replacement for
the connected assistant's conversation.

Drop a `.py` file here and it shows up in the control console's Routines table the next time
you start Canvas Expert. Each file registers one or more routines with the `@routine`
decorator. No imports needed — the helpers listed in `AUTHORING.md` are already in scope.

- Files starting with `_` are **templates** and are NOT loaded. Copy
  `_example_missing_work.py` to a name without the leading underscore to activate it.
- A broken file is skipped (the server prints the error to its console) — it never takes
  the app down.
- A custom routine that uses the same id as a built-in (`sweep`, `download`, `curve`,
  `grading_debt`, `student_reports`, `sis_bridge_sync`) is ignored.

To author one with an LLM, paste `AUTHORING.md` into your assistant and describe what you
want the routine to check or do.
