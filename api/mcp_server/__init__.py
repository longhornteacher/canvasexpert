"""Canvas MCP server for CanvasExpert.

Exposes the current read, scoring, and authoring tool set over stdio so any
MCP-capable assistant can help plan lessons and manage rosters. CanvasExpert
keeps sole custody of the Canvas PAT. This package reaches Canvas only
through bounded, digest-protected write paths, including the single
assignment-type-neutral Scoring Session submission path. It never binds a
network port.

Every student-data tool pseudonymizes real Canvas identity through the
existing identity vault (``api/feedback_vault.py``) and runs the outbound
safety gate (``api/feedback_safety.py``) before a result is returned. Real
names, Canvas IDs, and SIS IDs never leave this machine. Nothing here logs
tool arguments or results. Student-data tools use the same identity vault
and outbound safety gate.
"""
