/**
 * N13 Extension Core — background service worker (MV3).
 *
 * The central coordinator:
 *   - owns the single N13Bridge (the only N13 communication layer)
 *   - connection/authentication state + reconnect/backoff
 *   - message routing (content script, popup, grabber)
 *   - Link Grabber coordination (scan → stash → open grabber.html)
 *   - download delivery (single + batch) with launch-and-recover ONLY when the
 *     N13 server is genuinely unreachable
 *   - tab-lifecycle-safe Chrome API usage (no unhandled rejections)
 *
 * The dldm:// protocol is used ONLY to LAUNCH N13 when it is not running.  It
 * is never used as a per-URL delivery transport, and delivery always goes
 * through the authenticated local HTTP API after N13 is ready.  The launch is
 * silent (native messaging first; a hidden iframe otherwise — never a new
 * tab).
 */
importScripts("shared/i18n.js", "shared/mime-analyzer.js", "shared/header-analyzer.js", "shared/filename-resolver.js", "shared/url-analyzer.js", "shared/deduplicator.js", "shared/scoring-engine.js", "shared/download-detector.js", "shared/n13-bridge.js");

const MAX_BATCH = 100;
const MAX_RECENT = 20;
const MAX_STARTUP_WAIT_MS = 30000;
const RELAUNCH_WAIT_MS = 15000; // wait for a cold N13 launch before giving up

const DEFAULT_SETTINGS = {
  enabled: true,
  contextMenu: true,
  detection: true,
  showButton: true,
  openN13: true,
  language: "en",
};

let i18n = null;
let bridge = new N13Bridge();
let lastGrab = null;        // most recent Link Grabber payload (fallback)
let _menuSetupPromise = Promise.resolve();
let _connectPromise = null; // avoid duplicate simultaneous connects
let _launchPromise = null;  // avoid duplicate simultaneous N13 launches

function bgDbg(...args) {
  try { console.log("[N13]", ...args); } catch (e) { /* ignore */ }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isGoodState(state) {
  return state === "ready" || state === "authorized" || state === "connected";
}

// ---------------------------------------------------------------------------
// Settings / i18n
// ---------------------------------------------------------------------------

function getSettings() {
  return new Promise((resolve) => {
    chrome.storage.local.get(DEFAULT_SETTINGS, (items) => resolve({ ...DEFAULT_SETTINGS, ...items }));
  });
}

function setSettings(patch) {
  return getSettings().then((cur) => new Promise((resolve) => chrome.storage.local.set({ ...cur, ...patch }, resolve)));
}

async function initI18n() {
  const settings = await getSettings();
  i18n = new N13_I18N(settings.language);
}

function menuTitle(key) {
  return i18n ? i18n.t(key, key) : key;
}

// ---------------------------------------------------------------------------
// Context menus
// ---------------------------------------------------------------------------

async function setupContextMenus() {
  await initI18n();
  const settings = await getSettings();
  _menuSetupPromise = _menuSetupPromise.then(
    () => new Promise((resolve) => {
      chrome.contextMenus.removeAll(() => {
        if (settings.contextMenu && settings.enabled) {
          const created = () => { if (chrome.runtime.lastError) console.warn("[N13]", chrome.runtime.lastError.message); };
          chrome.contextMenus.create({ id: "n13-download-link", title: menuTitle("downloadWithN13"), contexts: ["link"] }, created);
          chrome.contextMenus.create({ id: "n13-download-media", title: menuTitle("downloadWithN13"), contexts: ["image", "video", "audio"] }, created);
          chrome.contextMenus.create({ id: "n13-download-page", title: menuTitle("downloadPage"), contexts: ["page", "frame"] }, created);
          chrome.contextMenus.create({ id: "n13-download-selection", title: menuTitle("downloadSelection"), contexts: ["selection"] }, created);
        }
        setTimeout(resolve, 50);
      });
    })
  );
  return _menuSetupPromise;
}

chrome.runtime.onInstalled.addListener(() => setupContextMenus().catch(() => {}));
chrome.runtime.onStartup.addListener(() => { setupContextMenus().catch(() => {}); connect().catch(() => {}); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.contextMenu || changes.enabled || changes.language)) setupContextMenus();
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    const settings = await getSettings();
    if (!settings.enabled) return;
    const id = info.menuItemId;
    if (id === "n13-download-link") {
      const url = info.linkUrl || info.srcUrl || info.frameUrl || info.pageUrl;
      if (url) await deliverSingle(url);
    } else if (id === "n13-download-media") {
      const url = info.srcUrl || info.linkUrl || info.pageUrl;
      if (url) await deliverSingle(url);
    } else if (id === "n13-download-page") {
      const url = info.frameUrl || info.pageUrl;
      if (url) await deliverSingle(url);
    } else if (id === "n13-download-selection") {
      const urls = await extractSelectionUrls(tab);
      if (urls.length) await deliverBatch(urls, i18n.t("downloadSelection"));
    }
  } catch (err) {
    bgDbg("context menu error:", err);
  }
});

