(function () {
  "use strict";
  var course = document.getElementById("names-course");
  if (!course) return;
  var search = document.getElementById("names-search");
  var body = document.getElementById("names-table-body");
  var status = document.getElementById("names-status");
  var safetyStatus = document.getElementById("names-safety-status");
  var packs = document.getElementById("names-protected-packs");
  var custom = document.getElementById("names-custom-protected");
  var scrub = document.getElementById("names-scrub-text");
  var scrubResult = document.getElementById("names-scrub-result");
  var exportButton = document.getElementById("names-export-who");
  var students = [];
  var requestNumber = 0;

  function json(response) {
    return response.json().then(function (data) {
      if (!response.ok || data.ok === false) throw new Error(data.error || "That action could not be completed.");
      return data;
    });
  }
  function safetyError(error) { safetyStatus.textContent = error.message || "That action could not be completed."; }
  function sections(student) {
    return (student.sections || []).map(function (section) { return section.name || ""; }).join(", ");
  }
  function render() {
    var query = search.value.trim().toLowerCase();
    var visible = students.filter(function (student) {
      return [student.pseudonym, student.real_name, sections(student)].join(" ").toLowerCase().indexOf(query) !== -1;
    });
    body.replaceChildren();
    visible.forEach(function (student) {
      var row = document.createElement("tr");
      [student.pseudonym, student.real_name, sections(student)].forEach(function (value) {
        var cell = document.createElement("td");
        cell.textContent = value || "";
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    status.textContent = course.value ? visible.length + " of " + students.length + " names" : "Select a course.";
  }
  function loadCourse() {
    var number = ++requestNumber;
    students = [];
    exportButton.disabled = true;
    render();
    if (!course.value) return;
    status.textContent = "Loading…";
    fetch("/api/names?course_id=" + encodeURIComponent(course.value)).then(json).then(function (data) {
      if (number !== requestNumber) return;
      students = data.students || [];
      exportButton.disabled = false;
      render();
      if (scrub.value) runScrub();
    }).catch(function (error) {
      if (number === requestNumber) status.textContent = error.message || "Names could not be loaded.";
    });
  }
  function loadProtected() {
    return fetch("/api/names/protected").then(json).then(function (data) {
      packs.replaceChildren();
      (data.packs || []).forEach(function (pack) {
        var label = document.createElement("label");
        var input = document.createElement("input");
        input.type = "checkbox";
        input.checked = pack.enabled;
        input.dataset.packId = pack.id;
        label.appendChild(input);
        label.appendChild(document.createTextNode(" " + pack.title));
        packs.appendChild(label);
      });
      custom.value = (data.custom || []).join(", ");
    }).catch(safetyError);
  }
  function runScrub() {
    if (!scrub.value) { scrubResult.hidden = true; return; }
    fetch("/api/names/scrub-test", { method: "POST", body: new URLSearchParams({text: scrub.value, course_id: course.value}) })
      .then(json).then(function (data) { scrubResult.textContent = data.scrubbed; scrubResult.hidden = false; }).catch(safetyError);
  }
  function action(button, url, params, done) {
    button.disabled = true;
    safetyStatus.textContent = "Working…";
    fetch(url, { method: "POST", body: params }).then(json).then(done).catch(safetyError)
      .finally(function () { button.disabled = button === exportButton && !course.value; });
  }
  course.addEventListener("change", loadCourse);
  document.getElementById("names-refresh").addEventListener("click", loadCourse);
  search.addEventListener("input", render);
  document.getElementById("names-scrub-run").addEventListener("click", runScrub);
  document.getElementById("names-save-protected").addEventListener("click", function () {
    var states = {};
    packs.querySelectorAll("input").forEach(function (input) { states[input.dataset.packId] = input.checked; });
    var names = custom.value.split(",").map(function (name) { return name.trim(); }).filter(Boolean);
    action(this, "/api/names/protected", new URLSearchParams({data: JSON.stringify({packs: states, custom: names})}), function () {
      safetyStatus.textContent = "Protected names saved.";
      loadProtected();
    });
  });
  exportButton.addEventListener("click", function () {
    if (!course.value) return;
    action(this, "/api/names/who-is-who", new URLSearchParams({course_id: course.value}), function (data) {
      safetyStatus.textContent = "Exported to " + data.path;
    });
  });
  document.getElementById("names-backup-vault").addEventListener("click", function () {
    action(this, "/api/names/backup-vault", undefined, function (data) {
      safetyStatus.textContent = "Backed up (" + data.entries + " entries).";
    });
  });
  loadProtected();
})();
