(function () {
  "use strict";

  var roster = window.CE_ROSTER || {};
  var ready = [
    "getStudents",
    "postForm",
    "toast",
    "setRowStatus",
    "reloadCourse"
  ].every(function (name) { return typeof roster[name] === "function"; });

  if (!ready) {
    window.alert("Roster inline edit tools need a page refresh.");
    return;
  }

  var tableBody = document.getElementById("roster-table-body");
  if (!tableBody) {
    return;
  }

  var saveTimeouts = {};

  function findStudent(id) {
    var students = roster.getStudents() || [];
    for (var i = 0; i < students.length; i++) {
      if (students[i].id === id) return students[i];
    }
    return null;
  }

  function saveField(userId, key, value) {
    roster.setRowStatus(userId, "saving locally...", "roster-v2-status-saving");

    if (saveTimeouts[userId]) {
      clearTimeout(saveTimeouts[userId]);
    }

    saveTimeouts[userId] = setTimeout(function () {
      var patch = {};
      patch[key] = value;

      var body = new URLSearchParams();
      body.append("course_id", roster.getCurrentCourseId());
      body.append("user_id", userId);
      body.append("patch", JSON.stringify(patch));

      fetch("/api/roster/student", { method: "POST", body: body })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            if (key === "regenerate_pseudonym" && data.pseudonym) {
              updateLocalStudent(userId, "pseudonym", data.pseudonym);
              var row = tableBody.querySelector('tr[data-id="' + userId + '"]');
              var pseudonymInput = row && row.querySelector(".roster-v2-pseudo");
              if (pseudonymInput) pseudonymInput.value = data.pseudonym;
            } else {
              updateLocalStudent(userId, key, value);
            }
            roster.setRowStatus(userId, "saved locally", "roster-v2-status-ok");
          } else {
            var apiMsg = data.error || "Save failed.";
            roster.setRowStatus(userId, "Error: " + apiMsg, "roster-v2-status-error");
            if (key === "classroom_profile") {
              var profileRow = tableBody.querySelector('tr[data-id="' + userId + '"]');
              var profileError = profileRow && profileRow.querySelector(".roster-profile-error");
              if (profileError) profileError.textContent = apiMsg;
            }
            roster.toast(apiMsg, true);
          }
        })
        .catch(function (e) {
          var netMsg = "Network error: " + e.message;
          roster.setRowStatus(userId, netMsg, "roster-v2-status-error");
          roster.toast(netMsg, true);
        });
    }, 300);
  }

  function updateLocalStudent(userId, key, value) {
    var s = findStudent(userId);
    if (!s) return;

    if (key === "nicknames") {
      s.nicknames = value;
    } else if (key === "pseudonym") {
      s.pseudonym = value;
    } else if (key === "extra_time") {
      s.extra_time = { enabled: !!value.enabled, days: value.days || 0 };
    } else if (key === "monitored") {
      s.monitored = { enabled: !!value.enabled, note: value.note || "" };
    } else if (key === "classroom_profile") {
      s.classroom_profile = value;
    }
  }

  function buildClassroomProfile(row) {
    var birthday = row.querySelector(".roster-v2-birthday");
    var celebrations = [];
    row.querySelectorAll(".roster-celebration").forEach(function (item) {
      celebrations.push({
        id: item.dataset.celebrationId || "",
        label: (item.querySelector(".roster-v2-celebration-label") || {}).value || "",
        start: (item.querySelector(".roster-v2-celebration-start") || {}).value || "",
        end: (item.querySelector(".roster-v2-celebration-end") || {}).value || ""
      });
    });
    return {birthday: birthday ? birthday.value.trim() : "", celebrations: celebrations};
  }

  function saveClassroomProfile(userId, row) {
    saveField(userId, "classroom_profile", buildClassroomProfile(row));
  }

  function newCelebrationId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return "celebration-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
  }

  function addCelebration(row) {
    var wrap = row.querySelector(".roster-celebrations");
    if (!wrap) return;
    var id = newCelebrationId();
    var item = document.createElement("div");
    item.className = "roster-celebration"; item.dataset.celebrationId = id;
    item.innerHTML = '<input type="text" class="roster-v2-input roster-v2-celebration-label" placeholder="Celebration" maxlength="160" data-id="' + row.dataset.id + '">' +
      '<input type="date" class="roster-v2-celebration-start" aria-label="Celebration start" data-id="' + row.dataset.id + '">' +
      '<input type="date" class="roster-v2-celebration-end" aria-label="Celebration end" data-id="' + row.dataset.id + '">' +
      '<button type="button" class="roster-v2-celebration-remove" data-id="' + row.dataset.id + '">Remove</button>';
    wrap.appendChild(item); attachProfileEvents(row); saveClassroomProfile(row.dataset.id, row);
  }

  function attachProfileEvents(row) {
    row.querySelectorAll(".roster-v2-birthday, .roster-v2-celebration-label, .roster-v2-celebration-start, .roster-v2-celebration-end").forEach(function (el) {
      if (el.dataset.profileBound) return;
      el.dataset.profileBound = "1";
      el.addEventListener("change", function () { saveClassroomProfile(row.dataset.id, row); });
    });
    row.querySelectorAll(".roster-v2-celebration-remove").forEach(function (button) {
      if (button.dataset.profileBound) return;
      button.dataset.profileBound = "1";
      button.addEventListener("click", function () { button.closest(".roster-celebration").remove(); saveClassroomProfile(row.dataset.id, row); });
    });
    row.querySelectorAll(".roster-v2-celebration-add").forEach(function (button) {
      if (button.dataset.profileBound) return;
      button.dataset.profileBound = "1";
      button.addEventListener("click", function () { addCelebration(row); });
    });
  }

  function attachInlineEvents() {
    tableBody.querySelectorAll(".roster-v2-nicknames").forEach(function (el) {
      el.addEventListener("change", function () {
        var id = el.dataset.id;
        var val = el.value.split(",").map(function (n) { return n.trim(); }).filter(Boolean);
        saveField(id, "nicknames", val);
      });
    });

    tableBody.querySelectorAll(".roster-v2-pseudo").forEach(function (el) {
      el.addEventListener("change", function () {
        var id = el.dataset.id;
        saveField(id, "pseudonym", el.value.trim());
      });
    });

    tableBody.querySelectorAll(".roster-v2-regen").forEach(function (el) {
      el.addEventListener("click", function () {
        var id = el.dataset.id;
        roster.setRowStatus(id, "saving...", "roster-v2-status-saving");
        saveField(id, "regenerate_pseudonym", true);
      });
    });

    tableBody.querySelectorAll(".roster-v2-et-cb").forEach(function (el) {
      el.addEventListener("change", function () {
        var id = el.dataset.id;
        var s = findStudent(id);
        var daysInput = el.closest("label").querySelector(".roster-v2-et-days");
        var enabled = el.checked;
        daysInput.hidden = !enabled;
        if (enabled) daysInput.value = daysInput.value || "2";
        saveField(id, "extra_time", {
          enabled: enabled,
          days: enabled ? parseInt(daysInput.value, 10) || 2 : 0,
          name: s ? (s.display_name || s.name) : ""
        });
      });
    });

    tableBody.querySelectorAll(".roster-v2-et-days").forEach(function (el) {
      el.addEventListener("change", function () {
        var id = el.dataset.id;
        var s = findStudent(id);
        var cb = el.closest("label").querySelector(".roster-v2-et-cb");
        saveField(id, "extra_time", {
          enabled: cb.checked,
          days: parseInt(el.value, 10) || 0,
          name: s ? (s.display_name || s.name) : ""
        });
      });
    });

    tableBody.querySelectorAll(".roster-v2-monitor").forEach(function (el) {
      el.addEventListener("change", function () {
        var id = el.dataset.id;
        var s = findStudent(id);
        var noteInput = el.closest("tr").querySelector(".roster-v2-note");
        saveField(id, "monitored", {
          enabled: el.checked,
          name: s ? (s.display_name || s.name) : "",
          note: noteInput ? noteInput.value : (s ? (s.monitored.note || "") : "")
        });
      });
    });

    tableBody.querySelectorAll(".roster-v2-note").forEach(function (el) {
      el.addEventListener("change", function () {
        var id = el.dataset.id;
        var s = findStudent(id);
        if (!s) return;
        var note = el.value.trim();
        saveField(id, "monitored", {
          enabled: s.monitored.enabled || !!note,
          name: s.display_name || s.name,
          note: note
        });
      });
    });

    tableBody.querySelectorAll(".roster-classroom-profile").forEach(function (editor) {
      attachProfileEvents(editor.closest("tr"));
    });
  }

  function attachRebindHooks() {
    roster.onTableRendered(function () {
      attachInlineEvents();
    });
  }

  attachRebindHooks();
  attachInlineEvents();
})();
