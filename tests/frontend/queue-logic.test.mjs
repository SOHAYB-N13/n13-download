// Focused tests for the Queue page logic (ui/frontend/js/queue.js).
//
// The module is loaded for real with minimal load-time mocks; the pure
// decision logic (ordering, estimates, selection, recommendations) is then
// exercised directly.  Two invariants get special attention because they are
// the ones a future change is most likely to break silently:
//
//   * an unmeasurable estimate must be `null`, never a plausible number;
//   * computing recommendations must not change a single setting.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const QUEUE_JS = path.resolve(__dirname, "../../ui/frontend/js/queue.js");

globalThis.document = {
  addEventListener() {},
  readyState: "complete",
  hidden: false,
  createElement() {
    return { className: "", innerHTML: "", appendChild() {}, setAttribute() {} };
  },
};
globalThis.window = globalThis;
globalThis.console = console;

// `I18N.fmt` mirrors the real implementation's substitution.
globalThis.I18N = {
  t: (k, f) => (f !== undefined ? f : k),
  fmt(k, vars, f) {
    let s = this.t(k, f);
    if (vars) Object.entries(vars).forEach(([n, v]) => { s = s.split(`{${n}}`).join(String(v)); });
    return s;
  },
  onChange() {},
};
globalThis.Utils = { $id: () => null, $qa: () => [], formatSpeed: (b) => `${b} B/s`, formatETA: (s) => `${s}s`, formatSize: (b) => `${b} B`, fileName: (t) => (t && t.filename) || "", escapeHtml: (s) => String(s), icon: () => "", hostOf: () => "", statusLabel: (s) => s, clamp: (v, a, b) => Math.min(b, Math.max(a, v)) };
globalThis.API = { logJs() {}, updateSettings() {}, queuePlan: async () => null, reorderTasks() {}, moveTaskTo() {}, moveToBottom() {} };
globalThis.Components = { toast() {}, confirm: async () => true, priorityDialog: async () => null };
globalThis.App = { state: { page: "queue", downloads: {}, settings: {} } };

const code = fs.readFileSync(QUEUE_JS, "utf8");
const Queue = (0, eval)(code + "\n;Queue");

/** Build a task snapshot with sane defaults. */
function task(id, over = {}) {
  return {
    id, state: "Queued", priority: 5, total: 1000, completed: 0, speed_bps: 0,
    queue_index: 0, filename: `${id}.zip`, url: `https://x/${id}.zip`, ...over,
  };
}

function setup(downloads, settings = {}, plan = null) {
  App.state.downloads = downloads;
  App.state.settings = settings;
  App.state.page = "queue";
  App.state.queuePaused = false;
  Queue.state.plan = plan;
  Queue.state.planAt = Date.now();
  Queue.state.order = "start";
  Queue.state.sel = new Set();
  Queue.state.anchor = null;
  Queue.state.focus = null;
  Queue.state.drag = null;
}

// ── Ordering ────────────────────────────────────────────────────────────────

test("_waiting follows the plan's effective start order, not queue_index", () => {
  // Manual order is a,b,c but priority makes c run first.
  setup(
    {
      a: task("a", { queue_index: 0 }),
      b: task("b", { queue_index: 1 }),
      c: task("c", { queue_index: 2, priority: 0 }),
    },
    {},
    [
      { id: "c", position: 1, priority: 0 },
      { id: "a", position: 2, priority: 5 },
      { id: "b", position: 3, priority: 5 },
    ],
  );
  assert.deepEqual(Queue._waiting().map((t) => t.id), ["c", "a", "b"]);
});

test("_waiting follows queue_index in manual order", () => {
  setup(
    {
      a: task("a", { queue_index: 2 }),
      b: task("b", { queue_index: 0 }),
      c: task("c", { queue_index: 1 }),
    },
    {},
    [
      { id: "b", position: 1 },
      { id: "c", position: 2 },
      { id: "a", position: 3 },
    ],
  );
  Queue.state.order = "manual";
  assert.deepEqual(Queue._waiting().map((t) => t.id), ["b", "c", "a"]);
});

