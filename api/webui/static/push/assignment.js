(function () {
  "use strict";

  var push = window.CE_PUSH || {};
  var ready = [
    "postForm",
    "showLog",
    "hideBanner",
    "moduleChoice",
    "pushContent",
  ].every(function (name) { return typeof push[name] === "function"; }) &&
    typeof window.localToISO === "function";

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
        var points = s.points == null ? "points required" : s.points + " pts";
        log((d.ok ? "✓ VALID" : "✗ INVALID") + " — " + s.type + ': "' + s.title + '" (' + points + ")");
        log("  submission: " + (s.submission_types || []).join(", "));
        log("  directions: " + s.directions + "; sections: " + s.sections +
            (s.rubric ? "; rubric" : "") + (s.supports ? "; supports" : ""));
        if (s.tiers.length) {
          s.tiers.forEach(function (t) {
            log("  tier " + t.label + " (content variant)" +
              (t.overrides && t.overrides.length ? " (overrides " + t.overrides.join(", ") + ")" : "") +
              (t.supports ? " (tier supports)" : ""));
          });
        } else {
          log("  whole-class (no tiers)");
        }
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
