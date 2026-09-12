(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/[&<>"']/g, function (c) {
        return {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[c];
      });
  }

  function showLog(el) {
    el.hidden = false;
    el.textContent = "";
    return function (line) {
      el.textContent += line + "\n";
      el.scrollTop = el.scrollHeight;
    };
  }

  function hideBanner(el) {
    if (!el) return;
    el.hidden = true;
    el.className = "push-banner";
    el.innerHTML = "";
  }

  function showBanner(el, kind, html) {
    if (!el) return;
    el.hidden = false;
    el.className = "push-banner " + kind;
    el.innerHTML = html;
  }


  var allBusyBtns = "#btn-validate,#btn-preview,#btn-push,#btn-push-variants,#btn-add-variant";

  function setBusy(v) {
    document.querySelectorAll(allBusyBtns).forEach(function (b) {
      b.disabled = v;
    });
  }

  // Unlike postJson below, this returns a normalised object rather than
  // throwing. Its callers are the validate/preview handlers, which branch on
  // `d.ok` / `d.error` and are not written around try/catch, so every failure
  // has to arrive in that shape. Same four cases gradebook.js's postForm
  // handles, and only the first was covered here:
  //   1. the request never lands (offline, app closed) -> outer catch
  //   2. non-2xx carrying valid JSON: HTTPException emits {"detail": ...},
  //      which has neither ok nor error, so callers showed "Error: undefined"
  //   3. non-2xx carrying no readable JSON at all
  //   4. 2xx whose body is not JSON (a proxy or error page in the way)
  async function postForm(url, fields) {
    return fetch(url, { method: "POST", body: new URLSearchParams(fields) })
      .then(function (r) {
        return r.json().then(
          function (data) { return { r: r, data: data, readable: true }; },
          function () { return { r: r, data: {}, readable: false }; }
        );
      })
      .then(function (res) {
        var data = res.data;
        if (!res.r.ok && !data.error) {
          data.ok = false;
          data.error = data.detail || ("Canvas Expert refused that request (HTTP " + res.r.status + ").");
        } else if (!res.readable && !data.error) {
          data.ok = false;
          data.error = "Canvas Expert sent back a response that could not be read.";
        }
        return data;
      })
      .catch(function () {
        return { ok: false, error: "Could not reach Canvas Expert. Check your connection and try again." };
      });
  }

  function renderBanner(el, results, exitOk) {
    if (!el) return;
    if (!results.length && !exitOk) {
      showBanner(el, "fail", "✗ Push failed — check the log above.");
      return;
    }
    if (!results.length) return;
    var allOk = results.every(function (r) { return r.ok; });
    var kind = allOk && exitOk ? "ok" : "warn";
    var lines = results.map(function (r) {
      var link = r.url
        ? ' — <a href="' + esc(r.url) + '" target="_blank" rel="noopener">Open in Canvas ↗</a>'
        : "";
      var icon = r.ok ? "✓" : "⚠";
      return icon + " <strong>" + esc(r.title) + "</strong>" + link;
    }).join("<br>");
    showBanner(el, kind, lines);
  }

  async function generatePhysical(path, logFn, bannerEl) {
    logFn("\nGenerating printable version (PDF + DOCX)…");
    try {
      var d = await postForm("/api/physical/quiz", { path: path });
      if (!d.ok) {
        logFn("⚠ Printable version failed: " + (d.error || "unknown error"));
        return;
      }
      logFn("✓ Printable version saved to: " + d.folder);
      (d.files || []).forEach(function (f) { logFn("    · " + f); });
      (d.warnings || []).forEach(function (w) { logFn("    ⚠ " + w); });
      if (d.fallback) {
        logFn("    (No OneDrive workspace found — saved to this PC's local Finished_Exports folder.)");
      }
      if (bannerEl) {
        var prev = bannerEl.hidden ? "" : bannerEl.innerHTML;
        var note = d.fallback
          ? '<br><span class="hint">No OneDrive workspace found — saved to this PC at the path above.</span>'
          : "";
        showBanner(
          bannerEl,
          bannerEl.classList.contains("fail") ? "warn" : "ok",
          (prev ? prev + "<br>" : "") +
          "📄 Printable PDF + DOCX saved to <code>" + esc(d.folder) + "</code> — " +
          '<a href="#" data-open-folder="' + esc(d.folder) + '">Open folder ↗</a>' + note
        );
        bannerEl.querySelector("[data-open-folder]")?.addEventListener("click", async function (e) {
          e.preventDefault();
          var r = await postForm("/api/open-path", { path: d.folder });
          if (r && r.ok === false) logFn("⚠ Could not open folder automatically — it's at: " + d.folder);
        });
      }
    } catch (e) {
      logFn("⚠ Printable version failed: " + e);
    }
  }

  var operationKinds = {
    qf: "content.quiz",
    quick: "content.quick_assignment",
    af: "content.assignment",
    pf: "content.page",
    rf: "content.rubric",
  };

  function csrfToken() {
    var meta = document.querySelector('meta[name="canvasexpert-csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  async function postJson(url, body) {
    var response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CanvasExpert-CSRF": csrfToken(),
      },
      body: JSON.stringify(body),
    });
    var data;
    try {
      data = await response.json();
    } catch (e) {
      data = { ok: false, error: "Server returned an unreadable response." };
    }
    if (!response.ok) {
      throw new Error(data.error || data.detail || ("Request failed (HTTP " + response.status + ")"));
    }
    return data;
  }

  function operationTargets() {
    var push = window.CE_PUSH || {};
    var selected = typeof push.targetCourses === "function" ? push.targetCourses() : [];
    return selected.map(function (target) {
      return { course_id: String(target.id) };
    });
  }

  function frozenReviewOptions(frozen, confirmLabel) {
    var first = frozen[0] || {};
    var details = [];
    var warnings = [];
    if (first.assignment_name) details.push("Assignment: " + first.assignment_name);
    if (first.page_title) details.push("Page: " + first.page_title);
    if (first.rubric_title) details.push("Rubric: " + first.rubric_title);
    if (first.points != null) details.push("Points: " + first.points);
    if (first.rubric_title && first.total_points != null) details.push("Rubric points: " + first.total_points);
    if (first.criteria_count != null) details.push("Criteria: " + first.criteria_count);
    if (!first.mode && first.due_at) details.push("Due: " + first.due_at);
    if (!first.mode && first.module_name) details.push("Module: " + first.module_name);
    if (first.student_page_title) details.push("Student page: " + first.student_page_title);
    if (!first.mode && first.post_to_sis) details.push("Sync to SIS: yes");
    if (!first.mode && first.published === true) warnings.push("The item will be published for students.");
    if (!first.mode && first.published === false) warnings.push("The item will be created unpublished.");
    if (first.tiered) {
      frozen.forEach(function (review) {
        (review.tiers || []).forEach(function (tier) {
          details.push((review.course_name || "Course") + " — " + tier.label +
            ": " + tier.group + " (" + tier.student_count + " students)");
        });
      });
      warnings.push("Canvas will create one assignment/gradebook column per tier.");
      warnings.push("Only that group's students can see each assignment (only_visible_to_overrides=true).");
    }
    frozen.forEach(function (review) {
      var prefix = review.course_name ? review.course_name + " — " : "";
      if (review.mode === "whole") {
        if (review.title) details.push(prefix + "Quiz: " + review.title);
        if (review.item_count != null) details.push(prefix + "Items: " + review.item_count);
        if (review.total_points != null) details.push(prefix + "Points: " + review.total_points);
        if (review.due_at) details.push(prefix + "Due: " + review.due_at);
        if (review.unlock_at) details.push(prefix + "Unlock: " + review.unlock_at);
        if (review.lock_at) details.push(prefix + "Lock: " + review.lock_at);
        if (review.module_name) details.push(prefix + "Module: " + review.module_name);
        if (review.assignment_group_name) details.push(prefix + "Assignment group: " + review.assignment_group_name);
        if (review.post_to_sis) details.push(prefix + "Sync to SIS: yes");
        if (review.multiple_attempts) {
          var attempts = review.allowed_attempts == null ? "multiple" : review.allowed_attempts;
          details.push(prefix + "Attempts: " + attempts +
            (review.score_to_keep ? " (keep " + review.score_to_keep + ")" : ""));
        }
        if (review.time_limit_minutes != null) details.push(prefix + "Time limit: " + review.time_limit_minutes + " minutes");
        if (review.shuffle_questions) details.push(prefix + "Shuffle questions: yes");
        if (review.shuffle_answers) details.push(prefix + "Shuffle answers: yes");
        if (review.one_at_a_time) details.push(prefix + "One question at a time: yes");
        if (review.allow_backtracking === false) details.push(prefix + "Backtracking: disabled");
        if (review.calculator_type) details.push(prefix + "Calculator: " + review.calculator_type);
        if (review.access_code) details.push(prefix + "Access code: configured");
        if (review.hide_results) warnings.push(prefix + "Student result visibility will be restricted.");
        if (review.published === true) warnings.push(prefix + "The quiz will be published for students.");
        if (review.published === false) warnings.push(prefix + "The quiz will be created unpublished.");
        if (review.baseline_has_existing) warnings.push(prefix + "A quiz with this title already exists; review carefully.");
      }
      if (review.mode === "differentiated") {
        (review.variants || []).forEach(function (variant) {
          var tier = (review.tiers || []).find(function (row) {
            return row.group === variant.group_name;
          });
          var count = tier && tier.student_count != null
            ? " (" + tier.student_count + " students)" : "";
          details.push(prefix + (variant.title || "Quiz variant") + " — " +
            (variant.group_name || "Canvas group") + count);
          if (variant.item_count != null || variant.total_points != null) {
            details.push(prefix + "Variant totals: " +
              (variant.item_count != null ? variant.item_count + " items" : "") +
              (variant.item_count != null && variant.total_points != null ? ", " : "") +
              (variant.total_points != null ? variant.total_points + " points" : ""));
          }
        });
        if (review.tier_warning) warnings.push(prefix + review.tier_warning);
        if (review.baseline_has_existing) warnings.push(prefix + "One or more matching quiz titles already exist; review carefully.");
      }
    });
    return {
      title: "Review Canvas content push",
      action: confirmLabel || "Review this Canvas change before continuing.",
      targets: frozen.map(function (review) { return { name: review.course_name }; }),
      details: details,
      warnings: warnings,
      confirmText: "Apply",
      cancelText: "Cancel",
    };
  }

  async function reviewAndApply(operationId, logFn, bannerEl, confirmLabel) {
    var batch = await postJson("/api/operation-batches/review", {
      operation_ids: [operationId],
    });
    var confirmed = await window.CE_WRITE_REVIEW.confirm(
      frozenReviewOptions(batch.frozen_reviews || [], confirmLabel)
    );
    if (!confirmed) {
      if (logFn) logFn("Canvas unchanged. The prepared operation remains available for later review.");
      renderOperationsList();
      return { cancelled: true };
    }
    var applyPromise = postJson(
      "/api/operation-batches/" + encodeURIComponent(batch.batch_id) + "/apply",
      { review_digest: batch.review_digest }
    );
    pollOperation(operationId);
    var applied = await applyPromise;
    if (logFn) {
      (applied.target_results || []).forEach(function (result, index) {
        var review = (batch.frozen_reviews || [])[index] || {};
        logFn((result.state === "applied" ? "✓ " : "⚠ ") +
          (review.course_name || "Target") + ": " + result.state);
      });
    }
    showBanner(
      bannerEl,
      applied.status === "applied" ? "ok" : "warn",
      applied.status === "applied" ? "✓ Canvas changes applied." : "⚠ Operation needs attention."
    );
    renderOperationsList();
    return applied;
  }

  async function prepareOperation(kind, payload, logFn) {
    var targets = operationTargets();
    if (!targets.length) throw new Error("Check at least one course on the right.");
    var prepared = await postJson(
      "/api/operations/" + encodeURIComponent(kind) + "/prepare",
      { payload: payload, targets: targets }
    );
    if (logFn) logFn("✓ Prepared operation " + prepared.operation_id);
    renderOperationsList();
    return prepared;
  }

  async function prepareOnly(kind, payload, logEl, bannerEl, btn) {
    var log = showLog(logEl);
    hideBanner(bannerEl);
    if (btn) btn.disabled = true;
    try {
      return await prepareOperation(kind, payload, log);
    } catch (e) {
      log("ERROR: " + e.message);
      showBanner(bannerEl, "fail", "✗ " + esc(e.message));
      return null;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Like work_rail.js's rail panels, this list has no freshness line to fall
  // back on, so a failed check must overwrite "Canvas unchanged." rather than
  // leave it standing -- that reassurance is misleading once it might be stale.
  function renderOperationsList() {
    var container = document.getElementById("ce-operations-list");
    if (!container) return;
    fetch("/api/operations")
      .then(function (response) {
        if (!response.ok) throw new Error("operations request failed");
        return response.json();
      })
      .then(function (data) {
        if (!data || data.ok !== true || !Array.isArray(data.operations)) {
          throw new Error("invalid operations response");
        }
        var ops = data.operations;
        if (!ops.length) {
          container.innerHTML = '<p class="ce-operations-empty">Canvas unchanged.</p>';
          return;
        }
        container.innerHTML = ops.map(function (op) {
          var buttons = "";
          if (op.status === "prepared" || op.status === "reviewed") {
            buttons += '<button class="ce-op-review primary" data-op-id="' + esc(op.operation_id) + '">Review &amp; Apply</button> ';
          }
          if (["attention", "partial", "failed"].indexOf(op.status) >= 0) {
            buttons += '<button class="ce-op-retry secondary" data-op-id="' + esc(op.operation_id) + '">Retry</button>';
          }
          return '<div class="ce-operation-item ce-op-status-' + esc(op.status) + '">' +
            '<span class="ce-op-kind">' + esc(op.kind) + '</span> ' +
            '<span class="ce-op-status">' + esc(op.status) + '</span> ' +
            '<span class="ce-op-targets">' + Number(op.target_count || 0) + ' target(s)</span> ' +
            buttons + '<div class="ce-op-progress" data-op-progress="' +
            esc(op.operation_id) + '"></div></div>';
        }).join("");
        ops.forEach(function (op) {
          if (op.status === "applying") {
            pollOperation(op.operation_id);
          } else {
            loadOperationProgress(op.operation_id);
          }
        });
      })
      .catch(function () {
        container.innerHTML = '<p class="ce-operations-empty">Operation history couldn&rsquo;t be checked. Preparing new operations still works.</p>';
      });
  }

  var operationPollers = Object.create(null);
  var terminalOperationStates = ["applied", "partial", "failed", "attention"];

  function stopOperationPoll(operationId) {
    var poller = operationPollers[operationId];
    if (poller && poller.timer) clearTimeout(poller.timer);
    delete operationPollers[operationId];
  }

  function renderOperationProgress(snapshot) {
    var container = document.querySelector('[data-op-progress="' + snapshot.operation_id + '"]');
    if (!container) return;
    container.innerHTML = (snapshot.targets || []).map(function (target, targetIndex) {
      var steps = (target.steps || []).map(function (step, stepIndex) {
        return '<span class="ce-op-step">Step ' + (stepIndex + 1) + ': ' + esc(step.state || "pending") + '</span>';
      }).join(" ");
      return '<div class="ce-op-target-progress">Target ' + (targetIndex + 1) + ': ' +
        esc(target.state || "pending") + (steps ? ' <span class="ce-op-steps">' + steps + '</span>' : "") + '</div>';
    }).join("");
  }

  function loadOperationProgress(operationId) {
    fetch("/api/operations/" + encodeURIComponent(operationId) + "/status")
      .then(function (response) {
        if (!response.ok) throw new Error("Status check failed (HTTP " + response.status + ")");
        return response.json();
      })
      .then(renderOperationProgress)
      .catch(function () {});
  }

  function pollOperation(operationId) {
    if (!operationId || operationPollers[operationId]) return;
    operationPollers[operationId] = { timer: null, attempts: 0, terminalHits: 0 };
    function tick() {
      var poller = operationPollers[operationId];
      if (!poller) return;
      poller.attempts += 1;
      fetch("/api/operations/" + encodeURIComponent(operationId) + "/status")
        .then(function (response) {
          if (!response.ok) throw new Error("Status check failed (HTTP " + response.status + ")");
          return response.json();
        })
        .then(function (snapshot) {
          renderOperationProgress(snapshot);
          if (terminalOperationStates.indexOf(snapshot.status) >= 0) {
            poller.terminalHits += 1;
            if (poller.terminalHits >= 2) {
              stopOperationPoll(operationId);
              renderOperationsList();
              return;
            }
          } else {
            poller.terminalHits = 0;
          }
          if (poller.attempts >= 120) {
            stopOperationPoll(operationId);
            var paused = document.querySelector('[data-op-progress="' + operationId + '"]');
            if (paused) paused.textContent = "Progress polling paused. Refresh Summary to check the operation.";
            return;
          }
          poller.timer = setTimeout(tick, 1000);
        })
        .catch(function (error) {
          stopOperationPoll(operationId);
          var container = document.querySelector('[data-op-progress="' + operationId + '"]');
          if (container) container.textContent = "Progress unavailable: " + error.message;
        });
    }
    tick();
  }

  if (window.addEventListener) {
    window.addEventListener("pagehide", function () {
      Object.keys(operationPollers).forEach(stopOperationPoll);
    });
  }

  document.addEventListener("click", function (event) {
    var reviewBtn = event.target.closest(".ce-op-review");
    if (reviewBtn) {
      reviewBtn.disabled = true;
      reviewAndApply(reviewBtn.dataset.opId, null, null)
        .catch(function (e) { alert(e.message); })
        .finally(function () { reviewBtn.disabled = false; });
      return;
    }
    var retryBtn = event.target.closest(".ce-op-retry");
    if (retryBtn) {
      retryBtn.disabled = true;
      var retryPromise = postJson("/api/operations/" + encodeURIComponent(retryBtn.dataset.opId) + "/retry", {});
      pollOperation(retryBtn.dataset.opId);
      retryPromise
        .catch(function (e) { alert(e.message); })
        .finally(function () { retryBtn.disabled = false; renderOperationsList(); });
    }
  });

  async function pushContent(kind, payload, logEl, bannerEl, btn, confirmLabel) {
    var push = window.CE_PUSH || {};
    var targets = typeof push.targetCourses === "function" ? push.targetCourses() : [];
    if (!targets.length) return alert("Check at least one course on the right.");
    var operationKind = operationKinds[kind];
    if (!operationKind) {
      throw new Error("Unsupported push kind: " + kind);
    }
    var log = showLog(logEl);
    hideBanner(bannerEl);
    if (btn) btn.disabled = true;
    try {
      var prepared = await prepareOperation(operationKind, payload, log);
      return await reviewAndApply(prepared.operation_id, log, bannerEl, confirmLabel);
    } catch (e) {
      log("ERROR: " + e.message);
      showBanner(bannerEl, "fail", "✗ " + esc(e.message));
      return null;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  window.CE_PUSH = Object.assign(window.CE_PUSH || {}, {
    esc: esc,
    postForm: postForm,
    postJson: postJson,
    showLog: showLog,
    hideBanner: hideBanner,
    pushContent: pushContent,
    prepareOnly: prepareOnly,
    reviewAndApply: reviewAndApply,
    renderOperationsList: renderOperationsList,
    operationKinds: operationKinds,
    canvasWriteReview: window.CE_WRITE_REVIEW.confirm,
    generatePhysical: generatePhysical,
    showBanner: showBanner,
    setBusy: setBusy,
  });

  renderOperationsList();
})();
