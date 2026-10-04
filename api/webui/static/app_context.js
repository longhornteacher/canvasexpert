(function () {
  "use strict";

  var STORAGE_KEY = "canvasExpert.context.v1";
  var listeners = [];
  var state = { focusedCourse: null, targetCourses: [] };

  function course(value) {
    if (value == null) return null;
    var id = typeof value === "object" ? value.id : value;
    if (id == null || String(id).trim() === "") return null;
    var name = typeof value === "object" && value.name != null ? String(value.name) : "";
    return { id: String(id), name: name };
  }

  function courses(values) {
    if (!Array.isArray(values)) return [];
    var seen = new Set();
    return values.map(course).filter(function (item) {
      if (!item || seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function readJson(key) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function load() {
    var saved = readJson(STORAGE_KEY);
    if (saved && typeof saved === "object") {
      state.focusedCourse = course(saved.focusedCourse);
      state.targetCourses = courses(saved.targetCourses);
      return;
    }
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // Context persistence is best effort; page behavior remains usable.
    }
  }

  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function publish(source, previous) {
    var detail = {
      focusedCourse: copy(state.focusedCourse),
      targetCourses: copy(state.targetCourses),
      source: source || "unknown",
    };
    persist();
    listeners.slice().forEach(function (callback) {
      try { callback(copy(detail)); } catch (e) { /* subscriber isolation */ }
    });
    document.dispatchEvent(new CustomEvent("ce:contextchange", { detail: detail }));
    return previous;
  }

  function mutate(next, source) {
    var previous = copy(state);
    if (same(previous, next)) return snapshot();
    state = next;
    publish(source, previous);
    return snapshot();
  }

  function snapshot() {
    return copy(state);
  }

  function setFocus(value, source) {
    return mutate({ focusedCourse: course(value), targetCourses: state.targetCourses }, source);
  }

  function setTargets(values, source) {
    return mutate({ focusedCourse: state.focusedCourse, targetCourses: courses(values) }, source);
  }

  function reconcile(available, options) {
    options = options || {};
    if (!options.authoritative) return snapshot();
    var ids = new Set(courses(available).map(function (item) { return item.id; }));
    var nextTargets = state.targetCourses.filter(function (item) { return ids.has(item.id); });
    var nextFocus = state.focusedCourse && ids.has(state.focusedCourse.id)
      ? state.focusedCourse
      : null;
    return mutate({ focusedCourse: nextFocus, targetCourses: nextTargets }, options.source);
  }

  function subscribe(callback) {
    if (typeof callback !== "function") return function () {};
    listeners.push(callback);
    return function () {
      listeners = listeners.filter(function (item) { return item !== callback; });
    };
  }

  load();
  window.CE_CONTEXT = {
    snapshot: snapshot,
    setFocus: setFocus,
    setTargets: setTargets,
    reconcile: reconcile,
    subscribe: subscribe,
  };
})();
