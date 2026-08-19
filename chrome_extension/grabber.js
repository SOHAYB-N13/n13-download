/**
 * N13 Link Grabber — dedicated window.
 *
 * Loads the context-aware scan produced by the content engine (via
 * chrome.storage.session with a background grab_get fallback), shows the links,
 * and sends ONLY the user's selection to N13 through the background coordinator
 * (which owns the N13Bridge).  Never a blank page: it has explicit states.
 *
 * States: LOADING / FOUND / EMPTY / SCAN_UNAVAILABLE / CONNECTION_ERROR /
 * SEND_ERROR / SUCCESS / PARTIAL_SUCCESS.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  let i18n;
  let items = [];            // [{url, name}] in display order
  let selected = new Set();  // indices
  let anchor = -1;
  let sending = false;
  let currentRequestId = null; // correlates delivery progress events

  init();

  async function init() {
    i18n = new N13_I18N("en");
    try {
      const stored = await new Promise((resolve) => chrome.storage.local.get({ language: "en" }, resolve));
      i18n = new N13_I18N(stored.language || "en");
    } catch (e) { /* ignore */ }
    document.documentElement.setAttribute("dir", i18n.isRtl() ? "rtl" : "ltr");
    applyTranslations();
    bindEvents();
    await loadResults();
  }

  function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = i18n.t(el.getAttribute("data-i18n"));
    });
  }

  function fmtCount(key, n, total, rejected) {
    return i18n.t(key, key)
      .replace("{n}", String(n))
      .replace("{total}", String(total))
      .replace("{rejected}", String(rejected != null ? rejected : ""));
  }

  function showMsg(text, ok) {
    const el = $("msg");
    el.textContent = text;
    el.className = "n13-msg visible " + (ok ? "ok" : "err");
    clearTimeout(showMsg._t);
    showMsg._t = setTimeout(() => el.classList.remove("visible"), 4000);
  }

  function setEmpty(text) {
    const el = $("empty");
    el.textContent = text;
    el.hidden = false;
    $("summary").textContent = "";
    $("list").innerHTML = "";
    renderCount();
  }

  // ------------------------------------------------------------------ results
  function loadResults() {
    return new Promise((resolve) => {
      const fromStorage = () => new Promise((res) => {
        try {
          chrome.storage.session.get("n13Grabber", (data) => res((data && data.n13Grabber) || null));
        } catch (e) { res(null); }
      });
      const fromBackground = () => new Promise((res) => {
        try {
          chrome.runtime.sendMessage({ action: "grab_get" }, (r) => {
            if (chrome.runtime.lastError) return res(null);
            res((r && r.payload) || null);
          });
        } catch (e) { res(null); }
      });

      (async () => {
        let payload = await fromStorage();
        if (!payload || !Array.isArray(payload.urls)) payload = await fromBackground();
        if (!payload || !Array.isArray(payload.urls)) {
          setEmpty(i18n.t("connectionError"));
          resolve();
          return;
        }
        if (!payload.ok) {
          setEmpty(payload.reason === "no_group" ? i18n.t("noGroup") : i18n.t("grabUnavailable") + " — " + i18n.t("grabUnavailableDetail"));
          resolve();
          return;
        }
        items = payload.urls
          .map((u) => ({ url: (u && u.url) || "", name: (u && u.name) || (u && u.url) || "" }))
          .filter((it) => it.url);
        if (!items.length) {
          setEmpty(i18n.t("grabbedNone"));
          resolve();
          return;
        }
        // relevance: hovered link first
        const primary = payload.primaryUrl || "";
        if (primary) {
          const idx = items.findIndex((it) => it.url === primary);
          if (idx > 0) { const [p] = items.splice(idx, 1); items.unshift(p); }
        }
        selected = new Set(items.map((_, i) => i));
        anchor = -1;
        renderList();
        resolve();
      })();
    });
  }

  function renderCount() {
    $("count").textContent = fmtCount("selectedCount", selected.size, items.length);
    $("addBtn").disabled = selected.size === 0;
  }

  function renderList() {
    const list = $("list");
    list.innerHTML = "";
    $("empty").hidden = items.length > 0;
    $("summary").textContent = items.length ? fmtCount("foundLinks", items.length, "") : "";
    if (!items.length) { renderCount(); return; }
    const frag = document.createDocumentFragment();
    items.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = "n13-grab-item" + (selected.has(idx) ? " sel" : "");
      row.dataset.idx = String(idx);
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "n13-grab-check";
      cb.checked = selected.has(idx);
      cb.tabIndex = -1;
      const body = document.createElement("span");
      body.className = "n13-grab-body";
      const name = document.createElement("span");
      name.className = "n13-grab-name";
      name.textContent = item.name;
      const url = document.createElement("span");
      url.className = "n13-grab-url";
      url.textContent = item.url;
      body.appendChild(name);
      body.appendChild(url);
      row.appendChild(cb);
      row.appendChild(body);
      row.addEventListener("click", (e) => onRowClick(idx, e));
      frag.appendChild(row);
    });
    list.appendChild(frag);
    renderCount();
  }

  // ------------------------------------------------------------------ selection
  function onRowClick(idx, e) {
    const ctrl = !!(e && (e.ctrlKey || e.metaKey));
    const shift = !!(e && e.shiftKey);
    if (shift && anchor >= 0) {
      const lo = Math.min(anchor, idx), hi = Math.max(anchor, idx);
      if (ctrl) {
        for (let i = lo; i <= hi; i++) selected.add(i);
      } else {
        selected.clear();
        for (let i = lo; i <= hi; i++) selected.add(i);
      }
    } else if (ctrl) {
      if (selected.has(idx)) selected.delete(idx); else selected.add(idx);
      anchor = idx;
    } else {
      selected.clear(); selected.add(idx); anchor = idx;
    }
    syncRows();
    renderCount();
  }

  function syncRows() {
    const rows = $("list").querySelectorAll(".n13-grab-item");
    for (let i = 0; i < rows.length; i++) {
      const on = selected.has(i);
      rows[i].classList.toggle("sel", on);
      const cb = rows[i].querySelector("input");
      if (cb) cb.checked = on;
    }
  }

  function selectAll() { selected = new Set(items.map((_, i) => i)); anchor = -1; syncRows(); renderCount(); }
  function clearSelection() { selected = new Set(); anchor = -1; syncRows(); renderCount(); }

  // ------------------------------------------------------------------ send
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

  async function sendSelected() {
    if (sending) return; // double-click protection
    const urls = items.filter((_, i) => selected.has(i)).map((it) => it.url);
    if (!urls.length) { showMsg(i18n.t("noLinksFound"), false); return; }

    sending = true;
    $("addBtn").disabled = true;
    const requestId = "g-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    currentRequestId = requestId;
    setStatus(i18n.t("connectingN13"));
    try {
      console.log("[Grabber] selected count:", urls.length);
      console.log("[Grabber] selected URLs:", JSON.stringify(urls));
      const res = await sendMessage({ action: "download_many", urls, label: "grabber", requestId });
      const accepted = Number(res && res.accepted) || 0;
      const total = Number(res && res.total) != null ? Number(res.total) : urls.length;
      const rejected = Number(res && res.rejected) != null ? Number(res.rejected) : Math.max(0, total - accepted);
      console.log("[Grabber] delivery result:", JSON.stringify({ status: res && res.status, accepted, rejected, total, reason: res && res.reason }));

      if (res && res.reason === "unauthorized") {
        showMsg(i18n.t("authFailed"), false);
      } else if (res && (res.reason === "network" || res.reason === "timeout")) {
        showMsg(i18n.t("serverUnavailable") + " — " + i18n.t("n13NotRunning"), false);
      } else if (res && res.status === "sent" && accepted === total && accepted > 0) {
        showMsg(fmtCount("addedToN13", accepted, ""), true);
        setTimeout(() => closeWindow(), 900);
      } else if (res && accepted > 0 && accepted < total) {
        showMsg(fmtCount("grabberPartial", accepted, total, rejected), false);
      } else if (res && accepted === 0 && total > 0) {
        showMsg(fmtCount("allRejected", 0, total, rejected), false);
      } else {
        showMsg(i18n.t("grabberSendFailed") + " — " + i18n.t("failedToSendDetail"), false);
      }
    } finally {
      sending = false;
      currentRequestId = null;
      $("addBtn").disabled = false;
      $("addBtn").textContent = i18n.t("addToQueue");
      clearStatus();
    }
  }

  function setStatus(text) {
    const el = $("msg");
    el.textContent = text;
    el.className = "n13-msg visible";
    clearTimeout(setStatus._t);
  }

  function clearStatus() {
    const el = $("msg");
    clearTimeout(setStatus._t);
    setStatus._t = setTimeout(() => { if (el && !el.classList.contains("ok") && !el.classList.contains("err")) el.classList.remove("visible"); }, 200);
  }

  function onDeliveryProgress(request) {
    if (!request || request.action !== "delivery_progress") return;
    if (currentRequestId && request.requestId !== currentRequestId) return;
    const phase = request.phase;
    if (phase === "connecting") setStatus(i18n.t("connectingN13"));
    else if (phase === "authenticating") setStatus(i18n.t("authenticatingN13"));
    else if (phase === "sending") setStatus(fmtCount("sendingLinks", request.count || "", ""));
    else if (phase === "launching") setStatus(i18n.t("startingN13"));
  }

  function closeWindow() { try { window.close(); } catch (e) { /* ignore */ } }

  function bindEvents() {
    $("selectAllBtn").addEventListener("click", selectAll);
    $("clearBtn").addEventListener("click", clearSelection);
    $("addBtn").addEventListener("click", sendSelected);
    $("closeBtn").addEventListener("click", closeWindow);
    chrome.runtime.onMessage.addListener(onDeliveryProgress);
  }
})();
