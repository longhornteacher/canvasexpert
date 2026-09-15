"""AI Authoring library builder: seeds a teacher's workspace with the paste-ready
MagicSchool / Copilot skill files that live at ``api/default_docs/AI Authoring/``.

That folder is the sole repository source for these files -- every "Author a ...",
START HERE, Reference, and MagicSchool Toolkit file there is already the exact
paste-ready text a teacher pastes into an AI assistant. This module never
regenerates that text; it only seeds it into a teacher's workspace (once, never
overwriting an edit).

Pure module: builds plain-text output files only. The web UI / server owns the
HTTP routes and startup hook.
"""
import hashlib
import os
import shutil


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.dirname(MODULE_DIR)
REPO_ROOT = os.path.dirname(API_DIR)
DEFAULT_DOCS_DIR = os.path.join(API_DIR, "default_docs")
DEFAULT_AI_TA_DIR = os.path.join(DEFAULT_DOCS_DIR, "AI Authoring")


# Seeded files that a newer version replaces, mapped to every version of them
# we previously shipped.
#
# Both seeding helpers below skip a path that already exists, which is a
# deliberate promise to teachers who hand-edit these files. The side effect is
# that new contents never reach anyone who already has the file: they keep the
# stale copy. Deleting the stale copy first, so the current one seeds back in
# on the same run, is what actually updates them. A copy is only deleted when
# it still hashes to something we shipped, so a hand-edited file survives.
#
# This covers two cases with one mechanism:
#   * a rename, where the old name is listed and no longer ships
#   * a content update under the same name, where the name still ships and its
#     PREVIOUS hashes are listed
#
# Maintenance: when the text of a listed file changes, append the hash of the
# version being replaced. Get it with
#   git log --format=%H -- <path>
#   git show <rev>:<path> | sha256sum      (normalise CRLF to LF first)
# Listing the CURRENT shipped hash would delete and re-seed forever, which
# test_a_retired_name_that_still_ships_cannot_churn guards against.
RETIRED_FILES = {
    # Superseded by "START HERE - CanvasAgent.txt".
    "START HERE - Canvas Expert.txt": frozenset({
        "d7b59318f61d733380349846b948858a98ad06aed969ba15f2b8eeceea5d6eed",
    }),
    # Indexed the file above, so an unedited copy is stale the moment it goes.
    "About This Folder.txt": frozenset({
        "c3d90d2d29fd36ac9b3fecbc8982b9681d967a409fcda69c3d4d65d9e70e63b6",
        "00a7c1d978e5d02effde0f4b6d0a96d7d131ca324372eea641be2ee1cd247115",
        # Before the folder index named the retired display contract, schedule,
        # objectives, and writing guides.
        "b31f29b5a32c1c4efa23ed9c9a3e53f408cdc029cee8aa1b503c6f981205a409",
    }),
    # Retired classroom-display authoring contract; remove only unchanged copies.
    "Author a SmartDeck (SlideForge).txt": frozenset({
        "1ddbd8451ec340171c1c2194bb45e18f71cea16c1640bd3458d8eabe5d46753f",
        "e6fa8e82ce0132c400a91f754962d63ef0cf7b50f7d8284a94317e5d14dcfeab",
    }),
    # Same-name updates. Teachers are told to hand this file to an AI, so a
    # stale copy answers setup questions wrongly rather than harmlessly.
    "START HERE - CanvasAgent.txt": frozenset({
        # First release, before the procedure-first rewrite.
        "66fb445401ff147e03b727d01d70e08f8337563f94d954ef6ac6fae9dfa0706b",
        # Procedure-first rewrite, before Appendix A on installing and running.
        "e5e4023c419e14de58339f32c3b6da5bafd81528aae91d41477640c5f27b21b6",
        # Before the scoring packet MCP tools were described.
        "94788ae4a8c063cd2e60f234e51a3f902e8fed8282b10caba80121135c8fb80b",
        # Before Panels and the Panel theme tools were described.
        "52f9755e202e51072cb687df1a0d1fa6b85d468dd371d7f8c30bd73e4c3656fe",
        # Before Appendix D described the local write tools and Appendix B added
        # School Calendar and Learning Objectives coverage.
        "2cb1a3c99d4d01f0158fe61ce4995a0d5bcdab360430f62ee8363d8a75aa5798",
        # Before "Automations" was renamed to "Routines" (feature-freeze
        # hardening initiative, D2).
        "a7f4d921a378a5044680db39f679cf66eba3cef7369179d5056cc99139c246e6",
        # Before the 2026-08-06 Appendix D disclosure fix for the one explicit
        # digest-protected MCP Canvas-group write.
        "c8a48dea97670303c973422218bebaf2b65f87e64691a71dc647891ab71f9978",
        # Before Appendix A described the app's private Python environment and
        # Appendix D described resolving a shared section name by section_id.
        "83d68d6cdb03eb4dbe009eceefec41a89f64447e8b1c6023502a9d887eecba2a",
        # Before the classroom display and its Panel theme tools were removed.
        "3f2e05954005949ba2116bb71ccc72f776b10b704d8128bf676cb419a3cf34fc",
        # Before the CORE write rule was corrected: it had claimed the
        # assistant never writes to Canvas at all, which the bounded New Quiz
        # and SIS bridge operations contradict.
        "19918f641efaab0447e756361de3eed45c065b4974398726568339592bf07898",
        # Same correction, one release earlier: the version that shipped
        # between the display removal and the guide scoping. A workspace
        # seeded in between holds this one, and an unlisted hash reads as
        # "the teacher edited it", so without this the fix never lands.
        "c01072b33ce1e844244abebad374201c645ba6a72d24f782204cacfce696d07e",
        # Before the CORE write rule stopped denying the direct-write path.
        # Staging is the default, and a teacher who asks for a direct write
        # gets one; "never write to Canvas on your own" only softened the
        # denial instead of correcting it.
        "cfbcb2e652948cb18ba2ebd1d22e6ffbba79757a9c50a3d4e0f44bd14286e962",
        # Before the direct write stopped being gated behind a second ask.
        # The teacher asking for the write is the authorization.
        "1da24d0600b3c50fd7ef78dadc22327dfdd4c38a2cca2d2adde2d2a36000d36a",
        # Before CORE named the scoring write path. It had said a teacher
        # could have "scores" written straight to Canvas, which promises
        # generic grade posting; only New Quiz item scores can go.
        "183bac2d77afc38c98c79def8cada375f7c9dea1afdb9fc15eae80adf54da931",
        # Before a staged draft could be landed from the chat. Appendix D said
        # authored content always waits in the UI for the teacher to push it,
        # which the content push pair contradicts.
        "d6898fbb9e3ebdf9b171eccc8789f1f492c848cabd76f7f04902eccfadfd60c7",
    }),
    # Named the removed classroom display alongside Calendar.
    "Author a Class Schedule.txt": frozenset({
        "b65df7567b0b26b29aa23c4c58ea4432767d8a7a05fb7987059fda9a30536275",
    }),
}


