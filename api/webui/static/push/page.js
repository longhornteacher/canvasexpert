(function () {
  "use strict";

  var push = window.CE_PUSH || {};
  var ready = ["postForm", "showLog", "hideBanner", "moduleChoice", "pushContent", "prepareOnly"]
    .every(function (name) { return typeof push[name] === "function"; });

  function requireReady() {
    if (ready) return true;
    alert("PageForge push controls did not load correctly. Refresh Canvas Expert and try again.");
    return false;
  }

  function getLog() {
    return document.getElementById("pf-log");
  }

  function getBanner() {
    return document.getElementById("pf-banner");
  }

  document.getElementById("btn-pf-validate")?.addEventListener("click", function () {
    if (!requireReady()) return;
    var path = document.getElementById("pf-file")?.value;
    if (!path) return alert("Pick a PageForge file.");
    var log = push.showLog(getLog());
    push.hideBanner(getBanner());
    log("Validating…\n");
    push.postForm("/api/pf/validate", { path: path })
      .then(function (d) {
        // A failed request comes back as {ok:false, error} with no problems and
        // no summary, so without this the log would just stop at "Validating…".
        if (d.error) { log("ERROR: " + d.error); return; }
        (d.problems || []).forEach(function (p) { log("✗ " + p); });
        var s = d.summary;
        if (s) {
          log((d.ok ? "✓ VALID" : "✗ INVALID") + ' — ' + s.type + ': "' + s.title + '"');
          log("  layout: " + s.layout + "; sections: " + s.sections +
              "; extras: " + s.extras);
        }
      })
      .catch(function (e) { if (log) log("ERROR: " + e); });
  });

  document.getElementById("btn-pf-push")?.addEventListener("click", function () {
    if (!requireReady()) return;
    var path = document.getElementById("pf-file")?.value;
    if (!path) return alert("Pick a PageForge file.");
    var payload = {
      path: path,
      published: document.getElementById("pf-publish")?.checked,
    };
    var mod = push.moduleChoice("pf-module");
    if (mod) payload.module_name = mod;
    push.pushContent("pf", payload, getLog(), getBanner(), this,
      "Create this page in Canvas.");
  });

  document.getElementById("btn-pf-prepare")?.addEventListener("click", function () {
    if (!requireReady()) return;
    var path = document.getElementById("pf-file")?.value;
    if (!path) return alert("Pick a PageForge file.");
    var payload = {
      path: path,
      published: document.getElementById("pf-publish")?.checked,
    };
    var mod = push.moduleChoice("pf-module");
    if (mod) payload.module_name = mod;

    push.prepareOnly("content.page", payload, getLog(), getBanner(), this);
  });
})();
