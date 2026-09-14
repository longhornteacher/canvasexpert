# New Quizzes response and grading capability

Last verified: **2026-07-14**

This is the canonical Canvas Expert reference for New Quizzes authentication, response
acquisition, and item-level manual grading. It separates Canvas capability from features
that Canvas Expert currently exposes. Do not revive the older blanket claim that New
Quizzes item write-back is blocked by personal access tokens.

## Capability summary

| Capability | Canvas behavior | Canvas Expert status |
|---|---|---|
| New Quiz list and report APIs | An actively enrolled teacher's PAT can reach `/api/quiz/v1/...`; concluded enrollment may return `403` | Diagnostic and Student Analysis report paths exist |
| Constructed responses | Student Analysis JSON/CSV exposes item responses; the UI CSV remains a fallback | PowerGrader uses JSON snapshots; CSV remains a manual fallback |
| Native file evidence | Canvas's signed native/LTI result path exposes current attempt item evidence | PowerGrader has a focused signed-file acquisition path; unsupported or failed evidence remains teacher-review-only |
| Per-item manual score | Canvas's first-party grader accepts an independent score for each manual item | Exposed: New Quiz sessions post teacher-reviewed item scores and per-item feedback through a reviewed, receipt-backed finalization lane (preflight freeze, result-version drift detection, idempotency, post-write verification; concluded enrollment may return `403`) |
| Per-item grader feedback | The same result update accepts one grader-feedback value per item ("Additional Comments") | Exposed through the same finalization lane |
| Assignment-level submission comments | The ordinary Submissions API accepts comment writes on a New Quiz submission without touching the quiz-engine score (write + delete verified live 2026-07-14) | Exposed: New Quiz sessions post teacher-reviewed feedback as assignment comments through the frozen manual push review (comment-only; never a score) |
| Assignment total | Canvas derives the New Quiz result total from item scores and fudge points | Do not replace item grading with a forced ordinary-assignment total write |

## Authentication boundaries

“PAT works” does not mean the PAT is sent directly to every quiz-LTI or quiz API endpoint.
There are two different paths:

1. Public New Quiz list/report endpoints accept the teacher's normal Canvas bearer token
   when enrollment and scope permit.
2. Manual item grading follows Canvas's own web/LTI grader launch. A PAT obtains a
   time-limited Canvas web session through `GET /login/session_token`; Canvas GraphQL
   supplies the submission preview launch; the signed LTI flow issues short-lived
   participant and result credentials for the quiz services.
   The separate sessionless native-launch credential supports the focused result-read
   chain, but a user-authorized Phase A probe on 2026-07-14 received `401` when it
   submitted a complete item-result collection plus current fudge to the write endpoint.
   Treat it as read-only; the web-session/signed-launch credential is required for writes.

Never persist or log the session URL, signed LTI fields, cookies, launch token, participant
credential, result token, or signed file URL. A direct PAT bearer request is not a substitute
for the first-party grader launch.

Official documentation:

- Canvas documents `/login/session_token` specifically for starting a normal web session
  when a feature is not supported through an ordinary API:
  https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints
- Canvas GraphQL uses `POST /api/graphql` and mirrors the requesting user's permissions:
  https://developerdocs.instructure.com/services/canvas/basics/file.graphql
- Public New Quiz item documentation covers quiz authoring, not student item-result grading:
  https://developerdocs.instructure.com/services/canvas/resources/new_quiz_items

## Verified first-party item-grading chain

With explicit user authorization, the following was verified against dummy course data:

1. Establish a short-lived Canvas web session and open the submission's signed New Quiz
   grading launch.
2. Resolve the participant grading context, quiz API host, quiz session, and short-lived
   result credential.
3. Read the quiz session's current `authoritative_result` and its complete
   `session_item_results` collection.
4. Submit the complete item-result collection plus current fudge points to
   `POST /api/quiz_sessions/:quiz_session_id/results` using the result credential.
5. Re-fetch the quiz session, follow its new authoritative result ID, and verify the item
   scores, grader feedback, and derived total there.

A temporary dummy item score and temporary grader feedback were each accepted with `201`,
verified on the newly authoritative result, and cleared afterward. No live identifiers,
credentials, responses, or grades were stored in the repository. This historical check
predates the current request-shape finding below and does not verify the current serializer.

## Versioning and write safety

Every accepted result update creates a new authoritative result version. The previous result
remains readable but is superseded. Verifying against the pre-write result ID can therefore
produce a false failure even when the write succeeded.

Any Canvas Expert implementation must:

- acquire and freeze the complete current item-result set immediately before review/apply;
- bind teacher decisions to stable item IDs and the preflight authoritative result ID;
- preserve auto-graded and untouched item values exactly;
- post once per deliberate student finalization, never once per keystroke;
- re-fetch the quiz session after the write and verify the new authoritative result;
- treat a timeout or ambiguous response as unknown until reconciliation completes;
- fail closed and link to the exact SpeedGrader target when authentication, shape, item
  identity, result version, or verification differs from the expected contract;
