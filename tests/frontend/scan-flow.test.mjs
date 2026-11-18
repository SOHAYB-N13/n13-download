// Regression tests for the Pattern Scan workflow:
//  - rendering discovery results must not create or start a download;
//  - submission happens only through the explicit confirmation dialog;
//  - the confirmation action depends on the chosen download behaviour.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");

function stub(extra = {}) {
  return {
    value: "", textContent: "", innerHTML: "", disabled: false, hidden: false,
    checked: false, options: [], selectedIndex: 0, dataset: {},
    addEventListener(name, fn) { (this._events ||= {})[name] = fn; },
    click() { return this._events?.click?.(); },
    replaceChildren(...kids) { this.innerHTML = kids.map((k) => k.textContent || "").join(""); },
    ...extra,
  };
}

function makeRoot() {
  const els = {};
  const get = (key) => (els[key] ||= stub());
  const behaviour = stub({ value: "queue" });
  const group = stub({ options: [stub({ text: "Default group" })], selectedIndex: 0 });
  return {
    innerHTML: "",
    querySelector(sel) {
      if (sel === "[data-scan-group]") return group;
      if (sel === 'input[name="scanBehavior"]:checked') return behaviour;
      return get(sel);
    },
    querySelectorAll(sel) {
      if (sel === "input[name=\"scanBehavior\"]") return [behaviour];
      if (sel === "[data-scan-pick]") return [get("pick0"), get("pick1")];
      if (sel === "[data-scan-remove]") return [get("remove0"), get("remove1")];
      return [];
    },
    _els: els, _get: get, _behaviour: behaviour, _group: group,
  };
}

globalThis.document = {
  addEventListener() {}, readyState: "complete", hidden: false, activeElement: null,
  createElement: () => stub(),
};
globalThis.window = globalThis;
globalThis.console = console;
globalThis.I18N = { t: (k, f) => (f !== undefined ? f : k), fmt: (k, v, f) => f || k, onChange() {} };
globalThis.Utils = {
  $id: () => globalThis.__scanRoot,
  $qa: () => [],
  icon: () => "",
  escapeHtml: (s) => String(s),
  fileName: (t) => t?.filename || "",
  formatSize: (n) => n > 0 ? `${n} B` : "unknown",
  statusLabel: (s) => s,
  clamp: (v, a, b) => Math.min(b, Math.max(a, v)),
};
globalThis.API = {
  cancelPatternScan: async () => true,
  addBatchReport: async () => ({ added: 0 }),
  scheduleDownloads: async () => ({ ok: true, scheduled: 1 }),
};
let modal = null;
globalThis.Components = {
  toast() {},
  showModal(html, opts) {
    modal = {
      html, opts, footer: "",
      qs(sel) { return (this._els ||= {})[sel] ||= stub(); },
      setFooter(f) { this.footer = f; },
      close() { this.closed = true; this.opts?.onClose?.(); },
    };
    return modal;
  },
};
globalThis.App = { state: { projects: [], activeProject: "all", page: "batch" } };

const ScanResultsUI = (0, eval)(
  fs.readFileSync(path.join(root, "ui/frontend/js/features/batch/scan-results.js"), "utf8")
  + "\n;ScanResultsUI"
);

const t = (k, f) => f || k;

function report() {
  return {
    items: [
      { url: "https://x.test/e-01.mkv", requested_url: "https://x.test/e-01.mkv", number: 1,
        available: true, status: "available", filename: "e-01.mkv", size: 1024,
        content_type: "video/mkv", server: "nginx", error: "" },
      { url: "https://x.test/e-02.mkv", requested_url: "https://x.test/e-02.mkv", number: 2,
        available: false, status: "not_found", filename: "", size: 0,
        content_type: "", server: "", error: "HTTP 404" },
    ],
    discovered: 2, available: 1, unavailable: 1, failed_probes: 0,
    scanned: 2, stop_reason: "consecutive_404", cancelled: false,
    urls: ["https://x.test/e-01.mkv"],
  };
}

test("rendering results alone never submits or starts a download", () => {
  globalThis.__scanRoot = makeRoot();
  globalThis.__submits = [];
  ScanResultsUI.show(App, report(), { pattern: "https://x.test/e-*.mkv", start: 1, padding: 2, directory: "" },
    async (choice) => { globalThis.__submits.push(choice); return {}; });
  assert.match(globalThis.__scanRoot.innerHTML, /scan-result/);
  assert.match(globalThis.__scanRoot.innerHTML, /Download configuration/);
  assert.match(globalThis.__scanRoot._get("[data-scan-list]").innerHTML, /e-01\.mkv/);
  assert.match(globalThis.__scanRoot._get("[data-scan-list]").innerHTML, /HTTP 404/);
  assert.deepEqual(globalThis.__submits, []);
});

