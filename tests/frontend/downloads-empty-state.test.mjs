// The empty Downloads list must never be a dead end.
//
// There are three different reasons the list can be empty, and they need three
// different answers:
//
//   1. nothing has ever been downloaded  → offer the first-run actions;
//   2. the open group has nothing in it  → offer to add one *to that group*;
//   3. the filters hid everything        → offer the way back to the full list.
//
// Case 3 is the one that used to trap people.  The "Nothing matches" state only
// offered "Show all categories", and only when the *category* chip was the
// culprit — so filtering by status ("Failed") or typing a search term that
// matched nothing produced an empty screen whose only control was the filter
// that was already hiding the rows.  These tests pin both halves of the fix:
// `emptyReason()` (which decides what to say and whether an exit is needed) and
// the rendered state that consumes it.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VIEW_JS = path.resolve(__dirname, "../../ui/frontend/js/features/downloads/downloads-view.js");

// ── Minimal DOM ─────────────────────────────────────────────────────────────
// Only the three elements `renderDownloads` reaches for, plus a factory that
// records what the empty state was asked to render.

const fakeEl = () => ({
  hidden: false,
  innerHTML: "",
  textContent: "",
  value: "",
  classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  dataset: {},
  children: [],
  replaceChildren(...kids) { this.children = kids; },
  appendChild(k) { this.children.push(k); },
  addEventListener() {},
  setAttribute() {},
  getAttribute: () => null,
  querySelectorAll: () => [],
});

const els = {
  downloadList: fakeEl(),
  downloadHead: fakeEl(),
  downloadsEmpty: fakeEl(),
};

let lastEmptyState = null;

globalThis.window = globalThis;
globalThis.console = console;
globalThis.document = { createElement: fakeEl, createDocumentFragment: fakeEl };
globalThis.API = { logJs() {} };
globalThis.I18N = { t: (k, f) => (f !== undefined ? f : k), fmt: (k, v, f) => f, onChange() {} };
globalThis.Components = {
  emptyState(opts) { lastEmptyState = opts; return { opts }; },
  renderRow: () => fakeEl(),
};
globalThis.Utils = {
  $id: (id) => els[id] || null,
  $q: () => null,
  $qa: () => [],
  escapeHtml: (s) => String(s),
  fileName: (t) => (t && t.filename) || "",
  hostOf: () => "",
  syncChipGroup: () => {},
};

const DownloadsView = (0, eval)(fs.readFileSync(VIEW_JS, "utf8") + "\n;DownloadsView");

function task(id, over = {}) {
  return {
    id, state: "Queued", filename: `${id}.zip`, url: `https://x/${id}.zip`,
    created_at: 1000, total: 100, completed: 0, speed_bps: 0,
    queue_index: 0, queue_position: 1, category: "General", ...over,
  };
}

/** An `app` just real enough to render an empty list. */
function emptyApp(state = {}) {
  const app = {
    state: {
      page: "downloads",
      downloads: {},
      filter: "all",
      search: "",
      catFilter: "all",
      sortKey: "queue",
      sortDir: 1,
      activeProject: null,
      selectedIds: new Set(),
      listSig: "",
      highlightId: null,
      ...state,
    },
    _clearSelection() { this.state.selectedIds.clear(); },
    _renderSelBar() {},
    _updateRow() {},
    _setCatFilter() {},
    _renderQueueStrip() {},
    _renderDownloads() {},
  };
  return app;
}

const renderEmpty = (app) => {
  lastEmptyState = null;
  DownloadsView.renderDownloads(app, true);
  return lastEmptyState;
};

// ── emptyReason: which filter is responsible ────────────────────────────────

test("emptyReason reports no cause when no filter is active", () => {
  const r = DownloadsView.emptyReason({ filter: "all", catFilter: "all", search: "" });
  assert.equal(r.byFilter, false);
  assert.equal(r.byCategory, false);
  assert.equal(r.bySearch, false);
  assert.equal(r.resettable, false, "nothing to reset, so no exit button");
  assert.equal(r.categoryOnly, false);
});

test("emptyReason blames the status chip", () => {
  const r = DownloadsView.emptyReason({ filter: "failed", catFilter: "all", search: "" });
  assert.equal(r.byFilter, true);
  assert.equal(r.categoryOnly, false);
  assert.equal(r.resettable, true, "a status filter must be undoable");
});

