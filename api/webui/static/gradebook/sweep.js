(function () {
  "use strict";

  var gb = window.CE_GRADEBOOK || {};
  var ready = ["postForm", "showLog", "showBanner", "hideBanner", "esc",
               "gbCourseId", "gbCourseName", "gbTargets", "sweepEntries",
               "canvasWriteReview"]
    .every(function (name) {
      if (name === "sweepEntries") return typeof Object.getOwnPropertyDescriptor(gb, "sweepEntries") !== "undefined";
      return typeof gb[name] === "function";
    });

  function requireReady() {
    if (ready) return true;
    alert("Gradebook controls did not load correctly. Refresh Canvas Expert and try again.");
    return false;
  }

  // ── Sweep helpers ─────────────────────────────────────────────────────

  function sweepSettings() {
    return {
      honor_extra_time: document.getElementById("sw-extra").checked,
      date_from:        document.getElementById("sw-from")?.value || null,
      date_to:          document.getElementById("sw-to")?.value   || null,
    };
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="canvasexpert-csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  // Ledger mutation routes require the CSRF header (see routes/operations.py).
  async function postJson(url, body) {
    var r;
    try {
      r = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CanvasExpert-CSRF": csrfToken(),
        },
        body: JSON.stringify(body),
      });
    } catch (e) {
      // Network failure: resolve to the same ok:false shape the callers
      // already handle, instead of an unhandled rejection that leaves the
      // in-progress log/status stuck.
      return { ok: false, error: "Could not reach Canvas Expert. Check your connection and try again." };
    }
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok && !data.error) data.error = "HTTP " + r.status;
    return data;
  }

  function updateSweepApplyBtn() {
    var n   = gb.sweepEntries.length;
    var btn = document.getElementById("btn-sweep-apply");
    if (!btn) return;
    btn.hidden = n === 0;
    btn.textContent = "Set lateness for " + n + " late submission" + (n === 1 ? "" : "s") + "…";
  }

  // ── Sweep handlers ────────────────────────────────────────────────────

  document.getElementById("btn-sweep-preview")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var id = gb.gbCourseId();
    if (!id) return alert("Pick a course first.");
    var st = document.getElementById("sw-status");
    var from = document.getElementById("sw-from")?.value;
    var to   = document.getElementById("sw-to")?.value;
    if (!from && !to) {
      if (!confirm("No date range set — this will scan the entire course history and may be very slow. Continue?")) return;
    }
    st.className = "status hint";
    st.textContent = from
      ? "Scanning late work from " + from + " to " + (to || "now") + "…"
      : "Scanning all late work… (give it a moment)";
    this.disabled = true;
    document.getElementById("sw-table-wrap").hidden = true;
    gb.hideBanner(document.getElementById("sw-banner"));
    try {
      var d = await gb.postForm("/api/sweep/preview",
        { course_id: id, settings: JSON.stringify(sweepSettings()) });
      if (!d.ok) { st.className = "status error"; st.textContent = "Error: " + d.error; return; }
      gb.sweepEntries = d.entries || [];
      if (!gb.sweepEntries.length) {
        st.className = "status ok";
        st.textContent = "✓ No late submissions found in this date range — nothing to correct.";
        updateSweepApplyBtn();
        return;
      }
      st.textContent = gb.sweepEntries.length + " late submission(s) found. No changes yet — " +
        "applying recomputes from current Canvas state and writes the whole set.";
      var tbody = document.getElementById("sw-tbody");
      tbody.innerHTML = "";
      gb.sweepEntries.forEach(function (e) {
        var tr = document.createElement("tr");
        var excl = e.excluded_dates || [];
        tr.title = excl.length
          ? "Excluded: " + excl.join(", ") + (e.extra_days ? " + " + e.extra_days + " extra-time day(s)" : "")
          : (e.extra_days ? "Extra-time: " + e.extra_days + " day(s) excused" : "");
        tr.innerHTML =
          '<td>' + gb.esc(e.student_name) + '</td>' +
          '<td>' + gb.esc(e.assignment_name) + '</td>' +
          '<td class="muted">' + gb.esc(e.due) + ' → ' + gb.esc(e.submitted) + '</td>' +
          '<td class="muted">' + e.canvas_days + '</td>' +
          '<td><strong>' + e.school_days + '</strong></td>' +
          '<td class="muted" style="font-size:12px">' + (excl.length ? excl.join(", ") : "—") + '</td>';
        tbody.appendChild(tr);
      });
      document.getElementById("sw-table-wrap").hidden = false;
      updateSweepApplyBtn();
    } finally { this.disabled = false; }
  });

  document.getElementById("btn-sweep-apply")?.addEventListener("click", async function () {
    if (!requireReady()) return;
    var id = gb.gbCourseId();
    if (!id) return alert("Pick a course first.");
    var settings = sweepSettings();
    var log = gb.showLog(document.getElementById("sw-log"));
    var banner = document.getElementById("sw-banner");
    gb.hideBanner(banner);
    this.disabled = true;
    try {
      // 1. Prepare — the server recomputes the write set from current Canvas
      //    state; the preview rows shown in the table are never sent.
      log("Preparing sweep operation (server recomputes from current Canvas state)…");
      var prep = await postJson("/api/operations/gradebook.sweep/prepare", {
        payload: settings,
        targets: [{ course_id: id }],
      });
      if (!prep.ok) {
        log("ERROR: " + prep.error);
        gb.showBanner(banner, "fail", "✗ " + gb.esc(prep.error || "prepare failed"));
        return;
      }
      var frozen = (prep.review_summary && prep.review_summary.frozen_reviews || [])[0] || {};
      var count = frozen.entry_count || 0;
      log("Server found " + count + " late submission(s) to set" +
          (frozen.skipped_count ? " (" + frozen.skipped_count + " skipped)" : "") + ".");
      if (count !== gb.sweepEntries.length) {
        log("⚠ Canvas changed since your preview: preview showed " + gb.sweepEntries.length +
            ", the server now finds " + count + ".");
      }
      if (!count) {
        gb.showBanner(banner, "ok", "✓ Nothing to write — no late submissions found at apply time.");
        return;
      }

      // 2. Teacher review of the server-recomputed set.
      var ok = await gb.canvasWriteReview({
        title: "Review lateness override write",
        action: "Set lateness overrides for " + count + " late submission(s) recomputed from current Canvas state.",
        targets: typeof gb.gbTargets === "function" ? gb.gbTargets() : [{ id: id, name: gb.gbCourseName() }],
        details: [
          count + " late submission(s) found by the server just now",
          "Date range: " + (settings.date_from || "course start") + " to " + (settings.date_to || "now"),
        ],
        warnings: [
          "This writes lateness overrides to Canvas through the reviewed operation ledger.",
          "The write set is recomputed on the server — the preview table is informational only.",
          "Canvas recalculates each student's late penalty using the course late policy.",
          "No direct score values are written by this action.",
        ],
        confirmText: "Set lateness overrides",
      });
      if (!ok) { log("Cancelled — nothing written."); return; }

      // 3. Freeze the review, then apply with the returned digest.
      var rev = await postJson("/api/operation-batches/review",
        { operation_ids: [prep.operation_id] });
      if (!rev.ok) {
        log("ERROR: " + rev.error);
        gb.showBanner(banner, "fail", "✗ " + gb.esc(rev.error || "review failed"));
        return;
      }
      log("Review frozen — applying…");
      var applied = await postJson(
        "/api/operation-batches/" + encodeURIComponent(rev.batch_id) + "/apply",
        { review_digest: rev.review_digest });
      if (applied.error && !applied.status) {
        log("ERROR: " + applied.error);
        gb.showBanner(banner, "fail", "✗ " + gb.esc(applied.error));
        return;
      }

      var results = applied.target_results || [];
      var drifted = results.some(function (t) { return t.error_code === "drift_detected"; });
      results.forEach(function (t) {
        log((t.state === "applied" ? "✓" : "✗") + " course target → " + t.state +
            (t.error_code ? " (" + t.error_code + ")" : ""));
      });
      if (drifted) {
        gb.showBanner(banner, "warn",
          "⚠ Canvas changed between review and apply — nothing was written. Run preview again.");
        return;
      }
      if (applied.status === "applied") {
        gb.showBanner(banner, "ok",
          "✓ Lateness override(s) set for " + count + " submission(s) — Canvas will apply its late policy.");
        document.getElementById("sw-table-wrap").hidden = true;
        gb.sweepEntries = [];
        updateSweepApplyBtn();
      } else {
        gb.showBanner(banner, "warn",
          "⚠ Sweep finished with status “" + gb.esc(applied.status || "unknown") +
          "” — see the operation results above for details.");
      }
    } finally { this.disabled = false; }
  });

})();
