/**
 * N13 Chrome Extension — background service worker.
 *
 * Responsibilities:
 *   - Load shared i18n + API helpers.
 *   - Create context menus based on user language.
 *   - Dispatch URLs to the local N13 app (Live Server or dldm:// fallback).
 *   - Keep a small recent-sends log for the popup.
 *   - Handle keyboard shortcuts.
 */
importScripts("shared/i18n.js", "shared/api.js");

const FILE_EXT_RE = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|apk|dmg|deb|rpm|pdf|docx?|xlsx?|pptx?|txt|md|mp4|mkv|avi|mov|webm|flv|mp3|flac|wav|ogg|m4a|jpg|jpe?g|png|gif|webp|svg|ico|torrent)(\?|#|$)/i;
const MAX_BATCH = 100;
const MAX_RECENT = 20;

const DEFAULT_SETTINGS = {
  enabled: true,
  contextMenu: true,
  detection: true,
  showButton: true,
  openN13: true,
  language: "en",
};

let api = null;
let i18n = null;
let _menuSetupPromise = Promise.resolve();

async function getSettings() {
  return new Promise((resolve) => {
    chrome.storage.local.get(DEFAULT_SETTINGS, (items) => {
      resolve({ ...DEFAULT_SETTINGS, ...items });
    });
  });
}

async function setSettings(patch) {
  const current = await getSettings();
  const next = { ...current, ...patch };
  return new Promise((resolve) => chrome.storage.local.set(next, resolve));
}

async function initI18n() {
  const settings = await getSettings();
  i18n = new N13_I18N(settings.language);
}

async function ensureAPI() {
  if (!api) api = new N13_API();
  await api.loadConfig();
}

function menuTitle(key) {
  return i18n ? i18n.t(key, key) : key;
}

async function setupContextMenus() {
  await initI18n();
  const settings = await getSettings();

  // Chain menu updates so concurrent calls (install + startup + settings change)
  // never race and create duplicate IDs.
  _menuSetupPromise = _menuSetupPromise.then(
    () =>
      new Promise((resolve) => {
        chrome.contextMenus.removeAll(() => {
          if (chrome.runtime.lastError) {
            console.warn("removeAll:", chrome.runtime.lastError.message);
          }
          if (!settings.contextMenu || !settings.enabled) {
            resolve();
            return;
          }

          const onCreated = () => {
            if (chrome.runtime.lastError) {
              console.warn("create menu:", chrome.runtime.lastError.message);
            }
          };

          chrome.contextMenus.create(
            {
              id: "n13-download-link",
              title: menuTitle("downloadWithN13"),
              contexts: ["link"],
            },
            onCreated
          );
          chrome.contextMenus.create(
            {
              id: "n13-download-media",
              title: menuTitle("downloadWithN13"),
              contexts: ["image", "video", "audio"],
            },
            onCreated
          );
          chrome.contextMenus.create(
            {
              id: "n13-download-page",
              title: menuTitle("downloadPage"),
              contexts: ["page", "frame"],
            },
            onCreated
          );
          chrome.contextMenus.create(
            {
              id: "n13-download-selection",
              title: menuTitle("downloadSelection"),
              contexts: ["selection"],
            },
            onCreated
          );

          // Allow create callbacks to fire before resolving.
          setTimeout(resolve, 50);
        });
      })
  );
  return _menuSetupPromise;
}

chrome.runtime.onInstalled.addListener(() => setupContextMenus().catch(() => {}));
chrome.runtime.onStartup.addListener(() => setupContextMenus().catch(() => {}));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.contextMenu || changes.enabled || changes.language)) {
    setupContextMenus();
  }
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const settings = await getSettings();
  if (!settings.enabled) return;

  const id = info.menuItemId;
  const safe = (p) => p && p.catch && p.catch((e) => console.warn("[N13]", e));
  if (id === "n13-download-link") {
    const url = info.linkUrl || info.srcUrl || info.frameUrl || info.pageUrl;
    if (url) safe(sendToN13(url));
  } else if (id === "n13-download-media") {
    const url = info.srcUrl || info.linkUrl || info.pageUrl;
    if (url) safe(sendToN13(url));
  } else if (id === "n13-download-page") {
    const url = info.frameUrl || info.pageUrl;
    if (url) safe(sendToN13(url));
  } else if (id === "n13-download-selection") {
    const urls = await extractSelectionUrls(tab);
    if (urls.length) safe(sendBatch(urls, i18n.t("downloadSelection")));
    else notify(i18n.t("noLinksFound"), i18n.t("noLinksFoundDetail"));
  }
});

