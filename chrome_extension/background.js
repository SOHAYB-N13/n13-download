const DEFAULT_SERVER = "http://127.0.0.1:6868/download";
const HEALTH_URL = "http://127.0.0.1:6868/health";
const PROTOCOL = "dldm";

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
      chrome.contextMenus.create({
        id: "tdm-download-link",
        title: "Download with TDM",
        contexts: ["link"]
      });
      chrome.contextMenus.create({
        id: "tdm-download-media",
        title: "Download media with TDM",
        contexts: ["image", "video", "audio"]
      });
      chrome.contextMenus.create({
        id: "tdm-download-page",
        title: "Download page with TDM",
        contexts: ["page", "frame"]
      });
    });
  } catch (_) {
    /* contextMenus may be unavailable */
  }
}

chrome.runtime.onInstalled.addListener(setupContextMenus);
chrome.runtime.onStartup.addListener(setupContextMenus);

chrome.contextMenus.onClicked.addListener((info) => {
  // Prefer linkUrl, then srcUrl (media), then the page/frame URL.
  let url = info.linkUrl || info.srcUrl || info.frameUrl || info.pageUrl;
  if (url) sendToTDM(url);
});

chrome.commands.onCommand.addListener((command) => {
  if (command !== "download-current-page") return;
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (tabs[0]?.url) sendToTDM(tabs[0].url);
  });
});

async function sendToTDM(url) {
  url = (url || "").trim();
  if (!/^https?:\/\//i.test(url)) {
    // Some hosts expose blob:/data: media — we cannot download those directly.
    notify("Unsupported link", "Only http(s) links can be sent to TDM");
    return false;
  }
  const ok = await sendViaLiveServer(url);
  if (ok) {
    notify("Download queued", url.slice(0, 80));
    return true;
  }
  return sendViaProtocol(url);
}

async function sendViaLiveServer(url) {
  const cfg = await loadConfig();
  const endpoint = cfg.live_server_url || DEFAULT_SERVER;
  const token = cfg.token || "";
  if (!token) return false;

  try {
    const health = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(800) });
    if (!health.ok) return false;
  } catch (_) {
    return false;
  }

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
        "X-TDM-Token": token
      },
      body: JSON.stringify({ url }),
      signal: AbortSignal.timeout(2000)
    });
    return response.ok;
  } catch (_) {
    return false;
  }
}

// Send via the dldm:// custom protocol WITHOUT disturbing the user's tab.
// We open a throwaway about:blank tab, navigate it to the protocol URL so the
// OS handler fires, then close it immediately. This never replaces whatever
// the user was looking at.
async function sendViaProtocol(url) {
  const encoded = encodeURIComponent(url);
  const protocolUrl = `${PROTOCOL}://${encoded}`;

  try {
    const tab = await chrome.tabs.create({ url: "about:blank", active: false });
    if (!tab || !tab.id) throw new Error("No tab");

    await chrome.tabs.update(tab.id, { url: protocolUrl });

    // Close the helper tab shortly after — enough time for the OS to dispatch.
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
    priority: 1
  });
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action === "download") {
    sendToTDM(request.url).then((ok) => sendResponse({ status: ok ? "sent" : "failed" }));
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
