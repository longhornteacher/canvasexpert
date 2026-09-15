(function () {
  "use strict";

  // Slice D of author-and-stage: shows assistant-staged Inbox drafts (Slice C's
  // marker-gated per-kind folders) as a distinct "pending review" section on
  // each push tab. Fetches /api/inbox-files?kind=<kind> (pre-validated by the
  // same validators the existing /api/validate, /api/af/validate, /api/pf/validate
  // routes use), then "Use this draft" hands a valid draft's
  // path to the matching <select>'s existing file_sources.js seam
  // (wrapper.ceFileSource.setTempOption/setMode) so the teacher runs the
  // existing Validate / Push controls unchanged.

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderList(listEl, files) {
    listEl.innerHTML = files.map(function (f) {
      var status = f.ok
        ? '<span class="inbox-pending-status ok">Valid</span>'
        : '<span class="inbox-pending-status err">Needs fixes</span>';
      var problems = (!f.ok && f.problems && f.problems.length)
        ? '<ul class="inbox-pending-problems">' +
          f.problems.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") +
          "</ul>"
        : "";
      var action = f.ok
        ? '<button type="button" class="small inbox-pending-use" data-path="' +
          esc(f.path) + '" data-label="' + esc(f.label) + '">Use this draft</button>'
        : "";
      return '<div class="inbox-pending-row">' +
        '<div class="inbox-pending-label">' + esc(f.label) + " " + status + "</div>" +
        problems + action +
        "</div>";
    }).join("");
  }

  function initInboxSection(root) {
    if (!root || root.dataset.inboxBound === "1") return;
    root.dataset.inboxBound = "1";

    var kind = root.dataset.inboxKind;
    var selectId = root.dataset.inboxSelect;
    var listEl = root.querySelector(".inbox-pending-list");
    var refreshBtn = root.querySelector(".inbox-pending-refresh");
    if (!kind || !listEl) return;

    var lastLoad = 0;
    var requestGeneration = 0;

    function setState(state) {
      root.dataset.inboxState = state;
    }

    function renderDegraded() {
      root.hidden = false;
      setState("degraded");
      listEl.innerHTML = '<p class="inbox-pending-degraded">Staged drafts couldn\'t be checked. Paste or upload still works.</p>';
    }

    function load() {
      lastLoad = Date.now();
      var generation = ++requestGeneration;
      if (root.dataset.inboxState === "degraded" || root.hidden) root.hidden = true;
      setState("loading");
      fetch("/api/inbox-files?kind=" + encodeURIComponent(kind))
        .then(function (r) {
          if (!r.ok) throw new Error("inbox request failed");
          return r.json();
        })
        .then(function (d) {
          if (generation !== requestGeneration) return;
          if (!d || d.ok !== true || !Array.isArray(d.files)) throw new Error("invalid inbox response");
          if (!d.files.length) {
            listEl.innerHTML = "";
            root.hidden = true;
            setState("empty");
            return;
          }
          renderList(listEl, d.files);
          root.hidden = false;
          setState("ready");
        })
        .catch(function () {
          if (generation !== requestGeneration) return;
          renderDegraded();
        });
    }

    if (refreshBtn) refreshBtn.addEventListener("click", load);

    // A teacher stages a draft in Claude or ChatGPT and alt-tabs back, so look
    // again on focus rather than making them reload the page.
    window.addEventListener("focus", function () {
      if (Date.now() - lastLoad > 3000) load();
    });

    listEl.addEventListener("click", function (event) {
      var btn = event.target.closest(".inbox-pending-use");
      if (!btn) return;
      var sel = selectId && document.getElementById(selectId);
      var wrapper = sel && sel.closest(".file-source");
      if (!wrapper || !wrapper.ceFileSource) return;
      wrapper.ceFileSource.setTempOption(btn.dataset.path, "Assistant draft: " + btn.dataset.label);
      wrapper.ceFileSource.setMode("select");
    });

    load();
  }

  function bindInboxSections() {
    document.querySelectorAll("[data-inbox-kind]").forEach(initInboxSection);
  }

  window.CE_PUSH = Object.assign(window.CE_PUSH || {}, {
    initInboxSection: initInboxSection,
    bindInboxSections: bindInboxSections,
  });
})();
