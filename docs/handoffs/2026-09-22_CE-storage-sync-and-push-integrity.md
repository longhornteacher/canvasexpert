# Handoff: CanvasExpert storage, sync and push integrity

**Created:** 2026-09-22. **Owner:** the teacher and CE developer.
**For:** the implementing agent (Codex or similar) working in the CanvasExpert codebase.
**Companion docs (read for background; this handoff is self-contained):**
- `CE-Fix-List-2026-09-22.md`: the full prioritized list. This handoff turns items **#1–#6, #15, #7–#11 and #14** into build specs.
- `claude/canvasexpert-dev-log.md`: Issues #1–#12 with verbatim tool output.
- `claude/push-walkthrough-2026-09-22.md`: today's investigation (W-1 … W-6).

---

## 0. TL;DR for the implementer
CE keeps all of its state (Canvas caches, the pseudonym vault, settings and in-flight operations) inside a **OneDrive-synced folder** that the teacher uses from **two or more Windows machines** (work laptop `LAPTOP-ID` and a home desktop). OneDrive is producing conflict copies (`<name>-LAPTOP-ID.json`). CE silently reads whichever fork wins, which is the leading explanation for CE reporting `"synced"` data that didn't match Canvas (dev-log Issue #12).

Separately, the "course content refresh" only refreshes one catalog section, the push path writes unconfirmed rows into the catalog, and read tools don't report their own freshness.

**Build order:** Phase 1 (storage split + locking + conflict detection) → Phase 2 (honest catalog/refresh + freshness envelope) → Phase 3 (`verify_live` + push-path fixes) → Phase 4 (SIS bridge/family fixes). Every phase has acceptance tests in §9. **Don't start Phase 3 until 1 and 2 pass.** Until then, push results can't be verified.

---

## 1. Hard requirements (teacher rulings — do not trade these away)
| ID | Requirement |
|---|---|
| R1 | **Pokémon pseudonyms are permanent.** An existing student→Pokémon mapping must never change, on any machine, ever. Pokémon are unique across *all* courses. |
| R2 | **In-progress work moves between machines.** A scoring session or frozen push op started on the work laptop must be resumable and appliable on the home desktop, and back again. |
| R3 | **Freshness rule for every agent-facing gate:** local data under **1 h** old during school hours (school days 07:00–16:30 America/Chicago) or under **10 h** old outside them is used silently, with no refresh and no prompt. Past that, return a prompt for the teacher. **Never auto-refresh.** |
| R4 | **The agent touches Live only to verify after a push.** Reads come from local caches. Tooling must make that one post-push check cheap and trustworthy. |
| R5 | **A course content refresh refreshes everything:** assignments, modules and their items, pages (including unpublished), and assignment groups. The badge shows the *oldest* section. |
| R6 | **Every Live write needs explicit teacher permission.** A teacher's request to put something in Canvas counts as that permission. Nothing in this handoff adds an unprompted Live write. |
| R7 | **Privacy:** private student data may be stored in the teacher's M365 OneDrive tenant, including the Identity Vault. Keep real PII behind the vault/service privacy boundary; never expose it in agent-facing output or logs. |
| R8 | Tier tags: Support→Silver, Core→Red, Accelerate→Blue, **Extend→Blue (intentional collapse)**. Bridges are **unsuffixed** titles (`- Bridge` suffix is legacy). |

## 2. Non-goals
- No new Canvas write capabilities in this handoff (update/delete tools are a separate effort, Fix List P3).
- No changes to the Forge authoring contracts beyond §8.4.
- No Canvas Group/pod logic. Tiers are unrestricted, and placement belongs to the teacher.

---

## 3. Observed current state (evidence, 2026-09-22)
Workspace root: `<configured OneDrive workspace>`

```
CanvasExpert/
  settings.json               # saved_courses, tier_tags, sis_grade_bridges, monitored_students, late_sweep
  settings.json.lock          # 1 byte, mtime 2026-08-13
  To Review/{Assignments,Quizzes,Pages,Rubrics,Claude outputs}/<label>.txt + <label>.txt.done
  Library/…  Printables/  Drafts/  Student Work/…  For AI/…  ScoringSession/…
  _System/
    Canvas Catalog/<course_id>/catalog.v3.json, catalog.v3.previous.json
    Canvas Mirror/<course_id>/_sync.v1.json, _refresh.v1.json, assignments.v1.json, roster.v1.json,
                              groups.v1.json, course_context.v1.json, submissions/<aid>.v1.json,
                              new_quizzes/_sync.v2.json, new_quizzes/<aid>/quiz.v2.json
    Identity Vault/vault.json, vault.json.lock   # lock mtime 2026-08-13 → writes are NOT taking it
    workbench/registry.v1.json, discovery-cache.v1.json, suppressions.v1.json, quarantine/
    PowerGrader/{Jobs,Sessions}/   Audits/   Archive/   DataForge/
```

