# Handoff: unify QuizForge stimulus guidance

## Objective

Make QuizForge guidance accurate and consistent about stimulus behavior across output paths. This is a documentation-only slice; describe the current behavior, do not redesign or change quiz transformation behavior.

## Established facts

- The canonical QuizForge contract describes `STIMULUS` as substantive, unscored content shared by 2–4 questions. It says an item without `stimulus_id` attaches to the most recent stimulus.
- Canvas Expert's live QuizForge push does not preserve that shared structure. `api/qf_pusher.py::prepare_items` removes `STIMULUS` and `STIMULUS_END`, prefixes the stimulus `prompt` to each scored item's prompt when that item has a matching explicit `stimulus_id`, and leaves each scored item to be posted separately. It does not implement the contract's implicit most-recent fallback.
- `api/README.md` already says the live push inlines stimulus HTML and drops `STIMULUS_END`, but does not make the per-question duplication and explicit-ID limitation clear.
- Other QuizForge output paths may preserve structural stimulus blocks. Do not generalize the live-push limitation to QTI or printables without checking their current consumers.

## Locked documentation decisions

- Keep `stimulus_id` explicit in authoring examples and preflight guidance because the live API push requires it. Explain that the QTI importer can fall back to the current preceding stimulus until `STIMULUS_END`; the live push cannot.
- Describe QTI as a separate zero-point text item with parent metadata on linked questions. Describe printables as one stimulus block followed by its questions in source order. Do not promise how Canvas displays an imported QTI package beyond the generated structure.
- `layout` is consumed by QTI orientation only. The live push and printable renderer do not use it. The printable renderer detects code or poetry from prompt markup. `format` and `assets` are not consumed by the named output paths; put needed media/content directly in the stimulus `prompt` with appropriate alt text.
- Preserve the authoring recommendation of 2–4 questions per substantive stimulus as guidance, not an enforced limit or a guarantee of a particular Canvas layout.

## Scope

Primary docs to reconcile:

1. `api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt` — sections 3–5, 6, 6a, 8, and 15; especially `stimulus_id`, `STIMULUS`, format/layout/assets, and the authoring summary.
2. `api/README.md` — “What each push does automatically” → Quizzes and “Confirmed Canvas API facts / limits”.
3. `api/webui/README.md` — “Quiz tab” only if its QTI/manual-import wording needs clarification to distinguish it from the API push.

Read-only references for fact-checking only:

- `api/qf_pusher.py` — `prepare_items`, `build_push_plan`, and the quiz-item POST loop.
- `api/transform.py` — module docstring and `build_item` boundary.
- `engine/rendering/canvas/qti_builder.py` and `engine/rendering/canvas/qti_metadata.py` — determine whether QTI keeps a structural stimulus and its parent links.
- The physical quiz importer / renderer and `engine/rendering/physical/templates/quiz.html.j2` — determine whether printables keep a shared stimulus block.

## Preflight

Before editing, verify the named consumers still match these decisions. Preserve unrelated worktree changes. If any assumption fails, stop and return YELLOW with the contradicting file and line.

## Acceptance criteria

- The canonical contract clearly separates QuizForge's stimulus authoring structure from the behavior of each supported output path.
- For the Canvas Expert live API push, guidance states plainly that stimulus prompt HTML is repeated in every explicitly linked question stem; it is not sent as one shared/native Canvas stimulus item. `STIMULUS_END` is omitted.
- Guidance does not promise implicit preceding-stimulus attachment for the live push. If other formats retain that authoring behavior, scope the distinction to the correct output path rather than deleting it globally.
- Claims about QTI/manual import, printables, and `format`, `layout`, or `assets` are included only after checking the named consumers; describe any unsupported fields accurately.
- `api/README.md` and the canonical contract agree on the live-push behavior. Update the Quiz tab paragraph only if needed to make the separate QTI path unambiguous.
- No changes to push behavior, generated quiz content, or live Canvas artifacts.

## Verification and stop conditions

- Documentation-only review: inspect the final diff and search the routed docs for `STIMULUS`, `stimulus_id`, and “inline” to ensure no remaining contradictory promises.
- No automated tests are required for this docs-only slice.
- Stop and return YELLOW if a named output consumer does not match the established facts or if making the authoring contract universal requires a product decision about whether to change runtime behavior.

## Execution result

GREEN. Documentation-only changes in `api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt` and `api/README.md`; no commit. Preflight checked `api/qf_pusher.py::prepare_items`, `build_push_plan`, the item POST loop, `api/transform.py::build_item`, `engine/importers.py::_packaged_to_domain`, QTI builders/metadata, and the physical quiz adapter/template. Their behavior matched the locked decisions. `api/webui/README.md` already distinguishes the standalone QTI-ZIP manual-import path, so it was left unchanged. Verification: `git diff --check` passed; final diff and routed-doc searches for `STIMULUS`, `stimulus_id`, and `inline` found no remaining contradictory stimulus promises. No automated tests required. Deviations: none. Unresolved decisions: none.