- capture a content-minimized private receipt without credentials or student content.

This transport is used by Canvas's current first-party grader but is not documented as a
stable public item-grading API. Keep it behind one narrow adapter and a runtime capability
check so Canvas drift does not weaken review or write safety.

## Current implementation facts

- A Scoring Session starts for a Current course and assignment without exposing the
  assignment type. It includes only submissions Canvas still marks as needing grading,
  while the first packet page includes the resolved scoring basis. If refreshed rows are
  all already graded, start reports `nothing_to_grade` with no session.
- New Quiz and ordinary assignment results share `start_scoring_session` ->
  `get_scoring_packet` -> `submit_scoring_results`. The MCP layer never receives a
  signed transport, operation id, temporary review token, or live Canvas response.
- For New Quizzes, `submit_scoring_results` privately calls
  `session_actions.review_new_quiz_finalization` and `finalize_new_quiz` separately
  for each eligible student. It preflights the complete result, binds decisions to
  stable item IDs and result version, finalizes once, verifies the new authoritative
  result, and records a content-minimized receipt. A per-student refusal does not
  stop safe rows for other students; an ambiguous write is not retried.
- Finalization explicitly serializes raw snake_case GET rows into the 12-member camelCase
  first-party POST shape. It supplies only the evidenced defaults for omitted `errors`
  and `graderId`, drops raw-only `id`, `grading_method`, and unknown members, preserves
  untouched `itemFeedback.neutral`, and sends edited feedback as
  `feedback.graderFeedback.content`. Assignment-total writes are never used as a
  substitute. Active/current instructor enrollment is required; concluded, closed,
  past-enrollment, or otherwise restricted courses may return `403`.
- Content-free request evidence (2026-09-12), captured with the POST aborted before
  transmission, showed the exact 12-member first-party row contract and the raw GET's
  additional `grading_method` member. Two Canvas Expert runs sent raw/snake or mixed-case
  rows; both were rejected 25/25 with zero verified changes. No successful live write
  using the explicit serializer has been verified.
- New Quiz uploads never enter the scoring packet as files or filenames. Locally
  extracted text may be included as an essay response; unreadable uploads remain held.
- The SAFE packet contains only teacher-scorable New Quiz responses: essays and uploads
  with complete locally extracted text. Auto-scored items and unsupported unscored types
  stay private; the complete private item collection remains available to preserve every
  auto-graded and untouched value during finalization.
- A stale catalog ID on an already-scored non-manual item does not make a cached attempt
  incomplete. Essay, upload, unscored, missing, and ambiguous identities still fail closed
  with no position, prompt, or points-based matching.
- `api/powergrader/new_quiz_fetch.py` uses native result acquisition; the live participant
  result key `quiz_api_quiz_session_id` is normalized alongside older/synthetic
  `quiz_session_id` shapes (fixed 2026-07-14, live-verified: file evidence downloads).
- The sessionless result credential is deliberately **not** a production write credential:
  its Phase A full-result POST was rejected without changing the authoritative result.
  The current item-finalization adapter acquires the web-session/GraphQL signed grader launch
  for each deliberate finalization, keeping all launch/session/result credentials in memory.
- The signed adapter explicitly sets `new_quizzes_native_experience_sessionless=false`
  on GraphQL preview URLs ending in `/external_tools/retrieve` (2026-09-10). Canvas can
  otherwise return native `ENV.NEW_QUIZZES` HTML with no signed form. The request option
  selects Canvas's existing signed web flow while preserving nested launch arguments;
  unsupported forms still fail with `signed_launch_shape`. It does not substitute the
  native read credential for a grading credential. Canvas's
  [external tools controller](https://github.com/instructure/canvas-lms/blob/master/app/controllers/external_tools_controller.rb)
  supports this true/false request option.
- Live report shape (verified 2026-07-14): upload answers arrive as filename-only strings
  with no file refs — `normalize()` seeds the expected file record from the answer so the
  native transport can materialize the upload. `item_responses[].item_type` carries the
  interaction slug; catalog points live on the OUTER `/items` record.
- AI-payload policy (2026-07-14): New Quiz uploads never enter the AI lane as files or
  filenames. Locally-extracted text (TXT/DOCX-style) is inlined as an essay-like response;
  unreadable uploads (image/PDF/failed) are excluded from the packet and stay teacher-review-only.
- Student Reports currently do not materialize New Quiz item responses. That is a missing
  consumer integration, not proof that Canvas cannot provide item responses.
- The ordinary Canvas Submissions API does not expose the complete New Quiz item-result
  collection. Use the focused report/native grader paths described above.

## Feedback composition

Canvas exposes one grader-feedback value per item. Student-facing feedback is not
labeled AI unless the teacher asked. The private scoring write path in
`api/powergrader/attribution.py` strips leftover Autofeedback banners and is shared
by ordinary assignment comments and New Quiz item feedback. Default feedback shape
is Glows & Grows. No hosted model supplies the feedback block.
