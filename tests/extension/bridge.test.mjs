import { test } from "node:test";
import assert from "node:assert/strict";
import { loadBridge } from "./helpers.mjs";

// Load the bridge module ONCE.  Each test then (re)installs the chrome/fetch
// mocks it needs before exercising a fresh bridge instance (the module's
// methods read the globals at call time, not load time).
const g = loadBridge();

function makeBridge() {
  const b = new g.N13Bridge();
  b._config = { live_server_url: "http://127.0.0.1:6868/download", token: "tok-123" };
  return b;
}

function mockFetch(status, payload) {
  globalThis.fetch = async () => ({
    status,
    ok: status >= 200 && status < 300,
    text: async () => (payload ? JSON.stringify(payload) : "{}"),
  });
}

function mockChrome(tabs = {}) {
  globalThis.chrome = {
    runtime: { getURL: (p) => "chrome-extension://test/" + p },
    tabs: {
      create: (o, cb) => { (tabs.created || []).push(o); cb && cb({ id: 7 }); },
      remove: (id, cb) => { (tabs.removed || []).push(id); cb && cb(); },
    },
  };
}

// ---------------------------------------------------------------------------
// base URL normalization
// ---------------------------------------------------------------------------

test("base URL strips a trailing /download (no /download/download)", () => {
  const b = makeBridge();
  assert.equal(b._baseUrl(), "http://127.0.0.1:6868");

  b._config.live_server_url = "http://127.0.0.1:6868/";
  assert.equal(b._baseUrl(), "http://127.0.0.1:6868");

  b._config.live_server_url = "http://localhost:6868/download/";
  assert.equal(b._baseUrl(), "http://localhost:6868");
});

// ---------------------------------------------------------------------------
// _mapResult — truthful result mapping (HTTP 200 alone is NOT success)
// ---------------------------------------------------------------------------

test("batch 20/20 accepted -> ok, not partial", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 200, ok: true, payload: { accepted: 20, rejected: 0 } }, 20, true);
  assert.deepEqual(r, { ok: true, accepted: 20, rejected: 0, total: 20, partial: false, reason: "" });
});

test("batch 18/20 accepted -> partial", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 200, ok: true, payload: { accepted: 18, rejected: 2 } }, 20, true);
  assert.equal(r.ok, true);
  assert.equal(r.accepted, 18);
  assert.equal(r.rejected, 2);
  assert.equal(r.partial, true);
  assert.equal(r.reason, "partial");
});

test("batch 0/20 accepted -> failure (never reported as success)", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 200, ok: true, payload: { accepted: 0, rejected: 20 } }, 20, true);
  assert.equal(r.ok, false);
  assert.equal(r.accepted, 0);
  assert.equal(r.rejected, 20);
  assert.equal(r.partial, false);
  assert.equal(r.reason, "rejected");
});

test("batch 401 -> unauthorized", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 401, ok: false, payload: { error: "Unauthorized" } }, 20, true);
  assert.equal(r.ok, false);
  assert.equal(r.reason, "unauthorized");
  assert.equal(r.rejected, 20);
});

test("single 200 -> accepted 1", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 200, ok: true, payload: { status: "queued", position: 1 } }, 1, false);
  assert.deepEqual(r, { ok: true, accepted: 1, rejected: 0, total: 1, partial: false, reason: "" });
});

test("single 400 -> rejected (invalid URL)", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 400, ok: false, payload: { error: "Invalid URL" } }, 1, false);
  assert.equal(r.ok, false);
  assert.equal(r.accepted, 0);
  assert.equal(r.reason, "rejected");
});

test("network failure -> reason network", () => {
  const b = makeBridge();
  const r = b._mapResult({ status: 0, ok: false, payload: null, reason: "network" }, 20, true);
  assert.equal(r.ok, false);
  assert.equal(r.reason, "network");
});

// ---------------------------------------------------------------------------
// _probe — authenticated state (not reachability alone)
// ---------------------------------------------------------------------------

test("probe: 400 (empty url) -> ready", async () => {
  mockFetch(400, { error: "Missing url" });
  const b = makeBridge();
  assert.equal(await b._probe(), "ready");
});

test("probe: 401 -> unauthorized", async () => {
  mockFetch(401, { error: "Unauthorized" });
  const b = makeBridge();
  assert.equal(await b._probe(), "unauthorized");
});

test("probe: network error -> disconnected", async () => {
  globalThis.fetch = async () => { throw new TypeError("fetch failed"); };
  const b = makeBridge();
  assert.equal(await b._probe(), "disconnected");
});

// ---------------------------------------------------------------------------
// sendBatch / sendDownload end-to-end (fetch mock)
// ---------------------------------------------------------------------------

test("sendBatch posts to /download_many and returns accepted counts", async () => {
  mockFetch(200, { status: "queued", accepted: 5, rejected: 0 });
  const b = makeBridge();
  const r = await b.sendBatch([
    "https://a.com/1.zip", "https://b.com/2.zip", "https://c.com/3.zip",
    "https://d.com/4.zip", "https://e.com/5.zip",
  ]);
  assert.equal(r.ok, true);
  assert.equal(r.accepted, 5);
  assert.equal(r.total, 5);
});

