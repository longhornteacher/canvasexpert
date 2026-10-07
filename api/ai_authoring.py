"""AI Authoring library builder: seeds a teacher's workspace with the paste-ready
assistant reference and authoring files that live at ``api/default_docs/AI Authoring/``.

That folder is the sole repository source for these files -- every "Author a ...",
START HERE and Reference file there is already the exact
paste-ready text a teacher pastes into an AI assistant. This module never
regenerates that text; it only seeds it into a teacher's workspace (once, never
overwriting an edit).

Pure module: builds plain-text output files only. The web UI / server owns the
HTTP routes and startup hook.
"""
import hashlib
import os
import shutil

from api import runtime_paths


API_DIR = runtime_paths.API_DIR
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
    # Same-name authoring/reference updates and the retired objectives
    # contract; replace only old shipped bytes and preserve teacher edits. The
    # reference entry is nested under Reference/.
    "Author an Assignment (AssignmentForge).txt": frozenset({
        # Before the console charter and removal of generated printables.
        "936f2bd4688abc25e8d519009b751f44715791fc27843360753665121e48713b",
        "9194737963ee24d8de9e65990bd2aa9f58eb88abffde4c5643cacaf0a2daca7e",
        # Before the required private correction package guidance.
        "2581440c2cd2720125390d5d7ff3c84ba2979d8e668e68936bc0a21f846f53d3",
    }),
    "Author a Page (PageForge).txt": frozenset({
        "deafeb6f8f4d6be6dc3324f278099155e1863ec45676218ebb4ae95163830d53",
    }),
    "Author a Quiz (QuizForge).txt": frozenset({
        # Before the role line stopped naming the retired QTI export.
        "631eaa6e04d27c90b77472fc27f1975ae489feb7530c33697a8e2d526e5fbb33",
        # Before the console charter and removal of generated printables.
        "7db317a92a6208833c8e8e8fc238798375bd0fa30d2557f31425ee43a7d962e4",
        "5f74c61040b83f5b3f43243adf5d424ab2c9037a14fa40d7f9232ca4d99fc181",
        # Before agent-directed quiz delivery settings.
        "28cab71bec3b8e8005ca7cbf6110d940a8d9437d78915adb00e02c72663fd1f5",
    }),
    "Reference/QF_REF_Stimulus_Formatting.md": frozenset({
        "8a33159cd3565e7b96a10b7196974a00a1d0c55e621d5681d9205835846297bf",
    }),
    # Learning Objectives authoring contract was retired and no longer ships.
    "Author a Learning Objective.txt": frozenset({
        "4cc0d2265f8fc2411b40fb57800f9c701b82cb16bcf2c8ef12c58687d9bdc5a6",
    }),
    # Superseded by "START HERE - CanvasAgent.txt".
    "START HERE - Canvas Expert.txt": frozenset({
        "d7b59318f61d733380349846b948858a98ad06aed969ba15f2b8eeceea5d6eed",
    }),
    # Indexed the file above, so an unedited copy is stale the moment it goes.
    "About This Folder.txt": frozenset({
        "e637eda43b8124b8ba166e272130a16b79716309b6531148c11561e8d6715f32",
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
        # Before the console charter and removal of generated printables.
        "09748aad91afbb4215ad61dc86122e7fdb1f4a9a584b1e96038a6a675acb9a25",
        # Before the MCP lean surface and feedback-revision scoring mode.
        "23e29b3a7df5e0994c3a17a78c2e0fc1640c597509114fb931c9f701eb895ed2",
        # Before routines, writing history, and learning objectives were retired.
        "9ec17496bab7b21bec1f18d6b1aca3f1b1a55fda4789bd7905bdea149446f9f2",
        # Before the local-first discovery and stage-then-explicit-apply workflow.
        "bd3fd6da937207228b588fff12b1705beec4fa62412f035c33f5886d87a95377",
        # First release, before the procedure-first rewrite.
        "66fb445401ff147e03b727d01d70e08f8337563f94d954ef6ac6fae9dfa0706b",
        # Procedure-first rewrite, before Appendix A on installing and running.
        "e5e4023c419e14de58339f32c3b6da5bafd81528aae91d41477640c5f27b21b6",
        # Before the scoring packet MCP tools were described.
        "94788ae4a8c063cd2e60f234e51a3f902e8fed8282b10caba80121135c8fb80b",
        # Before Panels and the Panel theme tools were described.
        "52f9755e202e51072cb687df1a0d1fa6b85d468dd371d7f8c30bd73e4c3656fe",
        # Before the historical connected-tool and capability appendix expansion.
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
        # Before assignment creation required private correction entries.
        "cadede6afdbe127bc9781385e1fce5d02a48532104c2e782f797397a4009398c",
        # Before CORE named the scoring write path. It had said a teacher
        # could have "scores" written straight to Canvas, which promises
        # generic grade posting; only New Quiz item scores can go.
        "183bac2d77afc38c98c79def8cada375f7c9dea1afdb9fc15eae80adf54da931",
        # Before a staged draft could be landed from the chat. Appendix D said
        # authored content always waits in the UI for the teacher to push it,
        # which the content push pair contradicts.
        "d6898fbb9e3ebdf9b171eccc8789f1f492c848cabd76f7f04902eccfadfd60c7",
        # Before the Quiz feedback-reference troubleshooting wording; still said
        # never to refresh an open session (a copy seeded from that release).
        "fbb080b9c8804121f4400bda13321320f003b260a6531107faf9d8d833f9d5b4",
        # Before refresh_scoring_session: said never to refresh an open session.
        "a62638a1f17733812e2d46c3c2639c2b4a5368b82f8b39d575e8d0a17bdf3ebe",
        # Before the agent refreshed on its own, showed a scoring preview before
        # any push, and could write teacher-only integrity commentary.
        "e62733fcdf1635a63cfdb109d4c14c52c0a75fbe57d05f88572869b4a03cbb54",
        # Before first-attempt late days, resubmission facts, and pushed-row corrections.
        "40c187d078f9772bc9500bbb3d628bdad5774558c61bcccb489e87e02e7785a7",
        # Superseded connected-workflow guides; refresh only unedited copies.
        "f396348bb3f2b5c47f726886a0e8063a2c5f743be257eff2125851601898833d",
        "5eae4b59cdef744346b81b0ec82456bf0ec546fcedd3e50ec6bb6243a21a1e32",
        "2758939a15fdce382b5cefefcc931e61dd9672216327503ec650dd229a0a6b20",
    }),
    # Said the agent must not draw integrity conclusions from the timeline; it now
    # may, in teacher-only agent commentary.
    "Writing Timeline (tracked assignments).txt": frozenset({
        # Before the retired console assignment view was dropped from the text.
        "7cf276c0b7e78cb28a0537eba33869d006c2a2a3939ceed7b3ef02f72fd82c8c",
        # Before the retired Students handwriting control was removed.
        "643763410cba72a0a1542e2068ce23c6144e13c6781698200eda917973ed0bc7",
        "7f69ea5e6f9171a2f2625b6fc7fed3e7fdb7cbe5253d816c0e249998dfb141c5",
    }),
    # Writing records and get_writing_history were retired; every shipped
    # version is listed so any unedited copy is removed.
    "Writing Record (longitudinal writing history).txt": frozenset({
        "f68f7a05a55690cdf5508999a118a1d4a687d7fb732cc00e7e5c653557291d7e",
        "2c851cd820d055113eeafd4fc23249abf35873002fd54656cb71151b6163909c",
        "79abb78507e8bc5dee89a6e41384586665228be821008e3af0ee109d45be03e4",
        "cab5e04457d488c4ab4f25ec7d85e004185413412508378b744cf4c525ae5237",
        "33867d18ea26d3bc93f798cfaa96f475615395dc1d8147d1f6fbf9464d07253c",
        "3bbc8706b8522303fd084b5a710480afeb74b4853e9df87082285e60a8392716",
        "14e1518a277e277c5477cd2ead375297752f9af9a79e386d97365aebedccaa4a",
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
        path = os.path.join(target_dir, os.path.normpath(name))
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