**`catalog.v3.json` shape** (per course):
```json
{ "version": 3, "course_id": "<course-A>", "course_name": "...", "updated_at": "...",
  "assignments":       {"state":"current|stale","last_success_at":"...","last_attempt_at":"...","error_code":"|invalidated","records":[...]},
  "modules":           { same shape },
  "pages":             { same shape },
  "assignment_groups": { same shape } }
```
**`_sync.v1.json`**: `passes.{full,delta,roster}.{state,last_success_at,last_attempt_at,error_code}` plus `watermarks.{submitted_since,graded_since}`.
**`_refresh.v1.json`**: `{operation_id,state:"synced",requested_at,started_at,finished_at,error_code,revision,snapshot_id:"<course>:<rev>"}`.

**Defects observed:**
1. **Conflict forks, 07:30–07:40 CT.** Nine copies named `*-LAPTOP-ID.json`: `catalog.v3` and `catalog.v3.previous` for all 3 courses, `Canvas Mirror/<course-A>/assignments.v1`, `Canvas Mirror/{<course-B>,<course-C>}/_sync.v1`. There was also a copy of `Identity Vault/vault`; the teacher confirmed it identical and removed it. The <course-A> conflict copy contained assignment `<assignment-A>` and module `<module-A>`, which the main copy lacked.
2. **Unconfirmed catalog inserts.** In that fork, module `<module-A>` (`Sample differentiated assignment`, 0 items) is present while `modules.last_success_at` is **2026-09-20**, before the module was created (2026-09-21). The push path is writing rows into `records` without a Canvas read.
3. **Partial refresh.** The WebUI "course content refresh" at 17:22Z updated **only** `assignments`. `modules` stayed `stale`/`invalidated` (15:41Z), and `pages` and `assignment_groups` stayed at 15:36Z. The UI showed green and "1 min old."
4. **Freshness hidden.** `get_course_assignments` returns no `state` or `synced_at` even when its section is `stale`/`invalidated`. `get_modules` and `get_course_pages` do return them.
5. **Gates use wrong windows.** `prepare_scoring_session` refuses at 30 min (`mirror_freshness_confirmation_required`). `get_modules` refuses module selection on any `stale`, however young. `discover_scoring_work` enqueues a new refresh on every call (Issue #2), which invalidates its own session ids.
6. **`get_course_pages` omits unpublished pages**, so a default (unpublished) page push can't be verified.
7. **Tiered `apply_content_push`** creates tier 0 only, returns `assignment_create_unverified`/`sent_unknown`, leaves it unpublished, then `drift_detected` forever (Issue #11, 3 of 3 attempts).
8. **SIS bridge repair deadlock** (Issue #8): `preview_sis_grade_bridge` → `family_link_required` → `reconcile_sis_grade_bridges` → `blocked` → `preview_sis_grade_bridge_reconciliation` refuses `blocked`.
9. The `Filesystem` MCP server's `outputSchema` declares JSON Schema draft-07; the Claude client requires 2020-12, so every call fails. Env issue, trivial fix: bump the `$schema` dialect or drop `outputSchema`.

---

## 4. Phase 1 — Storage split, locking, conflict detection *(Fix List #1)*

### 4.1 Classify every store
| Class | Stores | New location | Sync model |
|---|---|---|---|
| **CACHE** | everything under `Canvas Catalog/`, `Canvas Mirror/`, `workbench/discovery-cache*` | `%LOCALAPPDATA%\CanvasExpert\cache\` | Per machine, never synced. Each machine refreshes from Canvas. |
| **LEDGER** | Identity Vault | `…\CanvasExpert\_Shared\vault\` (OneDrive) | Append-only, one journal file per machine (§4.3) |
| **SHARED-KV** | `settings.json` keys, family links, learning objectives, feedback contracts, roster local settings, Writing Record, `workbench/registry`, `suppressions` | `…\CanvasExpert\_Shared\kv\<store>\` | Per-machine journals, merged on read (§4.4) |
| **WORK** | scoring sessions (packet, review Q&A, staged results, digests), frozen push ops (`preview_*` records), PowerGrader Sessions/Jobs | `…\CanvasExpert\_Shared\work\<work_id>\` | One writer at a time via a lease, append-only events (§4.5) |
| **LOCAL-ONLY** | logs, temp, quarantine, locks for CACHE | `%LOCALAPPDATA%\CanvasExpert\` | Never synced |
| **TEACHER FILES** | `To Review/`, `Library/`, `Printables/`, `Drafts/`, `Student Work/`, `For AI/`, `_System/Archive/`, `_System/Audits/` | unchanged | Write-once, unique names. `.done` marker stays |

**Rule:** no file anywhere under OneDrive is ever rewritten in place by CE, except teacher files the teacher edits. CE writes to OneDrive only by **creating a new file** or **appending to a file only this machine writes**.

### 4.2 Machine identity and single-process lock
- `machine_id` = the Windows computer name (`LAPTOP-ID`, …), normalized to upper-case. Store it with a random 8-char suffix in `%LOCALAPPDATA%\CanvasExpert\machine.json` on first run, to guard against renamed or duplicate computer names.
- **Process lock:** on start, the WebUI server, the MCP server and any CLI take an OS-level exclusive lock on `%LOCALAPPDATA%\CanvasExpert\ce.lock` (Windows `LockFileEx` / `msvcrt.locking` / `portalocker`). If one CE process already holds it, a second process (for example the MCP server launched by Claude while the WebUI runs) must **attach to the running one over local IPC or HTTP (the WebUI is at `127.0.0.1:8765`)** rather than open the stores itself. Exactly one process per machine touches the stores.
- Remove reliance on `vault.json.lock` / `settings.json.lock`. They're OneDrive-synced files and mean nothing across machines.

### 4.3 Pseudonym ledger (meets R1)
**Files**
```
_Shared/vault/
  seed.v1.json                     # frozen import of today's vault.json; never written again
  journal.<machine_id>.jsonl       # append-only; only that machine writes it
  pokemon.v1.json                  # canonical ordered Pokémon list (static, versioned)
```
**Seed import (one-time migration):** read the current `_System/Identity Vault/vault.json` and write `seed.v1.json` with `{canvas_user_id → pokemon, assigned_at, source:"seed"}` plus whatever other fields the vault holds today (keep them). Record the sha256 of the source. After import, the old `vault.json` is renamed to `vault.json.migrated-<date>` and never read again.

**Journal line**
```json
{"v":1,"ts":"2026-09-22T17:30:00Z","machine":"LAPTOP-ID","op":"assign",
 "canvas_user_id":"synthetic-user-id","pokemon":"Yveltal","k":0,"course_ids":["<course-A>"]}
```
The teacher confirmed on 2026-09-22 that private student data may remain in the M365 OneDrive tenant; the Identity Vault is the privacy boundary. Preserve the existing vault fields in the shared seed and keep agent-facing results pseudonymized. Do not split real-name fields into separate local-only stores solely because the workspace is OneDrive-synced.

**Merged view** = seed ∪ all journals. For each `canvas_user_id` the winning entry is the earliest `(ts, machine)`. For each Pokémon the owner is likewise the earliest entry. An entry whose Pokémon is already owned by a different student, earlier, is a **collision**.

**Assigning a new student** (roster sync finds a `canvas_user_id` with no entry):
```
secret = Windows Credential Manager "CanvasExpert/pseudonym-secret"  (32 random bytes, generated once,
         entered/pasted once on each additional machine; UI shows a 6-char fingerprint to confirm match)
for k in 0..:
    idx = int.from_bytes(HMAC_SHA256(secret, f"{canvas_user_id}:{k}")[:8]) % len(POKEMON)
    cand = POKEMON[idx]
    if cand not owned in merged view and cand not in DENYLIST: append assign(k, cand); break
```
The same ledger plus the same secret gives the same Pokémon on either machine.

**Collision handling (rare: two machines add different new students at once):** the later entry's student becomes `provisional`. Scoring and packet generation refuse that student (`pseudonym_provisional`) until that machine appends a new `assign` with the next free `k`. Never re-point an existing owned Pokémon.

**DENYLIST:** Pokémon that collide with proper nouns in assigned texts (see dev-log Issue #4: the scrubber replaced "Cherry", "Winston", "Parker" and "Belle"). Start with an empty list plus a hook. The scrubber fix itself is out of scope here.

**Invariants to test:** every `canvas_user_id` has exactly one non-provisional Pokémon; every Pokémon has at most one owner; seed entries never change.

### 4.4 Shared key-value stores
```
_Shared/kv/<store>/journal.<machine_id>.jsonl
  {"v":1,"ts":"...","machine":"...","key":"tier_tags.Extend","op":"set","value":"Blue"}
  {"v":1,"ts":"...","machine":"...","key":"sis_grade_bridges.<course-A>.Sample Family","op":"del"}
```
- Merge: for each key, the latest `(ts, machine)` wins. `del` is a tombstone.
- Compaction: once a day, if this machine holds the store's compaction lease (§4.5 lease mechanics, keyed `kv:<store>`), write `snapshot.<ts>.json` and start fresh journals that contain only entries newer than the snapshot. Readers use the latest snapshot plus journals.
- Migration: import current `settings.json` as the first snapshot for store `settings`. Rename the old file to `settings.json.migrated-<date>`.
- Note `settings.json` currently contains a test fixture under key `"42"` (`Practice - Red/Gold`). Drop it on import.

### 4.5 Work items and handoff (meets R2)
```
_Shared/work/<work_id>/
  manifest.json            # written once at creation: kind, course_id, assignment_id|label, created_by, created_at
  lease.<machine_id>.json  # rewritten only by its owner: {"machine","pid","heartbeat_at","state":"held|released","final_event_count"}
  events.<machine_id>.jsonl# append-only; each event has seq (global, monotonically increasing across machines)
  blobs/<sha256>           # immutable content: SAFE packet pages, snapshot data, staged results arrays, frozen previews
```
- **Self-contained:** a scoring session's packet and its underlying snapshot data are stored as blobs, and the session digest is computed over those blobs. **Nothing in a work item may reference a CACHE revision number.** Today's `session_stale` ("A newer CanvasMirror revision exists") compares against the local mirror revision. Replace it with: the session is valid if its blobs match their digests. Canvas drift is checked **at apply**, against Live, as today.
- **Holding:** a machine may write events only while its lease is `held` and no other machine's lease is `held` with `heartbeat_at` newer than 5 min. It rewrites its heartbeat every 30 s while CE is running with that work item open.
- **Handoff (clean):** a "Hand off" action, closing the item, or quitting CE sets `state:"released"` and `final_event_count` = the last `seq`.
- **Take over:** the other machine waits until its local view of `events.*` contains every seq up to `final_event_count`. The UI shows "Syncing from LAPTOP… 12/14". Then it writes its own lease `held`.
- **Take over (stale):** if the other lease says `held` but its heartbeat is older than 5 min (laptop lid closed), show a warning ("LAPTOP-ID last active 3:42 PM; events may still be syncing"). Allow take-over on confirm. Events from the old holder that arrive later with a `seq` beyond the take-over point are quarantined into `events.<old>.orphan.jsonl` and surfaced to the teacher, never merged silently.
- **Frozen push ops** (`preview_content_push`, `preview_assignment_update`, `preview_sis_grade_bridge`, …) become work items too. Their `operation_id`/`batch_id`/`review_digest` stay valid on any machine, because apply re-checks the Canvas baseline.
- **MCP surface:** add `list_work_items()`, `get_work_item(work_id)` (state, holder, event count, sync progress), and `take_over_work_item(work_id, confirm_stale=false)`. Existing tools that act on a session must refuse with `work_item_held_elsewhere {holder, heartbeat_at}` rather than failing obscurely.

### 4.6 Conflict detection (belt and braces)
- On startup and before any read of a `_Shared/` store, glob for `* -<ANYNAME>.*` / `*-<MACHINE>.*` siblings (the OneDrive conflict pattern: `name-COMPUTERNAME.ext`, possibly `-2`, `-3`) **across all of `_Shared/`**, not only the vault.
- If any are found: block writes to that store, show the red "Local workspace & privacy" card (it already exists for the vault) listing the files, and offer "Compare" (hash equal → safe to delete) and "Quarantine" (move to `_Shared/_conflicts/<date>/`).
- For CACHE stores, which are now local: on detecting a legacy conflict copy under the old `_System/` path, ignore it and log it. The data will be refetched.

### 4.7 Atomic writes everywhere
Write to `<file>.tmp.<pid>`, `fsync`, then `os.replace`. That applies to LOCAL-ONLY/CACHE writes and lease files. Journals are opened in append mode with one `write()` of a full line plus `\n`, then `fsync`.

### 4.8 Migration plan (run once per machine, idempotent)
1. Take the process lock (§4.2). Refuse if another CE process is running.
2. If `_Shared/vault/seed.v1.json` doesn't exist and `_System/Identity Vault/vault.json` does, import the seed (first machine only). Record the source sha256 in the seed. If the seed already exists (second machine), verify that any local vault.json matches the seed's sha256 or is a subset. On mismatch, **stop** and show both fingerprints. Never auto-merge a vault.
3. Import `settings.json` into `_Shared/kv/settings/` the same way.
4. Move open PowerGrader sessions and scoring sessions into `_Shared/work/`. If that's too hard, let open sessions finish under the old model and have only new sessions use the new one. Say which option you chose in the PR.
5. Point the CACHE paths to `%LOCALAPPDATA%`. Don't copy the old caches; do a full refresh on first run (R5 refresh).
6. Leave the old `_System/Canvas Catalog` and `Canvas Mirror` in place, read-only, for 30 days, with a `README-MIGRATED.txt`. **Preserve the nine `-LAPTOP-ID` conflict copies**; the teacher wants them as evidence.
7. Write `_Shared/migration.<machine_id>.json` with the version and timestamps.

---

## 5. Phase 2 — Honest catalog, full refresh, freshness envelope *(Fix List #2–#6, #14)*

### 5.1 No unconfirmed rows in the catalog *(#2)*
- Push and apply paths **must not** write into `catalog.records`. Add `pending_writes.v1.json` per course (CACHE): `{id, kind, title, created_by_op, created_at, confirmed:false}`.
- A refresh that reads the object from Canvas marks the entry confirmed and drops it. A pending entry older than 24 h that Canvas doesn't return is reported by read tools as `pending_unconfirmed`.
- **Deletions:** a section refresh **replaces** `records` wholesale from the Canvas list response. Never merge. Compute `deleted_ids = before − after` and return it (§5.3).

### 5.2 Content refresh = all sections *(#3, R5)*
- One entry point, `refresh_course_content(course_id)`, is used by the WebUI button, `refresh_course_structure`, and scheduled routines. It fetches, in this order: assignment_groups, assignments, modules **with items**, pages **including unpublished** (`published` flag per record). Success means every section succeeded. Otherwise the result is `partial`, with a per-section `error_code`.
- The UI badge shows `min(last_success_at)` across sections, with a per-section tooltip. "All green" is impossible while any section is `stale`.
- `refresh_mirror` keeps its current scope (submissions, roster, quizzes) but must also report per-pass results the same way.

### 5.3 Refresh responses prove a Canvas read *(#4)*
```json
{"ok":true,"course_id":"<course-A>","result":"complete|partial",
 "sections":{"modules":{"fetched_from_canvas":true,"http_status":200,"pages_fetched":2,
   "records_before":6,"records_after":7,"added_ids":["<module-B>"],"deleted_ids":[],"finished_at":"..."}, ...}}
```
Never return `synced` or `complete` from a cache short-circuit. If the refresh is skipped because data is within policy, return `result:"skipped_within_policy"` with ages.

### 5.4 Freshness envelope on every read tool *(#5)*
Every catalog and mirror read (`get_course_assignments`, `get_modules`, `get_course_pages`, `get_roster`, `get_submissions`, `get_gradebook_snapshot`, `list_groups`, `list_sections`, `discover_scoring_work` rows) includes:
```json
"freshness":{"source":"catalog|mirror","section":"assignments","synced_at":"...","age_minutes":42,
             "state":"current|stale","within_policy":true,"policy_window_minutes":60,"school_hours":true}
```
`within_policy` is computed per R3: school days Mon–Fri 07:00–16:30 America/Chicago give a 60-minute window; any other time gives 600 minutes. Read the district calendar/holidays from `settings.late_sweep.holidays`, where present, to decide what counts as a school day.

### 5.5 Gates follow R3 *(#6)*
- `prepare_scoring_session`: delete the 30-minute rule. Within policy, proceed silently. Outside policy, return `freshness_confirmation_required` with ages. `use_existing_mirror=true` still overrides.
- `get_modules` / module selection for push: within policy, `stale` must **not** block. Outside policy, prompt as above. An `invalidated` section (set by a push in this session) is treated as within policy for selection, since the pending write is known.
- `discover_scoring_work`: **read-only**. No refresh enqueueing. It reports freshness per course and stops.

### 5.6 Pages *(#14)*
`get_course_pages` returns unpublished pages with `published:false`. Add `include_unpublished=true` as the default.

---

## 6. Phase 3 — Post-push verification and push-path fixes *(Fix List #15, #7, #8, #9, #13)*

### 6.1 `verify_live(course_id, kind, id | title)` *(#15, R4)*
- One Canvas GET (or one small list call filtered by title), no catalog write. Returns `{found, id, title, published, points_possible, module_ids:[...], assignment_group_id, omit_from_final_grade, post_to_sis, due_at, url, checked_at}`. For `kind=page` it returns `{found, url, title, published, module_ids}`.
- Every apply tool's success response includes a `verify_hint` naming the ids to pass to `verify_live`. The agent runs **only** this call after a push.

### 6.2 Tiered AssignmentForge apply *(#7, Issue #11)*
Current failure: tier 0 is created, CE marks it `sent_unknown`/`assignment_create_unverified`, halts the family, and leaves it unpublished. Then `drift_detected` on retry.
- After any ambiguous create response, **look it up**: list assignments in the course filtered by exact title, created within the last 10 min. If exactly one is found, treat it as created and continue. If none, retry the create once. If more than one, stop with `duplicate_suspected {ids}`.
- Continue through all tiers, then the bridge, then the module items, then the family link, as the contract describes.
- `published:true` is applied to each source **after** its creation is verified. That call is currently missing or skipped on this path.
- Record every created id as it happens (a work-item event, §4.5) so a failure leaves an exact list.
- Add `resume_operation(operation_id)`, which continues from the last recorded step, and `abandon_operation(operation_id)`, which marks the op dead and returns the created ids for manual cleanup. `drift_detected` responses must name the drifted fields and point to one of these two tools.

### 6.3 Drift digest *(#8)*
Compute the drift digest over the **Canvas baseline**, meaning the fields CE reads back for the target: existing ids by title, module membership, and the family link. Don't include the draft content. A retry after a partial create then sees the partial state as a known baseline for `resume_operation`, instead of drift.

### 6.4 Partial-failure cleanup *(#9)*
On unrecoverable failure mid-family, return `repair_plan: [{step, created_id, state}]`. Don't delete anything automatically (there's no delete tool, and R6 applies).

### 6.5 SIS preconditions at preview *(#13)*
`preview_content_push` with `post_to_sis:true` and no `due_at` → refuse at preview with `sis_requires_due_at`. On any Canvas 4xx, pass `canvas_message` through verbatim.

---

## 7. Phase 4 — SIS bridges and family identity *(Fix List #10, #11, #12)*

### 7.1 Break the reconciliation deadlock *(Issue #8)*
- `preview_sis_grade_bridge_reconciliation` **accepts** `blocked` families. It refuses only when there are no sources, more than one unsuffixed bridge candidate (return all ids), or tier point totals differ.
- **Bridge inference:** when `bridge_assignment_id` is null and exactly one family member has no tier suffix, that member is the bridge candidate. Today it's classified as a fourth source; see Sample Short-Response Family: `source_count:4`, including <assignment-bridge-A>.
- The repair plan **sets** `omit_from_final_grade:true` and SIS sync off on tier sources, and SIS sync on for the bridge. These are currently listed as blockers (`source_counts_toward_final_grade`, `source_sis_sync_enabled`), which is backwards.
- `reconcile_sis_grade_bridges` includes the proposed repair plan per family, so the caller doesn't need a second gated call to see it.
- Fix `preview_sis_grade_bridge.user_action` text once the path works.

### 7.2 Family matching *(Issue #9)*
Normalize before title matching: casefold, collapse whitespace, unify `—`/`–`/`-`, strip trailing tier tag (`- Silver|Red|Blue`) and `- Bridge`, and tolerate a parenthetical that appears on the sources but not the bridge (for example `(Paper)`). Prefer a registered family link over title fallback. Test cases:
- `Sample Novel, Chapters 1-4 MAJOR — Blue` and `Sample Novel MAJOR, Chapters 1-4 — Red` are currently split by word order. Report them as `title_mismatch_suspected` rather than guessing; the teacher will normalize.
- `Sample Novel Ch 1 - Character, Setting & Nouns (Paper) - Silver` should match bridge `Sample Novel Ch 1 - Character, Setting & Nouns`.

### 7.3 Shared tier tags *(R8, Fix List #12)*
Allow several pedagogical labels to map to one public tag. Refuse only when a single envelope contains two labels that resolve to the same tag (for example Accelerate **and** Extend both → Blue), with `tier_tag_collision {labels, tag}`. The validation rule "each used public tag must exist and be unique" becomes "unique *within the envelope*." Update the AssignmentForge and QuizForge contract text to match.

---

## 8. Smaller items to fold in while you're in the code
1. **`stage_content` validates the envelope** before writing, using the same validator as push. Today it accepted a 46-byte `$(cat …)` string (`To Review/Quizzes/Sample Prep - Labeling the Parts - Course A.txt`).
2. `delete_staged_content(kind,label)`, which moves the file to `_System/Archive/<kind>/deleted-<date>/`, and `get_staged_content(kind,label)`.
3. The workspace reset's confirmation text must state that it archives AssignmentForge drafts only (observed 9/21: quizzes weren't archived).
4. Connected guide wording: "never left as unrestricted teacher-assignment work" contradicts "creates one unrestricted source per tier." Reword.
5. The `Filesystem` MCP `outputSchema` dialect (draft-07 → 2020-12).

---

## 9. Acceptance tests (the teacher will run these; each should be scriptable)
**Phase 1**
- [ ] T1.1 Run CE on laptop and desktop on the same day, refreshing on both, for one week → **zero** `*-<MACHINE>.*` files under `_Shared/`.
- [ ] T1.2 After migration, every `canvas_user_id` in the old vault maps to the **same Pokémon** on both machines (diff the merged views and expect an empty diff).
- [ ] T1.3 Add a test student on the desktop → the laptop shows the same Pokémon after OneDrive sync, without re-deriving differently.
- [ ] T1.4 Simulated collision (two journals assign the same Pokémon to different ids at the same time) → the later id is `provisional`, and scoring refuses it with `pseudonym_provisional`.
- [ ] T1.5 Start a scoring session on the laptop, stage partial results, click Hand off → open it on the desktop → "Syncing… n/n" → finish, stage, apply. No `session_stale`.
- [ ] T1.6 Close the laptop lid mid-session (no hand-off) → the desktop warns about the stale lease and allows take-over on confirm. Late laptop events land in an `orphan` file and are surfaced.
- [ ] T1.7 Plant `_Shared/kv/settings/journal.X-TEST.jsonl`-style conflict names → the red card appears and writes are blocked.
- [ ] T1.8 Launch the WebUI, then the MCP server → the MCP server attaches to the running process, and exactly one process holds `ce.lock`.

**Phase 2**
- [ ] T2.1 One WebUI refresh → all four catalog sections `current` with `last_success_at` within 60 s; the badge equals the oldest.
- [ ] T2.2 Create X in Canvas by hand → refresh → `added_ids` has X. Delete X in Canvas → refresh → `deleted_ids` has X and the catalog lacks X.
- [ ] T2.3 Push X via CE → the catalog does **not** contain X until the next refresh; `pending_writes` does.
- [ ] T2.4 Every read tool returns the `freshness` envelope. `get_course_assignments` on a stale section says `state:"stale"`.
- [ ] T2.5 A 50-minute-old mirror at 10:00 on a school day → `prepare_scoring_session` proceeds with no prompt. The same at 70 minutes → prompt. 9 h old at 21:00 → proceeds.
- [ ] T2.6 Four `discover_scoring_work` calls → zero new refresh operation ids.
- [ ] T2.7 Push an unpublished page → `get_course_pages` (after refresh) lists it with `published:false`.

**Phase 3**
- [ ] T3.1 `verify_live` on a known id returns found/published/module_ids with exactly one Canvas request (check the request log).
- [ ] T3.2 Tiered Sample Prep (draft in `To Review/Assignments/Sample Prep - Sample Novel and Sample Poem (Tiered) - Course A.txt`) to a **sandbox course** first, then Course A (<course-A>) with teacher permission. Expect 3 published sources titled `… - Silver/Red/Blue` in the chosen module, 1 unsuffixed bridge not in any module, and family link `verified`. The teacher confirms in Canvas.
- [ ] T3.3 Force an ambiguous create (mock) → the lookup-by-title path continues. Force a hard failure → `repair_plan` lists created ids, and `resume_operation` completes the family.
- [ ] T3.4 `post_to_sis:true` without `due_at` → refused at preview.

**Phase 4**
- [ ] T4.1 `Sample Course: Sample Family` (Course A): reconciliation preview accepts it, identifies <assignment-bridge-A> as the bridge, and plans omit/SIS changes. After apply (teacher permission), `preview_sis_grade_bridge` works.
- [ ] T4.2 The sample chapter paper sources match the unsuffixed bridge <assignment-bridge-B>.
- [ ] T4.3 A Support/Core/Extend envelope pushes as Silver/Red/Blue. An Accelerate+Extend envelope is refused with `tier_tag_collision`.

---

## 10. Safety rules for the implementer
- **Test Live writes in a sandbox Canvas course.** Touch Course A (<course-A>), Course B (<course-B>) or Course C (<course-C>) only with the teacher's explicit go-ahead for that specific action.
- Never open, print, log or upload the contents of the Identity Vault or roster caches outside the teacher's machine. Use hashes and fingerprints in diagnostics.
- Don't delete the nine `-LAPTOP-ID` conflict copies or the `_System/Archive/<teacher evidence>/` folder. Both are evidence and recovery material.
- Keep existing MCP tool names and argument shapes working. Add fields rather than renaming them. Where behavior must change (for example `session_stale`), bump the tool schema version and note it in the changelog.

## 11. Open questions for the teacher (ask, don't assume)
1. What exactly does `vault.json` store today: real names, or only ids and Pokémon? This determines whether the ledger split in §4.3 needs a name store.
2. Which machines besides `LAPTOP-ID` run CE, and does any run the start-up or scheduled "Routines" (submission download, late sweep)? Only one machine should run scheduled routines. Proposal: make it a per-machine setting, default off.
3. Is there a sandbox Canvas course for Phase 3 testing? If not, one should be requested before T3.2.
4. School-hours calendar: is Mon–Fri 07:00–16:30 CT plus `late_sweep.holidays` correct, or is there a district calendar to import?

### 11.1 Resolved teacher decisions (2026-09-22)
- Inspect the vault schema only. Preserve all existing fields, including real names and SIS IDs, in the private M365 OneDrive Identity Vault. The vault/service boundary keeps those identifiers out of agent-facing results; do not split them into a separate local-only store. Do not inspect the teacher's live vault contents during implementation.
- CE runs on one laptop and one desktop. Scheduled routines run on the laptop only. Routine enablement is already stored in each machine's `%LOCALAPPDATA%` config and defaults off; enable routines only on the laptop.
- There is no sandbox Canvas course yet. Phase 3 live-write acceptance stays blocked until a sandbox is available; do not substitute a production course.
- School hours are Monday–Friday, 07:00–16:30 America/Chicago, excluding holidays configured in `settings.late_sweep.holidays`.

## 12. Deliverables
- One PR per phase, each with its acceptance tests automated where possible.
- A short `MIGRATION.md` at the workspace root explaining the new layout for the teacher.
- An entry in `claude/canvasexpert-dev-log.md` per phase: what shipped, which Issues it closes (#2, #8, #9, #11, #12, and Fix List #1–#15), and test results.

## Execution result

**Traffic light:** YELLOW. Phase 1–2 implementation checkpoint only. No Phase 3/4 work started. No real OneDrive workspace or live Canvas course was opened or changed.

**Commit hash:** `85ce67a` (implementation checkpoint; this execution-result update is the follow-up commit).

**Changed files (71):**
- Root: `MIGRATION.md`.
- `api/`: `course_catalog.py`, `diagnostics.py`, `feedback_artifacts.py`, `feedback_safety.py`, `freshness_policy.py`, `identity_ledger.py`, `identity_vault_service.py`, `local_runtime.py`, `mcp_server/__main__.py`, `mcp_server/contract.py`, `mcp_server/server.py`, `mcp_server/tool_schema_v58.json`, `mcp_server/tools.py`, `mirror/store.py`, `operation_ledger/catalog_reconcile.py`, `operation_ledger/executor.py`, `operation_ledger/recovery.py`, `platform_services/config/__init__.py`, `platform_services/config/_io.py`, `platform_services/config/routines.py`, `platform_services/workspace.py`, `powergrader/context.py`, `powergrader/scoring_artifacts.py`, `powergrader/scoring_local.py`, `powergrader/scoring_preparation.py`, `powergrader/session_store.py`, `pseudonym_secret.py`, `qf_ui.py`, `runtime_paths.py`, `shared_kv.py`, `shared_storage.py`, `shared_vault.py`, `shared_work.py`, `webui/mirror_service.py`, `webui/routes/names.py`, `webui/routes/settings.py`, `webui/server.py`, `webui/static/canvasagent.js`, `webui/static/pages/canvasagent.css`, `webui/static/settings/identity-vault.js`, `webui/templates/canvasagent.html`, `webui/templates/roster.html`, `webui/templates/settings.html`, `work_registry/providers/roster_warnings.py`.
- `api/tests/`: `conftest.py`, `mcp_server/conftest.py`, `mcp_server/test_contract.py`, `mcp_server/test_runtime_mount.py`, `mcp_server/test_tools.py`, `mcp_server/test_work_item_tools.py`, `powergrader/test_scoring_local.py`, `powergrader/test_session_store.py`, `test_beta075_mcp.py`, `test_beta075_storage.py`, `test_course_catalog.py`, `test_freshness_policy.py`, `test_local_runtime.py`, `test_names_vault_backup.py`, `test_operation_ledger.py`, `test_route_contract.py`, `test_shared_kv.py`, `test_shared_vault.py`, `test_shared_work.py`, `test_vault_conflict.py`, `test_vault_conflict_endpoint.py`, `webui/test_mirror_service.py`, `webui/test_workspace.py`.
- `api/webui/`: `mirror_service.py`, `routes/names.py`, `routes/settings.py`, `server.py`, `static/canvasagent.js`, `static/pages/canvasagent.css`, `static/settings/identity-vault.js`, `templates/canvasagent.html`, `templates/roster.html`, `templates/settings.html`.
- `docs/`: `contracts/pseudonym-contract.md`, `handoffs/2026-09-22_CE-storage-sync-and-push-integrity.md`, `mcp-server.md`.

**Focused gates:**
- `py -m pytest -p no:randomly api/tests/test_freshness_policy.py api/tests/test_vault_conflict_endpoint.py api/tests/test_shared_kv.py api/tests/test_shared_vault.py api/tests/test_vault_conflict.py -q --tb=short` — **28 passed**.
- `py -m pytest -p no:randomly api/tests/test_course_catalog.py api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_contract.py api/tests/test_beta075_mcp.py api/tests/webui/test_mirror_service.py api/tests/webui/routes/test_course_catalog.py api/tests/powergrader/test_scoring_local.py -q --tb=short` — **222 passed**.
- `py -m pytest -p no:randomly api/tests/test_local_runtime.py api/tests/test_shared_kv.py api/tests/test_shared_vault.py api/tests/test_shared_work.py api/tests/test_vault_conflict.py api/tests/test_vault_conflict_endpoint.py api/tests/test_beta075_storage.py api/tests/test_route_contract.py api/tests/test_names_vault_backup.py api/tests/test_settings_rail.py api/tests/webui/test_workspace.py api/tests/mcp_server/test_runtime_mount.py api/tests/mcp_server/test_work_item_tools.py -q --tb=short` — **87 passed**.
- Isolated headless browser check of `/` used only a synthetic temp workspace containing a malformed reappeared vault file. HTTP 200; the existing “Local workspace & privacy” card showed `Legacy storage reappeared` / `unavailable`; zero JavaScript runtime errors and zero failed non-favicon requests. Chromium emitted only the existing missing-favicon 404 console message; no new runtime or route errors were observed.

**§4.8 step 4:** chose **new sessions only use shared work**. Existing open sessions remain in their original local store; they are not migrated or copied.

**Acceptance evidence:**
- Automatically exercised with synthetic stores/mocks: T1.2–T1.4 and T1.7; T2.1 service-level section refresh/oldest-section behavior; T2.2 add/delete replacement behavior; T2.3 pending-write confirmation; T2.4 freshness on covered read surfaces and the 49-tool registry/snapshot/inventory contract; T2.5 direct freshness matrix including the configured holiday and 07:00/16:30 boundaries; T2.7 unpublished-page default contract. The reappeared-vault/settings regression checks both reads and writes, leaves the file byte-for-byte untouched, and drives the red-card status.
- Needs the teacher on both computers: T1.1 one-week conflict-free use; T1.2/T1.3 actual OneDrive propagation; T1.5 cross-device session handoff; T1.6 stale-lease takeover/orphan handling; T1.8 live WebUI/MCP single-process attachment. Automated tests simulate storage or process boundaries; they do not establish OneDrive or field behavior.
- Still open: T2.1 live WebUI refresh through all four Canvas-backed sections; T2.2 real Canvas create/delete and refresh; T2.3 real push then post-refresh confirmation; T2.4 a complete all-read-tools freshness audit; T2.6 the explicit four-call/no-new-refresh-operation live gate; T2.7 unpublished-page push and refresh confirmation. No sandbox course is available, so no live writes were attempted.

**Deviations / limits:** the current Canvas list callbacks do not expose per-request HTTP status or page counts, so the refresh summary reports `http_status` and `pages_fetched` as `null`; this evidence remains open. `claude/canvasexpert-dev-log.md` is absent from this checkout, so no new dev-log file was created; this execution result is recorded here. The v58/v59 drafts were consolidated into one v58 snapshot matching all 49 live tools, including the shared-work tools and unpublished-page argument. Real migration remains deferred until the teacher backs up `_System/Identity Vault/` and `settings.json`, closes Canvas Expert on the desktop, and pulls this checkpoint on both computers.
