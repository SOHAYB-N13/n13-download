/**
 * N13 Download Detector v2 — Enhanced Detection Engine.
 *
 * The SINGLE detection engine for the extension, maintaining full backward
 * compatibility with the existing API while adding multi-layer detection:
 *
 *   Layer 1: DOM Detection — enhanced <a>, <video>, <audio>, <source>, <object>, <embed>, <iframe>, <img>
 *   Layer 2: Dynamic DOM Detection — MutationObserver with debounce/dedup
 *   Layer 3: Event Detection — click/auxclick/contextmenu signals
 *   Layer 4: Network-Aware Detection — fetch/XHR interception + PerformanceObserver
 *   Layer 5: URL Intelligence — advanced pattern analysis, false positive filtering
 *   Layer 6: MIME/Content-Type Analysis — downloadability from MIME type
 *   Layer 7: Header Analysis — Content-Disposition, Accept-Ranges
 *   Layer 8: Confidence Scoring — weighted scoring with positive/negative signals
 *   Layer 9: Deduplication — cross-layer dedup with signed URL support
 *
 * Backward-compatible API preserved:
 *   N13_DETECT(url, el)          → number (score)
 *   N13_IS_DOWNLOAD(url, el)     → boolean
 *   N13_DETECT_ITEM(url, el, src) → {url, name, confidence, type, source}
 *   N13_NAME(el, url)            → string
 *   N13_SCAN_PAGE()              → {urls, count}
 *   N13_SCAN_GROUP(el)           → {urls, count}
 *   N13_SCAN_SELECTION()         → {urls, count}
 *
 * New API:
 *   N13_DETECT_ADVANCED(url, el, context) → full scored candidate
 *   N13_SCAN_PAGE_ADVANCED()              → enhanced scan with scoring
 *   N13_ENGINE.init()                     → initialize all detection layers
 *   N13_ENGINE.addCandidate(candidate)    → process a candidate from any layer
 *   N13_ENGINE.getCandidates()            → get deduplicated candidate list
 *   N13_ENGINE.reset()                    → clear all candidates
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------------------
  // Backward-compatible regex patterns (kept for legacy N13_DETECT)
  // ---------------------------------------------------------------------------

  var EXT_RE = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|apk|dmg|deb|rpm|pdf|docx?|xlsx?|pptx?|txt|md|mp4|mkv|avi|mov|webm|flv|mp3|flac|wav|ogg|m4a|jpg|jpe?g|png|gif|webp|svg|bmp|ico|bin|img|torrent|dll)([?#]|$)/i;
  var ROUTE_RE = /(\/(download|downloads|dl|get|file|files|media|uploads?|attachments?)([./?#]|$))/i;
  var QUERY_RE = /[?&](download|file|dl|url|src|link|attachment|filename)=/i;
  var TEXT_RE = /download|دانلود|دریافت|بارگیری|لینک مستقیم|direct link|get file|save file/i;
  var THRESHOLD = 80;
  var SKIP_PROTO = /^(javascript:|mailto:|tel:|chrome:|chrome-extension:|data:|blob:|about:|file:)/i;

  // ---------------------------------------------------------------------------
  // Backward-compatible API (unchanged behavior)
  // ---------------------------------------------------------------------------

  /** Absolute URL from an anchor, or "" on failure. */
  function absUrl(el, base) {
    if (!el || !el.getAttribute) return "";
    var raw = (el.getAttribute("href") || el.getAttribute("src") || "").trim();
    if (!raw) return "";
    try { return new URL(raw, base || location.href).href; } catch (e) { return ""; }
  }

  /**
   * Confidence score (0..100+) — backward-compatible with original.
   * Uses legacy pattern matching only.
   */
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

  /** Structured detection result — backward-compatible. */
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

  /** Human-friendly display name — backward-compatible. */
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

  // ---------------------------------------------------------------------------
  // Enhanced DOM element analysis (new)
  // ---------------------------------------------------------------------------

  /**
   * Analyze a DOM element for download signals.
   * Returns a rich analysis object for the scoring engine.
   */
  function analyzeElement(el) {
    if (!el || typeof el !== "object") return {};

    var result = {
      hasDownloadAttribute: false,
      isMediaElement: false,
      isObjectElement: false,
      isIframeElement: false,
      isImageElement: false,
      typeAttribute: "",
      downloadTextMatch: false,
      hasDownloadSibling: false,
      elementSize: null,
      tag: "",
      src: "",
      data: "",
    };

    try {
      var tag = el.tagName ? el.tagName.toUpperCase() : "";
      result.tag = tag;

      // Download attribute
      if (typeof el.hasAttribute === "function") {
        result.hasDownloadAttribute = el.hasAttribute("download");
      }

      // Tag-based classification
      if (tag === "VIDEO" || tag === "AUDIO") {
        result.isMediaElement = true;
      }
      if (tag === "OBJECT" || tag === "EMBED") {
        result.isObjectElement = true;
      }
      if (tag === "IFRAME") {
        result.isIframeElement = true;
      }
      if (tag === "IMG") {
        result.isImageElement = true;
      }

      // Type attribute
      if (typeof el.getAttribute === "function") {
        result.typeAttribute = el.getAttribute("type") || "";
        result.src = el.getAttribute("src") || "";
        result.data = el.getAttribute("data") || "";
      }

      // Download-related text
      if (el.textContent) {
        var txt = el.textContent.trim().replace(/\s+/g, " ").slice(0, 120);
        if (TEXT_RE.test(txt)) {
          result.downloadTextMatch = true;
        }
      }

      // Element size (for tracking pixel detection)
      if (typeof el.getBoundingClientRect === "function") {
        try {
          var rect = el.getBoundingClientRect();
          if (rect && typeof rect.width === "number") {
            result.elementSize = { width: rect.width, height: rect.height };
          }
        } catch (e) { /* ignore */ }
      }

      // Sibling context: check if nearby elements have download attributes
      if (typeof el.parentElement === "object" && el.parentElement) {
        try {
          var siblings = el.parentElement.querySelectorAll("[download], a[href]");
          if (siblings.length > 0) {
            result.hasDownloadSibling = true;
          }
        } catch (e) { /* ignore */ }
      }
    } catch (e) { /* ignore */ }

    return result;
  }

  // ---------------------------------------------------------------------------
  // Enhanced scan functions (backward-compatible return format, richer data)
  // ---------------------------------------------------------------------------

  /** Collect download links inside a container (backward-compatible). */
  function collectLinks(container, base, source, max) {
    var seen = Object.create(null);
    var out = [];
    if (!container || typeof container.querySelectorAll !== "function") return out;

    // Scan <a href> elements
    var anchors = container.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length; i++) {
      var a = anchors[i];
      var href = (a.getAttribute("href") || "").trim();
      if (!href || SKIP_PROTO.test(href)) continue;
      var abs;
      try { abs = new URL(href, base).href; } catch (e) { continue; }
      if (SKIP_PROTO.test(abs) || seen[abs] || !N13_IS_DOWNLOAD(abs, a)) continue;
      if (out.length >= max) break;
      seen[abs] = true;
      out.push(N13_DETECT_ITEM(abs, a, source));
    }
    return out;
  }

  /**
   * Whole-page scan — backward-compatible + enhanced.
   * Now scans: <a href>, <video>, <audio>, <source>, <object>, <embed>
   */
  function N13_SCAN_PAGE() {
    var MAX = 2000;
    var LIMIT = 50000;
    var base = location.href;
    var seen = Object.create(null);
    var out = [];
    var scanned = 0;

    // --- Layer 1a: <a href> elements ---
    var anchors = document.querySelectorAll("a[href]");
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

    // --- Layer 1b: Media elements (video, audio, source) ---
    if (out.length < MAX) {
      var mediaSelectors = [
        "video[src]", "audio[src]",
        "video source[src]", "audio source[src]",
        "source[src]",
      ];
      for (var s = 0; s < mediaSelectors.length; s++) {
        if (out.length >= MAX) break;
        var mediaEls = document.querySelectorAll(mediaSelectors[s]);
        for (var j = 0; j < mediaEls.length; j++) {
          if (++scanned > LIMIT) break;
          var mel = mediaEls[j];
          var msrc = (mel.getAttribute("src") || "").trim();
          if (!msrc || SKIP_PROTO.test(msrc)) continue;
          var mabs;
          try { mabs = new URL(msrc, base).href; } catch (e) { continue; }
          if (SKIP_PROTO.test(mabs) || seen[mabs]) continue;

          // Media elements need stronger signal — use enhanced detection
          var mediaScore = enhancedMediaScore(mabs, mel);
          if (mediaScore < THRESHOLD) continue;

          if (out.length >= MAX) break;
          seen[mabs] = true;
          out.push({
            url: mabs,
            name: N13_NAME(mel, mabs),
            confidence: Math.min(100, mediaScore),
            type: "media",
            source: "media",
          });
        }
      }
    }

    // --- Layer 1c: Download attribute elements (already covered by <a> above, but include non-anchor) ---
    if (out.length < MAX) {
      var dlEls = document.querySelectorAll("[download]");
      for (var k = 0; k < dlEls.length; k++) {
        if (++scanned > LIMIT) break;
        var dlEl = dlEls[k];
        var dlTag = dlEl.tagName ? dlEl.tagName.toUpperCase() : "";
        if (dlTag === "A") continue; // already scanned
        var dlSrc = (dlEl.getAttribute("href") || dlEl.getAttribute("src") || "").trim();
        if (!dlSrc || SKIP_PROTO.test(dlSrc)) continue;
        var dlAbs;
        try { dlAbs = new URL(dlSrc, base).href; } catch (e) { continue; }
        if (SKIP_PROTO.test(dlAbs) || seen[dlAbs] || !N13_IS_DOWNLOAD(dlAbs, dlEl)) continue;
        if (out.length >= MAX) break;
        seen[dlAbs] = true;
        out.push(N13_DETECT_ITEM(dlAbs, dlEl, "page"));
      }
    }

    // --- Layer 1d: Object/embed elements with data/src pointing to downloadable content ---
    if (out.length < MAX) {
      var objectEls = document.querySelectorAll("object[data], embed[src]");
      for (var m = 0; m < objectEls.length; m++) {
        if (++scanned > LIMIT) break;
        var oEl = objectEls[m];
        var oSrc = (oEl.getAttribute("data") || oEl.getAttribute("src") || "").trim();
        if (!oSrc || SKIP_PROTO.test(oSrc)) continue;
        var oAbs;
        try { oAbs = new URL(oSrc, base).href; } catch (e) { continue; }
        if (SKIP_PROTO.test(oAbs) || seen[oAbs] || !N13_IS_DOWNLOAD(oAbs, oEl)) continue;
        if (out.length >= MAX) break;
        seen[oAbs] = true;
        out.push(N13_DETECT_ITEM(oAbs, oEl, "page"));
      }
    }

    return { urls: out, count: out.length };
  }

  /**
   * Enhanced media element scoring.
   * Media elements need more context than simple extension matching.
   */
  function enhancedMediaScore(url, el) {
    var score = 0;

    // URL extension check
    if (EXT_RE.test(url)) score += 100;

    // Type attribute check
    if (el && el.getAttribute) {
      var type = ((el.getAttribute("type") || "") + "").toLowerCase();
      if (type.indexOf("video/") === 0 || type.indexOf("audio/") === 0) score += 80;
      if (type.indexOf("application/") === 0) score += 60;
    }

    // Download attribute
    if (el && el.hasAttribute && el.hasAttribute("download")) score += 100;

    // Route check
    if (ROUTE_RE.test(url)) score += 80;

    return score;
  }

  /**
   * Context-aware group scan — backward-compatible.
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
   * Selection-aware scan — backward-compatible.
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
        if (!rawHref || rawHref.charAt(0) === "#") continue;
        var abs = absUrl(a, location.href);
        if (abs) push(abs, a);
      }
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

  // ---------------------------------------------------------------------------
  // Advanced detection API (new)
  // ---------------------------------------------------------------------------

  /**
   * Full advanced detection with all analysis layers.
   * Returns a complete scored candidate.
   *
   * @param {string} url - The URL to analyze
   * @param {object} el - DOM element (optional)
   * @param {object} context - Additional context: { headers, mimeType, resourceType, source, finalUrl }
   * @returns {object} Full candidate with score, signals, and metadata
   */
  function N13_DETECT_ADVANCED(url, el, context) {
    context = context || {};

    // Build analysis context
    var analysisContext = {
      urlAnalysis: typeof N13_URL !== "undefined" ? N13_URL.analyzeUrl(url) : {},
      domAnalysis: analyzeElement(el),
      mimeAnalysis: context.mimeType && typeof N13_MIME !== "undefined"
        ? { isDownloadable: N13_MIME.isDownloadable(context.mimeType),
            isNegativeSignal: N13_MIME.isNegativeSignal(context.mimeType),
            isStreamManifest: N13_MIME.isStreamManifest(context.mimeType),
            category: N13_MIME.getCategory(context.mimeType) }
        : null,
      headerAnalysis: (function () {
        if (!context.headers) return null;
        // If headers is already an analyzed object (from processNetworkRequest),
        // use it directly. Otherwise, parse it with N13_HEADERS.analyzeHeaders.
        if (typeof context.headers.isAttachment === "boolean") return context.headers;
        if (typeof N13_HEADERS !== "undefined") return N13_HEADERS.analyzeHeaders(context.headers);
        return null;
      })(),
      networkAnalysis: context.resourceType
        ? { resourceType: context.resourceType }
        : null,
      source: context.source || "link",
    };

    // Compute score
    var scoring = typeof N13_SCORING !== "undefined"
      ? N13_SCORING.computeScore(analysisContext)
      : { score: N13_DETECT(url, el) / 100, decision: "unknown", signals: [] };

    // Resolve filename
    var filename = url;
    if (typeof N13_FILENAME !== "undefined") {
      var fnContext = { url: url };
      if (context.headers && typeof N13_HEADERS !== "undefined") {
        var headerResult = N13_HEADERS.analyzeHeaders(context.headers);
        fnContext.dispositionFilename = headerResult.filename;
      }
      if (el) {
        fnContext.elementText = el.textContent || "";
      }
      filename = N13_FILENAME.resolve(fnContext);
      if (context.mimeType) {
        filename = N13_FILENAME.ensureExtension(filename, context.mimeType);
      }
    }

    // Determine type
    var type = "download";
    if (analysisContext.domAnalysis && analysisContext.domAnalysis.isMediaElement) type = "media";
    if (analysisContext.urlAnalysis && analysisContext.urlAnalysis.isStreamingUrl) type = "streaming";

    return {
      url: url,
      finalUrl: context.finalUrl || url,
      name: N13_NAME(el, url),
      filename: filename,
      confidence: Math.round(scoring.score * 100),
      score: scoring.score,
      decision: scoring.decision,
      type: type,
      source: context.source || "link",
      signals: scoring.signals,
      positiveSignals: scoring.positiveSignals,
      negativeSignals: scoring.negativeSignals,
      mimeType: context.mimeType || "",
      contentDisposition: context.headers && typeof N13_HEADERS !== "undefined"
        ? N13_HEADERS.analyzeHeaders(context.headers).contentDisposition
        : null,
    };
  }

  /**
   * Enhanced page scan with full scoring.
   * Returns candidates with advanced scoring metadata.
   */
  function N13_SCAN_PAGE_ADVANCED() {
    var result = N13_SCAN_PAGE();
    var enhanced = [];

    for (var i = 0; i < result.urls.length; i++) {
      var item = result.urls[i];
      var advanced = N13_DETECT_ADVANCED(item.url, null, { source: item.source });
      enhanced.push(advanced);
    }

    return { urls: enhanced, count: enhanced.length };
  }

  // ---------------------------------------------------------------------------
  // Detection Engine (new)
  // ---------------------------------------------------------------------------

  /**
   * The Detection Engine manages the full detection pipeline.
   * It coordinates DOM scanning, MutationObserver, network interception,
   * deduplication, and candidate management.
   */
  var engine = {
    _candidates: [],
    _dedupCache: null,
    _initialized: false,
    _observer: null,
    _debounceTimer: null,
    _settings: { enabled: true, detection: true, debug: false },

    /**
     * Initialize the detection engine.
     */
    init: function (settings) {
      if (this._initialized) return;
      this._initialized = true;
      this._settings = settings || this._settings;

      // Create deduplication cache
      if (typeof N13_DEDUP !== "undefined") {
        this._dedupCache = N13_DEDUP.createCache({ ttlMs: 30 * 60 * 1000, maxSize: 5000 });
      }

      // Start MutationObserver
      this._startObserver();
    },

    /**
     * Start MutationObserver for dynamic DOM changes.
     * Uses debounce to prevent excessive processing.
     */
    _startObserver: function () {
      var self = this;
      if (typeof MutationObserver === "undefined") return;
      if (!this._settings.detection) return;

      this._observer = new MutationObserver(function (mutations) {
        if (!self._settings.enabled || !self._settings.detection) return;

        // Debounce: batch mutations and process after 500ms of inactivity
        if (self._debounceTimer) clearTimeout(self._debounceTimer);
        self._debounceTimer = setTimeout(function () {
          self._processMutations(mutations);
        }, 500);
      });

      try {
        this._observer.observe(document.documentElement, {
          childList: true,
          subtree: true,
          attributes: true,
          attributeFilter: ["href", "src", "download", "type", "data"],
        });
      } catch (e) {
        // Observer setup failed — continue without dynamic detection
      }
    },

    /**
     * Process DOM mutations for new download candidates.
     */
    _processMutations: function (mutations) {
      var newElements = [];
      var modifiedUrls = [];

      for (var i = 0; i < mutations.length; i++) {
        var mutation = mutations[i];

        if (mutation.type === "childList") {
          // New nodes added
          for (var j = 0; j < mutation.addedNodes.length; j++) {
            var node = mutation.addedNodes[j];
            if (node.nodeType !== 1) continue; // skip text nodes
            var tag = node.tagName ? node.tagName.toUpperCase() : "";

            // Check if the node itself is a download element
            if (this._isDownloadElement(node)) {
              newElements.push(node);
            }

            // Check children
            if (typeof node.querySelectorAll === "function") {
              var downloadEls = node.querySelectorAll(
                "a[href], [download], video[src], audio[src], source[src], object[data], embed[src]"
              );
              for (var k = 0; k < downloadEls.length; k++) {
                newElements.push(downloadEls[k]);
              }
            }
          }
        } else if (mutation.type === "attributes") {
          // Attribute changed — check if the element is now a download candidate
          var target = mutation.target;
          if (target && target.nodeType === 1 && this._isDownloadElement(target)) {
            var url = this._getElementUrl(target);
            if (url) modifiedUrls.push({ url: url, el: target });
          }
        }
      }

      // Process new elements
      for (var n = 0; n < newElements.length; n++) {
        var el = newElements[n];
        var elUrl = this._getElementUrl(el);
        if (!elUrl) continue;

        var candidate = N13_DETECT_ADVANCED(elUrl, el, { source: "mutation" });
        if (candidate.decision === "download" || candidate.decision === "candidate") {
          this.addCandidate(candidate);
        }
      }

      // Process modified URLs
      for (var m = 0; m < modifiedUrls.length; m++) {
        var modCandidate = N13_DETECT_ADVANCED(modifiedUrls[m].url, modifiedUrls[m].el, { source: "mutation" });
        if (modCandidate.decision === "download" || modCandidate.decision === "candidate") {
          this.addCandidate(modCandidate);
        }
      }
    },

    /**
     * Check if a DOM element is a potential download source.
     */
    _isDownloadElement: function (el) {
      if (!el || !el.tagName) return false;
      var tag = el.tagName.toUpperCase();
      return tag === "A" || tag === "VIDEO" || tag === "AUDIO" ||
             tag === "SOURCE" || tag === "OBJECT" || tag === "EMBED" ||
             (typeof el.hasAttribute === "function" && el.hasAttribute("download"));
    },

    /**
     * Get the URL from a DOM element.
     */
    _getElementUrl: function (el) {
      if (!el || !el.getAttribute) return "";
      var url = el.getAttribute("href") || el.getAttribute("src") || el.getAttribute("data") || "";
      if (!url) return "";
      try { return new URL(url, location.href).href; } catch (e) { return ""; }
    },

    /**
     * Add a candidate to the engine's candidate list.
     * Handles deduplication automatically.
     *
     * @param {object} candidate - Full candidate object
     * @returns {{ added: boolean, isDuplicate: boolean }}
     */
    addCandidate: function (candidate) {
      if (!candidate || !candidate.url) return { added: false, isDuplicate: false };
      if (!this._settings.enabled || !this._settings.detection) return { added: false, isDuplicate: false };

      // Deduplication check
      if (this._dedupCache) {
        var dedupResult = this._dedupCache.check(candidate);
        if (dedupResult.isDuplicate) {
          // Update existing candidate if new one has higher score
          if (candidate.score > (dedupResult.existingCandidate.score || 0)) {
            var idx = this._candidates.indexOf(dedupResult.existingCandidate);
            if (idx >= 0) this._candidates[idx] = candidate;
          }
          return { added: false, isDuplicate: true };
        }
      }

      this._candidates.push(candidate);
      return { added: true, isDuplicate: false };
    },

    /**
     * Process a network request as a potential candidate.
     * Called by the content script when network events arrive.
     *
     * @param {object} request - { url, initiatorType, transferSize,
     *   contentType, contentDisposition, contentLength, acceptsRanges, ... }
     *   When contentType/contentDisposition are present (from MAIN world
     *   interceptor response phase), detection is significantly more accurate.
     */
    processNetworkRequest: function (request) {
      if (!request || !request.url) return;
      if (!this._settings.enabled || !this._settings.detection) return;

      var urlAnalysis = typeof N13_URL !== "undefined" ? N13_URL.analyzeUrl(request.url) : {};

      // Quick filter: skip obvious non-downloads by URL pattern
      if (urlAnalysis.isFalsePositive) return;
      if (urlAnalysis.isCdnAsset) return;

      // Build MIME analysis if we have Content-Type from response headers
      var mimeAnalysis = null;
      if (request.contentType && typeof N13_MIME !== "undefined") {
        mimeAnalysis = {
          isDownloadable: N13_MIME.isDownloadable(request.contentType),
          isNegativeSignal: N13_MIME.isNegativeSignal(request.contentType),
          isStreamManifest: N13_MIME.isStreamManifest(request.contentType),
          category: N13_MIME.getCategory(request.contentType),
        };
        // If MIME says negative (HTML/JSON/CSS/JS), skip even if URL looks download-ish
        if (mimeAnalysis.isNegativeSignal && !urlAnalysis.isStrongExtension) return;
      }

      // Build header analysis if we have response headers
      var headerAnalysis = null;
      if (request.contentDisposition || request.acceptsRanges || request.contentLength > 0) {
        headerAnalysis = {
          isAttachment: /attachment/i.test(request.contentDisposition || ""),
          filename: "",
          contentDisposition: request.contentDisposition || "",
          contentType: request.contentType || "",
          contentLength: request.contentLength || -1,
          acceptsRanges: !!request.acceptsRanges,
          contentRange: request.contentRange || null,
          isStreamManifest: mimeAnalysis ? mimeAnalysis.isStreamManifest : false,
        };
        // Extract filename from Content-Disposition
        if (request.contentDisposition && typeof N13_HEADERS !== "undefined") {
          var fnResult = N13_HEADERS.getFilenameFromDisposition(request.contentDisposition);
          headerAnalysis.filename = fnResult.filename;
          headerAnalysis.isAttachment = fnResult.isAttachment;
        }
      }

      // Determine if this is a download candidate
      var hasSignal = urlAnalysis.isStrongExtension || urlAnalysis.isMediaExtension ||
                      urlAnalysis.isStreamingManifest || urlAnalysis.hasDownloadPath ||
                      urlAnalysis.hasDownloadQueryParam || urlAnalysis.isStreamingUrl;

      // MIME-based signals
      if (mimeAnalysis && mimeAnalysis.isDownloadable) hasSignal = true;

      // Header-based signals
      if (headerAnalysis && headerAnalysis.isAttachment) hasSignal = true;

      // PerformanceObserver signals (no headers available)
      if (request.transferSize > 500 * 1024) hasSignal = true;
      if (request.initiatorType === "media") hasSignal = true;

      // Web asset extension without any positive signal: skip
      if (urlAnalysis.isWebAsset && !hasSignal) return;

      if (!hasSignal) return;

      // Build the full context for advanced scoring
      var candidate = N13_DETECT_ADVANCED(request.url, null, {
        source: "network",
        resourceType: request.initiatorType || request.resourceType || "",
        mimeType: request.contentType || "",
        headers: headerAnalysis || undefined,
      });

      // Use lower threshold for network-detected candidates with response headers
      // (headers provide strong confirmation of download intent)
      var threshold = headerAnalysis && headerAnalysis.isAttachment ? "weak" : "candidate";
      if (candidate.decision === "download" || candidate.decision === "candidate" ||
          (candidate.decision === "weak" && threshold === "weak")) {
        this.addCandidate(candidate);
      }
    },

    /**
     * Process a click event as a potential download signal.
     */
    processClick: function (event, url) {
      if (!url || !this._settings.enabled || !this._settings.detection) return;

      var candidate = N13_DETECT_ADVANCED(url, event && event.target, {
        source: "click",
      });

      if (candidate.decision === "download" || candidate.decision === "candidate") {
        this.addCandidate(candidate);
      }
    },

    /**
     * Get all deduplicated candidates.
     * @param {object} options - { minDecision: "weak"|"candidate"|"download", sortBy: "score"|"url" }
     */
    getCandidates: function (options) {
      options = options || {};
      var minDecision = options.minDecision || "weak";
      var sortBy = options.sortBy || "score";

      var decisionOrder = { ignore: 0, weak: 1, candidate: 2, download: 3 };
      var minLevel = decisionOrder[minDecision] || 0;

      var filtered = this._candidates.filter(function (c) {
        return (decisionOrder[c.decision] || 0) >= minLevel;
      });

      if (sortBy === "score") {
        filtered.sort(function (a, b) { return (b.score || 0) - (a.score || 0); });
      } else if (sortBy === "url") {
        filtered.sort(function (a, b) { return (a.url || "").localeCompare(b.url || ""); });
      }

      return filtered;
    },

    /**
     * Get candidates in the legacy format (backward-compatible).
     */
    getLegacyCandidates: function () {
      return this.getCandidates({ minDecision: "candidate", sortBy: "score" }).map(function (c) {
        return {
          url: c.url,
          name: c.name,
          confidence: c.confidence,
          type: c.type,
          source: c.source,
        };
      });
    },

    /**
     * Clear all candidates and reset state.
     */
    reset: function () {
      this._candidates = [];
      if (this._dedupCache) this._dedupCache.clear();
    },

    /**
     * Stop the MutationObserver and clean up.
     */
    destroy: function () {
      if (this._observer) {
        this._observer.disconnect();
        this._observer = null;
      }
      if (this._debounceTimer) {
        clearTimeout(this._debounceTimer);
        this._debounceTimer = null;
      }
      this.reset();
      this._initialized = false;
    },

    /**
     * Update settings.
     */
    updateSettings: function (settings) {
      if (settings) {
        this._settings = { ...this._settings, ...settings };
      }
      if (!this._settings.detection && this._observer) {
        this._observer.disconnect();
        this._observer = null;
      } else if (this._settings.detection && !this._observer && this._initialized) {
        this._startObserver();
      }
    },

    /**
     * Get debug info for the current state.
     */
    debugInfo: function () {
      return {
        initialized: this._initialized,
        candidates: this._candidates.length,
        settings: { ...this._settings },
        dedupCache: this._dedupCache ? this._dedupCache.stats() : null,
        observerActive: !!this._observer,
      };
    },
  };

  // ---------------------------------------------------------------------------
  // Exports (backward-compatible + new)
  // ---------------------------------------------------------------------------

  // Backward-compatible globals
  global.N13_DETECT = N13_DETECT;
  global.N13_IS_DOWNLOAD = N13_IS_DOWNLOAD;
  global.N13_DETECT_ITEM = N13_DETECT_ITEM;
  global.N13_NAME = N13_NAME;
  global.N13_SCAN_PAGE = N13_SCAN_PAGE;
  global.N13_SCAN_GROUP = N13_SCAN_GROUP;
  global.N13_SCAN_SELECTION = N13_SCAN_SELECTION;

  // New globals
  global.N13_DETECT_ADVANCED = N13_DETECT_ADVANCED;
  global.N13_SCAN_PAGE_ADVANCED = N13_SCAN_PAGE_ADVANCED;
  global.N13_ANALYZE_ELEMENT = analyzeElement;
  global.N13_ENGINE = engine;
})(typeof globalThis !== "undefined" ? globalThis : this);
