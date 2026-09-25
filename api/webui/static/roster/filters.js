(function () {
  "use strict";

  var roster = window.CE_ROSTER || {};
  var ready = [
    "getStudents",
    "setFilteredStudents",
    "renderTable",
    "hasLoadedCourse",
    "onCourseLoaded"
  ].every(function (name) { return typeof roster[name] === "function"; });

  if (!ready) {
    window.alert("Roster filter tools need a page refresh.");
    return;
  }

  var shell = document.getElementById("roster-workbench");
  var searchInput = document.getElementById("roster-search");
  var lensBtns = document.querySelectorAll(".roster-lens-btn");
  var safetyCard = document.getElementById("roster-safety-card");
  if (!shell || !searchInput || !lensBtns.length) {
    return;
  }

  var lensConfig = {
    students: { filter: "all", focus: "" },
    accommodations: { filter: "extra_time", focus: "extra-time" },
    monitoring: { filter: "monitored", focus: "monitoring" },
    issues: { filter: "warnings", focus: "issues" },
    privacy: { filter: null, focus: "privacy" },
    reports: { filter: null, focus: "reports" }
  };
  var activeLens = "students";
  var activeFilter = "all";

  function filterStudents() {
    var students = roster.getStudents() || [];
    var q = (searchInput.value || "").toLowerCase().trim();
    var filtered = [];

    for (var i = 0; i < students.length; i++) {
      var s = students[i];
      if (q) {
        var haystack = (s.name + " " + s.display_name + " " + s.short_name + " " +
          (s.nicknames || []).join(" ") + " " + (s.pseudonym || "") + " " +
          ((s.monitored && s.monitored.note) || "")).toLowerCase();
        if (haystack.indexOf(q) === -1) continue;
      }
      if (activeFilter === "extra_time" && !s.extra_time.enabled) continue;
      if (activeFilter === "monitored" && !s.monitored.enabled) continue;
      if (activeFilter === "warnings" && (!s.warnings || s.warnings.length === 0)) continue;
      filtered.push(s);
    }

    roster.setFilteredStudents(filtered);
    roster.renderTable();
    return filtered;
  }

  function focusLensPanel() {
    var target = null;
    if (activeLens === "privacy") {
      if (safetyCard && !safetyCard.hidden) safetyCard.open = true;
      target = safetyCard;
    }
    if (target && !target.hidden) {
      var summary = target.querySelector("summary");
      if (summary && typeof summary.focus === "function") summary.focus({ preventScroll: true });
    }
  }

  function syncLensButtons() {
    for (var i = 0; i < lensBtns.length; i++) {
      var active = lensBtns[i].dataset.lens === activeLens;
      lensBtns[i].classList.toggle("active", active);
      lensBtns[i].setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function updateUrl() {
    var params = new URLSearchParams(window.location.search);
    var focus = lensConfig[activeLens].focus;
    if (focus) params.set("focus", focus);
    else params.delete("focus");
    var query = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (query ? "?" + query : "") + window.location.hash);
  }

  function setLens(lens, updateHistory) {
    if (!lensConfig[lens]) lens = "students";
    activeLens = lens;
    if (lensConfig[lens].filter) activeFilter = lensConfig[lens].filter;
    shell.dataset.rosterLens = lens;
    syncLensButtons();
    if (updateHistory) updateUrl();
    filterStudents();
    if (roster.hasLoadedCourse()) focusLensPanel();
  }

  function lensFromQuery() {
    var focus = new URLSearchParams(window.location.search).get("focus") || "";
    var map = {
      "extra-time": "accommodations",
      monitoring: "monitoring",
      issues: "issues",
      privacy: "privacy",
      reports: "reports"
    };
    return map[focus] || "students";
  }

  searchInput.addEventListener("input", filterStudents);
  for (var i = 0; i < lensBtns.length; i++) {
    lensBtns[i].addEventListener("click", function () {
      setLens(this.dataset.lens, true);
    });
  }

  roster.applyFilters = filterStudents;
  roster.onCourseLoaded(focusLensPanel);
  setLens(lensFromQuery(), false);
})();
