/** N13 extension popup — status + actions. */
(function () {
  "use strict";

  let i18n;
  let settings = {};

  const $ = (id) => document.getElementById(id);

  function sendMessage(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => {
          if (chrome.runtime.lastError) return resolve({});
          resolve(r || {});
        });
      } catch (e) { resolve({}); }
    });
  }

  async function init() {
    const res = await sendMessage({ action: "settings_get" });
    settings = res.settings || { language: "en" };
    i18n = new N13_I18N(settings.language);
    document.documentElement.setAttribute("dir", i18n.isRtl() ? "rtl" : "ltr");
    applyTranslations();
    bindEvents();
    refreshStatus();
    loadRecent();
    fillCurrentPage();
  }

  function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = i18n.t(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.placeholder = i18n.t(el.getAttribute("data-i18n-placeholder"));
    });
  }

  function showMsg(el, text, ok) {
    if (!el) return;
    el.textContent = text;
    el.className = "n13-msg visible " + (ok ? "ok" : "err");
    clearTimeout(showMsg._t);
    showMsg._t = setTimeout(() => el.classList.remove("visible"), 3200);
  }

  async function refreshStatus() {
    const res = await sendMessage({ action: "status_get" });
    const state = (res && res.state) || "disconnected";
    let label, connected = false;
    if (state === "ready" || state === "authorized" || state === "connected") {
      label = i18n.t("statusConnected"); connected = true;
    } else if (state === "connecting" || state === "authenticating" || state === "reconnecting") {
      label = i18n.t("statusConnecting");
    } else if (state === "unauthorized") {
      label = i18n.t("statusAuthError");
    } else {
      label = i18n.t("statusNotRunning");
    }
    $("statusDot").classList.toggle("connected", connected);
    $("statusText").textContent = label;
  }

  async function fillCurrentPage() {
    const res = await sendMessage({ action: "get_current_tab_url" });
    const url = (res && res.url) || "";
    $("downloadPageBtn").disabled = !/^https?:\/\//i.test(url);
  }

  function extractUrls(text) {
    return String(text || "").split("\n").map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  }

  async function sendUrls(urls) {
    if (!urls.length) { showMsg($("msg"), i18n.t("noLinksFound"), false); return; }
    $("downloadBtn").disabled = true;
    const res = await sendMessage(urls.length === 1
      ? { action: "download", url: urls[0] }
      : { action: "download_many", urls: urls, label: "popup" });
    $("downloadBtn").disabled = false;
    if (res.status === "sent" && res.partial) {
      showMsg($("msg"), i18n.t("grabberPartial")
        .replace("{n}", String(res.accepted || 0))
        .replace("{total}", String(res.total || urls.length))
        .replace("{rejected}", String(res.rejected || 0)), false);
      $("urlInput").value = "";
      loadRecent();
    } else if (res.status === "sent") {
      showMsg($("msg"), i18n.t("sentToQueue"), true);
      $("urlInput").value = "";
      loadRecent();
    } else if (res.reason === "unauthorized") {
      showMsg($("msg"), i18n.t("authFailed"), false);
    } else {
      showMsg($("msg"), i18n.t("failedToSend"), false);
    }
  }

  async function loadRecent() {
    const res = await sendMessage({ action: "recent_get" });
    const list = (res && res.recent) || [];
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
      const span = document.createElement("span");
      span.className = "n13-url";
      span.textContent = item.url;
      li.appendChild(span);
      li.addEventListener("click", () => sendUrls([item.url]));
      ul.appendChild(li);
    });
    $("recentSection").style.display = "block";
  }

  async function openGrabber() {
    showMsg($("grabMsg"), i18n.t("scanning"), true);
    const res = await sendMessage({ action: "grab_links" });
    if (res && res.ok && res.urls.length) {
      await sendMessage({ action: "open_grabber", urls: res.urls });
      showMsg($("grabMsg"), "", true);
    } else {
      showMsg($("grabMsg"), i18n.t("grabbedNone"), false);
    }
  }

  function bindEvents() {
    $("downloadBtn").addEventListener("click", () => sendUrls(extractUrls($("urlInput").value)));
    $("urlInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendUrls(extractUrls($("urlInput").value)); }
    });
    $("downloadPageBtn").addEventListener("click", async () => {
      const res = await sendMessage({ action: "get_current_tab_url" });
      if (res && res.url) sendUrls([res.url]);
    });
    $("grabBtn").addEventListener("click", openGrabber);
    $("clearRecentBtn").addEventListener("click", async () => {
      await sendMessage({ action: "recent_clear" });
      loadRecent();
    });
    $("openN13Btn").addEventListener("click", () => sendMessage({ action: "open_n13" }));
    $("settingsBtn").addEventListener("click", () => chrome.runtime.openOptionsPage());
    $("aboutBtn").addEventListener("click", () => {
      const manifest = chrome.runtime.getManifest();
      showMsg($("msg"), `${i18n.t("aboutText")} ${i18n.t("version")} ${manifest.version}`, true);
    });
    setInterval(refreshStatus, 4000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
