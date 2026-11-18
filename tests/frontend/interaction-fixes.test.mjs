// Regression tests for the verified Phase-3 interaction bugs:
//  - queue reorders must translate a waiting-row index into the backend's
//    absolute _order index (running tasks live in that array too);
//  - context-menu actions must target only the rows they can affect, and
//    "Open folder" must follow a completed row in a mixed selection.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");

globalThis.document = {
  addEventListener() {},
  readyState: "complete",
  hidden: false,
  activeElement: null,
  createElement() { return { className: "", innerHTML: "", appendChild() {}, setAttribute() {} }; },
};
globalThis.window = globalThis;
globalThis.console = console;
globalThis.I18N = {
  t: (k, f) => (f !== undefined ? f : k),
  fmt: (k, v, f) => (f !== undefined ? f : k),
  onChange() {},
};
globalThis.Utils = {
  $id: () => null,
  $qa: () => [],
  icon: () => "",
  escapeHtml: (s) => String(s),
  fileName: (t) => (t && t.filename) || "",
  formatSpeed: () => "",
  formatETA: () => "",
  formatSize: () => "",
  hostOf: () => "",
  statusLabel: (s) => s,
  clamp: (v, a, b) => Math.min(b, Math.max(a, v)),
};
globalThis.API = { logJs() {}, queuePlan: async () => null, reorderTasks() {}, moveTaskTo() {}, moveToBottom() {} };
globalThis.Components = { toast() {}, confirm: async () => true, priorityDialog: async () => null };
globalThis.App = { state: { page: "queue", downloads: {}, settings: {} } };

const Queue = (0, eval)(
  fs.readFileSync(path.join(root, "ui/frontend/js/queue.js"), "utf8") + "\n;Queue"
);
const ContextMenuUI = (0, eval)(
  fs.readFileSync(path.join(root, "ui/frontend/js/components/context-menu.js"), "utf8") + "\n;ContextMenuUI"
);

function task(id, state, queue_index) {
  return { id, state, queue_index, filename: `${id}.zip`, priority: 5, total: 100, completed: 0 };
}

test("waiting-row position 0 maps to the first waiting task's absolute index", () => {
  // A running task owns _order[0]; the two waiting rows are at 1 and 2.
  App.state.downloads = {
    active: task("active", "Downloading", 0),
    q1: task("q1", "Queued", 1),
    q2: task("q2", "Queued", 2),
  };
  const rows = ["q1", "q2"];
  assert.equal(Queue._absolutePosition(rows, ["q2"], 0), 1);
  assert.equal(Queue._absolutePosition(rows, ["q1"], 1), 2);
  // Moving the first waiting row past the last one lands at the end of the
  // full order with the moved row removed (active + q2 => absolute index 2).
  assert.equal(Queue._absolutePosition(rows, ["q1"], 2), 2);
});

test("selection actions target only eligible rows in a mixed selection", () => {
  const tasks = [
    task("q", "Queued", 0),
    task("d", "Downloading", 1),
    task("c", "Complete", 2),
  ];
  const cb = { onCancel() {}, onPause() {}, onOpenFolder() {} };
  const actions = ContextMenuUI.selectionActions(tasks, cb);
  const cancel = actions.find((a) => a.id === "cancel");
  assert.match(cancel.label, /\(2\)$/, "only the queued + downloading rows are cancellable");

  let folderTarget = null;
  cb.onOpenFolder = (id) => { folderTarget = id; };
  const actions2 = ContextMenuUI.selectionActions(tasks, cb);
  actions2.find((a) => a.id === "folder").run();
  assert.equal(folderTarget, "c", "Open folder must follow the completed row, not ids[0]");
});

test("row menu filters a mixed selection per action", () => {
  const byId = {
    a: task("a", "Downloading", 0),
    b: task("b", "Complete", 1),
  };
  const cb = {
    targetsFor: () => ["a", "b"],
    taskFor: (id) => byId[id],
    onPause: (ids) => { cb.paused = ids; },
    onCancel: (ids) => { cb.cancelled = ids; },
  };
  const items = ContextMenuUI.rowMenu(byId.a, cb);
  items.find((i) => i.label.startsWith("Pause")).action();
  assert.deepEqual(cb.paused, ["a"], "the completed row must not be paused");
  items.find((i) => i.label.startsWith("Cancel")).action();
  assert.deepEqual(cb.cancelled, ["a"]);
});