chrome.commands.onCommand.addListener((command) => {
  if (command !== "download-current-page") return;
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs[0]?.url) sendToN13(tabs[0].url).catch((e) => console.warn("[N13]", e));
  });
});

// ---------------------------------------------------------------------------
// Page scanning helpers (executed in the target tab)
// ---------------------------------------------------------------------------

async function extractSelectionUrls(tab) {
  if (!tab?.id) return [];
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const text = window.getSelection ? window.getSelection().toString() : "";
        return [...new Set((text.match(/https?:\/\/[^\s"'<>]+/g) || []))];
      },
    });
    return res?.result || [];
  } catch (_) {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

async function sendToN13(url) {
  url = String(url || "").trim();
  if (!/^https?:\/\//i.test(url)) {
    notify(i18n.t("unsupportedLink"), i18n.t("onlyHttpSupported"));
    return { ok: false };
  }

  await ensureAPI();
  const res = await api.send([url]);
  if (res.ok) {
    notify(i18n.t("sentToQueue"), url.slice(0, 80));
    await addRecent(url);
    return { ok: true, count: 1 };
  }

  // Fallback to protocol handler (also wakes/launches N13 if registered).
  const protocolOk = await api.sendViaProtocol(url);
  if (protocolOk) {
    notify(i18n.t("sentToQueue"), url.slice(0, 80));
    await addRecent(url);
    return { ok: true, count: 1 };
  }

  notify(i18n.t("failedToSend"), i18n.t("failedToSendDetail"));
  return { ok: false };
}

async function sendBatch(urls, label) {
  const unique = [...new Set(urls.map((u) => (u || "").trim()).filter((u) => /^https?:\/\//i.test(u)))].slice(0, MAX_BATCH);
  if (!unique.length) return { ok: false };

  await ensureAPI();
  const res = await api.send(unique);
  if (res.ok) {
    notify(i18n.t("batchQueued"), `${unique.length} · ${label || ""}`);
    for (const u of unique) await addRecent(u);
    return { ok: true, count: unique.length };
  }

  // Fallback: send each URL via protocol, limited to a few to avoid tab spam.
  let sent = 0;
  for (const u of unique.slice(0, 3)) {
    const ok = await api.sendViaProtocol(u);
    if (!ok) break;
    sent++;
    await addRecent(u);
  }
  if (sent) {
    notify(i18n.t("batchQueued"), `${sent}/${unique.length} · ${label || ""}`);
    return { ok: true, count: sent };
  }

  notify(i18n.t("failedToSend"), i18n.t("failedToSendDetail"));
  return { ok: false };
}

// ---------------------------------------------------------------------------
// Recent sends
// ---------------------------------------------------------------------------

async function addRecent(url) {
  const data = await new Promise((resolve) => chrome.storage.local.get({ recent: [] }, resolve));
  const list = data.recent || [];
  list.unshift({ url, time: Date.now() });
  while (list.length > MAX_RECENT) list.pop();
  return new Promise((resolve) => chrome.storage.local.set({ recent: list }, resolve));
}

async function getRecent() {
  const data = await new Promise((resolve) => chrome.storage.local.get({ recent: [] }, resolve));
  return data.recent || [];
}

async function clearRecent() {
  return new Promise((resolve) => chrome.storage.local.set({ recent: [] }, resolve));
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

function notify(title, message) {
  try {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: title || "N13",
      message: message || "",
      priority: 1,
    });
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// Message API for popup / content scripts
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  const reply = (payload) => {
    try {
      sendResponse(payload);
    } catch (_) {}
  };

  if (request.action === "download") {
    sendToN13(request.url)
      .then((r) => reply({ status: r.ok ? "sent" : "failed" }))
      .catch((e) => { console.warn("[N13]", e); reply({ status: "failed" }); });
    return true;
  }
  if (request.action === "download_many") {
    sendBatch(request.urls || [], request.label || "popup")
      .then((r) => reply({ status: r.ok ? "sent" : "failed", count: r.count || 0 }))
      .catch((e) => { console.warn("[N13]", e); reply({ status: "failed" }); });
    return true;
  }
  if (request.action === "health") {
    ensureAPI().then(() => api.isConnected(request.timeout || 1200))
      .then((ok) => reply({ ok }))
      .catch(() => reply({ ok: false }));
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
    clearRecent().then(() => reply({ ok: true }));
    return true;
  }
  if (request.action === "get_current_tab_url") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      reply({ url: tabs[0]?.url || "" });
    });
    return true;
  }
});

// Initialise i18n on load so context menus are ready quickly.
initI18n().then(setupContextMenus);
