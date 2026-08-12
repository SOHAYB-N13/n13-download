/**
 * N13 Chrome Extension — content script.
 *
 * Adds an unobtrusive floating "Download with N13" button when the user hovers
 * over a link that points to a downloadable resource. All UI lives inside a
 * single container to avoid polluting the host page.
 */
(function () {
  "use strict";

  // Avoid double-injection.
  if (window.__n13ContentInjected) return;
  window.__n13ContentInjected = true;

  const FILE_EXT_RE = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|apk|dmg|deb|rpm|pdf|docx?|xlsx?|pptx?|txt|md|mp4|mkv|avi|mov|webm|flv|mp3|flac|wav|ogg|m4a|jpg|jpe?g|png|gif|webp|svg|ico|torrent)(\?|#|$)/i;
  const CONTAINER_ID = "n13-download-floater";

  let settings = {
    enabled: true,
    detection: true,
    showButton: true,
    language: "en",
  };
  let i18n = new N13_I18N(settings.language);
  let currentTarget = null;
  let hostEl = null;

  // -------------------------------------------------------------------------
  // Settings sync
  // -------------------------------------------------------------------------

  function loadSettings() {
    chrome.storage.local.get(settings, (items) => {
      settings = { ...settings, ...items };
      i18n = new N13_I18N(settings.language);
    });
  }

  loadSettings();
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local") loadSettings();
  });

  // -------------------------------------------------------------------------
  // UI container (closed shadow root to isolate styles)
  // -------------------------------------------------------------------------

  function buildButton(shadow) {
    const style = document.createElement("style");
    style.textContent = `
      .n13-btn {
        position: absolute;
        display: none;
        align-items: center;
        gap: 6px;
        padding: 6px 10px;
        border-radius: 8px;
        border: 1px solid rgba(0, 212, 255, 0.35);
        background: linear-gradient(135deg, rgba(15, 52, 96, 0.96), rgba(8, 12, 30, 0.96));
        color: #00d4ff;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Vazirmatn", Arial, sans-serif;
        font-size: 12px;
        font-weight: 600;
        line-height: 1;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
        cursor: pointer;
        user-select: none;
        white-space: nowrap;
        backdrop-filter: blur(4px);
        transition: opacity 120ms ease, transform 120ms ease;
      }
      .n13-btn:hover { transform: translateY(-1px); opacity: 1 !important; }
      .n13-btn svg { width: 14px; height: 14px; fill: currentColor; flex-shrink: 0; }
      .n13-btn.n13-visible { display: inline-flex; }
    `;
    shadow.appendChild(style);

    const btn = document.createElement("button");
    btn.className = "n13-btn";
    btn.innerHTML = `<svg viewBox="0 0 24 24"><path d="M12 15l-5-5h3V4h4v6h3l-5 5zM5 18h14v2H5z"/></svg><span class="n13-label"></span>`;
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (currentTarget) {
        const url = currentTarget.href;
        if (url && /^https?:\/\//i.test(url)) {
          sendUrl(url);
        }
        hideButton();
      }
    });
    shadow.appendChild(btn);
  }

  // Keep a module-level reference to OUR host element. We must NOT locate it
  // with document.getElementById(): a page may own an element with the same id
  // (id collision) or replace our node during re-render, which would give us a
  // node with no shadow root and crash at .shadowRoot.querySelector.
  function getHost() {
    if (hostEl && hostEl.isConnected) return hostEl;
    hostEl = document.createElement("div");
    hostEl.id = CONTAINER_ID;
    hostEl.style.cssText = "position:fixed;z-index:2147483646;top:0;left:0;width:0;height:0;overflow:visible;";
    document.documentElement.appendChild(hostEl);
    buildButton(hostEl.attachShadow({ mode: "open" }));
    return hostEl;
  }

  function getButton() {
    const host = getHost();
    if (!host || !host.shadowRoot) return null;
    return host.shadowRoot.querySelector(".n13-btn");
  }

  function updateButtonText() {
    const btn = getButton();
    if (!btn) return;
    const label = btn.querySelector(".n13-label");
    if (label) label.textContent = i18n.t("downloadWithN13");
  }

  function showButton(rect) {
    if (!settings.enabled || !settings.detection || !settings.showButton) return;
    const btn = getButton();
    if (!btn) return;
    updateButtonText();

    const offset = 8;
    let top = rect.bottom + window.scrollY + offset;
    let left = rect.left + window.scrollX + offset;
    const maxLeft = window.innerWidth - 180;
    if (left > maxLeft) left = maxLeft;

    btn.style.top = `${top}px`;
    btn.style.left = `${left}px`;
    btn.style.opacity = "0.92";
    btn.classList.add("n13-visible");
  }

  function hideButton() {
    const btn = getButton();
    if (btn) {
      btn.classList.remove("n13-visible");
      btn.style.opacity = "0";
    }
    currentTarget = null;
  }

  // -------------------------------------------------------------------------
  // Detection
  // -------------------------------------------------------------------------

  function looksDownloadable(el) {
    const href = (el.getAttribute("href") || "").trim();
    if (!href) return false;
    try {
      const url = new URL(href, location.href);
      if (url.protocol !== "http:" && url.protocol !== "https:") return false;
      if (FILE_EXT_RE.test(url.pathname) || FILE_EXT_RE.test(url.href)) return true;
      // Heuristic: links with download attribute
      if (el.hasAttribute("download")) return true;
    } catch (_) {}
    return false;
  }

  function sendUrl(url) {
    chrome.runtime.sendMessage({ action: "download", url }, (response) => {
      if (chrome.runtime.lastError) return;
      if (response?.status !== "sent") {
        console.warn("[N13] failed to send", url);
      }
    });
  }

  // -------------------------------------------------------------------------
  // Event listeners
  // -------------------------------------------------------------------------

  document.addEventListener(
    "mouseover",
    (e) => {
      if (!settings.enabled || !settings.detection || !settings.showButton) return;
      const el = e.target.closest ? e.target.closest("a[href]") : null;
      if (!el || !looksDownloadable(el)) {
        if (currentTarget && currentTarget !== el) hideButton();
        return;
      }
      if (currentTarget === el) return;
      currentTarget = el;
      const rect = el.getBoundingClientRect();
      showButton(rect);
    },
    { passive: true }
  );

  document.addEventListener(
    "mouseout",
    (e) => {
      const related = e.relatedTarget;
      const host = document.getElementById(CONTAINER_ID);
      if (host && host.contains(related)) return;
      if (currentTarget && !currentTarget.contains(related)) {
        hideButton();
      }
    },
    { passive: true }
  );

  document.addEventListener(
    "scroll",
    () => {
      if (currentTarget) {
        const rect = currentTarget.getBoundingClientRect();
        showButton(rect);
      }
    },
    { passive: true }
  );

  window.addEventListener("resize", hideButton);

  // Listen for background messages (e.g. language change refresh).
  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === "ping") {
      sendResponse({ ok: true });
      return false;
    }
  });
})();
