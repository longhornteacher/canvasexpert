# Private scoring engine route card

The former PowerGrader teacher UI is retired. This legacy filename remains only as
a pointer for existing internal references; it is not product or UI guidance.

For the current private scoring engine owners, privacy/write boundaries, and test
routing, use the single source of truth: `docs/reference/powergrader-scoring-map.md`.

Public teacher flow: MCP `start_scoring_session` -> `get_scoring_packet` ->
`submit_scoring_results`. Canvas Live is the review/edit surface.