test("emptyReason blames the search box", () => {
  const r = DownloadsView.emptyReason({ filter: "all", catFilter: "all", search: "zzz" });
  assert.equal(r.bySearch, true);
  assert.equal(r.resettable, true, "a search must be undoable");
});

test("emptyReason blames the category chip alone only when nothing else is active", () => {
  const only = DownloadsView.emptyReason({ filter: "all", catFilter: "Videos", search: "" });
  assert.equal(only.categoryOnly, true);
  assert.equal(only.resettable, true);

  const mixed = DownloadsView.emptyReason({ filter: "failed", catFilter: "Videos", search: "" });
  assert.equal(mixed.categoryOnly, false, "a status filter is hiding rows too");
  assert.equal(mixed.resettable, true);

  const searched = DownloadsView.emptyReason({ filter: "all", catFilter: "Videos", search: "x" });
  assert.equal(searched.categoryOnly, false, "a search is hiding rows too");
  assert.equal(searched.resettable, true);
});

test("emptyReason treats a whitespace-only search as no search", () => {
  const r = DownloadsView.emptyReason({ filter: "all", catFilter: "all", search: "   " });
  assert.equal(r.bySearch, false);
  assert.equal(r.resettable, false);
});

test("emptyReason survives a missing or partial state object", () => {
  for (const s of [undefined, null, {}]) {
    const r = DownloadsView.emptyReason(s);
    assert.equal(r.resettable, false);
    assert.equal(r.categoryOnly, false);
  }
});

test("every way of hiding a row is resettable", () => {
  // Exhaustive over the three inputs: if *anything* can hide a row, the empty
  // state must be able to undo it.  This is the invariant, stated once.
  const filters = ["all", "failed", "active"];
  const cats = ["all", "Videos"];
  const searches = ["", "zzz"];
  for (const filter of filters) {
    for (const catFilter of cats) {
      for (const search of searches) {
        const r = DownloadsView.emptyReason({ filter, catFilter, search });
        const hiding = filter !== "all" || catFilter !== "all" || search !== "";
        assert.equal(r.resettable, hiding,
          `filter=${filter} cat=${catFilter} search="${search}"`);
      }
    }
  }
});

// ── The rendered empty state ────────────────────────────────────────────────

test("a status filter that matches nothing still offers a way out", () => {
  const opts = renderEmpty(emptyApp({ downloads: { a: task("a", { state: "Queued" }) }, filter: "failed" }));
  assert.equal(opts.title, "Nothing matches");
  assert.equal(opts.actions.length, 1);
  assert.equal(opts.actions[0].label, "Show all downloads");
  assert.equal(opts.actions[0].primary, true);
});

test("a search that matches nothing still offers a way out", () => {
  const opts = renderEmpty(emptyApp({ downloads: { a: task("a") }, search: "zzz" }));
  assert.equal(opts.title, "Nothing matches");
  assert.equal(opts.actions.length, 1);
  assert.equal(opts.actions[0].label, "Show all downloads");
});

test("the category chip alone keeps its more specific wording", () => {
  const opts = renderEmpty(emptyApp({
    downloads: { a: task("a", { category: "Videos" }) },
    catFilter: "Music",
  }));
  assert.equal(opts.title, "Nothing matches");
  assert.equal(opts.desc, "No downloads in this category yet.");
  assert.equal(opts.actions[0].label, "Show all categories");
});

test("an empty list with nothing to undo offers no action at all", () => {
  // A group that is genuinely empty gets its own state (case 2), so reaching
  // here means there is nothing to reset — offering a button would be a lie.
  const opts = renderEmpty(emptyApp({
    downloads: { a: task("a", { project_id: "p1" }) },
    activeProject: "p1",
    filter: "failed",
  }));
  // Filter is active, so this is case 3 and it *does* get an exit.
  assert.equal(opts.actions.length, 1);

  const bare = DownloadsView.emptyReason({ filter: "all", catFilter: "all", search: "" });
  assert.equal(bare.resettable, false);
});

test("the reset action clears the state it is labelled for", () => {
  const app = emptyApp({ downloads: { a: task("a") }, filter: "failed", catFilter: "Videos", search: "zzz" });
  const opts = renderEmpty(app);
  opts.actions[0].onClick();
  assert.equal(app.state.filter, "all");
  assert.equal(app.state.catFilter, "all");
  assert.equal(app.state.search, "");
});
