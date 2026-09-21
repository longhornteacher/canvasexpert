# Private scoring engine route card

The former PowerGrader teacher UI is retired. This legacy filename remains only as
a pointer for existing internal references; it is not product or UI guidance.

For the current private scoring engine owners, privacy/write boundaries, and test
routing, use the single source of truth: `docs/reference/powergrader-scoring-map.md`.

Public teacher flow: MCP `prepare_scoring_session` -> `get_scoring_packet` ->
`stage_scoring_results` -> `apply_staged_scoring_results`. One assignment-scoped session contains one SAFE packet;
packets and writes remain assignment-bounded. New preparation owns one private
session JSON and one scrubbed SAFE bundle JSON. Canvas Live is the review/edit
surface.
