(function () {
  "use strict";

  var roster = window.CE_ROSTER || {};
  var ready = [
    "toast",
    "postForm",
    "getCurrentCourseId",
    "getSelectedNameMap",
    "onTableRendered",
    "reloadCourse"
  ].every(function (name) { return typeof roster[name] === "function"; });

  if (!ready) {
    window.alert("Roster bulk tools need a page refresh.");
    return;
  }

  var selectAll = document.getElementById("roster-select-all");
  var bulkBar = document.getElementById("roster-bulk-bar");
  var bulkCount = document.getElementById("roster-bulk-count");
  var bulkExtraDays = document.getElementById("roster-bulk-extra-days");
  var bulkActions = document.querySelector(".roster-bulk-actions");
  var tableBody = document.getElementById("roster-table-body");

  if (!selectAll || !bulkBar || !bulkCount || !bulkExtraDays || !bulkActions || !tableBody) {
    return;
  }

  function getSelectedIds() {
    var ids = [];
    var checks = tableBody.querySelectorAll(".roster-row-check:checked");
    for (var i = 0; i < checks.length; i++) {
      ids.push(checks[i].dataset.id);
    }
    return ids;
  }

  function updateBulkBar() {
    var ids = getSelectedIds();
    bulkBar.hidden = ids.length === 0;
    if (ids.length > 0) bulkCount.textContent = "Bulk edit: " + ids.length + " selected";
  }

  function refreshBulkUi() {
    updateBulkBar();
  }

  function doBulkAction(action, ids, value) {
    roster.postForm("/api/roster/bulk", {
      course_id: roster.getCurrentCourseId(),
      user_ids: JSON.stringify(ids),
      action: action,
      value: JSON.stringify(value)
    })
      .then(function (data) {
        if (data.ok) {
          var msg = "Updated " + data.updated + " students.";
          if (data.failed && data.failed > 0) {
            msg = "Updated " + data.updated + "; failed " + data.failed + ": " + (data.errors || []).join("; ");
          }
          roster.toast(msg, data.failed > 0);
          roster.reloadCourse();
        } else {
          roster.toast(data.error || "Bulk action failed.", true);
        }
      })
      .catch(function (e) { roster.toast("Error: " + e.message, true); });
  }

  selectAll.addEventListener("change", function () {
    var checked = selectAll.checked;
    var checkboxes = tableBody.querySelectorAll(".roster-row-check");
    for (var i = 0; i < checkboxes.length; i++) {
      checkboxes[i].checked = checked;
    }
    updateBulkBar();
  });

  tableBody.addEventListener("change", function (e) {
    if (e.target.classList.contains("roster-row-check")) {
      updateBulkBar();
    }
  });

  bulkActions.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-bulk]");
    if (!btn) return;
    var action = btn.dataset.bulk;
    var ids = getSelectedIds();
    if (ids.length === 0) return;

    var value = {};
    if (action === "set_extra_time") {
      value = { days: parseInt(bulkExtraDays.value, 10) || 2, names: roster.getSelectedNameMap() };
    } else if (action === "set_monitored") {
      value = { names: roster.getSelectedNameMap() };
    }

    doBulkAction(action, ids, value);
  });

  roster.onTableRendered(refreshBulkUi);
  refreshBulkUi();
})();
