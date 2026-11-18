// Regression tests for the Pattern Scan SUBMISSION callback in app.js.
// The callback is extracted from the real source so these tests exercise the
// shipped code path, not a copy of it.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");
const appJs = fs.readFileSync(path.join(root, "ui/frontend/js/app.js"), "utf8");

/** Extract the onSubmit arrow passed to ScanResultsUI.show in the scan handler. */
function extractOnSubmit() {
  const marker = "ScanResultsUI.show(this, report,";
  const start = appJs.indexOf(marker);
  assert.ok(start > 0, "scan handler must call ScanResultsUI.show");
  const cb = appJs.indexOf("async (choice) => {", start);
  assert.ok(cb > start, "scan handler must pass an async submit callback");
  let depth = 0, end = -1;
  for (let i = appJs.indexOf("{", cb); i < appJs.length; i++) {
    if (appJs[i] === "{") depth++;
    else if (appJs[i] === "}" && --depth === 0) { end = i + 1; break; }
  }
  assert.ok(end > cb, "submit callback must be balanced");
  return appJs.slice(cb, end);
}

const onSubmitSource = extractOnSubmit();

function callbackWith(api) {
  globalThis.API = api;
  return (0, eval)(onSubmitSource);
}

function choice(overrides = {}) {
  return {
    urls: ["https://x.test/e-01.mkv"], directory: "/custom/dir",
    projectId: "pr_movies", behavior: "queue", dueAt: 0,
    localTime: "2099-01-01T12:00", tzName: "UTC",
    source: { pattern: "https://x.test/e-*.mkv", start: 1, padding: 2, directory: "/custom/dir" },
    ...overrides,
  };
}

test("Add to Queue calls addBatchReport with autostart=false and maps counts", async () => {
  const calls = [];
  const onSubmit = callbackWith({
    addBatchReport: async (...args) => { calls.push(args); return { added: 2, duplicates: 1, skipped: 0, failed: 0 }; },
    scheduleDownloads: async () => { throw new Error("must not schedule"); },
  });
  const res = await onSubmit(choice({ urls: ["a", "b", "c"] }));
  assert.deepEqual(calls, [[["a", "b", "c"], "/custom/dir", "pr_movies", false]]);
  assert.deepEqual(res, { added: 2, duplicates: 1, skipped: 0, failed: 0, scheduled: 0 });
});

test("Download Now calls addBatchReport with autostart=true", async () => {
  const calls = [];
  const onSubmit = callbackWith({
    addBatchReport: async (...args) => { calls.push(args); return { added: 1, duplicates: 0, failed: 0 }; },
  });
  await onSubmit(choice({ behavior: "now" }));
  assert.equal(calls[0][3], true);   // autostart
});

test("Schedule calls scheduleDownloads with local time and group", async () => {
  const calls = [];
  const onSubmit = callbackWith({
    scheduleDownloads: async (...args) => { calls.push(args); return { ok: true, scheduled: 1 }; },
    addBatchReport: async () => { throw new Error("must not add directly"); },
  });
  const res = await onSubmit(choice({ behavior: "schedule", urls: ["a", "b"] }));
  assert.deepEqual(calls[0], [["a", "b"], "/custom/dir", "pr_movies", "2099-01-01T12:00", "UTC", true]);
  assert.deepEqual(res, { added: 0, scheduled: 1, duplicates: 0, failed: 1 });
});

test("Schedule rejection is reported as an error, not a silent success", async () => {
  const onSubmit = callbackWith({
    scheduleDownloads: async () => ({ ok: false, error: "past_time", scheduled: 0 }),
  });
  await assert.rejects(onSubmit(choice({ behavior: "schedule" })), /past_time/);
});

test("custom destination and group are preserved verbatim", async () => {
  const calls = [];
  const onSubmit = callbackWith({ addBatchReport: async (...a) => { calls.push(a); return { added: 1 }; } });
  await onSubmit(choice({ directory: "D:\\N13\\Shows", projectId: "pr_series" }));
  assert.equal(calls[0][1], "D:\\N13\\Shows");
  assert.equal(calls[0][2], "pr_series");
});

test("partial submission keeps successful counts and reports duplicates/failures", async () => {
  const onSubmit = callbackWith({
    addBatchReport: async () => ({ added: 2, duplicates: 1, skipped: 1, failed: 1 }),
  });
  const res = await onSubmit(choice({ urls: ["a", "b", "c", "d", "e"] }));
  assert.equal(res.added, 2);
  assert.equal(res.duplicates, 1);
  assert.equal(res.failed, 1);
  assert.equal(res.skipped, 1);
});

test("missing/invalid source information never throws", async () => {
  const onSubmit = callbackWith({ addBatchReport: async () => ({ added: 1 }) });
  const c = choice();
  delete c.source;
  await assert.doesNotReject(onSubmit(c));
  const c2 = choice({ source: null });
  await assert.doesNotReject(onSubmit(c2));
});
