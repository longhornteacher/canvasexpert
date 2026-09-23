(function () {
  "use strict";

  var status = document.getElementById("identity-vault-key-status");
  var showButton = document.getElementById("identity-vault-show-key");
  var copyButton = document.getElementById("identity-vault-copy-key");
  var transferWrap = document.getElementById("identity-vault-transfer-wrap");
  var transferInput = document.getElementById("identity-vault-transfer-key");
  var importInput = document.getElementById("identity-vault-import-key");
  var saveButton = document.getElementById("identity-vault-save-key");
  if (!status || !showButton || !copyButton || !importInput || !saveButton) return;

  var transferKey = "";
  function setStatus(message, isError) {
    status.textContent = message;
    status.dataset.error = isError ? "true" : "false";
  }
  function post(action, secret) {
    var body = new URLSearchParams({ action: action, secret: secret || "" });
    return fetch("/settings/identity-vault-secret", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: body.toString(),
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Identity Vault key action failed.");
        return payload;
      });
    });
  }
  function loadStatus() {
    fetch("/settings/identity-vault", { headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error === "identity_seed_mismatch"
            ? "This device's legacy vault differs from the shared seed. Stop using student tools and resolve the migration mismatch."
            : "Identity Vault status is unavailable. Review Local workspace & privacy.", true);
          return;
        }
        var message = data.configured
          ? "This device has the shared key. Fingerprint: " + data.fingerprint + "."
          : "No shared key is stored on this device. Show the transfer key on the primary device, then enter it below.";
        if (data.conflicts) message += " Shared-store conflicts are blocking writes.";
        setStatus(message, !!data.conflicts);
      })
      .catch(function () { setStatus("Identity Vault status is unavailable.", true); });
  }

  showButton.addEventListener("click", function () {
    showButton.disabled = true;
    setStatus("Loading the transfer key…", false);
    post("reveal").then(function (data) {
      transferKey = data.secret;
      transferInput.value = transferKey;
      transferWrap.hidden = false;
      copyButton.disabled = false;
      setStatus("Transfer key shown for manual device setup. Fingerprint: " + data.fingerprint + ". Keep this key private.", false);
    }).catch(function (error) {
      transferKey = "";
      transferInput.value = "";
      transferWrap.hidden = true;
      copyButton.disabled = true;
      setStatus(error.message || "The key could not be shown.", true);
    }).finally(function () { showButton.disabled = false; });
  });

  copyButton.addEventListener("click", function () {
    if (!transferKey) return;
    navigator.clipboard.writeText(transferKey).then(function () {
      setStatus("Transfer key copied. Paste it into Settings on the other device, then compare fingerprints.", false);
    }).catch(function () {
      setStatus("Copy failed. Use Show transfer key again and copy it manually.", true);
    });
  });

  saveButton.addEventListener("click", function () {
    var value = importInput.value.trim();
    if (!/^[0-9a-fA-F]{64}$/.test(value)) {
      setStatus("Enter the 64-character key copied from the other device.", true);
      return;
    }
    saveButton.disabled = true;
    post("save", value).then(function (data) {
      importInput.value = "";
      setStatus("Saved in Credential Manager. Fingerprint: " + data.fingerprint + ". Confirm it matches the other device.", false);
      loadStatus();
    }).catch(function (error) {
      setStatus(error.message || "The key could not be saved.", true);
    }).finally(function () { saveButton.disabled = false; });
  });

  window.addEventListener("pagehide", function () {
    transferKey = "";
    transferInput.value = "";
    transferWrap.hidden = true;
  });

  loadStatus();
})();