test("sendBatch deduplicates and filters non-http", async () => {
  let captured;
  globalThis.fetch = async (url, opts) => {
    captured = JSON.parse(opts.body);
    return { status: 200, ok: true, text: async () => JSON.stringify({ accepted: 2, rejected: 0 }) };
  };
  const b = makeBridge();
  await b.sendBatch(["https://a.com/1.zip", "https://a.com/1.zip", "not-a-url", "ftp://x/y"]);
  assert.deepEqual(captured.urls, ["https://a.com/1.zip"]);
});

test("sendDownload posts single url to /download", async () => {
  let path, body;
  globalThis.fetch = async (url, opts) => {
    path = url;
    body = JSON.parse(opts.body);
    return { status: 200, ok: true, text: async () => JSON.stringify({ status: "queued", position: 1 }) };
  };
  const b = makeBridge();
  const r = await b.sendDownload("https://a.com/1.zip");
  assert.ok(path.endsWith("/download"));
  assert.equal(body.url, "https://a.com/1.zip");
  assert.equal(body.autostart, true);
  assert.equal(r.accepted, 1);
});

// ---------------------------------------------------------------------------
// waitUntilReady
// ---------------------------------------------------------------------------

test("waitUntilReady polls until the server becomes ready", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls < 3) throw new TypeError("fetch failed");
    return { status: 400, ok: false, text: async () => '{"error":"Missing url"}' };
  };
  const b = makeBridge();
  const status = await b.waitUntilReady(5000, 10);
  assert.equal(status.state, "ready");
  assert.ok(calls >= 3);
});

test("waitUntilReady gives up on unauthorized (no infinite loop)", async () => {
  mockFetch(401, { error: "Unauthorized" });
  const b = makeBridge();
  const status = await b.waitUntilReady(2000, 10);
  assert.equal(status.state, "unauthorized");
});

// ---------------------------------------------------------------------------
// launchApp
// ---------------------------------------------------------------------------

test("launchApp launches dldm://launch and never passes a URL", async () => {
  const tabs = { created: [], removed: [] };
  mockChrome(tabs);
  const b = makeBridge();
  const ok = await b.launchApp();
  assert.equal(ok, true);
  assert.equal(tabs.created.length, 1);
  assert.equal(tabs.created[0].url, "dldm://launch");
  assert.ok(!tabs.created[0].url.includes("http"));
  // removal is scheduled after the linger; assert it was NOT removed synchronously.
  assert.equal(tabs.removed.length, 0);
});

test("launchApp resolves false (no throw) when the tab creation fails", async () => {
  globalThis.chrome = {
    runtime: { getURL: (p) => "chrome-extension://test/" + p },
    tabs: {
      create: (o, cb) => { chrome.runtime.lastError = { message: "blocked" }; cb(); },
      remove: () => {},
    },
  };
  const b = makeBridge();
  const ok = await b.launchApp();
  assert.equal(ok, false);
  delete chrome.runtime.lastError;
});

test("launchApp remove callback tolerates a gone tab (no unhandled lastError)", async () => {
  const tabs = { created: [], removed: [] };
  globalThis.chrome = {
    runtime: { getURL: (p) => "chrome-extension://test/" + p },
    tabs: {
      create: (o, cb) => { tabs.created.push(o); cb({ id: 99 }); },
      // Simulate the tab already being gone when remove runs.
      remove: (id, cb) => { tabs.removed.push(id); chrome.runtime.lastError = { message: "No tab with id: 99" }; cb(); },
    },
  };
  const b = makeBridge();
  const ok = await b.launchApp();
  assert.equal(ok, true);
  assert.equal(tabs.created.length, 1);
  // The removal is scheduled 3s later; this asserts create succeeded and no
  // synchronous throw occurred.  (The `void chrome.runtime.lastError` in the
  // remove callback is what suppresses Chrome's "Unchecked runtime.lastError".)
  delete chrome.runtime.lastError;
});

// ---------------------------------------------------------------------------
// connect re-reads token.json (stale-token recovery)
// ---------------------------------------------------------------------------

test("connect re-reads token.json so a freshly-synced token is picked up", async () => {
  let configCalls = 0;
  globalThis.fetch = async (url) => {
    if (url === "chrome-extension://test/token.json") {
      configCalls++;
      return { ok: true, json: async () => ({ live_server_url: "http://127.0.0.1:6868/download", token: "fresh-token-" + configCalls }) };
    }
    return { status: 400, ok: false, text: async () => '{"error":"Missing url"}' };
  };
  const b = makeBridge(); // _config initially "tok-123"
  await b.connect();
  assert.equal(b._token(), "fresh-token-1");
  await b.connect(); // second connect re-reads the (updated) token.json
  assert.equal(b._token(), "fresh-token-2");
});
