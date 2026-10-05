// Focused tests for the Downloads context-menu action-targeting logic.
//
// Loads the real app.js (via indirect eval) with minimal load-time mocks and
// exercises the actual `App._actionTargetIds`, `App._asIds`, `App._forEachId`,
// `App.rowCallbacks.onMove`, `onCopyUrl`, `onCancel`, `onPause`.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_JS = path.resolve(__dirname, "../../ui/frontend/js/app.js");

// Minimal load-time mocks: app.js only runs `document.addEventListener` at the
// top level; every other global is referenced inside methods at call time.
globalThis.document = { addEventListener() {} };
globalThis.window = globalThis;
globalThis.console = console;

// Default call-time mocks (overridden per-test where needed).
globalThis.I18N = { t(k, f) { return f; } };
globalThis.API = { logJs() {}, moveTask() {}, pauseDownload() {}, resumeDownload() {}, startTask() {}, retryDownload() {}, cancelDownload() {}, removeDownload() {} };
globalThis.Components = { toast() {}, confirm: async () => true };
globalThis.Utils = { $qa: () => [], fileName: (t) => (t && t.filename) || "" };
// Node 21+ exposes a read-only `navigator` global; add a mock clipboard to it.
const nav = globalThis.navigator || {};
nav.clipboard = nav.clipboard || { writeText: async () => {} };

const code = fs.readFileSync(APP_JS, "utf8");
const dlView = fs.readFileSync(path.resolve(__dirname, "../../ui/frontend/js/features/downloads/downloads-view.js"), "utf8");
const dlActions = fs.readFileSync(path.resolve(__dirname, "../../ui/frontend/js/features/downloads/downloads-actions.js"), "utf8");
// Concatenate the feature modules the same way the browser script tags
// do, so the real code (not stubs) still runs under test.
const App = (0, eval)(dlView + "\n" + dlActions + "\n" + code + "\n;App");
// App.init is not run by this harness; rebuild the callback table itself.
App.rowCallbacks = App._buildRowCallbacks();

const tick = () => new Promise((r) => setTimeout(r, 0));

test("5 selected + right-click a selected item -> 5 targets", () => {
  App.state.selectedIds = new Set(["a", "b", "c", "d", "e"]);
  assert.deepEqual(App._actionTargetIds("c"), ["a", "b", "c", "d", "e"]);
});

test("5 selected + right-click an unselected item -> 1 target", () => {
  App.state.selectedIds = new Set(["a", "b", "c", "d", "e"]);
  assert.deepEqual(App._actionTargetIds("x"), ["x"]);
});

test("1 selected + right-click the selected item -> 1 target", () => {
  App.state.selectedIds = new Set(["a"]);
  assert.deepEqual(App._actionTargetIds("a"), ["a"]);
});

test("empty selection + right-click -> 1 target", () => {
  App.state.selectedIds = new Set();
  assert.deepEqual(App._actionTargetIds("a"), ["a"]);
});

test("resolving targets does not mutate the selection", () => {
  const sel = new Set(["a", "b", "c"]);
  App.state.selectedIds = sel;
  App._actionTargetIds("b");
  assert.deepEqual([...App.state.selectedIds], ["a", "b", "c"]);
});

test("_asIds normalizes single id and arrays", () => {
  assert.deepEqual(App._asIds("a"), ["a"]);
  assert.deepEqual(App._asIds(["a", "b"]), ["a", "b"]);
});

test("_forEachId dispatches to every id and survives per-item failure", async () => {
  const got = [];
  await App._forEachId(["a", "b", "c", "d", "e"], async (id) => {
    if (id === "c") throw new Error("boom");
    got.push(id);
  });
  assert.deepEqual(got, ["a", "b", "d", "e"]);
});

test("Move up preserves selected group order (forward)", () => {
  const calls = [];
  globalThis.API.moveTask = (id, delta) => calls.push([id, delta]);
  globalThis.Utils.$qa = () => ["a", "b", "c", "d", "e", "f"].map((id) => ({ dataset: { id } }));
  App.rowCallbacks.onMove(["b", "c", "d"], -1);
  assert.deepEqual(calls, [["b", -1], ["c", -1], ["d", -1]]);
});

test("Move down preserves selected group order (reverse)", () => {
  const calls = [];
  globalThis.API.moveTask = (id, delta) => calls.push([id, delta]);
  globalThis.Utils.$qa = () => ["a", "b", "c", "d", "e", "f"].map((id) => ({ dataset: { id } }));
  App.rowCallbacks.onMove(["b", "c", "d"], 1);
  assert.deepEqual(calls, [["d", 1], ["c", 1], ["b", 1]]);
});

test("Copy URL copies all selected URLs, one per line", async () => {
  let copied = null;
  globalThis.navigator.clipboard.writeText = async (t) => { copied = t; };
  App.state.downloads = {
    a: { url: "https://example.com/1.zip" },
    b: { url: "https://example.com/2.zip" },
    c: { url: "https://example.com/3.zip" },
  };
  await App.rowCallbacks.onCopyUrl(["a", "b", "c"]);
  assert.equal(copied, "https://example.com/1.zip\nhttps://example.com/2.zip\nhttps://example.com/3.zip");
});

test("Cancel dispatches to all selected ids (single confirm)", async () => {
  const cancelled = [];
  globalThis.Components.confirm = async () => true;
  globalThis.API.cancelDownload = (id) => cancelled.push(id);
  await App.rowCallbacks.onCancel(["a", "b", "c", "d", "e"]);
  await tick();
  assert.deepEqual(cancelled, ["a", "b", "c", "d", "e"]);
});

test("Pause dispatches to all selected ids", async () => {
  const paused = [];
  globalThis.API.pauseDownload = (id) => paused.push(id);
  App.rowCallbacks.onPause(["a", "b", "c"]);
  await tick();
  assert.deepEqual(paused, ["a", "b", "c"]);
});
