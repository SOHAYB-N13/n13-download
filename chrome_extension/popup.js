/** N13 extension popup. */
(function () {
  "use strict";

  let i18n;
  let settings = {};

  const $ = (id) => document.getElementById(id);

  async function init() {
    const res = await sendMessage({ action: "settings_get" });
    settings = res?.settings || { language: "en" };
    i18n = new N13_I18N(settings.language);
    document.documentElement.setAttribute("dir", i18n.isRtl() ? "rtl" : "ltr");
    applyTranslations();
    bindEvents();
    refreshStatus();
    loadRecent();
    fillCurrentPage();
  }

  function sendMessage(msg) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (response) => {
        if (chrome.runtime.lastError) return resolve({});
        resolve(response || {});
      });
    });
  }

  function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      el.textContent = i18n.t(key);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      el.placeholder = i18n.t(key);
    });
  }

  function showMsg(text, ok) {
    const el = $("msg");
    el.textContent = text;
    el.className = "n13-msg visible " + (ok ? "ok" : "err");
    setTimeout(() => el.classList.remove("visible"), 3200);
  }

  async function refreshStatus() {
    const res = await sendMessage({ action: "health", timeout: 1500 });
    const connected = !!res?.ok;
    $("statusDot").classList.toggle("connected", connected);
    $("statusText").textContent = i18n.t(connected ? "statusConnected" : "statusNotRunning");
  }

  async function fillCurrentPage() {
    const res = await sendMessage({ action: "get_current_tab_url" });
    const url = res?.url || "";
    $("downloadPageBtn").disabled = !/^https?:\/\//i.test(url);
  }

  function extractUrls(text) {
    return text.split("\n").map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  }

  async function sendUrls(urls) {
    if (!urls.length) {
      showMsg(i18n.t("noLinksFound"), false);
      return;
    }
    $("downloadBtn").disabled = true;
    const msg = urls.length === 1
      ? { action: "download", url: urls[0] }
      : { action: "download_many", urls: urls, label: "popup" };
    const res = await sendMessage(msg);
    $("downloadBtn").disabled = false;
    if (res?.status === "sent") {
      showMsg(i18n.t("sentToQueue"), true);
      $("urlInput").value = "";
      loadRecent();
    } else {
      showMsg(i18n.t("failedToSend"), false);
    }
  }

  async function loadRecent() {
    const res = await sendMessage({ action: "recent_get" });
    const list = res?.recent || [];
    const ul = $("recentList");
    ul.innerHTML = "";
    if (!list.length) {
      ul.innerHTML = `<li class="n13-empty">${i18n.t("noRecent")}</li>`;
      $("recentSection").style.display = "block";
      return;
    }
    list.slice(0, 8).forEach((item) => {
      const li = document.createElement("li");
      li.title = item.url;
      li.innerHTML = `<span class="n13-url">${escapeHtml(item.url)}</span>`;
      li.addEventListener("click", () => sendUrls([item.url]));
      ul.appendChild(li);
    });
    $("recentSection").style.display = "block";
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function bindEvents() {
    $("downloadBtn").addEventListener("click", () => sendUrls(extractUrls($("urlInput").value)));
    $("urlInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendUrls(extractUrls($("urlInput").value));
      }
    });

    $("downloadPageBtn").addEventListener("click", async () => {
      const res = await sendMessage({ action: "get_current_tab_url" });
      if (res?.url) sendUrls([res.url]);
    });

    $("clearRecentBtn").addEventListener("click", async () => {
      await sendMessage({ action: "recent_clear" });
      loadRecent();
    });

    $("openN13Btn").addEventListener("click", () => {
      // Prefer the dldm:// protocol to launch the app; a harmless URL triggers it.
      chrome.tabs.create({ url: "dldm://launch", active: true }).catch(() => {});
    });

    $("settingsBtn").addEventListener("click", () => {
      chrome.runtime.openOptionsPage();
    });

    $("aboutBtn").addEventListener("click", () => {
      const manifest = chrome.runtime.getManifest();
      showMsg(`${i18n.t("aboutText")} ${i18n.t("version")} ${manifest.version}`, true);
    });

    // Refresh connection status every 4s while popup is open.
    setInterval(refreshStatus, 4000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
