/**
 * N13 Content Engine — content script.
 *
 * Responsibilities (detection + UI only, NO N13 I/O):
 *   - detect downloadable resources (shared download-detector.js)
 *   - floating "Download with N13" button (own Shadow-DOM host)
 *   - selection tracking (window.getSelection) and hover tracking
 *   - scan-on-activation: selected region → hovered link group → single link
 *   - messaging to the background coordinator
 *
 * The button's host is a fixed, topmost element owned by the extension
 * (module-level reference; never document.getElementById).  The button stays
 * visible while the pointer travels from the link to it, and hides only after
 * the pointer leaves both (grace) or on click / document leave / resize.
 */
(function () {
  "use strict";

  if (window.__n13ContentInjected) return;
  window.__n13ContentInjected = true;

  const CONTAINER_ID = "n13-download-floater";
  const HIDE_GRACE_MS = 350;
  const SOURCE_PAD = 6;
  const BUTTON_PAD = 16;

  let settings = { enabled: true, detection: true, showButton: true, debug: false, language: "en" };
  let i18n = new N13_I18N(settings.language);
  let hostEl = null;
  let currentTarget = null;   // hovered download anchor
  let activeContext = null;   // { mode: "link"|"selection", url, el }
  let hideTimer = null;
  let lastPointer = null;
  let buttonHovered = false;

  function dbg(...args) {
    try { if (window.__n13Debug || settings.debug) console.log("[N13]", ...args); } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ settings
  function loadSettings() {
    chrome.storage.local.get(settings, (items) => {
      settings = { ...settings, ...items };
      i18n = new N13_I18N(settings.language);
      if (currentTarget && (!settings.enabled || !settings.detection || !settings.showButton)) hideButton();
    });
  }
  loadSettings();
  chrome.storage.onChanged.addListener((changes, area) => { if (area === "local") loadSettings(); });

  // ------------------------------------------------------------------ host/button
  function buildButton(shadow) {
    const style = document.createElement("style");
    style.textContent = `
      .n13-btn {
        position: absolute; z-index: 2147483647; display: none;
        align-items: center; gap: 6px; padding: 6px 10px; border-radius: 8px;
        border: 1px solid rgba(0,212,255,.35);
        background: linear-gradient(135deg, rgba(15,52,96,.97), rgba(8,12,30,.97));
        color: #00d4ff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Vazirmatn", Arial, sans-serif;
        font-size: 12px; font-weight: 600; line-height: 1;
        box-shadow: 0 4px 14px rgba(0,0,0,.45); cursor: pointer; user-select: none;
        white-space: nowrap; backdrop-filter: blur(4px); pointer-events: auto;
        transition: opacity 120ms ease, transform 120ms ease;
      }
      .n13-btn:hover { transform: translateY(-1px); opacity: 1 !important; }
      .n13-btn svg { width: 14px; height: 14px; fill: currentColor; flex-shrink: 0; }
      .n13-btn.n13-visible { display: inline-flex; }
    `;
    shadow.appendChild(style);
    const btn = document.createElement("button");
    btn.className = "n13-btn";
    btn.type = "button";
    btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 15l-5-5h3V4h4v6h3l-5 5zM5 18h14v2H5z"/></svg><span class="n13-label"></span>';
    btn.addEventListener("pointerenter", () => { buttonHovered = true; cancelHide(); });
    btn.addEventListener("pointerleave", () => { buttonHovered = false; scheduleHide(); });
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dbg("floating button clicked");
      const ctx = activeContext;
      hideButton();
      onActivate(ctx);
    });
    shadow.appendChild(btn);
  }

  function getHost() {
    if (hostEl && hostEl.isConnected) return hostEl;
    hostEl = document.createElement("div");
    hostEl.id = CONTAINER_ID;
    hostEl.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;overflow:visible;pointer-events:none;z-index:2147483647;";
    document.documentElement.appendChild(hostEl);
    buildButton(hostEl.attachShadow({ mode: "open" }));
    return hostEl;
  }

  function getButton() {
    const host = getHost();
    if (!host || !host.isConnected || !host.shadowRoot) return null;
    return host.shadowRoot.querySelector(".n13-btn");
  }

  function showButton(rect) {
    if (!settings.enabled || !settings.detection || !settings.showButton) return;
    const btn = getButton();
    if (!btn) return;
    const label = btn.querySelector(".n13-label");
    if (label) label.textContent = i18n.t("downloadWithN13");
    btn.classList.add("n13-visible");
    const w = btn.offsetWidth || 190;
    const h = btn.offsetHeight || 32;
    let top = rect.bottom + 8;
    let left = rect.left + 8;
    if (left + w > window.innerWidth - 6) left = Math.max(6, window.innerWidth - w - 6);
    if (top + h > window.innerHeight - 6) top = Math.max(6, rect.top - h - 8);
    if (left < 6) left = 6;
    btn.style.top = top + "px";
    btn.style.left = left + "px";
    btn.style.opacity = "0.92";
    dbg("showButton at", top, left);
  }

  function hideButton() {
    cancelHide();
    const btn = getButton();
    if (btn) { btn.classList.remove("n13-visible"); btn.style.opacity = "0"; }
    currentTarget = null;
    activeContext = null;
    buttonHovered = false;
    lastPointer = null;
  }

  // ------------------------------------------------------------------ hide logic
  function cancelHide() { if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; } }
  function scheduleHide() {
    if (hideTimer || buttonHovered) return;
    hideTimer = setTimeout(() => { hideTimer = null; hideButton(); }, HIDE_GRACE_MS);
  }
  function isPointerInRect(x, y, rect, pad) {
    if (!rect) return false;
    const p = pad || 0;
    return x >= rect.left - p && x <= rect.right + p && y >= rect.top - p && y <= rect.bottom + p;
  }
  function updatePointerState(x, y) {
    if (!currentTarget && !activeContext) return;
    if (buttonHovered) { cancelHide(); return; }
    const src = currentTarget ? currentTarget.getBoundingClientRect() : null;
    if (src && (src.width > 0 || src.height > 0) && isPointerInRect(x, y, src, SOURCE_PAD)) { cancelHide(); return; }
    const btn = getButton();
    if (btn && isPointerInRect(x, y, btn.getBoundingClientRect(), BUTTON_PAD)) { cancelHide(); return; }
    scheduleHide();
  }

  // ------------------------------------------------------------------ activation
  function onActivate(ctx) {
    let scan = { urls: [], count: 0 };
    let primary = "";
    if (ctx && ctx.mode === "selection") {
      scan = N13_SCAN_SELECTION();
      primary = scan.urls.length ? scan.urls[0].url : "";
    } else if (ctx && ctx.el) {
      scan = N13_SCAN_GROUP(ctx.el);
      primary = ctx.url || "";
    } else if (ctx && ctx.url) {
      scan = { urls: [{ url: ctx.url, name: ctx.url, confidence: 100, type: "download", source: "link" }], count: 1 };
      primary = ctx.url;
    }
    dbg("activation scan links=" + scan.urls.length + " primary=" + primary);
    try {
      chrome.runtime.sendMessage({ action: "open_grabber", primaryUrl: primary, urls: scan.urls }, () => {
        if (chrome.runtime.lastError) console.warn("[N13]", chrome.runtime.lastError.message);
      });
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ hover detect
  function looksDownloadable(el) {
    const href = (el && el.getAttribute && el.getAttribute("href")) || "";
    if (!href) return false;
    try {
      const url = new URL(href, location.href);
      return N13_IS_DOWNLOAD(url.href, el);
    } catch (e) { return false; }
  }

  function onLinkHover(e) {
    if (!settings.enabled || !settings.detection || !settings.showButton) return;
    const t = e.target;
    const el = t && t.closest ? t.closest("a[href]") : null;
    if (!el || !looksDownloadable(el)) return;
    if (currentTarget === el) return;
    currentTarget = el;
    activeContext = { mode: "link", url: hrefOf(el), el: el };
    cancelHide();
    showButton(el.getBoundingClientRect());
  }

  function hrefOf(el) {
    const raw = (el && el.getAttribute && el.getAttribute("href")) || "";
    try { return new URL(raw, location.href).href; } catch (e) { return raw; }
  }

  // ------------------------------------------------------------------ selection detect
  function selectionRect() {
    try {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
      const r = sel.getRangeAt(0).getBoundingClientRect();
      if (r && (r.width > 0 || r.height > 0)) return r;
    } catch (e) { /* ignore */ }
    return null;
  }

  let selectionCheckTimer = null;
  function onSelectionChange() {
    if (!settings.enabled || !settings.detection || !settings.showButton) return;
    clearTimeout(selectionCheckTimer);
    selectionCheckTimer = setTimeout(() => {
      try {
        const rect = selectionRect();
        if (!rect) { if (activeContext && activeContext.mode === "selection" && !currentTarget) hideButton(); return; }
        const scan = N13_SCAN_SELECTION();
        if (scan.count > 0) {
          currentTarget = null;
          activeContext = { mode: "selection", url: "", el: null };
          cancelHide();
          showButton(rect);
        }
      } catch (e) { /* ignore */ }
    }, 150);
  }

  // ------------------------------------------------------------------ events
  document.addEventListener("pointerover", (e) => onLinkHover(e), { passive: true });
  document.addEventListener("pointermove", (e) => {
    lastPointer = { x: e.clientX, y: e.clientY };
    updatePointerState(e.clientX, e.clientY);
  }, { passive: true });
  document.addEventListener("pointerleave", () => { if (currentTarget || activeContext) scheduleHide(); }, { passive: true });
  document.addEventListener("pointerout", (e) => { if (!e.relatedTarget && (currentTarget || activeContext)) scheduleHide(); }, { passive: true });
  document.addEventListener("scroll", () => {
    if (currentTarget) showButton(currentTarget.getBoundingClientRect());
    if (lastPointer) updatePointerState(lastPointer.x, lastPointer.y);
  }, { passive: true });
  document.addEventListener("selectionchange", onSelectionChange);
  document.addEventListener("mouseup", () => setTimeout(onSelectionChange, 0));
  window.addEventListener("resize", hideButton);

  // ------------------------------------------------------------------ messages
  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === "scan_page") {
      try { sendResponse(N13_SCAN_PAGE()); } catch (e) { sendResponse({ urls: [], count: 0 }); }
      return false;
    }
    if (request.action === "scan_selection") {
      try { sendResponse(N13_SCAN_SELECTION()); } catch (e) { sendResponse({ urls: [], count: 0 }); }
      return false;
    }
    if (request.action === "ping") {
      sendResponse({ ok: true });
      return false;
    }
  });
})();
