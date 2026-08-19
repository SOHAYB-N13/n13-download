/**
 * N13 Download Detector — the SINGLE detection engine for the extension.
 *
 * Used by the floating button, the Link Grabber and the popup so they always
 * agree.  Confidence scoring:
 *
 *   +100  known downloadable file extension (.zip .rar .7z .tar .gz .bz2 .xz
 *         .iso .exe .msi .apk .dmg .pdf .mp4 .mkv .mp3 .jpg ...)
 *   +100  element has a `download` attribute
 *   + 80  download-route path segment (/download, /downloads, /dl, /get,
 *         /file, /files, /media, /uploads, /attachment — incl. download.php)
 *   + 80  download-ish query parameter (?download= ?file= ?dl= ?url= ...)
 *   + 60  media/file `type` attribute (application/audio/video/image MIME)
 *   + 60  download link text (Download / دانلود / دریافت / لینک مستقیم) —
 *         NEVER enough alone; only strengthens other signals.
 *
 * Threshold 80.  A single strong signal (extension, download attribute, route,
 * query) is enough; download text alone is not.
 */
(function (global) {
  var EXT_RE = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|apk|dmg|deb|rpm|pdf|docx?|xlsx?|pptx?|txt|md|mp4|mkv|avi|mov|webm|flv|mp3|flac|wav|ogg|m4a|jpg|jpe?g|png|gif|webp|svg|bmp|ico|bin|img|torrent|dll)([?#]|$)/i;
  var ROUTE_RE = /\/(download|downloads|dl|get|file|files|media|uploads?|attachments?)([./?#]|$)/i;
  var QUERY_RE = /[?&](download|file|dl|url|src|link|attachment|filename)=/i;
  var TEXT_RE = /download|دانلود|دریافت|بارگیری|لینک مستقیم|direct link|get file|save file/i;
  var THRESHOLD = 80;
  var SKIP_PROTO = /^(javascript:|mailto:|tel:|chrome:|chrome-extension:|data:|blob:|about:|file:)/i;

  /** Absolute URL from an anchor, or "" on failure. */
  function absUrl(el, base) {
    if (!el || !el.getAttribute) return "";
    var raw = (el.getAttribute("href") || "").trim();
    if (!raw) return "";
    try { return new URL(raw, base || location.href).href; } catch (e) { return ""; }
  }

  /** Confidence score (0..100+) that a URL/element is a download. */
  function N13_DETECT(url, el) {
    if (!/^https?:\/\//i.test(url || "")) return 0;
    var score = 0;
    if (EXT_RE.test(url)) score += 100;
    if (el && el.hasAttribute && el.hasAttribute("download")) score += 100;
    if (ROUTE_RE.test(url)) score += 80;
    if (QUERY_RE.test(url)) score += 80;
    if (el && el.getAttribute) {
      var type = ((el.getAttribute("type") || "") + "").toLowerCase();
      if (type && (type.indexOf("application/") === 0 ||
                   type.indexOf("audio/") === 0 ||
                   type.indexOf("video/") === 0 ||
                   type.indexOf("image/") === 0)) score += 60;
    }
    if (el && el.textContent) {
      var txt = el.textContent.trim().replace(/\s+/g, " ").slice(0, 120);
      if (TEXT_RE.test(txt)) score += 60;
    }
    return score;
  }

  function N13_IS_DOWNLOAD(url, el) {
    return N13_DETECT(url, el) >= THRESHOLD;
  }

  /** Structured detection result. */
  function N13_DETECT_ITEM(url, el, source) {
    var score = N13_DETECT(url, el);
    return {
      url: url,
      name: N13_NAME(el, url),
      confidence: Math.min(100, score),
      type: "download",
      source: source || "link",
    };
  }

  /** Human-friendly display name (link text → URL filename → URL). */
  function N13_NAME(el, url) {
    if (el && el.textContent) {
      var txt = el.textContent.trim().replace(/\s+/g, " ");
      if (txt && txt.length <= 120 && txt.indexOf("://") === -1 && txt !== url) return txt;
    }
    try {
      var path = new URL(url).pathname;
      var seg = decodeURIComponent((path.split("/").filter(Boolean).pop() || "").trim());
      if (seg && seg.length <= 120) return seg;
    } catch (e) { /* ignore */ }
    return url;
  }

  /** Collect download links inside a container (deduplicated, capped). */
  function collectLinks(container, base, source, max) {
    var seen = Object.create(null);
    var out = [];
    if (!container || typeof container.querySelectorAll !== "function") return out;
    var anchors = container.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length; i++) {
      var a = anchors[i];
      var abs = absUrl(a, base);
      if (!abs || seen[abs] || !N13_IS_DOWNLOAD(abs, a)) continue;
      if (out.length >= max) break;
      seen[abs] = true;
      out.push(N13_DETECT_ITEM(abs, a, source));
    }
    return out;
  }

  /** Whole-page scan (popup "Grab Download Links"). Self-contained for executeScript. */
  function N13_SCAN_PAGE() {
    var MAX = 2000;
    var LIMIT = 50000;
    var base = location.href;
    var seen = Object.create(null);
    var out = [];
    var anchors = document.querySelectorAll("a[href]");
    var scanned = 0;
    for (var i = 0; i < anchors.length; i++) {
      if (++scanned > LIMIT) break;
      var a = anchors[i];
      var raw = (a.getAttribute("href") || "").trim();
      if (!raw || SKIP_PROTO.test(raw)) continue;
      var abs;
      try { abs = new URL(raw, base).href; } catch (e) { continue; }
      if (SKIP_PROTO.test(abs) || seen[abs] || !N13_IS_DOWNLOAD(abs, a)) continue;
      if (out.length >= MAX) break;
      seen[abs] = true;
      out.push(N13_DETECT_ITEM(abs, a, "page"));
    }
    // media/download-source elements (video/audio src, download attr)
    if (out.length < MAX) {
      var dlEls = document.querySelectorAll("[download], video[src], audio[src]");
      for (var j = 0; j < dlEls.length; j++) {
        var el = dlEls[j];
        var src = (el.getAttribute("href") || el.getAttribute("src") || "").trim();
        if (!src || SKIP_PROTO.test(src)) continue;
        var abs2;
        try { abs2 = new URL(src, base).href; } catch (e) { continue; }
        if (SKIP_PROTO.test(abs2) || seen[abs2] || !N13_IS_DOWNLOAD(abs2, el)) continue;
        seen[abs2] = true;
        out.push(N13_DETECT_ITEM(abs2, el, "page"));
      }
    }
    return { urls: out, count: out.length };
  }

  /**
   * Context-aware group scan: from the active link, walk up to the SMALLEST
   * ancestor container holding >= 2 downloadable links and return ONLY those.
   * If no group exists, return just the clicked link. NEVER the whole page.
   */
  function N13_SCAN_GROUP(el) {
    if (!el) return { urls: [], count: 0 };
    var node = el.parentElement;
    var depth = 0;
    while (node && node !== document.body && node !== document.documentElement && depth < 8) {
      var links = collectLinks(node, location.href, "group", 500);
      if (links.length >= 2) return { urls: links, count: links.length };
      node = node.parentElement;
      depth++;
    }
    var abs = absUrl(el, location.href);
    if (abs && /^https?:\/\//i.test(abs)) {
      var single = [N13_DETECT_ITEM(abs, el, "group")];
      return { urls: single, count: 1 };
    }
    return { urls: [], count: 0 };
  }

  /**
   * Selection-aware scan: only anchors inside the user's current text selection
   * (window.getSelection). Returns the links in the selected region.
   *
   * An explicit user selection is a strong signal of intent, so it takes
   * priority over confidence heuristics: every http(s) anchor inside the
   * selection is included (deduplicated), while javascript:/mailto:/etc. links
   * are dropped.  Confidence is still computed for display/ordering, but it is
   * NOT used to exclude a link the user explicitly selected.  The whole page is
   * never scanned for a selection grab.
   */
  function N13_SCAN_SELECTION() {
    var out = [];
    var seen = Object.create(null);
    function push(url, el) {
      if (!url || seen[url]) return;
      if (SKIP_PROTO.test(url) || !/^https?:\/\//i.test(url)) return;
      seen[url] = true;
      out.push(N13_DETECT_ITEM(url, el, "selection"));
    }
    try {
      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return { urls: [], count: 0 };
      var range = sel.getRangeAt(0);
      var frag = range.cloneContents();
      var anchors = frag.querySelectorAll ? frag.querySelectorAll("a[href]") : [];
      for (var i = 0; i < anchors.length; i++) {
        var a = anchors[i];
        var rawHref = (a.getAttribute && a.getAttribute("href")) || "";
        // Same-page fragment links are navigation, not downloads.
        if (!rawHref || rawHref.charAt(0) === "#") continue;
        var abs = absUrl(a, location.href);
        if (abs) push(abs, a);
      }
      // text selected inside a link: include the enclosing anchor
      var common = range.commonAncestorContainer;
      var node = common && common.nodeType === 3 ? common.parentNode : common;
      while (node && node.nodeType === 1) {
        if (node.tagName === "A" && node.getAttribute && node.getAttribute("href")) {
          var abs2 = absUrl(node, location.href);
          if (abs2) push(abs2, node);
          break;
        }
        node = node.parentNode;
      }
    } catch (e) { /* ignore */ }
    return { urls: out, count: out.length };
  }

  global.N13_DETECT = N13_DETECT;
  global.N13_IS_DOWNLOAD = N13_IS_DOWNLOAD;
  global.N13_DETECT_ITEM = N13_DETECT_ITEM;
  global.N13_NAME = N13_NAME;
  global.N13_SCAN_PAGE = N13_SCAN_PAGE;
  global.N13_SCAN_GROUP = N13_SCAN_GROUP;
  global.N13_SCAN_SELECTION = N13_SCAN_SELECTION;
})(typeof globalThis !== "undefined" ? globalThis : this);