chrome.commands.onCommand.addListener((command) => {
  if (command !== "download-current-page") return;
  chrome.tabs.query({ active: true, currentWindow: true })
    .then((tabs) => { if (tabs[0] && tabs[0].url) return deliverSingle(tabs[0].url); })
    .catch(() => {});
});

// ---------------------------------------------------------------------------
// Connection state
// ---------------------------------------------------------------------------

function connect() {
  if (!_connectPromise) {
    _connectPromise = bridge.connect().catch(() => bridge.getStatus()).finally(() => { _connectPromise = null; });
  }
  return _connectPromise;
}

/** Launch N13 exactly once (guarded). Never used to deliver URLs. */
function launchN13() {
  if (!_launchPromise) {
    _launchPromise = bridge.launchApp().finally(() => { _launchPromise = null; });
  }
  return _launchPromise;
}

/**
 * Launch N13 (if needed) and wait for its authenticated API to become ready.
 * Returns true only when the bridge reaches an authenticated+reachable state.
 */
async function launchAndWait(timeoutMs = MAX_STARTUP_WAIT_MS) {
  await launchN13();
  const status = await bridge.waitUntilReady(timeoutMs);
  return isGoodState(status.state);
}

/** True when the last send failed because the server was genuinely down. */
function isUnreachable(res) {
  return res && (res.reason === "network" || res.reason === "timeout");
}

// ---------------------------------------------------------------------------
// Delivery
// ---------------------------------------------------------------------------

/** One-way progress broadcast to the grabber/popup (never awaited, never errors). */
function emitProgress(requestId, phase, extra) {
  if (!requestId) return;
  try {
    chrome.runtime.sendMessage({ action: "delivery_progress", requestId, phase, ...(extra || {}) }).catch(() => {});
  } catch (e) { /* ignore */ }
}

/**
 * Silent launch via the registered Native Messaging host (no Chrome dialog).
 * Resolves false when the host is not registered (extension was loaded before
 * the app ever ran) — callers then fall back to the dldm:// protocol launch.
 */
function nativeLaunch() {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage("com.n13.download_manager", { action: "launch" }, (resp) => {
        if (chrome.runtime.lastError) {
          bgDbg("native launch unavailable:", chrome.runtime.lastError.message);
          resolve(false);
          return;
        }
        bgDbg("native launch response:", JSON.stringify(resp));
        resolve(!!(resp && resp.ok));
      });
    } catch (e) {
      bgDbg("native launch error:", e);
      resolve(false);
    }
  });
}

/**
 * When delivery fails because N13 is unreachable, launch it — silently via
 * native messaging when available, otherwise via the dldm:// protocol (hidden
 * iframe, never a new tab) — and wait for its API.  Returns null when the
 * caller should retry the send.
 */
async function recoverUnreachable(res, requestId) {
  if (!isUnreachable(res)) return res;
  const settings = await getSettings();
  if (!settings.openN13) return res;
  bgDbg("N13 unreachable — attempting launch and retry");
  emitProgress(requestId, "launching");
  const launchedSilently = await nativeLaunch();
  if (!launchedSilently) await launchN13(); // fallback: dldm:// protocol
  const ready = await bridge.waitUntilReady(RELAUNCH_WAIT_MS).then((s) => isGoodState(s.state)).catch(() => false);
  if (!ready) {
    bgDbg("N13 did not become ready after launch");
    return res;
  }
  return null; // caller should retry the send
}

/** Single URL: authenticated HTTP only. Auto-launches N13 if unreachable. */
async function deliverSingle(url, requestId) {
  url = String(url || "").trim();
  if (!/^https?:\/\//i.test(url)) {
    notify(i18n.t("unsupportedLink"), i18n.t("onlyHttpSupported"));
    return { ok: false, accepted: 0, rejected: 1, total: 1, reason: "unsupported" };
  }
  emitProgress(requestId, "connecting");
  await connect();
  emitProgress(requestId, "authenticating");
  let res = await bridge.sendDownload(url);
  if (isUnreachable(res)) {
    const retry = await recoverUnreachable(res, requestId);
    if (retry === null) {
      emitProgress(requestId, "sending");
      res = await bridge.sendDownload(url);
    }
  }
  if (isUnreachable(res)) {
    bgDbg("N13 still unreachable after launch attempt");
    notify(i18n.t("n13NotRunning"), i18n.t("startN13Manually"));
    emitProgress(requestId, "done", { accepted: 0, rejected: 1, total: 1, reason: "unreachable" });
    return res;
  }
  emitProgress(requestId, "done", { accepted: res.accepted, rejected: res.rejected, total: res.total, reason: res.reason });
  if (res.ok) {
    notify(i18n.t("sentToQueue"), url.slice(0, 80));
    await addRecent(url);
  } else if (res.reason === "unauthorized") {
    notify(i18n.t("authFailed"), i18n.t("failedToSendDetail"));
  } else {
    notify(i18n.t("failedToSend"), i18n.t("failedToSendDetail"));
  }
  return res;
}

