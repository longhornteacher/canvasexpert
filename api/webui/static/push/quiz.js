(function () {
  "use strict";

  var push = window.CE_PUSH || {};
  var ready = [
    "postForm",
    "showLog",
    "hideBanner",
    "showBanner",
    "setBusy",
    "pushContent",
    "currentCourseId",
    "targetCourses",
    "collectSettings",
    "generatePhysical",
  ].every(function (name) { return typeof push[name] === "function"; });

  var fileSel = document.getElementById("quizfile");
  var result = document.getElementById("result");
  var variantRows = document.getElementById("variant-rows");
  var fileOptions = window.QF_QUIZ_FILES || window.QF_FILES || [];

  function requireReady() {
    if (ready) return true;
    alert("Quiz push controls did not load correctly. Refresh Canvas Expert and try again.");
    return false;
  }

  function resetCourseGroups() {
    // Kept as a no-op for the shared course picker. Differentiated delivery
    // no longer depends on Roster group-set state.
  }

  function addVariantRow(path) {
    var row = document.createElement("div");
    row.className = "variant-row";
    var fileWrap = document.createElement("label");
    fileWrap.textContent = "Tier quiz file";
    var fileSl = document.createElement("select");
    fileSl.className = "variant-file";
    fileSl.appendChild(Object.assign(document.createElement("option"), { value: "", textContent: "— select —" }));
    fileOptions.forEach(function (f) {
      var o = document.createElement("option");
      o.value = f.path; o.textContent = f.label;
      if (f.path === path) o.selected = true;
      fileSl.appendChild(o);
    });
    fileWrap.appendChild(fileSl);
    row.appendChild(fileWrap);
    var rm = document.createElement("button");
    rm.type = "button"; rm.className = "small danger variant-remove";
    rm.textContent = "✕"; rm.title = "Remove this row";
    rm.addEventListener("click", function () { row.remove(); });
    row.appendChild(rm);
    variantRows?.appendChild(row);
  }

  function rebuildVariantRows() {
    var existing = Array.from(variantRows?.querySelectorAll(".variant-row") || []).map(function (r) {
      return {
        path: r.querySelector(".variant-file")?.value || "",
      };
    });
    if (variantRows) variantRows.innerHTML = "";
    if (existing.length) {
      existing.forEach(function (e) { addVariantRow(e.path); });
    } else {
      addVariantRow("");
    }
  }

  window.CE_QUIZ = { resetCourseGroups: resetCourseGroups };

  document.getElementById("btn-validate")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var path = fileSel?.value;
    if (!path) return alert("Pick a quiz file first.");
    var log = push.showLog(result);
    push.hideBanner(document.getElementById("push-banner"));
    log("Validating…");
    var data = await push.postForm("/api/validate", { path: path });
    if (data.error) { log("ERROR: " + data.error); return; }
    var advisoryLines = (data.advisories || []).map(function (a) { return "  ! " + a; });
    var lines;
    if (data.ok) {
      lines = ["✓ No compliance issues found."].concat(advisoryLines);
      log(lines.join("\n"));
      push.showBanner(document.getElementById("push-banner"), "ok", "✓ Validation passed — quiz is ready to push.");
    } else {
      lines = data.problems.map(function (p) { return "  ✗ " + p; }).concat(advisoryLines);
      log(lines.join("\n"));
      push.showBanner(document.getElementById("push-banner"), "fail",
        "✗ " + data.problems.length + " issue(s) found — see log above.");
    }
  });

  document.getElementById("btn-preview")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var id = push.currentCourseId();
    var path = fileSel?.value;
    if (!id) return alert("Choose a course first.");
    if (!path) return alert("Pick a quiz file first.");
    var log = push.showLog(result);
    push.hideBanner(document.getElementById("push-banner"));
    log("Building dry-run preview (no live Canvas calls)…");
    push.setBusy(true);
    try {
      var settings = push.collectSettings();
      var data = await push.postForm("/api/push/preview", { course_id: id, path: path, settings: settings });
      log(data.error ? "ERROR: " + data.error : data.output);
    } finally {
      push.setBusy(false);
    }
  });

  document.getElementById("btn-push")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var targets = push.targetCourses();
    var path = fileSel?.value;
    if (!targets.length) return alert("Check at least one course on the right.");
    if (!path) return alert("Pick a quiz file first.");
    var settingsObj = {};
    try {
      settingsObj = JSON.parse(push.collectSettings() || "{}");
    } catch (e) {
      return alert("Quiz delivery settings could not be read. Refresh and try again.");
    }
    var wantPhysical = document.getElementById("physical-version")?.checked;
    push.setBusy(true);
    try {
      var applied = await push.pushContent(
        "qf",
        { mode: "whole", path: path, settings: settingsObj },
        result,
        document.getElementById("push-banner"),
        this,
        "Create this QuizForge quiz in Canvas."
      );
      if (applied && applied.status === "applied" && wantPhysical) {
        var physicalLog = function (line) {
          result.textContent += line + "\n";
          result.scrollTop = result.scrollHeight;
        };
        await push.generatePhysical(path, physicalLog, document.getElementById("push-banner"));
      }
    } finally {
      push.setBusy(false);
    }
  });

  document.getElementById("btn-add-variant")?.addEventListener("click", function () {
    addVariantRow("", "");
  });

  document.getElementById("btn-push-variants")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var targets = push.targetCourses();
    if (!targets.length) return alert("Check at least one course on the right.");
    var rows = variantRows ? Array.from(variantRows.querySelectorAll(".variant-row")) : [];
    var variants = rows.map(function (r) {
      return {
        path: r.querySelector(".variant-file")?.value || "",
      };
    }).filter(function (v) { return v.path; });
    if (variants.length < 2) return alert("Add at least 2 tier rows with files selected.");
    var settingsObj = {};
    try {
      settingsObj = JSON.parse(push.collectSettings() || "{}");
    } catch (e) {
      return alert("Quiz delivery settings could not be read. Refresh and try again.");
    }
    push.setBusy(true);
    try {
      await push.pushContent(
        "qf",
        { mode: "differentiated", variants: variants, settings: settingsObj },
        document.getElementById("variants-result"),
        document.getElementById("variants-banner"),
        this,
        "Create these differentiated QuizForge quizzes in Canvas."
      );
    } finally {
      push.setBusy(false);
    }
  });
})();
