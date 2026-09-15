"""Single source for the student-facing system name.

Change the one constant below and every surface follows, including the on-disk
store folder -- `store.repo.STORE_FOLDER` derives from it rather than repeating
the string, so the two cannot drift.

Do not use "Forge": that word already names Canvas Expert's authoring content
kinds, and a collision would corrupt the docs.

Not to be confused with `api.platform_services.workspace.SYSTEM_NAME`, which is the name
of the workspace's `_System` folder and has nothing to do with this.
"""
from __future__ import annotations

# Chosen by the teacher 2026-07-28, replacing the "Daily Writing" placeholder:
# the record now spans every written response, not one piece per class day.
SYSTEM_NAME = "WritingReps"

# The unit a student produces in one class day, used in student-facing copy.
REP_NOUN = "rep"
REP_NOUN_PLURAL = "reps"
