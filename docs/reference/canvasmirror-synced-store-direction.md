# CanvasMirror: durable synced course store

Status: teacher-approved direction, revised 2026-10-05. The functional-read-path
brief is implemented through S11 (synthetic GREEN); S12/S13 field acceptance
remains. Grading periods guide the agent rather than gate CE/CanvasMirror. No
code or state reset has been performed. The teacher permits a fresh mirror start
instead of a lossless pilot migration. Prior synthetic acceptance does not
establish pilot functionality.

## Teacher-visible outcome

Make current work easy, and do not make historical completeness anybody's problem.
Useful acquired course information lives in one CanvasMirror store in the synced
workspace. Another computer or agent can use that information
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
The 2026-10-05 decisions below supersede conflicting retention and cutover
requirements in earlier briefs and contracts for this transition.

### Functional milestone and transition decisions — 2026-10-05

- Final teacher refinement: **make current work easy; historical completeness is
  not required.** Grading periods (the four instructional periods, not semesters)
  guide the agent's choice of relevant work, not programmatic acquisition eligibility.
  This supersedes the earlier hard period-boundary interpretation. No mandatory
  calendar, assignment-to-period mapping, rollover gate, three-week cutoff, or
  date-based queue cancellation. Missing period knowledge never blocks CE/CM.
- CE executes bounded requests; explicitly requested older work remains valid.
  Previously acquired older evidence remains readable and incidental older rows
  need not be filtered from collection responses. Missing history does not trigger
  completeness backfill or block current work. Prioritize requested/new work over
  unrelated backlog; do not require the whole attachment archive to be processed.
  District calendars belong in private teacher/agent guidance, not source defaults.
- First milestone: an agent reliably reads current-course assignments,
  submissions, and available attachment text, with explicit gaps. Scoring follows
  once the required evidence is complete enough.
- Old or incomplete evidence remains readable with its age and missing pieces.
  Hold only work that requires the missing evidence, such as scoring an unread
  required attachment. Privacy and Canvas write safeguards remain mandatory.
- Switching computers is automatic when versions are compatible. The agent
  should explicitly explain when an update or refresh is required.
- Current functionality far outweighs preservation of six-month-old work.
  Retention is desirable, not a lossless-history acceptance condition or a reason
  to build elaborate compatibility and migration machinery.
- A maintenance window and a complete reset of existing CanvasMirror local and
  synced state are acceptable if needed. Nothing in the existing mirror sync
  must be treated as sacred. Prefer a fresh acquisition over a complex migration
  when it produces the simpler reliable system.
- This is permission to plan a reset, not an instruction to delete files during
  diagnosis. Identify exact reset targets in the implementation brief. Do not
  infer that unrelated workspace artifacts, credentials, identity mappings, or
  live Canvas objects need deletion.
- Remain in diagnosis/design until the replacement implementation brief is
  agreed. No reset or implementation was performed when recording these decisions.

### Continuing requirements

- Primary use is agent-led ELA and CS scoring: writing assessment, authenticity
  and plagiarism investigation, and actionable feedback. Comparisons across
  students and across successive drafts of one assignment are central.
- Submission timestamps and the applicable late/extra-time context are essential
  evidence, not incidental display fields. Accommodation decisions come from the
  teacher-agent conversation. Preserve each attempt's original submission time;
  a later attempt or changed Canvas lateness state never overwrites it.
- Only explicitly selected courses are acquired. Acquire relevant available evidence
  through bounded requests; the agent supplies teaching context, not a required
  grading-period gate. Historical completeness is not a goal.
  Deselection stops refresh; retaining available history is desirable, subject to
  the functionality-first and reset decisions above.
- Expected scale is approximately three courses, 30 students per course, 30
  assignments per term, four terms, and about five major assignments with
  multiple attempts. Drafts are attempts on the same Canvas assignment.
- Default integrity comparison is across students within the assignment, not
  across years. Retention does not imply multi-year comparison scope.
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
- Retain useful observed history where straightforward; do not reconstruct missing
  history as a prerequisite for current work. The OneDrive tenant disallows some file types,
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
- Acquisition should be useful and complete for the requested work, with explicit
  gaps, efficient change detection, and incremental updates where possible. This
  does not require a historically complete course archive.
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

The activation/import machinery is deleted. The functional-read implementation
passed synthetic gates and bounded laptop read/restart checks, but desktop
field acceptance was incomplete and scoring discovery still used private
projections. That brief is superseded, not declared GREEN.

The single current execution pointer is
[`docs/handoffs/canvasmirror-performance-discovery.md`](../handoffs/canvasmirror-performance-discovery.md).
Start at batch row **D00** in
[`canvasmirror-performance-discovery-slices.md`](canvasmirror-performance-discovery-slices.md),
reading sections **1–5**, **6 → D00**, and **7**, plus investigation sections
**1–7** named by the brief. Execute D01–D06 within that one batch.
The teacher selected bounded publication performance, evidence-index discovery,
and side-effect-free session summaries on 2026-10-06. Preparation and attachment
consumption remain outside this batch. Carried desktop/second-machine checks
are explicit in D06; no reset is proposed. Field timing and useful results are
required for acceptance, not just passing unit tests.

### Workflow-review constraints — 2026-10-05

These constraints remain product direction; the previous execution references
are historical and do not route the new batch:

- Publishing assignments/quizzes/pages must not acquire a new global mirror or
  OCR readiness gate. Keep the owning operation's real safety checks.
- A slow MCP refresh needs status-only continuation of the same operation.
  Acquisition, local indexing, and attachment failures have different recovery
  actions. The teacher should not be sent to an equivalent browser refresh button
  or asked for permission to refresh read-only data.
- Newest-first attachment ordering is retained. Existing job identities keep
  their timestamps, so this does not guarantee priority for an older requested
  assignment. The executor must not claim otherwise or add a priority system.
- A second machine's empty job queue does not mean the shared evidence has no
  missing files. Attachment availability comes from associations/extractions.
- The remaining scoring/private-projection split is a real workflow limitation.
  Discovery is addressed by the selected current batch; preparation, extracted
  attachments and feedback revision still need their own evidence contract.

### Next senior assessment

After D06 acceptance, assess assignment-scoped scoring input and packet
continuation. Read only
[`canvasmirror-performance-discovery-investigation.md`](canvasmirror-performance-discovery-investigation.md)
section **6**, the current scoring guide, and the exact owners it names. Resolve
missing scoring fields, usable extracted attachments and live-session preservation
before writing that brief. Do not infer that discovery acceptance establishes
scoring acceptance or start a repository-wide migration/cleanup project.
