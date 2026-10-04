## 7. Batch 4: Canvas rubric from the payload

**Risk:** high, since it is a new Canvas write. **Depends on:** Batch 2. D2 = yes.

**Objective.** The payload `rubric` also becomes the assignment's Canvas
rubric, so scoring, the rubric box, and the printable share one source. The work is to
create and associate the rubric through the Operation Ledger, verify it, record the
receipt, and resume.

Before writing the brief, read `docs/reference/operation-ledger-module-map.md`,
`docs/contracts/operation-ledger-contract.md`, and the "slice 11c1" exclusion history
(`git log -S "11c1"`). The senior writes the acceptance criteria then, against that
code. Locked now:
- The payload `rubric` is the single source. The Canvas rubric, the collapsed box, and
  the printable table all derive from it.
- Each tier source gets the same rubric. The bridge gets none.
- A Hub (D7) gets the rubric on the hub assignment only. Tier pages get none.
- A push without `rubric` creates no Canvas rubric.

## 8. Teacher decisions (resolved 2026-09-25)

- **D1: remove it all.** Canvas Expert stops choosing, labeling, displaying, and editing
  student group membership. The teacher manages tier/pod membership in Canvas. Batch 1
  AC5 implements this.
- **D2: do Batch 4.** The payload rubric becomes the Canvas rubric. Between Batches 2 and
  4, the teacher attaches rubrics in Canvas by hand.
- **D3: keep the double title.** Canvas's own title and the banner's `<h2>` both stay.
- **D4: attachments in Batch 3.** Teacher-provided files upload and link through the
  printable path (contract §6.1).
- **Live testing uses CS8**, with unpublished `[TEST]` items. There is no sandbox course.
- **D5: colors are a synced teacher preference** chosen from fixed swatches (contract
  §2 and §3). Layout is not a preference. This is implemented by Batch 2b.
- **D6: attachments come from Canvas Files or from chat, never from a teacher-managed
  folder.** Existing Canvas files are found by name lookup only, with no listing tool.
  Chat-posted files come through `stage_attachment`, capped at 25 MB (contract §6.1).
  This is implemented by Batch 3b.
- **D7: Differentiated Hub is a third AssignmentForge style** (2026-09-25), next to
  Everyone and Bridge. Canvas differentiation tags are the pod source of truth, and CE
  assigns tier pages to tags by exact public-tag name, with the teacher as the fallback.
  Design: `docs/reference/assignment-differentiation-design.md`, "Delivery styles" and
  "Differentiated Hub". Hub live checks use ELA 7, because CS8 has no tags. The Hub
  brief is separate from this plan's batch sequence and does not move the §9 pointer.

## 9. Next batch (single current pointer)

**Next: Batch 4 (§7).** Read this plan's §0, §1.1 (authoring and assembly), §1.5
(rubrics), §7, and §8 (D2); the contract's §2, §4 item 6, §6 (rubric in the
printable), and §7 law 4. Also read `docs/reference/operation-ledger-module-map.md`,
`docs/contracts/operation-ledger-contract.md`, and the "slice 11c1" exclusion
history (`git log -S "11c1"`) before writing the direct brief. There are no
outstanding teacher decisions. The senior must write Batch 4 acceptance criteria
against the current code and lock the rubric create/associate, verification, receipt,
and resume path before delegation. The CS8 live check for Batches 2b, 3, and 3b
remains outstanding; use unpublished `[TEST]` items and the smoke path in §6b.

## 10. Known adjacent defects (outside this plan)

- `{{page:…}}` placeholders are never resolved (§1.5). They are refused until page links
  are designed. `{{file:…}}` is refused and directs authors to `canvas_file` attachments.
- `PageAdapter.build_payload` silently drops `module_id` and `create_module`, which
  `content_push._KIND_OPTIONS["page"]` accepts. That contradicts the rule that unsupported
  options are refused, not dropped.
- `preview_assignment_update` and `apply_operation` would write description HTML
  outside the renderer. This needs a later decision: re-render from a 2.0 payload, or
  restrict edits to fields that don't bypass the look.
