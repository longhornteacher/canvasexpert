(function () {
  "use strict";

  var roster = window.CE_ROSTER || {};
  var ready = [
    "getStudents",
    "getFilteredStudents",
    "getSelectedNameMap",
    "setSelectedNameMap",
    "onTableRendered"
  ].every(function (name) { return typeof roster[name] === "function"; });

  if (!ready) {
    window.alert("Roster table tools need a page refresh.");
    return;
  }

  var tableBody = document.getElementById("roster-table-body");
  if (!tableBody) {
    return;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function warningLabel(code) {
    var labels = {
      missing_pseudonym: "Missing pseudonym",
      extra_time_without_days: "Extra time needs days",
      protected_name_collision: "Protected name collision",
      nickname_collision: "Nickname collision",
      student_added: "New student",
      student_changed_section: "Changed section"
    };
    return labels[code] || String(code || "Issue").replace(/_/g, " ");
  }

  function rowStatus(warnings) {
    var issues = (warnings || []).map(warningLabel);
    if (issues.length > 0) {
      return {
        text: issues[0] + (issues.length > 1 ? " +" + (issues.length - 1) : ""),
        title: issues.join("; "),
        cls: "roster-v2-status-warning"
      };
    }
    return {
      text: "Ready",
      title: "No roster issues",
      cls: "roster-v2-status-ok"
    };
  }

  function setRowStatus(rowId, msg, cls) {
    var row = tableBody.querySelector('tr[data-id="' + rowId + '"]');
    if (!row) return;
    var el = row.querySelector(".roster-v2-status");
    if (!el) return;
    el.textContent = msg;
    el.title = msg;
    el.className = "roster-v2-status " + (cls || "");
  }

  function updateSelectedNameMap() {
    var names = {};
    var checks = tableBody.querySelectorAll(".roster-row-check:checked");
    var students = roster.getStudents() || [];
    for (var i = 0; i < checks.length; i++) {
      var id = checks[i].dataset.id;
      var found = null;
      for (var j = 0; j < students.length; j++) {
        if (students[j].id === id) {
          found = students[j];
          break;
        }
      }
      names[id] = found ? (found.display_name || found.name) : id;
    }
    roster.setSelectedNameMap(names);
    return names;
  }

  function classroomProfileEditor(profile, id) {
    profile = profile || {birthday: "", celebrations: []};
    var html = '<div class="roster-classroom-profile" data-id="' + esc(id) + '">' +
      '<label>Birthday <input type="text" class="roster-v2-input roster-v2-birthday" value="' + esc(profile.birthday || "") + '" placeholder="MM-DD" maxlength="5" data-id="' + esc(id) + '"></label>' +
      '<div class="roster-celebrations">';
    var celebrations = Array.isArray(profile.celebrations) ? profile.celebrations : [];
    for (var i = 0; i < celebrations.length; i++) {
      var item = celebrations[i] || {};
      html += '<div class="roster-celebration" data-celebration-id="' + esc(item.id || "") + '">' +
        '<input type="text" class="roster-v2-input roster-v2-celebration-label" value="' + esc(item.label || "") + '" placeholder="Celebration" maxlength="160" data-id="' + esc(id) + '">' +
        '<input type="date" class="roster-v2-celebration-start" value="' + esc(item.start || "") + '" aria-label="Celebration start" data-id="' + esc(id) + '">' +
        '<input type="date" class="roster-v2-celebration-end" value="' + esc(item.end || "") + '" aria-label="Celebration end" data-id="' + esc(id) + '">' +
        '<button type="button" class="roster-v2-celebration-remove" data-id="' + esc(id) + '">Remove</button></div>';
    }
    html += '</div><button type="button" class="roster-v2-celebration-add" data-id="' + esc(id) + '">Add celebration</button>' +
      '<span class="roster-profile-error" data-profile-error="' + esc(id) + '" role="status"></span></div>';
    return html;
  }

  function renderTable() {
    var filteredStudents = roster.getFilteredStudents() || [];

    if (filteredStudents.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="9" class="roster-empty">No students.</td></tr>';
      updateSelectedNameMap();
      if (typeof roster.notifyTableRendered === "function") {
        roster.notifyTableRendered();
      }
      return;
    }

    var html = "";
    for (var i = 0; i < filteredStudents.length; i++) {
      var s = filteredStudents[i];
      var nnVal = esc((s.nicknames || []).join(", "));
      var pseudoVal = esc(s.pseudonym || "");
      var noteVal = esc((s.monitored && s.monitored.note) || "");
      var status = rowStatus(s.warnings);

      html += "<tr data-id=\"" + esc(s.id) + "\">" +
        '<td class="roster-col-check"><input type="checkbox" class="roster-row-check" data-id="' + esc(s.id) + '"></td>' +
        '<td class="roster-col-name"><span class="roster-v2-name">' + esc(s.display_name || s.name) + "</span></td>" +
        '<td class="roster-col-classroom-profile">' + classroomProfileEditor(s.classroom_profile, s.id) + "</td>" +
        '<td class="roster-col-nicknames"><input type="text" class="roster-v2-input roster-v2-nicknames" value="' + nnVal + '" placeholder="nicknames" data-id="' + esc(s.id) + '"></td>' +
        '<td class="roster-col-pseudo"><span class="roster-v2-pseudo-row"><input type="text" class="roster-v2-input roster-v2-pseudo" value="' + pseudoVal + '" placeholder="pseudonym" data-id="' + esc(s.id) + '" data-field="pseudo"><button type="button" class="roster-v2-regen" data-id="' + esc(s.id) + '" title="Regenerate">&#x21bb;</button></span></td>' +
        '<td class="roster-col-extratime"><label class="roster-v2-et"><input type="checkbox" class="roster-v2-et-cb" data-id="' + esc(s.id) + '"' + (s.extra_time.enabled ? " checked" : "") + ">" +
        (s.extra_time.enabled ? ('<input type="number" class="roster-v2-et-days" value="' + (s.extra_time.days || 0) + '" min="0" max="30" data-id="' + esc(s.id) + '">') : '<input type="number" class="roster-v2-et-days" value="0" min="0" max="30" data-id="' + esc(s.id) + '" hidden>') +
        "</label></td>" +
        '<td class="roster-col-monitor"><input type="checkbox" class="roster-v2-monitor" data-id="' + esc(s.id) + '"' + (s.monitored.enabled ? " checked" : "") + "></td>" +
        '<td class="roster-col-note"><input type="text" class="roster-v2-input roster-v2-note" value="' + noteVal + '" data-id="' + esc(s.id) + '"></td>' +
        '<td class="roster-col-status"><span class="roster-v2-status ' + status.cls + '" title="' + esc(status.title) + '">' + esc(status.text) + "</span></td>" +
        "</tr>";
    }

    tableBody.innerHTML = html;
    updateSelectedNameMap();
    if (typeof roster.notifyTableRendered === "function") {
      roster.notifyTableRendered();
    }
  }

  function bindSelectionSync() {
    tableBody.addEventListener("change", function (e) {
      if (e.target && e.target.classList && e.target.classList.contains("roster-row-check")) {
        updateSelectedNameMap();
      }
    });
  }

  roster.renderTable = renderTable;
  roster.setRowStatus = setRowStatus;
  roster.getSelectedNameMap = function () {
    return updateSelectedNameMap();
  };

  bindSelectionSync();
  updateSelectedNameMap();
})();
