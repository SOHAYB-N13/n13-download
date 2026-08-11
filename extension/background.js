const DEFAULT_SERVER = "http://127.0.0.1:6868/download";
const BATCH_ENDPOINT = "http://127.0.0.1:6868/download_many";
const HEALTH_URL = "http://127.0.0.1:6868/health";
const PROTOCOL = "dldm";

// Extensions that indicate a direct download link.
const FILE_EXT_RE = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|apk|dmg|deb|rpm|pdf|docx?|xlsx?|pptx?|txt|md|mp4|mkv|avi|mov|webm|flv|mp3|flac|wav|ogg|m4a|jpg|jpe?g|png|gif|webp|svg|ico|torrent)(\?|#|$)/i;
const MAX_BATCH = 100;
const MAX_PROTOCOL_FALLBACK = 5;

// Reload token.json on every call (cheap, avoids stale cached config when the
// user regenerates the token). Service-worker keepalive is short anyway.
async function loadConfig() {
  try {
    const res = await fetch(chrome.runtime.getURL("token.json"), { cache: "no-store" });
    if (res.ok) {
      return await res.json();
    }
  } catch (_) {}
  return { live_server_url: DEFAULT_SERVER, token: "" };
}

function setupContextMenus() {
  // Recreate idempotently so reinstall/update does not throw "duplicate id".
  try {
    chrome.contextMenus.removeAll(() => {
      chrome.contextMenus.create({ id: "tdm-download-link", title: "Download with TDM", contexts: ["link"] });
      chrome.contextMenus.create({ id: "tdm-download-media", title: "Download media with TDM", contexts: ["image", "video", "audio"] });
      chrome.contextMenus.create({ id: "tdm-download-page", title: "Download page with TDM", contexts: ["page", "frame"] });
      chrome.contextMenus.create({ id: "tdm-download-selection", title: "Download selected links with TDM", contexts: ["selection"] });
      chrome.contextMenus.create({ id: "tdm-download-all", title: "Download all links on this page", contexts: ["page"] });
    });
  } catch (_) {
    /* contextMenus may be unavailable */
  }
}

chrome.runtime.onInstalled.addListener(setupContextMenus);
chrome.runtime.onStartup.addListener(setupContextMenus);

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const id = info.menuItemId;
  if (id === "tdm-download-link") {
    const url = info.linkUrl || info.srcUrl || info.frameUrl || info.pageUrl;
    if (url) sendToTDM(url);
  } else if (id === "tdm-download-media") {
    const url = info.srcUrl || info.linkUrl || info.pageUrl;
    if (url) sendToTDM(url);
  } else if (id === "tdm-download-page") {
    const url = info.frameUrl || info.pageUrl;
    if (url) sendToTDM(url);
  } else if (id === "tdm-download-selection") {
    const urls = await extractSelectionUrls(tab);
    if (urls.length) sendBatch(urls, "selected links");
    else notify("No links found", "The selected text contains no http(s) links");
  } else if (id === "tdm-download-all") {
    const urls = await extractAllLinks(tab);
    if (urls.length) sendBatch(urls, `${urls.length} links`);
    else notify("No links found", "No downloadable links were detected on this page");
  }
});

chrome.commands.onCommand.addListener((command) => {
  if (command !== "download-current-page") return;
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs[0]?.url) sendToTDM(tabs[0].url);
  });
});

// ---------------------------------------------------------------------------
// Page scanning
// ---------------------------------------------------------------------------

async function extractAllLinks(tab) {
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => {
      const out = [];
      document.querySelectorAll("a[href]").forEach((a) => {
        try {
          const u = new URL(a.href, location.href);
          if (u.protocol === "http:" || u.protocol === "https:") out.push(u.href);
        } catch (e) {}
      });
      return out;
    }});
    return (res?.result || []).filter((u) => FILE_EXT_RE.test(u));
  } catch (_) {
    return [];
  }
}

async function extractSelectionUrls(tab) {
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => {
      const text = window.getSelection ? window.getSelection().toString() : "";
      return text;
    }});
    const text = res?.result || "";
    return [...new Set(text.match(/https?:\/\/[^\s"'<>]+/g) || [])];
  } catch (_) {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

async function sendToTDM(url) {
  url = (url || "").trim();
  if (!/^https?:\/\//i.test(url)) {
    notify("Unsupported link", "Only http(s) links can be sent to TDM");
    return false;
  }
  const ok = await sendViaLiveServer([url]);
  if (ok) {
    notify("Download queued", url.slice(0, 80));
    return true;
  }
  return sendViaProtocol(url);
}

async function sendBatch(urls, label) {
  const unique = [...new Set(urls.map((u) => (u || "").trim()).filter((u) => /^https?:\/\//i.test(u)))].slice(0, MAX_BATCH);
  if (!unique.length) return;
  const ok = await sendViaLiveServer(unique);
  if (ok) {
    notify("Batch queued", `${unique.length} download${unique.length === 1 ? "" : "s"} sent (${label})`);
    return;
  }
  // Fallback: send the first few via the dldm:// protocol.
  for (const u of unique.slice(0, MAX_PROTOCOL_FALLBACK)) {
    if (!sendViaProtocol(u)) break;
  }
}

async function sendViaLiveServer(urls) {
  const cfg = await loadConfig();
  const token = cfg.token || "";
  if (!token) return false;

  try {
    const health = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(800) });
    if (!health.ok) return false;
  } catch (_) {
    return false;
  }

  const body = Array.isArray(urls) && urls.length > 1
    ? JSON.stringify({ urls })
    : JSON.stringify({ url: urls[0] });
  const endpoint = (Array.isArray(urls) && urls.length > 1) ? BATCH_ENDPOINT : (cfg.live_server_url || DEFAULT_SERVER);

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
        "X-TDM-Token": token,
      },
      body,
      signal: AbortSignal.timeout(2000),
    });
    return response.ok;
  } catch (_) {
    return false;
  }
}

// Send via the dldm:// custom protocol WITHOUT disturbing the user's tab.
async function sendViaProtocol(url) {
  const encoded = encodeURIComponent(url);
  const protocolUrl = `${PROTOCOL}://${encoded}`;

  try {
    const tab = await chrome.tabs.create({ url: "about:blank", active: false });
    if (!tab || !tab.id) throw new Error("No tab");
    await chrome.tabs.update(tab.id, { url: protocolUrl });
    setTimeout(() => {
      try { chrome.tabs.remove(tab.id); } catch (_) {}
    }, 1500);
    notify("Sent via protocol", url.slice(0, 80));
    return true;
  } catch (err) {
    console.warn("Protocol dispatch failed", err);
    notify("Could not send", "Live Server is off and protocol did not respond");
    return false;
  }
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icon48.png",
    title,
    message,
    priority: 1,
  });
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action === "download") {
    sendToTDM(request.url).then((ok) => sendResponse({ status: ok ? "sent" : "failed" }));
    return true;
  }
  if (request.action === "download_many") {
    sendBatch(request.urls || [], "popup").then(() => sendResponse({ status: "sent" }));
    return true;
  }
  if (request.action === "health") {
    fetch(HEALTH_URL, { signal: AbortSignal.timeout(1000) })
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, data }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
});
