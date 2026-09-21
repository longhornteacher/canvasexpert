(function () {
  "use strict";

  var root = document.getElementById("canvasagent-root");
  if (!root) return;
  var overall = document.getElementById("canvasagent-overall");
  var refreshButton = document.getElementById("canvasagent-refresh");
  var mirrorResult = document.querySelector("[data-mirror-result]");
  var currentClients = {};
  var currentHealth = null;
  var currentReadiness = null;
  var currentMirror = null;
  document.querySelectorAll("[data-client-card]").forEach(function (card) {
    currentClients[card.getAttribute("data-client")] = {
      client: card.getAttribute("data-client"),
      connected: card.dataset.connected === "true",
      current: card.dataset.current === "true",
      detected: card.dataset.detected === "true",
      error: card.dataset.error === "true",
    };
  });

  function setState(node, state) {
    if (node) node.dataset.state = state;
  }
  function say(node, text) {
    if (node) node.textContent = text;
  }
  function responseJson(response) {
    return response.json().catch(function () { return {}; }).then(function (body) {
      return { response: response, body: body };
    });
  }
  function relativeTime(date) {
    var minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
    if (minutes < 1) return "just now";
    if (minutes < 60) return minutes + (minutes === 1 ? " minute ago" : " minutes ago");
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + (hours === 1 ? " hour ago" : " hours ago");
    var days = Math.floor(hours / 24);
    return days + (days === 1 ? " day ago" : " days ago");
  }
  function setComponent(id, status, label, detail) {
    var card = document.getElementById(id);
    if (!card) return;
    setState(card, status);
    say(card.querySelector("[data-" + (id === "canvas-account" ? "canvas" : "mirror") + "-status]"), label);
    say(card.querySelector("[data-" + (id === "canvas-account" ? "canvas" : "mirror") + "-detail]"), detail);
  }
  function componentState(item) {
    return item && item.status ? item.status : "unknown";
  }
  function canvasState(item) {
    var status = componentState(item);
    var code = item && item.code;
    if (status === "ready") return { state: "ready", label: "Ready", detail: "Canvas accepted the saved credentials." };
    if (status === "unconfigured") return { state: "attention", label: "Not configured", detail: "Add the Canvas URL and token in Settings." };
    if (status === "degraded" && code === "unauthorized") return { state: "attention", label: "Credentials rejected", detail: "Canvas rejected this token. Replace it in Settings." };
    if (status === "degraded" && code === "timeout") return { state: "attention", label: "Network timeout", detail: "Canvas did not respond in time. Check the connection and try again." };
    if (status === "degraded") return { state: "attention", label: "Network unavailable", detail: "Canvas could not be reached. Check the connection and try again." };
    return { state: "loading", label: "Checking", detail: "Waiting for a fresh Canvas check…" };
  }
  function renderCanvas(item) {
    var result = canvasState(item);
    setComponent("canvas-account", result.state, result.label, result.detail);
    return result.state;
  }
  function clientState(status) {
    if (!status || status.error) return "unavailable";
    if (status.connected && status.current) return "ready";
    return "attention";
  }
  function renderClient(card, status) {
    var name = card.getAttribute("data-client");
    var state = clientState(status);
    var label = !status || status.error ? "Status unavailable"
      : status.connected && status.current ? "Connected"
      : status.connected ? "Update available"
      : !status.detected ? "App not found" : "Not connected";
    setState(card, state);
    card.dataset.connected = status && status.connected ? "true" : "false";
    card.dataset.current = status && status.current ? "true" : "false";
    card.dataset.detected = status && status.detected ? "true" : "false";
    card.dataset.error = status && status.error ? "true" : "false";
    say(card.querySelector("[data-client-status]"), label);
    var actions = card.querySelector("[data-client-actions]");
    if (!actions) return { state: state, current: false };
    actions.replaceChildren();
    function addAction(action, text, primary) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ce-btn ce-agent-action" + (primary ? " ce-btn--primary" : "");
      button.dataset.action = action;
      button.textContent = text;
      actions.appendChild(button);
    }
    if (status && status.connected && status.current) {
      addAction("connect", "Reconnect", false);
      addAction("disconnect", "Disconnect", false);
    } else if (status && status.connected) {
      addAction("connect", "Update connection", true);
      addAction("disconnect", "Disconnect", false);
    } else {
      addAction("connect", card.getAttribute("data-connect-label") || "Connect", true);
    }
    return { state: state, current: !!(status && status.connected && status.current) };
  }
  function renderMcp(clients, health) {
    var runtime = !!(health && health.python && health.python.available && health.mcp && health.mcp.importable && health.mcp.entrypoint_present);
    var outcomes = [];
    document.querySelectorAll("[data-client-card]").forEach(function (card) {
      var client = card.getAttribute("data-client");
      outcomes.push(renderClient(card, clients && clients[client]));
    });
    var ready = outcomes.some(function (item) { return item.current; });
    var state = !runtime ? "unavailable" : ready ? "ready" : "attention";
    var label = !runtime ? "MCP runtime unavailable" : ready ? "Ready" : "Connect an assistant";
    var detail = !runtime ? "Canvas Expert’s local MCP server is not ready. Check the installation."
      : ready ? "At least one desktop app has a current local connection. An unused app does not affect readiness."
      : "Connect one desktop app to enable local assistant access.";
    var card = document.getElementById("mcp-connections");
    setState(card, state);
    say(card && card.querySelector("[data-mcp-status]"), label);
    say(card && card.querySelector("[data-mcp-detail]"), detail);
    return state;
  }
  function mirrorSummary(data) {
    if (!data || data.ok === false) return { state: "unavailable", label: "Status unavailable", detail: "CanvasMirror status could not be read.", sync: false };
    if (!data.enabled) return { state: "attention", label: "Sync is off", detail: "Enable CanvasMirror in Settings to keep course data current.", sync: false };
    if (!data.workspace_configured) return { state: "attention", label: "Workspace not configured", detail: "Choose a local workspace in Settings before syncing.", sync: false };
    var courses = Array.isArray(data.courses) ? data.courses : [];
    if (!courses.length) return { state: "attention", label: "No current courses", detail: "Select courses in Settings to create a CanvasMirror snapshot.", sync: false };
    var oldest = null;
    var missing = 0;
    courses.forEach(function (course) {
      var passes = course.passes || {};
      var full = (passes.full || {}).last_success_at || "";
      var delta = (passes.delta || {}).last_success_at || "";
      var newest = full > delta ? full : delta;
      if (!newest) { missing += 1; return; }
      if (oldest === null || newest < oldest) oldest = newest;
    });
    if (missing === courses.length) return { state: "attention", label: "Not synced yet", detail: courses.length + " current " + (courses.length === 1 ? "course has" : "courses have") + " no successful sync yet.", sync: true };
    if (missing) return { state: "attention", label: "Some courses not synced", detail: missing + " of " + courses.length + " current courses have no successful sync yet.", sync: true };
    var date = new Date(oldest);
    if (Number.isNaN(date.getTime())) return { state: "unavailable", label: "Status unavailable", detail: "CanvasMirror returned an unreadable sync time.", sync: false };
    var fresh = Date.now() - date.getTime() <= (Number(data.serve_max_age_hours) || 6) * 3600000;
    return {
      state: fresh ? "ready" : "attention",
      label: fresh ? "Current" : "Needs refresh",
      detail: "Oldest current-course sync was " + relativeTime(date) + ".",
      sync: true,
    };
  }
  function renderMirror(data) {
    var result = mirrorSummary(data);
    setComponent("canvas-data", result.state, result.label, result.detail);
    if (refreshButton) {
      refreshButton.hidden = false;
      refreshButton.disabled = !result.sync;
    }
    return result.state;
  }
  function privacyResult(readiness, health, mirror) {
    var privacy = readiness && readiness.components && readiness.components.privacy;
    var state = componentState(privacy);
    var workspace = health && health.workspace;
    var registry = health && health.pseudonym_registry;
    var conflicts = mirror && Array.isArray(mirror.vault_conflict) ? mirror.vault_conflict.length : 0;
    if (conflicts) return { state: "attention", label: "Review privacy files", detail: "Conflicting identity-vault copies were found. Review local privacy settings before using student data." };
    if (state === "ready" && workspace && workspace.configured && workspace.writable && registry && registry.configured && !registry.low_runway) {
      return { state: "ready", label: "Ready", detail: "Workspace is writable and local privacy protections are available." };
    }
    if (state === "degraded" || (workspace && workspace.configured && !workspace.writable)) {
      return { state: "unavailable", label: "Safety check needs repair", detail: "A local workspace or privacy protection is unavailable. Review Settings before working with student data." };
    }
    if (state === "ready" && registry && registry.low_runway) return { state: "attention", label: "Privacy review needed", detail: "The local pseudonym registry is running low. Review it in Settings." };
    if (state === "ready" && (!registry || !registry.configured)) return { state: "attention", label: "Privacy registry unavailable", detail: "The local pseudonym registry could not be confirmed. Review local privacy settings before working with student data." };
    if (state === "unconfigured" || (workspace && !workspace.configured)) return { state: "attention", label: "Workspace not configured", detail: "Choose a local workspace in Settings to enable privacy protections." };
    return { state: "loading", label: "Checking", detail: "Waiting for the local workspace and privacy checks…" };
  }
  function renderPrivacy(readiness, health, mirror) {
    var result = privacyResult(readiness, health, mirror);
    var card = document.getElementById("local-privacy");
    setState(card, result.state);
    say(card && card.querySelector("[data-privacy-status]"), result.label);
    say(card && card.querySelector("[data-privacy-detail]"), result.detail);
    return result.state;
  }
  function renderOverall(states) {
    var state = states.indexOf("unavailable") !== -1 ? "unavailable"
      : states.indexOf("loading") !== -1 ? "loading"
      : states.indexOf("attention") !== -1 ? "attention" : "ready";
    setState(overall, state);
    say(overall.querySelector("[data-overall-label]"), state === "ready" ? "Ready"
      : state === "attention" ? "Needs attention"
      : state === "unavailable" ? "Unavailable" : "Checking status");
    say(overall.querySelector("[data-overall-detail]"), state === "ready"
      ? "Local assistant access, Canvas access, course data, and privacy checks are ready."
      : state === "attention" ? "One or more checks need attention. Canvas has not been changed."
      : state === "unavailable" ? "A required local service or privacy safeguard is unavailable. Canvas has not been changed."
      : "Loading the current status. This page does not change Canvas.");
    say(overall.querySelector(".ce-agent-state-icon"), state === "ready" ? "✓" : state === "attention" ? "!" : state === "unavailable" ? "×" : "…");
    root.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }
  function rerenderCurrent() {
    var readiness = currentReadiness;
    var health = currentHealth;
    var mirror = currentMirror;
    var states = [renderMcp(currentClients, health), renderCanvas(readiness && readiness.components && readiness.components.canvas), renderMirror(mirror), renderPrivacy(readiness, health, mirror)];
    renderOverall(states);
  }
  function update(readiness, health, mirror) {
    currentHealth = health;
    currentReadiness = readiness;
    currentMirror = mirror;
    rerenderCurrent();
  }
  function loadHealthAndMirror() {
    return Promise.all([
      fetch("/api/connections/health", { headers: { Accept: "application/json" } }).then(responseJson),
      fetch("/api/mirror/status", { headers: { Accept: "application/json" } }).then(responseJson),
    ]).then(function (results) {
      if (!results[0].response.ok || !results[1].response.ok) throw new Error("status_unavailable");
      return { health: results[0].body, mirror: results[1].body };
    });
  }
  function refreshAll() {
    Promise.all([
      loadHealthAndMirror(),
      fetch("/api/readiness/probe?force=true", { method: "POST", headers: { Accept: "application/json" } }).then(responseJson),
    ]).then(function (results) {
      if (!results[1].response.ok || results[1].body.ok === false) throw new Error("readiness_unavailable");
      update(results[1].body, results[0].health, results[0].mirror);
    }).catch(function () {
      renderOverall(["unavailable"]);
      setComponent("canvas-account", "unavailable", "Check unavailable", "Canvas or local readiness could not be checked. Try again or review Settings.");
      setComponent("canvas-data", "unavailable", "Status unavailable", "CanvasMirror status could not be checked.");
      setComponent("local-privacy", "unavailable", "Safety check unavailable", "Local privacy health could not be confirmed. Review Settings before working with student data.");
      var mcp = document.getElementById("mcp-connections");
      setState(mcp, "unavailable");
      say(mcp && mcp.querySelector("[data-mcp-status]"), "Status unavailable");
      say(mcp && mcp.querySelector("[data-mcp-detail]"), "Local MCP health could not be confirmed.");
    });
  }
  function setResult(node, message, isError) {
    say(node, message || "");
    if (node) node.dataset.error = isError ? "true" : "false";
  }
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest(".ce-agent-action");
    if (!button) return;
    var card = button.closest("[data-client-card]");
    if (!card) return;
    var client = card.getAttribute("data-client");
    var action = button.dataset.action;
    var resultNode = card.querySelector("[data-client-result]");
    card.querySelectorAll("button").forEach(function (item) { item.disabled = true; });
    setResult(resultNode, action === "disconnect" ? "Disconnecting…" : "Connecting…", false);
    fetch("/api/connections/" + encodeURIComponent(client) + "/" + action, { method: "POST", headers: { Accept: "application/json" } })
      .then(responseJson).then(function (result) {
        var body = result.body || {};
        if (!result.response.ok || !body.ok || !body.status) throw new Error(body.detail || "That action could not be completed.");
        currentClients[client] = body.status;
        renderClient(card, body.status);
        setResult(resultNode, action === "disconnect" ? "Disconnected." : "Connected. " + (card.dataset.restartNote || ""), false);
        loadHealthAndMirror().then(function (statuses) {
          currentHealth = statuses.health;
          currentMirror = statuses.mirror;
          rerenderCurrent();
        }).catch(function () {});
      }).catch(function (error) {
        setResult(resultNode, error.message || "Could not reach Canvas Expert. Is it still running?", true);
      }).finally(function () { card.querySelectorAll("button").forEach(function (item) { item.disabled = false; }); });
  });
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    return Promise.resolve();
  }
  function agentCoreOf(text) {
    var start = text.indexOf("CORE: begin");
    var end = text.indexOf("CORE: end");
    if (start < 0 || end < start) return null;
    return text.slice(text.indexOf("\n", start) + 1, end).replace(/=+\s*$/, "").trim();
  }
  document.querySelectorAll("[data-agent-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var result = document.querySelector("[data-agent-result]");
      var kind = button.dataset.agentCopy;
      button.disabled = true;
      fetch("/api/download-contract?name=CanvasAgent").then(function (response) {
        if (!response.ok) throw new Error("download_unavailable");
        return response.text();
      }).then(function (text) {
        var payload = kind === "core" ? agentCoreOf(text) : text;
        if (!payload) throw new Error("instructions_unavailable");
        return copyText(payload).then(function () { setResult(result, "Instructions copied to the clipboard.", false); });
      }).catch(function () { setResult(result, "Copy failed. Download the file and copy from it instead.", true); })
        .finally(function () { button.disabled = false; });
    });
  });
  document.querySelectorAll("[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      copyText(target.textContent || "").then(function () { button.textContent = "Copied"; })
        .catch(function () { button.textContent = "Copy failed"; });
    });
  });
  function pollPlan(planId, attempts) {
    return fetch("/api/mirror/status?plan_id=" + encodeURIComponent(planId), { headers: { Accept: "application/json" } })
      .then(responseJson).then(function (result) {
        var plans = result.body && result.body.plan && result.body.plan.plans;
        var plan = Array.isArray(plans) ? plans[0] : null;
        if (!result.response.ok || !plan) throw new Error("Could not read the refresh status.");
        if (["succeeded", "failed", "cancelled"].indexOf(plan.state) !== -1) return plan;
        if (!attempts) throw new Error("Refresh is still running. Check Canvas data again shortly.");
        setResult(mirrorResult, "Refreshing Canvas data…", false);
        return new Promise(function (resolve) { setTimeout(resolve, 750); }).then(function () { return pollPlan(planId, attempts - 1); });
      });
  }
  if (refreshButton) refreshButton.addEventListener("click", function () {
    refreshButton.disabled = true;
    setResult(mirrorResult, "Starting read-only Canvas refresh…", false);
    fetch("/api/mirror/sync-now", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" }, body: "" })
      .then(responseJson).then(function (result) {
        if (!result.response.ok || !result.body.plan_id) throw new Error("Canvas refresh could not be started.");
        return pollPlan(result.body.plan_id, 160);
      }).then(function (plan) {
        if (plan.state === "failed") throw new Error("Canvas refresh failed. Check your Canvas connection and try again.");
        setResult(mirrorResult, plan.state === "succeeded" ? "Refresh complete." : "Refresh stopped.", plan.state !== "succeeded");
        return fetch("/api/mirror/status", { headers: { Accept: "application/json" } }).then(responseJson);
      }).then(function (result) { if (result.response.ok) { currentMirror = result.body; rerenderCurrent(); } })
      .catch(function (error) { setResult(mirrorResult, error.message || "Canvas refresh failed.", true); })
      .finally(function () { refreshButton.disabled = false; });
  });

  refreshAll();
})();
