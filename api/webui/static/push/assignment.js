(function () {
  "use strict";

  var push = window.CE_PUSH || {};
  var ready = [
    "postForm",
    "showLog",
    "hideBanner",
    "moduleChoice",
    "pushContent",
    "syncRubricControls",
  ].every(function (name) { return typeof push[name] === "function"; }) &&
    typeof window.localToISO === "function";

  var afRubricSel = document.getElementById("af-rubric");
  var afRubricMode = document.getElementById("af-rubric-mode");
  var afRubricLink = document.getElementById("af-rubric-link");

  function requireReady() {
    if (ready) return true;
    alert("AssignmentForge push controls did not load correctly. Refresh Canvas Expert and try again.");
    return false;
  }

  function getLog() {
    return document.getElementById("af-log");
  }

  function getBanner() {
    return document.getElementById("af-banner");
  }

  afRubricSel?.addEventListener("change", function () {
    if (!requireReady()) return;
    push.syncRubricControls();
  });

  document.getElementById("btn-af-validate")?.addEventListener("click", function () {
    if (!requireReady()) return;
    var path = document.getElementById("af-file")?.value;
    if (!path) return alert("Pick an AssignmentForge file.");
    var log = push.showLog(getLog());
    push.hideBanner(getBanner());
    log("Validating…\n");
    push.postForm("/api/af/validate", { path: path }).then(function (d) {
      // A failed request comes back as {ok:false, error} with no problems and
      // no summary, so without this the log would just stop at "Validating…".
      if (d.error) { log("ERROR: " + d.error); return; }
      if (d.problems?.length) d.problems.forEach(function (p) { log("✗ " + p); });
      var s = d.summary;
      if (s) {
        log((d.ok ? "✓ VALID" : "✗ INVALID") + " — " + s.type + ': "' + s.title + '" (' + s.points + " pts)");
        log("  submission: " + s.submission_types.join(", "));
        if (s.tiers.length) {
          s.tiers.forEach(function (t) {
            log("  tier " + t.label + " (content variant)" +
              (t.scaffolded ? " (scaffolded)" : ""));
          });
        } else {
          log("  whole-class (no tiers)");
        }
        (s.placeholders || []).forEach(function (p) {
          log("  placeholder {{" + p + "}} — resolved per course at push");
        });
      }
    }).catch(function (e) { log("ERROR: " + e); });
  });

  document.getElementById("btn-af-push")?.addEventListener("click", function () {
    if (!requireReady()) return;
    var path = document.getElementById("af-file")?.value;
    if (!path) return alert("Pick an AssignmentForge file.");
    var payload = {
      path: path,
      post_to_sis: document.getElementById("af-sis")?.checked,
      published: document.getElementById("af-publish")?.checked,
    };
    if (afRubricSel?.value) {
      payload.rubric_path = afRubricSel.value;
      payload.rubric_mode = afRubricMode?.value || "grading";
      payload.rubric_link_page = afRubricLink?.checked !== false;
    }
    var due = window.localToISO(document.getElementById("af-due")?.value);
    var unlock = window.localToISO(document.getElementById("af-unlock")?.value);
    var lock = window.localToISO(document.getElementById("af-lock")?.value);
    if (due) payload.due_at = due;
    if (unlock) payload.unlock_at = unlock;
    if (lock) payload.lock_at = lock;
    var agSel = document.getElementById("af-aggroup");
    if (agSel?.value) payload.assignment_group_name = agSel.selectedOptions[0].text;
    var mod = push.moduleChoice("af-module");
    if (mod) payload.module_name = mod;
    push.pushContent("af", payload,
      getLog(), getBanner(), this,
      "Create this assignment in Canvas.");
  });
})();