/** Batch URLs: authenticated HTTP only. Auto-launches N13 if unreachable. */
async function deliverBatch(urls, label, requestId) {
  const unique = Array.from(new Set((urls || []).map((u) => String(u || "").trim()).filter((u) => /^https?:\/\//i.test(u)))).slice(0, MAX_BATCH);
  if (!unique.length) return { ok: false, accepted: 0, rejected: 0, total: 0, reason: "no_urls" };

  bgDbg("delivery batch urls=" + unique.length + " label=" + label);
  emitProgress(requestId, "connecting");
  await connect();
  emitProgress(requestId, "sending", { count: unique.length });
  let res = await bridge.sendBatch(unique);
  if (isUnreachable(res)) {
    const retry = await recoverUnreachable(res, requestId);
    if (retry === null) {
      emitProgress(requestId, "sending", { count: unique.length });
      res = await bridge.sendBatch(unique);
    }
  }
  if (isUnreachable(res)) {
    bgDbg("N13 still unreachable after launch attempt");
    notify(i18n.t("n13NotRunning"), i18n.t("startN13Manually"));
    emitProgress(requestId, "done", { accepted: 0, rejected: unique.length, total: unique.length, reason: "unreachable" });
    return res;
  }
  bgDbg("delivery result accepted=" + res.accepted + " rejected=" + res.rejected + "/" + res.total + " reason=" + res.reason);
  emitProgress(requestId, "done", { accepted: res.accepted, rejected: res.rejected, total: res.total, reason: res.reason });
  if (res.ok) {
    notify(i18n.t("batchQueued"), `${res.accepted}/${res.total} · ${label || ""}`);
    for (const u of unique) await addRecent(u);
  } else if (res.reason === "unauthorized") {
    notify(i18n.t("authFailed"), i18n.t("failedToSendDetail"));
  } else {
    notify(i18n.t("failedToSend"), i18n.t("failedToSendDetail"));
  }
  return res;
}

// ---------------------------------------------------------------------------
// Recent sends
// ---------------------------------------------------------------------------

async function addRecent(url) {
  const data = await new Promise((resolve) => chrome.storage.local.get({ recent: [] }, resolve));
  const list = (data.recent || []).filter((x) => x.url !== url);
  list.unshift({ url, time: Date.now() });
  while (list.length > MAX_RECENT) list.pop();
  return new Promise((resolve) => chrome.storage.local.set({ recent: list }, resolve));
}

async function getRecent() {
  const data = await new Promise((resolve) => chrome.storage.local.get({ recent: [] }, resolve));
  return data.recent || [];
}

function notify(title, message) {
  try {
    chrome.notifications.create({ type: "basic", iconUrl: "icon128.png", title: title || "N13", message: message || "", priority: 1 });
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Link Grabber
// ---------------------------------------------------------------------------

async function extractSelectionUrls(tab) {
  if (!tab || !tab.id) return [];
  // Prefer the content engine's real DOM-range selection scan.
  try {
    const res = await chrome.tabs.sendMessage(tab.id, { action: "scan_selection" }, { frameId: 0 });
    if (res && Array.isArray(res.urls) && res.urls.length) return res.urls.map((u) => u.url || u).filter(Boolean);
  } catch (e) { /* content script unavailable; fall back below */ }
  // Fallback: extract http(s) URLs from the raw selected text.
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const t = window.getSelection ? window.getSelection().toString() : "";
        return Array.from(new Set((t.match(/https?:\/\/[^\s"'<>]+/g) || [])));
      },
    });
    return r && r.result ? r.result : [];
  } catch (e) { return []; }
}

/** Scan the active tab's page (popup "Grab Download Links"). */
async function grabPageLinks(tabId) {
  if (!tabId) {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      tabId = tab ? tab.id : 0;
    } catch (e) { tabId = 0; }
  }
  if (!tabId) return { ok: false, urls: [], reason: "no_tab" };
  try {
    const res = await chrome.tabs.sendMessage(tabId, { action: "scan_page" }, { frameId: 0 });
    if (res && Array.isArray(res.urls)) return { ok: true, urls: res.urls };
  } catch (e) { /* content script unavailable; inject below */ }
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [0] }, func: N13_SCAN_PAGE });
    const urls = res && res.result && Array.isArray(res.result.urls) ? res.result.urls : null;
    if (urls) return { ok: true, urls };
  } catch (e) { /* restricted page / tab gone */ }
  return { ok: false, urls: [], reason: "unavailable" };
}

/** Open the Link Grabber with the provided context-aware scan result. */
async function openGrabber(tabId, primaryUrl, urls) {
  const payload = {
    ok: Array.isArray(urls) && urls.length > 0,
    urls: Array.isArray(urls) ? urls : [],
    primaryUrl: primaryUrl || "",
    reason: Array.isArray(urls) && urls.length ? "" : "no_group",
  };
  lastGrab = payload;
  bgDbg("open_grabber tab=" + tabId + " links=" + payload.urls.length);
  try {
    await chrome.storage.session.set({ n13Grabber: payload });
  } catch (e) { bgDbg("session.set failed (in-memory fallback):", e); }
  try {
    await chrome.tabs.create({ url: chrome.runtime.getURL("grabber.html"), active: true });
  } catch (e) { bgDbg("open grabber tab failed:", e); }
}

// ---------------------------------------------------------------------------
// Message API
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  const reply = (p) => { try { sendResponse(p); } catch (e) { /* ignore */ } };

  if (request.action === "status_get") {
    connect().then((s) => reply(s)).catch(() => reply({ state: "error", server: "" }));
    return true;
  }
  if (request.action === "download") {
    deliverSingle(request.url, request.requestId).then((r) => reply({
      status: r.ok ? "sent" : "failed",
      accepted: r.accepted != null ? r.accepted : (r.ok ? 1 : 0),
      rejected: r.rejected != null ? r.rejected : 0,
      total: r.total != null ? r.total : 1,
      partial: !!r.partial,
      reason: r.reason || "",
    })).catch((e) => { bgDbg("download error:", e); reply({ status: "failed", reason: "error" }); });
    return true;
  }
  if (request.action === "download_many") {
    deliverBatch(request.urls, request.label || "grabber", request.requestId).then((r) => reply({
      status: r.ok ? "sent" : "failed",
      accepted: r.accepted || 0,
      rejected: r.rejected != null ? r.rejected : Math.max(0, (request.urls || []).length - (r.accepted || 0)),
      total: r.total != null ? r.total : (request.urls || []).length,
      partial: !!r.partial,
      reason: r.reason || "",
    })).catch((e) => { bgDbg("download_many error:", e); reply({ status: "failed", reason: "error" }); });
    return true;
  }
  if (request.action === "grab_links") {
    grabPageLinks(request.tabId).then((r) => reply({ ok: r.ok, urls: r.urls || [], reason: r.reason || "" }))
      .catch(() => reply({ ok: false, urls: [], reason: "unavailable" }));
    return true;
  }
  if (request.action === "open_grabber") {
    // Content script supplies the context-aware scan (selection/group) in request.urls.
    const tabId = (sender && sender.tab && sender.tab.id) || request.tabId || 0;
    openGrabber(tabId, request.primaryUrl || "", request.urls);
    reply({ ok: true });
    return true;
  }
  if (request.action === "grab_get") {
    reply({ ok: true, payload: lastGrab });
    return true;
  }
  if (request.action === "settings_get") {
    getSettings().then((s) => reply({ settings: s }));
    return true;
  }
  if (request.action === "settings_set") {
    setSettings(request.settings || {}).then(() => reply({ ok: true }));
    setupContextMenus();
    return true;
  }
  if (request.action === "recent_get") {
    getRecent().then((list) => reply({ recent: list }));
    return true;
  }
  if (request.action === "recent_clear") {
    chrome.storage.local.set({ recent: [] }, () => reply({ ok: true }));
    return true;
  }
  if (request.action === "open_n13") {
    connect().then(() => { if (!isGoodState(bridge.getState())) launchN13(); reply({ ok: true }); });
    return true;
  }
  if (request.action === "get_current_tab_url") {
    chrome.tabs.query({ active: true, currentWindow: true }).then((tabs) => reply({ url: (tabs[0] && tabs[0].url) || "" }))
      .catch(() => reply({ url: "" }));
    return true;
  }
});

initI18n().then(setupContextMenus).catch(() => {});
connect().catch(() => {});