test("_waiting falls back to queue_index when no plan is available", () => {
  setup(
    { a: task("a", { queue_index: 1 }), b: task("b", { queue_index: 0 }) },
    {},
    null,
  );
  assert.deepEqual(Queue._waiting().map((t) => t.id), ["b", "a"]);
});

test("_waiting excludes tasks that are not queued", () => {
  setup({
    a: task("a", { state: "Downloading" }),
    b: task("b"),
    c: task("c", { state: "Completed" }),
  });
  assert.deepEqual(Queue._waiting().map((t) => t.id), ["b"]);
});

test("a task missing from the queue order sorts last, not first", () => {
  // queue_index -1 means "not in the order list at all".
  setup({ a: task("a", { queue_index: -1 }), b: task("b", { queue_index: 0 }) });
  assert.deepEqual(Queue._waiting().map((t) => t.id), ["b", "a"]);
});

test("_prioritiesDiverge is false for a uniform queue and true otherwise", () => {
  setup({ a: task("a", { priority: 5 }), b: task("b", { priority: 5 }) });
  assert.equal(Queue._prioritiesDiverge(), false);
  setup({ a: task("a", { priority: 5 }), b: task("b", { priority: 1 }) });
  assert.equal(Queue._prioritiesDiverge(), true);
  // A single waiting task can never diverge with itself.
  setup({ a: task("a", { priority: 5 }) });
  assert.equal(Queue._prioritiesDiverge(), false);
});

test("_active collects running states, fastest first", () => {
  setup({
    a: task("a", { state: "Downloading", speed_bps: 100 }),
    b: task("b", { state: "Analyzing", speed_bps: 0 }),
    c: task("c", { state: "Downloading", speed_bps: 900 }),
    d: task("d", { state: "Queued" }),
  });
  assert.deepEqual(Queue._active().map((t) => t.id), ["c", "a", "b"]);
});

// ── Estimates: honesty ──────────────────────────────────────────────────────

test("_drain is null without a plan", () => {
  setup({ a: task("a") }, {}, null);
  assert.equal(Queue._drain(), null);
});

test("_drain is null when no throughput has been measured", () => {
  setup(
    { a: task("a"), b: task("b") },
    {},
    [
      { id: "a", position: 1, estimated_start_seconds: 0, remaining: 1000 },
      { id: "b", position: 2, estimated_start_seconds: 10, remaining: 1000 },
    ],
  );
  // Nothing is running -> per-slot throughput is unknown -> no honest figure.
  assert.equal(Queue._drain(), null);
});

test("_drain is null when nothing at all is measurable", () => {
  setup(
    {
      run: task("run", { state: "Downloading", speed_bps: 1000, total: 100000, completed: 20000 }),
      a: task("a", { total: 0 }),
    },
    {},
    [{ id: "a", position: 1, estimated_start_seconds: null, remaining: 0 }],
  );
  assert.equal(Queue._drain(), null);
});

test("_drain reports a lower bound when a waiting size is unknown", () => {
  setup(
    {
      run: task("run", { state: "Downloading", speed_bps: 1000, total: 100000, completed: 20000 }),
      a: task("a", { total: 5000 }),
      b: task("b", { total: 0 }),
    },
    {},
    [
      { id: "a", position: 1, estimated_start_seconds: 80, remaining: 5000 },
      { id: "b", position: 2, estimated_start_seconds: 85, remaining: 0 },
    ],
  );
  const d = Queue._drain();
  // The known task still finishes at 85; the unknown one can only add time.
  assert.deepEqual(d, { seconds: 85, exact: false });
});

test("_drain is exact and uses the last start plus its own transfer time", () => {
  setup(
    {
      run: task("run", { state: "Downloading", speed_bps: 1000, total: 100000, completed: 20000 }),
      a: task("a", { total: 5000 }),
      b: task("b", { total: 8000 }),
    },
    {},
    [
      { id: "a", position: 1, estimated_start_seconds: 80, remaining: 5000 },
      { id: "b", position: 2, estimated_start_seconds: 85, remaining: 8000 },
    ],
  );
  // per-slot throughput = 1000 B/s -> b finishes at 85 + 8 = 93.
  assert.deepEqual(Queue._drain(), { seconds: 93, exact: true });
});

