/**
 * N13 Content Engine v2 — content script with multi-layer detection.
 *
 * Responsibilities (detection + UI only, NO N13 I/O):
 *   - detect downloadable resources (enhanced download-detector.js)
 *   - floating "Download with N13" button (own Shadow-DOM host)
 *   - selection tracking (window.getSelection) and hover tracking
 *   - scan-on-activation: single link → direct send; selection → grabber
 *   - messaging to the background coordinator
 *   - MutationObserver for dynamic DOM changes (via engine)
 *   - Network request interception (MAIN world fetch/XHR patching)
 *   - PerformanceObserver for resource timing
 *   - Click/context menu signal detection
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
      if (typeof N13_ENGINE !== "undefined") {
        N13_ENGINE.updateSettings({
          enabled: settings.enabled,
          detection: settings.detection,
          debug: settings.debug,
        });
      }
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
      .n13-toast {
        position: fixed; right: 16px; bottom: 16px; z-index: 2147483647;
        display: none; align-items: center; gap: 8px;
        max-width: 340px; padding: 10px 14px; border-radius: 10px;
        border: 1px solid rgba(0,212,255,.35);
        background: linear-gradient(135deg, rgba(15,52,96,.97), rgba(8,12,30,.97));
        color: #e8f6ff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Vazirmatn", Arial, sans-serif;
        font-size: 13px; line-height: 1.4;
        box-shadow: 0 6px 20px rgba(0,0,0,.5); pointer-events: none;
      }
      .n13-toast.n13-visible { display: inline-flex; }
      .n13-toast.n13-error { border-color: rgba(255,107,107,.55); color: #ffd9d9; }
      .n13-toast svg { width: 15px; height: 15px; fill: currentColor; flex-shrink: 0; }
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

    const toast = document.createElement("div");
    toast.className = "n13-toast";
    toast.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 15l-5-5h3V4h4v6h3l-5 5zM5 18h14v2H5z"/></svg><span class="n13-toast-msg"></span>';
    shadow.appendChild(toast);
  }

  let toastTimer = null;
  function showToast(message, isError) {
    const host = getHost();
    if (!host || !host.shadowRoot) return;
    const toast = host.shadowRoot.querySelector(".n13-toast");
    if (!toast) return;
    const msg = toast.querySelector(".n13-toast-msg");
    if (msg) msg.textContent = message || "";
    toast.classList.toggle("n13-error", !!isError);
    toast.classList.add("n13-visible");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastTimer = null;
      toast.classList.remove("n13-visible");
    }, 4000);
  }

  function reportSendResult(res, err) {
    const lastErrMsg = err || (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.lastError && chrome.runtime.lastError.message) || "";
    if (/extension context invalidated|receiving end does not exist/i.test(lastErrMsg)) {
      showToast(i18n.t("toastReloadPage"), true);
      return;
    }
    if (res && res.status === "sent") {
      showToast(i18n.t("toastSent"));
      return;
    }
    const reason = res && res.reason;
    if (reason === "unreachable" || reason === "network" || reason === "timeout") {
      showToast(i18n.t("n13NotRunning") + " — " + i18n.t("startN13Manually"), true);
    } else if (reason === "unauthorized") {
      showToast(i18n.t("authFailed"), true);
    } else {
      showToast(i18n.t("failedToSend") + " — " + i18n.t("failedToSendDetail"), true);
    }
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
    if (!ctx) return;

    // MODE 1: Single hovered link → send DIRECTLY to N13 (no grabber)
    if (ctx.mode === "link" && ctx.url) {
      dbg("direct send single link:", ctx.url);
      try {
        chrome.runtime.sendMessage({ action: "download", url: ctx.url }, (res) => {
          if (chrome.runtime.lastError) {
            dbg("direct send failed:", chrome.runtime.lastError.message);
            reportSendResult(null, chrome.runtime.lastError.message);
            return;
          }
          if (res && res.status === "sent") {
            dbg("link sent to N13 successfully");
          } else {
            dbg("link send result:", JSON.stringify(res));
          }
          reportSendResult(res);
        });
      } catch (e) {
        dbg("direct send error:", e);
        reportSendResult(null, String(e && e.message || e));
      }
      return;
    }

    // MODE 2: Text selection → open grabber with selection scan
    if (ctx.mode === "selection") {
      const scan = N13_SCAN_SELECTION();
      const primary = scan.urls.length ? scan.urls[0].url : "";
      dbg("activation selection scan links=" + scan.urls.length);
      try {
        chrome.runtime.sendMessage({ action: "open_grabber", primaryUrl: primary, urls: scan.urls }, () => {
          if (chrome.runtime.lastError) console.warn("[N13]", chrome.runtime.lastError.message);
        });
      } catch (e) { /* ignore */ }
      return;
    }

    // MODE 3: Fallback — send single URL directly
    if (ctx.url) {
      dbg("fallback direct send:", ctx.url);
      try {
        chrome.runtime.sendMessage({ action: "download", url: ctx.url }, (res) => {
          if (chrome.runtime.lastError) dbg("fallback send failed:", chrome.runtime.lastError.message);
          reportSendResult(res, chrome.runtime.lastError && chrome.runtime.lastError.message);
        });
      } catch (e) {
        dbg("fallback send error:", e);
        reportSendResult(null, String(e && e.message || e));
      }
    }
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

  // ------------------------------------------------------------------ detection engine init
  function initDetectionEngine() {
    if (typeof N13_ENGINE === "undefined") return;

    N13_ENGINE.init({
      enabled: settings.enabled,
      detection: settings.detection,
      debug: settings.debug,
    });

    dbg("Detection engine initialized");

    // --- Network interception: MAIN world fetch/XHR patching ---
    // This is the PRIMARY network detection method. It intercepts fetch/XHR
    // in the page's MAIN world, reads response headers, and emits events.
    if (typeof N13_NET !== "undefined" && N13_NET.isAvailable()) {
      N13_NET.init().then(function (ok) {
        if (ok) {
          dbg("Network interceptor injected into MAIN world");
        } else {
          dbg("Network interceptor injection failed — falling back to PerformanceObserver only");
        }
      }).catch(function (e) {
        dbg("Network interceptor init error:", e);
      });

      // Listen for events from the MAIN world interceptor
      // These include both request and response phases with headers
      N13_NET.onRequest(function (request) {
        if (request.phase === "response" && request.contentType) {
          // Response phase: we have Content-Type, Content-Disposition, etc.
          // Feed full context into the engine
          N13_ENGINE.processNetworkRequest({
            url: request.url,
            initiatorType: request.initiator,
            resourceType: request.resourceType || request.initiator,
            transferSize: request.contentLength || 0,
            // NEW: Response headers from MAIN world
            contentType: request.contentType || "",
            contentDisposition: request.contentDisposition || "",
            contentLength: request.contentLength || 0,
            acceptsRanges: request.acceptsRanges || false,
            contentRange: request.contentRange || "",
          });
        } else if (request.phase === "request") {
          // Request phase: only URL + method available
          // Still useful for URL-based detection
          N13_ENGINE.processNetworkRequest({
            url: request.url,
            initiatorType: request.initiator,
            resourceType: request.resourceType || request.initiator,
          });
        }
      });
    }

    // --- PerformanceObserver: supplemental resource timing ---
    // Catches resources loaded via <script>, <link>, <img>, <video>, etc.
    // that don't go through fetch/XHR. Does NOT have response headers.
    if (typeof N13_NET !== "undefined") {
      N13_NET.onPerformanceEntry(function (entry) {
        N13_ENGINE.processNetworkRequest({
          url: entry.url,
          initiatorType: entry.initiatorType,
          transferSize: entry.transferSize,
          decodedBodySize: entry.decodedBodySize,
          phase: "performance",
        });
      });
      dbg("PerformanceObserver active (supplemental)");
    }
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

  // Click signal for detection context
  document.addEventListener("click", function (e) {
    if (!settings.enabled || !settings.detection) return;
    if (typeof N13_ENGINE === "undefined") return;

    const t = e.target;
    const el = t && t.closest ? t.closest("a[href]") : null;
    if (el) {
      const url = hrefOf(el);
      if (url) {
        N13_ENGINE.processClick(e, url);
      }
    }
  }, { passive: true });

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
    if (request.action === "scan_page_advanced") {
      try {
        var advanced = N13_SCAN_PAGE_ADVANCED();
        var engineCandidates = [];
        if (typeof N13_ENGINE !== "undefined") {
          engineCandidates = N13_ENGINE.getCandidates({ minDecision: "candidate" });
        }
        var seen = {};
        var merged = [];
        for (var i = 0; i < advanced.urls.length; i++) {
          var c = advanced.urls[i];
          if (!seen[c.url]) { seen[c.url] = true; merged.push(c); }
        }
        for (var j = 0; j < engineCandidates.length; j++) {
          var ec = engineCandidates[j];
          if (!seen[ec.url]) { seen[ec.url] = true; merged.push(ec); }
        }
        sendResponse({ urls: merged, count: merged.length });
      } catch (e) { sendResponse({ urls: [], count: 0 }); }
      return false;
    }
    if (request.action === "engine_debug") {
      try {
        if (typeof N13_ENGINE !== "undefined") {
          sendResponse(N13_ENGINE.debugInfo());
        } else {
          sendResponse({ error: "engine not available" });
        }
      } catch (e) { sendResponse({ error: e.message }); }
      return false;
    }
    if (request.action === "ping") {
      sendResponse({ ok: true });
      return false;
    }
  });

  // ------------------------------------------------------------------ initialize
  try {
    initDetectionEngine();
  } catch (e) {
    dbg("Detection engine init failed:", e);
  }
})();
