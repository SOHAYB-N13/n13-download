// Focused tests for the Downloads list ordering (downloads-view.js).
//
// The `queue` sort is the one that used to lie: it ordered rows by
// `queue_index`, which is the *manual* order the user drags, while the engine
// actually starts downloads by `(priority, manual position)`.  With priorities
// in play a row could be displayed first and start fifth.  These tests pin the
// corrected behaviour:
//
//   * the queue sort follows `queue_position` — the effective start order;
//   * anything not waiting stays behind the waiting work;
//   * tuple sort keys compare numerically, element by element.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VIEW_JS = path.resolve(__dirname, "../../ui/frontend/js/features/downloads/downloads-view.js");

globalThis.window = globalThis;
globalThis.console = console;
globalThis.document = { createElement: () => ({ innerHTML: "", appendChild() {}, querySelectorAll: () => [] }) };
globalThis.API = { logJs() {} };
globalThis.I18N = { t: (k, f) => (f !== undefined ? f : k), fmt: (k, v, f) => f, onChange() {} };
globalThis.Components = {};
globalThis.Utils = {
  $id: () => null,
  $q: () => null,
  $qa: () => [],
  escapeHtml: (s) => String(s),
  fileName: (t) => (t && t.filename) || "",
  hostOf: () => "",
};

const code = fs.readFileSync(VIEW_JS, "utf8");
const DownloadsView = (0, eval)(code + "\n;DownloadsView");

/** A task snapshot with sane defaults. */
function task(id, over = {}) {
  return {
    id,
    state: "Queued",
    filename: `${id}.zip`,
    url: `https://x/${id}.zip`,
    created_at: 1000,
    total: 100,
    completed: 0,
    speed_bps: 0,
    queue_index: 0,
    queue_position: 1,
    category: "General",
    ...over,
  };
}

function setup(downloads, state = {}) {
  globalThis.App = {
    state: {
      downloads,
      filter: "all",
      search: "",
      catFilter: "all",
      sortKey: "queue",
      sortDir: 1,
      ...state,
    },
  };
  return globalThis.App;
}

const ids = () => DownloadsView.filteredTasks(globalThis.App).map((t) => t.id);

// ── Effective vs manual order ───────────────────────────────────────────────

test("the queue sort follows the effective start position, not the manual index", () => {
  setup({
    a: task("a", { queue_index: 0, queue_position: 3 }),
    b: task("b", { queue_index: 1, queue_position: 1 }),
    c: task("c", { queue_index: 2, queue_position: 2 }),
  });
  assert.deepEqual(ids(), ["b", "c", "a"]);
});

test("the queue sort matches the manual order when priorities are uniform", () => {
  setup({
    a: task("a", { queue_index: 0, queue_position: 1 }),
    b: task("b", { queue_index: 1, queue_position: 2 }),
    c: task("c", { queue_index: 2, queue_position: 3 }),
  });
  assert.deepEqual(ids(), ["a", "b", "c"]);
});

test("descending queue sort reverses the effective order", () => {
  setup(
    {
      a: task("a", { queue_position: 1 }),
      b: task("b", { queue_position: 2 }),
      c: task("c", { queue_position: 3 }),
    },
    { sortDir: -1 },
  );
  assert.deepEqual(ids(), ["c", "b", "a"]);
});

test("work that is not waiting sorts behind the waiting queue", () => {
  setup({
    run: task("run", { state: "Downloading", queue_position: -1, queue_index: 0 }),
    done: task("done", { state: "Complete", queue_position: -1, queue_index: 1 }),
    wait: task("wait", { queue_position: 1, queue_index: 2 }),
  });
  assert.deepEqual(ids(), ["wait", "run", "done"]);
});

test("a task the manager no longer tracks does not jump to the front", () => {
  // Both positions are -1: it must land at the end, never at index 0.
  setup({
    gone: task("gone", { queue_index: -1, queue_position: -1 }),
    wait: task("wait", { queue_index: 0, queue_position: 1 }),
  });
  assert.deepEqual(ids(), ["wait", "gone"]);
});

// ── Key comparison ──────────────────────────────────────────────────────────

test("compareKeys compares tuples numerically, element by element", () => {
  // As strings "[10,0]" < "[9,0]" is true; numerically it must be false.
  assert.ok(DownloadsView.compareKeys([10, 0], [9, 0]) > 0);
  assert.ok(DownloadsView.compareKeys([9, 0], [10, 0]) < 0);
  // The secondary key only breaks a tie.
  assert.ok(DownloadsView.compareKeys([9, 2], [9, 3]) < 0);
  assert.equal(DownloadsView.compareKeys([9, 3], [9, 3]), 0);
});

test("compareKeys still handles plain scalar keys", () => {
  assert.ok(DownloadsView.compareKeys(1, 2) < 0);
  assert.ok(DownloadsView.compareKeys(3, 2) > 0);
  assert.equal(DownloadsView.compareKeys(2, 2), 0);
  // Mixed scalar/tuple must not throw.
  assert.equal(DownloadsView.compareKeys(5, [5, 0]), 0);
});

test("other sorts are unaffected by the tuple key", () => {
  setup(
    {
      a: task("a", { filename: "b.zip", total: 300 }),
      b: task("b", { filename: "a.zip", total: 100 }),
      c: task("c", { filename: "c.zip", total: 200 }),
    },
    { sortKey: "name" },
  );
  assert.deepEqual(ids(), ["b", "a", "c"]);

  globalThis.App.state.sortKey = "size";
  globalThis.App.state.sortDir = -1;
  assert.deepEqual(ids(), ["a", "c", "b"]);
});
