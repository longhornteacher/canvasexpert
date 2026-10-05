# CanvasMirror: durable synced course store

Status: teacher-approved direction, 2026-10-04. Synthetic/local implementation
is complete through the read-activation repair; private pilot import, live
activation, and two-computer field acceptance remain separate checks.

## Teacher-visible outcome

The teacher's accumulated course information lives in one durable CanvasMirror
store in the synced workspace. Another computer or agent can use that information
after sync without rebuilding its course knowledge from Canvas first. Optional
machine-local indexes are disposable conveniences, not the durable source.

Canvas remains authoritative for current live state. Canvas Expert refreshes the
store, and Canvas writes still use the existing reviewed operation paths and
live verification.

## Approved decisions

- CanvasMirror is a durable, synced course store, rather than exclusively a
  disposable cache on each computer.
- Student identities in the agent-readable store are stable Pokemon pseudonyms.
  Real student names, student Canvas/SIS IDs, identity mappings, and other real
  PII do not belong in it. Canvas Expert pseudonymizes identities and scrubs
  acquired content before persistence in this store. Course and content IDs
  remain usable for navigation.
- Agents may read the CanvasMirror store directly under a documented reading
  contract. The teacher accepts that contract as sufficient for this access;
  MCP is not a mandatory gateway for reading these pseudonymized records.
- MCP remains a convenient query interface over the same information, with one
  consistent reading contract rather than a separate interpretation of records.
- Identity Vault data and original evidence that can contain real PII remain
  outside the agent-readable store. Direct mirror access does not authorize
  browsing those private stores or exposing Canvas credentials.
- The contract describes record identity, schema, relationships, freshness,
  completeness, and current observations versus retained history. An agent
  should be able to discover and understand the information without depending
  on a particular machine's paths or local index.

This explicitly changes the target direction from mandatory MCP-only mirror
reads to supported direct reads of the pseudonymized store. Existing runtime
behavior remains as implemented until an execution brief changes it.

## Teacher priorities and constraints

Recorded 2026-10-04 during planning; these decisions govern the implementation.

- Primary use is agent-led ELA and CS scoring: writing assessment, authenticity
  and plagiarism investigation, and actionable feedback. Comparisons across
  students and across successive drafts of one assignment are central.
- Submission timestamps and the applicable late/extra-time context are essential
  evidence, not incidental display fields. Accommodation decisions come from the
  teacher-agent conversation. Preserve each attempt's original submission time;
  a later attempt or changed Canvas lateness state never overwrites it.
- Only explicitly selected courses are acquired. Acquire all available evidence.
  Deselection stops refresh but retains the course and its history indefinitely.
- Expected scale is approximately three courses, 30 students per course, 30
  assignments per term, four terms, and about five major assignments with
  multiple attempts. Drafts are attempts on the same Canvas assignment.
- Default integrity comparison is across students within the assignment, not
  across years. Indefinite retention does not imply multi-year comparison scope.
- Preserve informational content and metadata useful to integrity analysis;
  reproducing Canvas presentation is not the priority.
- Files and attachments are required scope: DOCX, PDF, PPTX, XLSX, and JPG.
  Text-entry-only support is insufficient. Acquisition covers submitted text and
  files, not externally linked documents or URLs.
- Retain assignment instructions, rubrics, relevant modules/pages, submission
  comments, existing feedback/grades, due dates, and individual Canvas overrides.
- Preserve useful formatting and document revision evidence where present,
  including quote characters. The writing itself is the primary integrity
  evidence; extraction must not unnecessarily flatten it or invent unavailable
  editing history.
- Preserve observed history. The OneDrive tenant disallows some file types,
  including `.py`, `.exe`, and `.js`; ZIP is allowed. The teacher has no complete
  blocked-type list. Attachment retention needs a tenant-permitted representation;
  do not assume raw uploaded filenames/extensions can be synced.
- Normal use alternates between desktop at home and laptop at work. Concurrent
  operation is accidental but possible and must not silently damage records.
  Both machines are Windows and use the same OneDrive tenant/workspace. Design
  the store to be cloud-provider agnostic, without speculative provider adapters.
- Machine transitions should be automatic; the user should not have to manage
  handoffs. The current acquisition owner has priority when both machines run.
- The teacher intends to keep the store fully downloaded. Agent/setup guidance
  may explain the provider's keep-on-device setting; do not silently assume all
  files have arrived. Use complete available evidence with focused acquisition
  when synchronization is incomplete.
- Reliability takes precedence over speed targets: slow is acceptable if work
  continues reliably. Eliminate avoidable refresh/sync reasoning. Offline
  operation is not the driving requirement, and no fixed refresh SLA is chosen.
- Acquisition should be very complete, with efficient change detection and
  incremental updates where possible.
- Agent-created summaries, notes, and derived analyses may persist in one
  contained area, clearly distinguished from acquired Canvas evidence.
  The senior may choose sensible persistence defaults.
- Individual extraction/acquisition failures do not stop useful assignment work.
  Process available evidence, hold affected items as necessary, and notify the
  teacher of actionable gaps. Preserve reviewed work when a new attempt arrives
  during scoring and explicitly identify the newer evidence.
- Teacher filesystem readability has no value as a design requirement here.
  This is the agent's working area; choose the representation for reliable
  acquisition, synchronization, and agent retrieval.
- Remain in thinking/planning. The senior does not execute the implementation;
  any later coding work needs an agreed brief for an executor.

## Execution design

S00–S08 of the durable evidence store have passed their synthetic/local gates,
but production pilot import and activation have not occurred. A senior review
found index/cutover, incomplete-attachment hold, and comparison-view integration
gaps. The current repair brief is
`docs/handoffs/canvasmirror-read-activation-repair.md`; the original slice brief
is retained under `docs/handoffs/retired/` as historical execution evidence.

Agent-host prioritization is not yet specified; the teacher requested clarification
of that question. This does not reopen the approved direct-read/MCP access model.

No live pilot data has been moved or deleted. Complete the current repair brief
and separately report its private import and two-computer field acceptance.
