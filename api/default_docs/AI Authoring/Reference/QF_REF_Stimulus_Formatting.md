# QuizForge stimulus behavior

Canvas Expert's live New Quizzes push treats stimulus items as source content to prepend to explicitly linked questions.

- A `STIMULUS` item has an `id` and a `prompt`; a question refers to it with `stimulus_id`.
- During push preparation, the pusher builds a map from stimulus IDs to their prompt strings. It removes `STIMULUS` and `STIMULUS_END` records from the question list, then prepends the matching stimulus prompt to each linked question's prompt.
- Only explicit `stimulus_id` links are applied. Unlinked questions receive no stimulus content.
- The combined prompt is passed to Canvas as the question body. Canvas Expert does not infer prose or poetry, number passage lines, or apply special excerpt syntax.
- Code blocks written as `<pre><code>...</code></pre>` are syntax-highlighted by the live pusher before submission. Other content is preserved by that step.

Author the stimulus prompt in the format that should appear before the question. Use ordinary HTML or text supported by the live New Quizzes content path. Keep the content and question wording in separate fields so the explicit link controls which questions receive it.