test("_waitingBytes sums remaining bytes and is null on an unknown size", () => {
  setup({ a: task("a", { total: 1000, completed: 400 }), b: task("b", { total: 500, completed: 0 }) });
  assert.equal(Queue._waitingBytes(), 1100);
  setup({ a: task("a", { total: 0, completed: 0 }) });
  assert.equal(Queue._waitingBytes(), null);
  setup({ a: task("a", { state: "Completed", total: 900 }) });
  assert.equal(Queue._waitingBytes(), 0);
});

// ── Selection ───────────────────────────────────────────────────────────────

test("_selectedWaitingIds intersects the selection with the waiting list", () => {
  setup({ a: task("a"), b: task("b"), c: task("c", { state: "Downloading" }) });
  Queue.state.sel = new Set(["a", "c"]);
  assert.deepEqual(Queue._selectedWaitingIds(), ["a"]);
});

test("_selectedWaitingIds falls back to the focused row", () => {
  setup({ a: task("a"), b: task("b") });
  Queue.state.focus = "b";
  assert.deepEqual(Queue._selectedWaitingIds(), ["b"]);
});

test("_selectedWaitingIds is empty when the focus is not waiting", () => {
  setup({ a: task("a"), b: task("b", { state: "Downloading" }) });
  Queue.state.focus = "b";
  assert.deepEqual(Queue._selectedWaitingIds(), []);
});

// ── Recommendations ─────────────────────────────────────────────────────────

test("recommendations never change a setting on their own", async () => {
  let writes = 0;
  globalThis.API.updateSettings = () => { writes++; };
  setup(
    { a: task("a"), b: task("b"), c: task("c") },
    { max_concurrent: 1 },
  );
  const recs = Queue._recommendations();
  assert.ok(recs.length > 0, "expected at least one suggestion");
  assert.equal(writes, 0, "computing suggestions must not write settings");
  // The slots suggestion must expose an explicit action, not fire it.
  const slots = recs.find((r) => r.id === "slots");
  assert.ok(slots && typeof slots.action.run === "function");
  assert.equal(writes, 0);
});

test("no slots suggestion when enough slots are already configured", () => {
  setup({ a: task("a"), b: task("b") }, { max_concurrent: 4 });
  assert.equal(Queue._recommendations().some((r) => r.id === "slots"), false);
});

test("slots suggestion targets the work available, capped at four", () => {
  const downloads = {};
  for (let i = 0; i < 9; i++) downloads[`t${i}`] = task(`t${i}`);
  setup(downloads, { max_concurrent: 1 });
  const rec = Queue._recommendations().find((r) => r.id === "slots");
  assert.ok(rec);
  assert.match(rec.action.label, /4/, "the cap should keep the suggestion at 4 slots");
});

test("uniform priorities are called out once there are enough waiting", () => {
  setup({ a: task("a"), b: task("b"), c: task("c") }, { max_concurrent: 4 });
  assert.equal(Queue._recommendations().some((r) => r.id === "priority"), true);
  // Only two waiting tasks: not worth mentioning.
  setup({ a: task("a"), b: task("b") }, { max_concurrent: 4 });
  assert.equal(Queue._recommendations().some((r) => r.id === "priority"), false);
});

test("a speed cap that is not the bottleneck is reported", () => {
  setup(
    {
      run: task("run", { state: "Downloading", speed_bps: 1000 }),
      a: task("a"),
    },
    { max_concurrent: 4, max_speed_bps: 100000 },
  );
  assert.equal(Queue._recommendations().some((r) => r.id === "cap"), true);
});

test("a speed cap that is being saturated is not reported", () => {
  setup(
    {
      run: task("run", { state: "Downloading", speed_bps: 95000 }),
      a: task("a"),
    },
    { max_concurrent: 4, max_speed_bps: 100000 },
  );
  assert.equal(Queue._recommendations().some((r) => r.id === "cap"), false);
});

