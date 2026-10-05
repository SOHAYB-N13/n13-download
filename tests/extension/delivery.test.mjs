import { test } from "node:test";
import assert from "node:assert/strict";
import { loadBridge } from "./helpers.mjs";

// Mirrors the exact launch-and-recover orchestration background.js runs
// (background.js uses importScripts, which Node cannot load), over the same
// bridge primitives.  A `fetch` router supplies both token.json (so connect()
// re-reads it, as the real bridge does) and the /download_many responses.

const g = loadBridge();

function makeBridge() {
  const b = new g.N13Bridge();
  return b;
}

// fetch router: token.json + /download_many
function routeFetch({ token = "tok-123", responder }) {
  globalThis.fetch = async (url, opts) => {
    if (url === "chrome-extension://test/token.json") {
      return { ok: true, json: async () => ({ live_server_url: "http://127.0.0.1:6868/download", token }) };
    }
    return responder(url, opts);
  };
}

async function deliverBatch(bridge, urls, maxWait = 30000) {
  const unique = Array.from(new Set((urls || []).map((u) => String(u || "").trim()).filter((u) => /^https?:\/\//i.test(u))));
  if (!unique.length) return { ok: false, accepted: 0, rejected: 0, total: 0, reason: "no_urls" };

  await bridge.connect();
  let res = await bridge.sendBatch(unique);

  const unreachable = res.reason === "network" || res.reason === "timeout";
  if (unreachable) {
    await bridge.launchApp();
    const status = await bridge.waitUntilReady(maxWait, 10);
    if (status.state === "ready") res = await bridge.sendBatch(unique);
  }
  return res;
}

test("N13 closed -> launch -> wait -> retry -> 5/5 accepted", async () => {
  let serverUp = false;
  const sentBodies = [];
  const launched = [];

  globalThis.chrome = {
    runtime: { getURL: (p) => "chrome-extension://test/" + p },
    tabs: {
      create: (o, cb) => { launched.push(o.url); cb({ id: 9 }); },
      remove: () => {},
    },
  };
  routeFetch({
    responder: async (url, opts) => {
      if (!serverUp) throw new TypeError("connection refused"); // N13 closed
      sentBodies.push(JSON.parse(opts.body));
      return { status: 200, ok: true, text: async () => JSON.stringify({ status: "queued", accepted: 5, rejected: 0 }) };
    },
  });

  const bridge = makeBridge();
  const boot = setTimeout(() => { serverUp = true; }, 40);

  const urls = ["1", "2", "3", "4", "5"].map((n) => `https://example.com/files/part${n}.zip`);
  const res = await deliverBatch(bridge, urls);
  clearTimeout(boot);

  assert.equal(res.ok, true);
  assert.equal(res.accepted, 5);
  assert.equal(res.rejected, 0);
  assert.equal(res.total, 5);
  assert.equal(res.partial, false);

  assert.ok(launched.includes("dldm://launch"));
  assert.equal(sentBodies.length, 2); // first attempt failed, retry succeeded
  assert.deepEqual(sentBodies[1].urls, urls);
});

test("N13 up and authorized -> no launch, single HTTP send", async () => {
  let launches = 0;
  globalThis.chrome = {
    runtime: { getURL: (p) => "chrome-extension://test/" + p },
    tabs: { create: () => { launches++; }, remove: () => {} },
  };
  routeFetch({
    responder: async () => ({ status: 200, ok: true, text: async () => JSON.stringify({ status: "queued", accepted: 5, rejected: 0 }) }),
  });

  const bridge = makeBridge();
  const urls = ["1", "2", "3", "4", "5"].map((n) => `https://example.com/part${n}.zip`);
  const res = await deliverBatch(bridge, urls);

  assert.equal(res.accepted, 5);
  assert.equal(launches, 0); // no dldm:// launch when N13 is already running
});

test("partial acceptance is reported truthfully (18/20)", async () => {
  routeFetch({
    responder: async () => ({ status: 200, ok: true, text: async () => JSON.stringify({ status: "queued", accepted: 18, rejected: 2 }) }),
  });
  const bridge = makeBridge();
  const urls = Array.from({ length: 20 }, (_, i) => `https://example.com/f${i}.zip`);
  const res = await deliverBatch(bridge, urls);
  assert.equal(res.ok, true);
  assert.equal(res.accepted, 18);
  assert.equal(res.rejected, 2);
  assert.equal(res.partial, true);
});

test("wrong token -> unauthorized (never reported as success)", async () => {
  routeFetch({
    token: "wrong-token",
    responder: async () => ({ status: 401, ok: false, text: async () => JSON.stringify({ error: "Unauthorized" }) }),
  });
  const bridge = makeBridge();
  const urls = ["https://a.com/1.zip", "https://b.com/2.zip"];
  const res = await deliverBatch(bridge, urls);
  assert.equal(res.ok, false);
  assert.equal(res.reason, "unauthorized");
  assert.equal(res.accepted, 0);
});

test("server unavailable -> network (distinct from authorization)", async () => {
  routeFetch({
    responder: async () => { throw new TypeError("connection refused"); },
  });
  // launch also fails; deliverBatch must still report a network failure, not
  // a fake success and not an authorization error.
  globalThis.chrome = {
    runtime: { getURL: (p) => "chrome-extension://test/" + p },
    tabs: { create: (o, cb) => cb({ id: 1 }), remove: () => {} },
  };
  const bridge = makeBridge();
  const urls = ["https://a.com/1.zip", "https://b.com/2.zip"];
  const res = await deliverBatch(bridge, urls, 50); // short wait
  assert.equal(res.ok, false);
  assert.ok(res.reason === "network" || res.reason === "timeout");
  assert.notEqual(res.reason, "unauthorized");
});
