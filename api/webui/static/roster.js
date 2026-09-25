/**
 * Roster bootstrap and shared state owner.
 *
 * Feature modules attach later under window.CE_ROSTER.
 */
(function () {
  "use strict";

  var courseSelect = document.getElementById("roster-course");
  var refreshBtn = document.getElementById("roster-refresh");
  var openCanvas = document.getElementById("roster-open-canvas");
  var statusEl = document.getElementById("roster-status");
  var tableCard = document.getElementById("roster-table-card");
  var safetyCard = document.getElementById("roster-safety-card");
  var changesCard = document.getElementById("roster-changes");

  var students = [];
  var filteredStudents = [];
  var selectedNameMap = {};
  var scoreMatrix = { columns: [], values_by_section: {} };
  var relationships = { by_section: {} };
  var rosterChanges = { baseline_set: false, added_count: 0, changed_section_count: 0, departed: [] };
  var currentCourseId = "";
  var courseLoaded = false;
  var courseLoadHooks = [];
  var tableRenderHooks = [];

  // Switching courses (or clicking Refresh again) starts a new request
  // without cancelling the old one, so a slower earlier response could land
  // last and fill the roster table with the previous course's students while
  // the new course sits selected. Stamp each request and let only the
  // newest one write, the same way inbox.js does.
  var loadGeneration = 0;

  function setStatus(msg, isOk) {
    statusEl.textContent = msg;
    statusEl.className = "hint" + (isOk ? " ok" : " error");
  }

  function toast(msg, isError) {
    var el = document.getElementById("ce-toast");
    if (!el) return;
    el.textContent = msg;
    el.className = "ce-toast" + (isError ? " ce-toast--error" : " ce-toast--ok");
    el.hidden = false;
    setTimeout(function () { el.hidden = true; }, 3000);
  }

  function postForm(url, fields) {
    return fetch(url, { method: "POST", body: new URLSearchParams(fields) })
      .then(function (r) { return r.json(); });
  }

  function notifyCourseLoaded() {
    for (var i = 0; i < courseLoadHooks.length; i++) {
      try {
        courseLoadHooks[i]();
      } catch (err) {
        // Ignore hook failures so roster loading still completes.
      }
    }
  }

  function notifyTableRendered() {
    for (var i = 0; i < tableRenderHooks.length; i++) {
      try {
        tableRenderHooks[i]();
      } catch (err) {
        // Ignore hook failures so roster rendering still completes.
      }
    }
  }

  function renderSummary(counts) {
    if (!counts) return;
    document.getElementById("roster-summary-total").textContent = counts.total + " students";
    document.getElementById("roster-summary-extra").textContent = "Extra " + counts.extra_time;
    document.getElementById("roster-summary-monitored").textContent = "Monitored " + counts.monitored;
    document.getElementById("roster-summary-warnings").textContent = "Issues " + counts.warnings;
  }

  function loadCourse() {
    var generation = ++loadGeneration;
    var cid = courseSelect.value;
    // The relationship editor holds private, course-scoped context. Hide it
    // before every reload so a prior course can never remain visible while a
    // new selection is loading (or if that load fails).
    var relationshipsCard = document.getElementById("roster-relationships");
    if (relationshipsCard) relationshipsCard.hidden = true;
    if (!cid) {
      tableCard.hidden = true;
      safetyCard.hidden = true;
      if (changesCard) changesCard.hidden = true;
      return;
    }

    currentCourseId = cid;
    courseLoaded = false;
    setStatus("Loading...", true);
    openCanvas.href = window.CANVAS_BASE
      ? window.CANVAS_BASE + "/courses/" + cid + "/users"
      : "#";

    fetch("/api/roster?course_id=" + encodeURIComponent(cid))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (generation !== loadGeneration) return;
        if (!data.ok) {
          setStatus(data.error || "Failed to load roster.", false);
          return;
        }

        students = data.students || [];
        scoreMatrix = data.score_matrix || { columns: [], values_by_section: {} };
        relationships = data.relationships || { by_section: {} };
        rosterChanges = data.roster_changes || { baseline_set: false, added_count: 0, changed_section_count: 0, departed: [] };
        selectedNameMap = {};
        renderSummary(data.counts);
        tableCard.hidden = false;
        safetyCard.hidden = false;
        courseLoaded = true;

        if (window.CE_ROSTER && typeof window.CE_ROSTER.applyFilters === "function") {
          window.CE_ROSTER.applyFilters();
        } else if (window.CE_ROSTER && typeof window.CE_ROSTER.renderTable === "function") {
          window.CE_ROSTER.renderTable();
        }

        notifyCourseLoaded();
        setStatus("Loaded " + students.length + " students" + (data.note ? " — " + data.note : ""), true);
      })
      .catch(function (e) {
        if (generation !== loadGeneration) return;
        setStatus("Network error: " + e.message, false);
      });
  }

  window.CE_ROSTER = {
    toast: toast,
    postForm: postForm,
    getCurrentCourseId: function () { return currentCourseId; },
    hasLoadedCourse: function () { return courseLoaded; },
    reloadCourse: loadCourse,
    getStudents: function () { return students; },
    getScoreMatrix: function () { return scoreMatrix; },
    setScoreMatrix: function (value) {
      scoreMatrix = value || { columns: [], values_by_section: {} };
    },
    getRelationships: function () { return relationships; },
    setRelationships: function (value) {
      relationships = value || { by_section: {} };
    },
    getRosterChanges: function () { return rosterChanges; },
    setRosterChanges: function (value) {
      rosterChanges = value || { baseline_set: false, added_count: 0, changed_section_count: 0, departed: [] };
    },
    getFilteredStudents: function () {
      return filteredStudents;
    },
    setFilteredStudents: function (value) {
      filteredStudents = value || [];
    },
    getSelectedNameMap: function () {
      return selectedNameMap;
    },
    setSelectedNameMap: function (value) {
      selectedNameMap = value || {};
    },
    onCourseLoaded: function (fn) {
      if (typeof fn !== "function") return function () {};
      courseLoadHooks.push(fn);
      return function () {
        for (var i = courseLoadHooks.length - 1; i >= 0; i--) {
          if (courseLoadHooks[i] === fn) {
            courseLoadHooks.splice(i, 1);
          }
        }
      };
    },
    onTableRendered: function (fn) {
      if (typeof fn !== "function") return function () {};
      tableRenderHooks.push(fn);
      return function () {
        for (var i = tableRenderHooks.length - 1; i >= 0; i--) {
          if (tableRenderHooks[i] === fn) {
            tableRenderHooks.splice(i, 1);
          }
        }
      };
    },
    applyFilters: function () {
      if (window.CE_ROSTER && typeof window.CE_ROSTER.renderTable === "function") {
        window.CE_ROSTER.renderTable();
      }
    },
    renderTable: function () {},
    setRowStatus: function () {},
    notifyTableRendered: notifyTableRendered
  };

  window.CANVAS_BASE = document.querySelector('meta[name="canvas-base"]')
    ? document.querySelector('meta[name="canvas-base"]').content
    : "";

  courseSelect.addEventListener("change", loadCourse);
  refreshBtn.addEventListener("click", loadCourse);

  if (courseSelect.value) {
    setTimeout(loadCourse, 0);
  }
})();
