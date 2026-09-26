/* ── Onboarding wizard (welcome.html) ─────────────────────────────────────
 * Step navigation and form submission for the first-run flow.
 * Workspace → Canvas URL → API token → Done.
 */

var WIZARD_HIDDEN_CLASS = "ce-wizard-initially-hidden";

function setWizardVisible(el, visible) {
  if (el) el.classList.toggle(WIZARD_HIDDEN_CLASS, !visible);
}

var WIZARD = {
  currentStep: 0,
  totalSteps:  2,

  // ── Step tracking ────────────────────────────────────────────────────

  goTo: function (step) {
    var panels = document.querySelectorAll('.wizard-panel');
    var indicators = document.querySelectorAll('.wiz-step');
    for (var i = 0; i < panels.length; i++) {
      setWizardVisible(panels[i], i === step);
      if (indicators[i]) {
        indicators[i].classList.toggle('active', i === step);
        indicators[i].classList.toggle('done', i < step);
      }
    }
    this.currentStep = step;
  },

  next: function () {
    if (this.currentStep < this.totalSteps) {
      this.goTo(this.currentStep + 1);
    }
  },

  back: function () {
    if (this.currentStep > 0) {
      this.goTo(this.currentStep - 1);
    }
  },

  finish: function () {
    // Show the success panel by id (it's not a numbered step, so goTo() can't reach it).
    var panels = document.querySelectorAll('.wizard-panel');
    for (var i = 0; i < panels.length; i++) {
      setWizardVisible(panels[i], panels[i].id === 'step-done');
    }
    this.currentStep = 99;
  },

  // ── Step 0: Workspace ─────────────────────────────────────────────────

  browseWorkspace: function (e) {
    var btn = e.target;
    var input = document.getElementById('workspace-path');
    btn.disabled = true;
    var originalLabel = btn.textContent;
    btn.textContent = 'Choosing…';

    fetch('/welcome/browse-workspace', { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.ok && d.path) {
        input.value = d.path;
      } else if (!d.ok) {
        WIZARD.showResult('workspace-result', 'Could not open a folder picker on this computer. Type the path instead.', true);
      }
    })
    .catch(function () {
      WIZARD.showResult('workspace-result', 'Network error. Please try again.', true);
    })
    .finally(function () {
      btn.disabled = false;
      btn.textContent = originalLabel;
    });
  },

  saveWorkspace: function (e) {
    e.preventDefault();
    var path = document.getElementById('workspace-path').value.trim();
    if (!path) return;

    var btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Saving…';

    fetch('/welcome/workspace', {
      method: 'POST',
      body: new URLSearchParams({ path: path }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var result = document.getElementById('workspace-result');
      var done = document.getElementById('workspace-done');
      if (d.ok) {
        result.className = 'ce-notice ce-notice--ok';
        result.innerHTML = '✓ Workspace created at <strong>' + d.root + '</strong>' +
          '<div class="subfolder-list">' +
          d.subfolders.map(function (s) { return '<span>' + s + '</span>'; }).join('') +
          '</div>';
        setWizardVisible(result, true);
        setWizardVisible(document.getElementById('workspace-form'), false);
        setWizardVisible(done, true);
        WIZARD.markStepDone(0);
      } else {
        result.className = 'ce-notice';
        result.textContent = d.error || 'Something went wrong.';
        setWizardVisible(result, true);
        btn.disabled = false;
        btn.textContent = 'Confirm folder';
      }
    })
    .catch(function () {
      var result = document.getElementById('workspace-result');
      result.className = 'ce-notice';
      result.textContent = 'Network error. Please try again.';
      setWizardVisible(result, true);
      btn.disabled = false;
      btn.textContent = 'Confirm folder';
    });
  },

  // ── Step 1: Canvas URL ───────────────────────────────────────────────

  saveCanvasUrl: function (e) {
    e.preventDefault();
    var raw = document.getElementById('canvas-url').value.trim();
    if (!raw) return;

    // Normalize: prepend https:// if missing, strip path, strip trailing slash
    var url = raw;
    if (!/^https?:\/\//i.test(url)) {
      url = 'https://' + url;
    }
    try {
      var parsed = new URL(url);
      url = parsed.origin; // protocol + host only
    } catch (_) {
      WIZARD.showResult('canvas-url-result', 'Invalid URL. Please enter a valid web address.', true);
      return;
    }

    var btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Saving…';

    fetch('/settings/canvas', {
      method: 'POST',
      body: new URLSearchParams({ base_url: url }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var result = document.getElementById('canvas-url-result');
      var done = document.getElementById('canvas-url-done');
      var form = document.getElementById('canvas-url-form');
      if (d.ok) {
        // Write the normalized origin back so the token step reads the clean base.
        document.getElementById('canvas-url').value = url;
        result.className = 'ce-notice ce-notice--ok';
        result.textContent = '✓ Saved: ' + url;
        setWizardVisible(result, true);
        setWizardVisible(form, false);
        setWizardVisible(done, true);
        // Update the token step's Canvas link
        var link = document.getElementById('token-canvas-link');
        if (link) link.href = url + '/profile/settings';
        WIZARD.markStepDone(1);
      } else {
        result.className = 'ce-notice';
        result.textContent = d.error || 'Failed to save.';
        setWizardVisible(result, true);
        btn.disabled = false;
        btn.textContent = 'Save & continue';
      }
    })
    .catch(function () {
      WIZARD.showResult('canvas-url-result', 'Network error. Please try again.', true);
      btn.disabled = false;
      btn.textContent = 'Save & continue';
    });
  },

  // ── Step 2: API Token ────────────────────────────────────────────────

  saveToken: function (e) {
    e.preventDefault();
    var token = document.getElementById('token-input').value.trim();
    if (!token) return;

    // Use the base saved in step 1. Normalize defensively in case this step is
    // reached directly (re-run) or the value wasn't normalized yet.
    var baseUrl = document.getElementById('canvas-url').value.trim();
    if (baseUrl && !/^https?:\/\//i.test(baseUrl)) baseUrl = 'https://' + baseUrl;
    try { baseUrl = new URL(baseUrl).origin; } catch (_) { /* leave as-is; server will error */ }

    var btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Testing…';

    // First test the connection
    fetch('/settings/test-connection', {
      method: 'POST',
      body: new URLSearchParams({ base_url: baseUrl, token: token }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var result = document.getElementById('token-result');
      var done = document.getElementById('token-done');
      var form = document.getElementById('token-form');
      if (d.ok) {
        // Connection test passed — now save token with the base URL
        return fetch('/settings/canvas', {
          method: 'POST',
          body: new URLSearchParams({ base_url: baseUrl, token: token }),
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
        })
        .then(function (r2) { return r2.json(); })
        .then(function (d2) {
          if (d2.ok) {
            document.getElementById('connected-name').textContent = d.display_name;
            setWizardVisible(result, false);
            setWizardVisible(form, false);
            setWizardVisible(done, true);
            WIZARD.markStepDone(2);
          } else {
            throw new Error(d2.error || 'Save failed');
          }
        });
      } else {
        result.className = 'ce-notice';
        result.textContent = d.error || 'Connection test failed.';
        setWizardVisible(result, true);
        btn.disabled = false;
        btn.textContent = 'Test & save';
      }
    })
    .catch(function (err) {
      WIZARD.showResult('token-result', err.message || 'Network error. Please try again.', true);
      btn.disabled = false;
      btn.textContent = 'Test & save';
    });
  },

  // ── Helpers ──────────────────────────────────────────────────────────

  markStepDone: function (step) {
    var indicators = document.querySelectorAll('.wiz-step');
    if (indicators[step]) {
      indicators[step].classList.remove('active');
      indicators[step].classList.add('done');
    }
  },

  showResult: function (id, msg, isWarn) {
    var el = document.getElementById(id);
    if (!el) return;
    el.className = 'ce-notice' + (isWarn ? '' : ' ce-notice--ok');
    el.textContent = msg;
    setWizardVisible(el, true);
  }
};

// Make step functions globally accessible from inline onclick handlers
var wizardNext = WIZARD.next.bind(WIZARD);
var wizardBack = WIZARD.back.bind(WIZARD);
var wizardFinish = WIZARD.finish.bind(WIZARD);

// ── Init ───────────────────────────────────────────────────────────────
(function () {
  // Step 0: workspace form
  var wsForm = document.getElementById('workspace-form');
  if (wsForm) wsForm.addEventListener('submit', WIZARD.saveWorkspace.bind(WIZARD));
  var wsBrowseBtn = document.getElementById('workspace-browse');
  if (wsBrowseBtn) wsBrowseBtn.addEventListener('click', WIZARD.browseWorkspace.bind(WIZARD));

  // Step 1: Canvas URL form
  var urlForm = document.getElementById('canvas-url-form');
  if (urlForm) urlForm.addEventListener('submit', WIZARD.saveCanvasUrl.bind(WIZARD));

  // Step 2: Token form
  var tokenForm = document.getElementById('token-form');
  if (tokenForm) tokenForm.addEventListener('submit', WIZARD.saveToken.bind(WIZARD));

  // Pre-fill Canvas URL input if already saved (re-run scenario)
  var canvasUrlInput = document.getElementById('canvas-url');
  var savedBase = canvasUrlInput ? canvasUrlInput.getAttribute('data-saved') : '';
  if (savedBase && canvasUrlInput) {
    canvasUrlInput.value = savedBase;
  }

  // Pre-fill workspace path suggestion from template
  var wsInput = document.getElementById('workspace-path');
  if (wsInput && wsInput.value.endsWith('\\CanvasExpert')) {
    // Already filled by template — let user adjust
  }
})();