def _shipped_hash(raw):
    """Hash with line endings normalised.

    A Windows checkout stores these files CRLF while the committed blob is LF,
    so the same shipped text has two different raw byte hashes. Comparing
    without normalising would never match and would quietly retire nothing.
    """
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _retire_superseded(target_dir):
    """Delete superseded seeded files the teacher has not modified."""
    removed = []
    for name, shipped_hashes in RETIRED_FILES.items():
        path = os.path.join(target_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                if _shipped_hash(f.read()) not in shipped_hashes:
                    continue
        except OSError:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        removed.append(path)
    return removed


def _write_text_if_missing(path, text):
    """Seed a file once and preserve teacher edits on later runs."""
    if os.path.exists(path):
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def _copy_tree_if_missing(source_dir, dest_dir):
    """Seed every file under source_dir into dest_dir, preserving teacher edits."""
    written = []
    if not os.path.isdir(source_dir):
        return written
    for root, _, files in os.walk(source_dir):
        rel_dir = os.path.relpath(root, source_dir)
        target_root = dest_dir if rel_dir == "." else os.path.join(dest_dir, rel_dir)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            src = os.path.join(root, name)
            dest = os.path.join(target_root, name)
            if os.path.exists(dest):
                continue
            shutil.copy2(src, dest)
            written.append(dest)
    return written


def build_library(target_dir):
    """Seed the AI Authoring library, then retire files superseded by newer ones."""
    os.makedirs(target_dir, exist_ok=True)
    _retire_superseded(target_dir)
    return _copy_tree_if_missing(DEFAULT_AI_TA_DIR, target_dir)
