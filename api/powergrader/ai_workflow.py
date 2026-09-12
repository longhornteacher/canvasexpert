"""Build a private session and its SAFE bundle for chat-first scoring."""

from api import feedback_pipeline as fp
from api import feedback_safety
from api.platform_services import config, workspace
from api.webui import source_materials
from api.powergrader import ai_workflow_support, context, privacy


def run_ai_workflow(
    *,
    mode: str,
    submitted: list[dict],
    assignment_name: str,
    assignment_description: str,
    course_id: str,
    course_name: str = "",
    assignment_id: str,
    session_id: str,
    rubric_name: str,
    persona_id: str,
    source_text: str = "",
    source_files_json: str = "",
    source_uploads=None,
    rubric_text_override: str | None = None,
    source_context_override: dict | None = None,
    artifact_assignment_name: str | None = None,
) -> dict:
    """Create the local SAFE/private artifacts; never call an external model.

    Legacy keyword parameters remain accepted by the internal start-workflow
    caller, but they do not select or authorize any hosted grader.
    """
    privacy_steps: list[dict] = []
    privacy_artifacts: dict = {}
    artifact_name = artifact_assignment_name or assignment_name
    ai_by_uid: dict = {}
    ai_item_by_uid: dict = {}
    ai_failures: dict = {}

    if mode != "packet":
        return ai_workflow_support.workflow_result(
            ok=False,
            error="Scoring Sessions prepare a SAFE packet for the teacher’s connected agent.",
        )

    workspace.ensure_workspace()
    try:
        source_context = source_context_override or context.build_source_context(
            source_text, source_files_json, source_uploads, strict=True
        )
    except Exception as exc:
        return ai_workflow_support.workflow_result(
            ok=False,
            error=f"Could not read selected source material: {exc}",
            privacy_steps=privacy_steps,
        )

    source_warning_text = "; ".join(source_materials.context_warnings(source_context))
    ai_workflow_support.append_source_context_step(
        privacy_steps, privacy.privacy_step, source_context, source_warning_text
    )

    vault = context.vault()
    with vault.transaction():
        bundle = fp.pseudonymize_submissions(submitted, vault, artifact_name)
        bundle = context.apply_shared_context(bundle, assignment_description, source_context)
        verdict = feedback_safety.scan_payload(bundle, vault) if bundle.get("students") else None

    if not bundle.get("students"):
        privacy_steps.append(privacy.privacy_step(
            "pseudonymize", "Prepared eligible responses", "warn",
            "No submitted responses were eligible for the SAFE packet.",
        ))
    else:
        privacy_steps.append(privacy.privacy_step(
            "pseudonymize", "Assigned pseudonyms and separated identities", "ok",
            f"{len(bundle['students'])} pseudonymized response(s); real names remain in the local vault.",
        ))
        privacy_steps.append(privacy.privacy_step(
            "safety_scan", "Checked pseudonymized response data", "warn" if not verdict["green"] else "ok",
            "Potential identifying text will be scrubbed or held before packet delivery."
            if not verdict["green"] else "No hard identity matches were found before the final scrub.",
        ))

    rubric_text = (
        rubric_text_override if rubric_text_override is not None
        else context.load_rubric_text(rubric_name)
    )
    persona = config.get_persona(persona_id) if persona_id else {
        "name": "", "personality": "", "signoff_policy": "none", "signoff_text": "",
    }
    try:
        safe_dir, private_dir = privacy.feedback_artifact_dirs(
            course_name=course_name or course_id,
            course_id=course_id,
            assignment_name=artifact_name,
            assignment_id=assignment_id,
            mode="scoring-session",
        )
    except workspace.TeacherVisiblePathBudgetError:
        return ai_workflow_support.workflow_result(
            ok=False,
            error="The workspace location is too deep for private scoring-session artifacts.",
            privacy_steps=privacy_steps,
        )
    if not safe_dir or not private_dir:
        return ai_workflow_support.workflow_result(
            ok=False,
            error="Could not resolve the private scoring-session folders; finish workspace setup first.",
            privacy_steps=privacy_steps,
        )

    write_result = fp.write_safe_and_private(
        bundle,
        vault,
        safe_dir,
        private_dir,
        ai_ta_name=persona.get("name") or "your teaching assistant",
        persona=persona,
        protected=config.active_protected_names(),
        submissions=submitted,
        rubric_text=rubric_text,
    )
    if not write_result.get("safe_bundle"):
        return ai_workflow_support.workflow_result(
            ok=False,
            error="SAFE packet validation failed; no scoring session was exposed.",
            privacy_steps=privacy_steps,
        )

    privacy_artifacts = ai_workflow_support.build_privacy_artifacts(
        write_result, safe_dir, private_dir
    )
    for hold in write_result.get("media_holds") or []:
        who = vault.reverse(str(hold.get("pseudonym") or ""))
        if who and who.get("canvas_id"):
            ai_failures[str(who["canvas_id"])] = {
                "code": "media_oral_reading_hold",
                "message": str(hold.get("message") or "Media evidence requires teacher review."),
            }
    privacy_steps.append(privacy.privacy_step(
        "safe_private", "Saved SAFE and private scoring artifacts", "ok",
        "The SAFE bundle is ready for the connected agent; identity mappings remain private.",
    ))
    return ai_workflow_support.workflow_result(
        ok=True,
        privacy_steps=privacy_steps,
        privacy_artifacts=privacy_artifacts,
        ai_by_uid=ai_by_uid,
        ai_item_by_uid=ai_item_by_uid,
        ai_failures=ai_failures,
        source_context=source_context,
    )
