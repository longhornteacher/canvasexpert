(function () {
  "use strict";

  var CE = window.CE_SETTINGS || {};

  var btnFetch = document.getElementById("btn-fetch-courses");
  var browser = document.getElementById("course-browser");
  var searchInput = document.getElementById("course-search");
  var courseList = document.getElementById("course-list");
  var allCourses = [];

  function renderCourseList(courses) {
    courseList.innerHTML = "";
    courses.forEach(function (c) {
      var row = document.createElement("div");
      row.className = "course-row";
      row.innerHTML =
        "<span>" + CE.esc(c.name) + ' <span class="muted">#' + CE.esc(c.id) + "</span></span>" +
        '<button type="button" class="small" data-id="' + CE.esc(c.id) + '" data-name="' + CE.esc(c.name) + '">Add as Current</button>';
      row.querySelector("button").addEventListener("click", async function (ev) {
        var btn = ev.currentTarget;
        var nickname = prompt("Nickname for this Current course:\n" + c.name + " (#" + c.id + ")", c.name);
        if (nickname === null) return;
        btn.disabled = true;
        btn.textContent = "Saving…";
        var r = await fetch("/settings/courses/bookmark", {
          method: "POST",
          body: new URLSearchParams({
            course_id: c.id,
            course_name: c.name,
            nickname: nickname || c.name,
          }),
        });
        var d = await r.json();
        if (d.ok) {
          btn.textContent = "✓ Current";
          location.reload();
        } else {
          btn.disabled = false;
          btn.textContent = "Add as Current";
          alert("Failed: " + d.error);
        }
      });
      courseList.appendChild(row);
    });
    if (!courses.length) {
      courseList.innerHTML = "<p class='hint'>No courses match.</p>";
    }
  }

  btnFetch && btnFetch.addEventListener("click", async function () {
    btnFetch.disabled = true;
    btnFetch.textContent = "Loading…";
    var resp = await fetch("/api/courses");
    var data = await resp.json();
    btnFetch.disabled = false;
    btnFetch.textContent = "Browse Canvas courses…";
    if (!data.ok) {
      alert("Could not fetch courses: " + data.error);
      return;
    }
    allCourses = data.courses;
    browser.hidden = false;
    renderCourseList(allCourses);
    searchInput.focus();
  });

  searchInput && searchInput.addEventListener("input", function () {
    var q = searchInput.value.toLowerCase();
    renderCourseList(allCourses.filter(function (c) {
      return c.name.toLowerCase().includes(q) || c.id.includes(q);
    }));
  });

  document.querySelectorAll(".active-toggle").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      var id = btn.dataset.courseId;
      var current = btn.dataset.current === "true";
      var newVal = !current;
      if (current && !confirm(
        "Move this course to Previous?\n\n" +
        "Local sessions, receipts, downloads, and settings will be retained. " +
        "Desk scans and scheduled routines will pause for this course until you make it Current again."
      )) return;
      btn.disabled = true;
      var r = await fetch("/settings/courses/" + encodeURIComponent(id) + "/set-active", {
        method: "POST",
        body: new URLSearchParams({ active: newVal }),
      });
      var d = await r.json();
      if (!d.ok) {
        btn.disabled = false;
        alert("Failed to update.");
        return;
      }
      location.reload();
    });
  });

  document.querySelectorAll("[data-remove-course]").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      var id = btn.dataset.removeCourse;
      var name = btn.dataset.removeName;
      if (!confirm(
        'Remove "' + name + '" from Canvas Expert?\n\n' +
        "This is separate from moving a course to Previous. Local work is retained, but the course will no longer appear in either course list."
      )) return;
      await fetch("/settings/courses/" + encodeURIComponent(id) + "/remove", { method: "POST" });
      location.reload();
    });
  });
})();
