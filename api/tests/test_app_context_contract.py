"""Behavioral contract tests for the shared browser course context."""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "api/webui/static/app_context.js").read_text(encoding="utf-8")


def run_context_assertions():
    script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.env.CE_CONTEXT_SOURCE, "utf8");

function boot(seed) {
  const store = Object.assign({}, seed || {});
  const events = [];
  const document = {
    dispatchEvent(event) { events.push(event); },
  };
  const localStorage = {
    getItem(key) { return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null; },
    setItem(key, value) { store[key] = String(value); },
  };
  function CustomEvent(type, options) { this.type = type; this.detail = options.detail; }
  const sandbox = { window: {}, document, localStorage, CustomEvent, JSON, Set };
  vm.runInNewContext(source, sandbox);
  return { context: sandbox.window.CE_CONTEXT, store, events };
}

let b = boot();
if (JSON.stringify(b.context.snapshot()) !== JSON.stringify({focusedCourse:null,targetCourses:[]})) throw new Error("empty state");
let calls = 0;
const unsubscribe = b.context.subscribe(detail => { calls += 1; if (detail.source !== "test") throw new Error("source"); });
b.context.setTargets([{id: 7,name:"A"},{id:"7",name:"duplicate"},{id:"8",name:"B"}], "test");
b.context.setFocus({id: 9,name:"Focus"}, "test");
let s = b.context.snapshot();
if (JSON.stringify(s.targetCourses.map(x => x.id)) !== JSON.stringify(["7","8"])) throw new Error("target normalization");
if (s.focusedCourse.id !== "9") throw new Error("focus independence");
if (calls !== 2 || b.events.length !== 2 || b.events[1].detail.targetCourses.length !== 2) throw new Error("events");
unsubscribe();
b.context.setTargets([], "after-unsubscribe");
if (calls !== 2) throw new Error("unsubscribe");
b.context.setTargets([{id:"stale",name:"Stale"}], "test");
b.context.reconcile([{id:"fresh",name:"Fresh"}], {source:"bookmark", authoritative:false});
if (b.context.snapshot().targetCourses[0].id !== "stale") throw new Error("non-authoritative prune");
b.context.reconcile([{id:"fresh",name:"Fresh"}], {source:"all-courses", authoritative:true});
if (b.context.snapshot().targetCourses.length !== 0 || b.context.snapshot().focusedCourse !== null) throw new Error("authoritative prune");

b = boot({"canvasExpert.push.coursePicker.v1": JSON.stringify({selectedIds:[12,"13"],focusedId:13})});
if (b.context.snapshot().focusedCourse !== null || b.context.snapshot().targetCourses.length !== 0) throw new Error("legacy state ignored");
if (b.store["canvasExpert.push.coursePicker.v1"] !== JSON.stringify({selectedIds:[12,"13"],focusedId:13}) || b.store["canvasExpert.context.v1"]) throw new Error("legacy state untouched");
b = boot({"canvasExpert.context.v1":"not-json"});
if (b.context.snapshot().targetCourses.length !== 0) throw new Error("malformed storage");
"ok";
"""
    env = {"CE_CONTEXT_SOURCE": str(ROOT / "api/webui/static/app_context.js")}
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, env={**__import__("os").environ, **env}, check=False)
    assert result.returncode == 0, result.stderr or result.stdout


def test_app_context_state_matrix():
    run_context_assertions()


def test_app_context_source_exposes_only_contract_methods():
    assert "window.CE_CONTEXT =" in SOURCE
    for name in ["snapshot", "setFocus", "setTargets", "reconcile", "subscribe"]:
        assert f"{name}: {name}" in SOURCE
    assert "canvasExpert.context.v1" in SOURCE
    assert "canvasExpert.push.coursePicker.v1" not in SOURCE
