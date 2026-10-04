(function () {
  "use strict";
  var CE = window.CE_SETTINGS || {};
  var workspaceCard = document.getElementById("workspace-card");
  var accountStatus = document.getElementById("account-status");
  document.getElementById("btn-workspace-open")?.addEventListener("click", async function () {
    var root = workspaceCard ? (workspaceCard.dataset.path || "") : "";
    if (!root) return CE.setStatus(accountStatus, "No OneDrive workspace is available on this machine.", "error");
    var r = await fetch("/api/open-folder", {
      method: "POST",
      body: new URLSearchParams({ path: root }),
    });
    var d = await r.json();
    if (d.ok) CE.setStatus(accountStatus, "Workspace opened in Explorer.", "ok");
    else CE.setStatus(accountStatus, d.error || "Could not open workspace folder.", "error");
  });

})();