test("EXACT REGRESSION: show -> Add to Queue -> Confirm -> Go submits with source", async () => {
  // This is the workflow that produced `source is not defined`: the submit
  // payload is assembled in confirm(), which is a different scope from
  // show()'s `source` parameter. Go must call onSubmit with no ReferenceError.
  globalThis.__scanRoot = makeRoot();
  globalThis.__submits = [];
  const src = { pattern: "https://x.test/e-*.mkv", start: 1, padding: 2, directory: "/tmp/n13" };
  ScanResultsUI.show(App, report(), src, async (choice) => {
    globalThis.__submits.push(choice);
    return { added: 1, duplicates: 0, failed: 0, scheduled: 0 };
  });
  const dom = globalThis.__scanRoot;
  assert.equal(globalThis.__submits.length, 0, "selection alone must not submit");
  await dom._get("[data-scan-confirm]").click();
  assert.ok(modal);
  await modal.qs("[data-cf-go]").click();
  assert.equal(globalThis.__submits.length, 1, "Confirm -> Go must submit once");
  assert.deepEqual(globalThis.__submits[0].source, src);
  assert.deepEqual(globalThis.__submits[0].urls, ["https://x.test/e-01.mkv"]);
  const err = dom._get("[data-scan-error]");
  assert.ok(!err.textContent, `submission must not surface an error, got: ${err.textContent}`);
  assert.match(dom._get(".scan-actions").innerHTML || "", /Submission result|added/);
});

test("confirmation is explicit and Cancel submits nothing", async () => {
  const dom = makeRoot();
  let calls = 0;
  await ScanResultsUI.confirm({ submitting: false }, report().items, new Set([0]), dom,
    async () => { calls++; return { added: 1 }; }, t);
  assert.ok(modal, "confirmation dialog must be shown");
  assert.match(modal.html, /Review before submitting/);
  assert.match(modal.footer, /data-cf-go/);
  assert.match(modal.footer, /data-cf-cancel/);
  modal.qs("[data-cf-cancel]").click();
  assert.equal(calls, 0);
});

test("the confirmation action follows the chosen download behaviour", async () => {
  for (const [value, expected] of [["queue", "Add to queue"], ["now", "Download now"], ["schedule", "Schedule downloads"]]) {
    const dom = makeRoot();
    dom._behaviour.value = value;
    if (value === "schedule") {
      const future = new Date(Date.now() + 3600_000);
      const pad = (n) => String(n).padStart(2, "0");
      dom._get("[data-scan-date]").value =
        `${future.getFullYear()}-${pad(future.getMonth() + 1)}-${pad(future.getDate())}`;
      dom._get("[data-scan-time]").value = `${pad(future.getHours())}:${pad(future.getMinutes())}`;
    }
    await ScanResultsUI.confirm({ submitting: false }, report().items, new Set([0]), dom,
      async () => ({ added: 1 }), t);
    assert.match(modal.footer, new RegExp(expected));
  }
});

test("the app's scan handler never calls addBatch or addBatchReport", () => {
  const appJs = fs.readFileSync(path.join(root, "ui/frontend/js/app.js"), "utf8");
  const start = appJs.indexOf('Utils.$id("btnPatternScan").addEventListener');
  const end = appJs.indexOf("// The project list may not have arrived yet", start);
  assert.ok(start > 0 && end > start, "scan handler must exist");
  const handler = appJs.slice(start, end);
  assert.match(handler, /scanPatternPreview/);
  // The only submission calls allowed are inside the onSubmit callback that
  // ScanResultsUI invokes after explicit confirmation — never in the scan
  // path before the review UI is shown.
  const beforeReview = handler.slice(0, handler.indexOf("ScanResultsUI.show"));
  assert.doesNotMatch(beforeReview, /\bAPI\.addBatch\(/);
  assert.doesNotMatch(beforeReview, /\bAPI\.addBatchReport\(/);
  assert.match(handler, /ScanResultsUI\.show/);
});

test("repeated Confirm clicks cannot submit twice", async () => {
  globalThis.__scanRoot = makeRoot();
  let calls = 0;
  ScanResultsUI.show(App, report(), { pattern: "p", directory: "" }, async () => { calls++; return { added: 1 }; });
  const dom = globalThis.__scanRoot;
  await dom._get("[data-scan-confirm]").click();
  const go = modal.qs("[data-cf-go]");
  await go.click();
  await go.click();
  assert.equal(calls, 1, "the dialog's own disabled guard must prevent a double submit");
});

test("empty selection shows an error and never opens a confirmation", async () => {
  const dom = makeRoot();
  let called = false;
  modal = null;
  await ScanResultsUI.confirm({ submitting: false }, report().items, new Set(), dom,
    async () => { called = true; return {}; }, t);
  assert.equal(called, false);
  assert.equal(modal, null, "no confirmation dialog may open without a selection");
  assert.match(dom._get("[data-scan-error]").textContent, /Select at least one/);
});
