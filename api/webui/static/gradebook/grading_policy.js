(function () {
  "use strict";

  var gb = window.CE_GRADEBOOK || {};
  var ready = ["postForm", "esc", "gbCourseId", "gbCourseName", "_markLoaded", "_needsLoad"]
    .every(function (name) { return typeof gb[name] === "function"; });

  function requireReady() {
    if (ready) return true;
    alert("Gradebook controls did not load correctly. Refresh Canvas Expert and try again.");
    return false;
  }

  // Switching courses re-triggers this loader (see gradebook.js's
  // _autoloadTab) without cancelling a slower earlier request. Stamp each
  // request and let only the newest one write, the same way policy.js does.
  var loadGeneration = 0;

  function _renderWarnings(warnings) {
    var el = document.getElementById("gp-warnings");
    if (!el) return;
    el.innerHTML = "";
    (warnings || []).forEach(function (w) {
      var li = document.createElement("li");
      li.textContent = w;
      el.appendChild(li);
    });
    el.hidden = !warnings || warnings.length === 0;
  }

  // ── Grading policy (auto-loaded alongside the Late Policy panel) ───────

  window.CE_GRADEBOOK.loadGradingPolicy = async function _loadGradingPolicy() {
    if (!requireReady()) return;
    var id = gb.gbCourseId();
    if (!id) return;
    var generation = ++loadGeneration;
    gb._markLoaded("grading-policy");
    var st = document.getElementById("gp-status");
    if (st) { st.className = "status hint"; st.textContent = "Loading…"; }
    try {
      var response = await Promise.all([
        fetch("/api/grading-policy?course_id=" + encodeURIComponent(id)).then(function (r) { return r.json(); }),
        fetch("/api/no-school-dates").then(function (r) { return r.json(); }),
      ]);
      if (generation !== loadGeneration) return;
      var policyResp = response[0], datesResp = response[1];
      if (!policyResp.ok) {
        if (st) { st.className = "status error"; st.textContent = "Error: " + policyResp.error; }
        return;
      }
      var p = policyResp.policy;
      document.getElementById("gp-floor").value = p ? p.floor_percent : 30;
      document.getElementById("gp-missing").value = p ? p.missing_percent : 20;
      document.getElementById("gp-sweep").value = p ? p.sweep_after_school_days : 15;
      document.getElementById("gp-no-school-dates").value = (datesResp.dates || []).join("\n");
      _renderWarnings(policyResp.warnings);
      if (st) {
        st.className = "status hint";
        st.textContent = p
          ? "Loaded the grading policy for " + gb.gbCourseName() + "."
          : gb.gbCourseName() + " has no grading policy yet. Set one below.";
      }
    } catch (e) {
      if (generation !== loadGeneration) return;
      if (st) { st.className = "status error"; st.textContent = "Could not load the grading policy. Try again."; }
    }
  };

  document.getElementById("btn-save-grading-policy")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var id = gb.gbCourseId();
    if (!id) return alert("Pick a course first.");
    var floor = document.getElementById("gp-floor").value;
    var missing = document.getElementById("gp-missing").value;
    var sweep = document.getElementById("gp-sweep").value;
    var dates = document.getElementById("gp-no-school-dates").value
      .split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
    var st = document.getElementById("gp-status");
    var button = this;
    button.disabled = true;
    try {
      var saved = await gb.postForm("/api/grading-policy", {
        course_id: id,
        policy: JSON.stringify({
          floor_percent: parseInt(floor, 10),
          missing_percent: parseInt(missing, 10),
          sweep_after_school_days: parseInt(sweep, 10),
        }),
      });
      if (!saved.ok) {
        st.className = "status error"; st.textContent = "Error: " + saved.error;
        return;
      }
      var datesSaved = await gb.postForm("/api/no-school-dates", { dates: JSON.stringify(dates) });
      if (!datesSaved.ok) {
        st.className = "status error"; st.textContent = "Error: " + datesSaved.error;
        return;
      }
      _renderWarnings(saved.warnings);
      st.className = "status ok";
      st.textContent = "✓ Saved grading policy for " + gb.gbCourseName() + ".";
    } finally {
      button.disabled = false;
    }
  });

})();