test("an idle queue produces no suggestions at all", () => {
  setup({}, { max_concurrent: 3 });
  assert.deepEqual(Queue._recommendations(), []);
});

// ── Queue gate ──────────────────────────────────────────────────────────────
//
// The gate is runtime state reported by the backend.  These cover the two
// rendering decisions that follow from it: whether a banner exists at all, and
// whether the manual position is worth showing alongside the effective one.

test("_gateBannerEl is null while the gate is open", () => {
  setup({ a: task("a") });
  App.state.queuePaused = false;
  assert.equal(Queue._gateBannerEl(), null, "an open gate must not add a node");
});

test("_gateBannerEl offers the action that clears the gate", () => {
  setup({ a: task("a") });
  App.state.queuePaused = true;
  const el = Queue._gateBannerEl();
  assert.ok(el, "a paused queue must render a banner");
  assert.match(el.className, /qk-gate/);
  assert.match(el.innerHTML, /data-qk="resume-queue"/);
  assert.match(el.innerHTML, /Resume queue/);
});

test("_manualPosRow appears only when the two positions disagree", () => {
  // Uniform priorities: manual 0 (shown as 1) and effective 1 are the same
  // fact, so a second row would be noise.
  assert.equal(Queue._manualPosRow({ queue_index: 0, queue_position: 1 }), "");

  // Priority jumped it: manual 2 (shown as 3) but it starts first.
  const html = Queue._manualPosRow({ queue_index: 2, queue_position: 1 });
  assert.match(html, /Position in list/);
  assert.match(html, /<dd>3<\/dd>/);

  // Not waiting, or not in the list at all: nothing to compare.
  assert.equal(Queue._manualPosRow({ queue_index: 2, queue_position: -1 }), "");
  assert.equal(Queue._manualPosRow({ queue_index: -1, queue_position: 2 }), "");
});

test("a paused queue stops claiming its waiting work will run", () => {
  setup({ a: task("a"), b: task("b") }, { max_concurrent: 1 }, [
    { id: "a", position: 1, estimated_start_seconds: 5, remaining: 1000 },
    { id: "b", position: 2, estimated_start_seconds: 10, remaining: 1000 },
  ]);

  App.state.queuePaused = false;
  assert.match(Queue._overviewEl().innerHTML, /queued behind them/);

  // With the gate closed the plan's finish time is an "if you resume now"
  // figure and the waiting work is held, not queued behind anything.
  App.state.queuePaused = true;
  const html = Queue._overviewEl().innerHTML;
  assert.match(html, /held — the queue is paused/);
  assert.match(html, /estimate — the queue is paused/);
});

// ── Order view switching ────────────────────────────────────────────────────

test("setOrder clears a selection that belonged to the other view", () => {
  setup({ a: task("a"), b: task("b") });
  Queue.state.sel = new Set(["a"]);
  Queue.state.anchor = "a";
  Queue.setOrder("manual");
  assert.equal(Queue.state.order, "manual");
  assert.equal(Queue.state.sel.size, 0);
  assert.equal(Queue.state.anchor, null);
});

test("setOrder ignores an unknown mode and keeps the current view", () => {
  setup({ a: task("a") });
  Queue.state.order = "start";
  Queue.setOrder("nonsense");
  assert.equal(Queue.state.order, "start");
});

test("_priorityLabel maps the 0-10 scale onto three buckets", () => {
  assert.match(Queue._priorityLabel(0), /High/);
  assert.match(Queue._priorityLabel(3), /High/);
  assert.match(Queue._priorityLabel(4), /Normal/);
  assert.match(Queue._priorityLabel(7), /Normal/);
  assert.match(Queue._priorityLabel(8), /Low/);
  assert.match(Queue._priorityLabel(10), /Low/);
});

test("_slots clamps a missing or nonsense max_concurrent to at least one", () => {
  App.state.settings = {};
  assert.equal(Queue._slots(), 1);
  App.state.settings = { max_concurrent: 0 };
  assert.equal(Queue._slots(), 1);
  App.state.settings = { max_concurrent: "3" };
  assert.equal(Queue._slots(), 3);
});
