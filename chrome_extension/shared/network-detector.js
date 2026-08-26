/**
 * N13 Network Detector v2 — MV3-compatible network request interception.
 *
 * Architecture:
 *   1. Content script calls N13_NET.init() which uses chrome.scripting to
 *      inject network-interceptor.js into the page's MAIN world.
 *   2. The interceptor monkey-patches fetch/XHR, reads response headers,
 *      and emits CustomEvents with full request+response metadata.
 *   3. The content script listens for these events and feeds them into
 *      the detection engine.
 *
 * What we CAN capture (via MAIN world interceptor):
 *   - Request URL + HTTP method
 *   - Response Content-Type (same-origin or CORS-enabled)
 *   - Response Content-Disposition (same-origin or CORS-enabled)
 *   - Response Content-Length (same-origin or CORS-enabled)
 *   - Response Accept-Ranges header
 *
 * What we CANNOT capture:
 *   - Response headers for opaque or non-CORS responses
 *   - Response body (intentionally — would cause memory issues)
 *   - Request headers (not available from page context)
 *
 * MV3 Compatibility:
 *   - Uses chrome.scripting.executeScript with world: "MAIN"
 *   - Content scripts CAN call chrome.scripting (needs scripting permission)
 *   - Tab ID acquired via chrome.tabs.getCurrent() (works in content scripts)
 *   - CSP does NOT block chrome.scripting injection (browser-level)
 *
 * Race Condition (inherent limitation):
 *   - Content script loads at document_idle
 *   - Page scripts that ran BEFORE content script load are missed
 *   - PerformanceObserver in isolated world catches resource timing for
 *     resources loaded after the observer is created
 *   - The MAIN world interceptor catches fetch/XHR calls made AFTER injection
 */
(function (global) {
  "use strict";

  var EVENT_NAME = "n13-net-request";
  var initialized = false;
  var initInProgress = false;

  /**
   * Check if network detection is available in this context.
   */
  function isAvailable() {
    return typeof chrome !== "undefined" &&
           chrome.scripting &&
           typeof chrome.scripting.executeScript === "function" &&
           typeof chrome.tabs !== "undefined" &&
           typeof chrome.tabs.getCurrent === "function";
  }

  /**
   * Inject the network interceptor into the page's MAIN world.
   * Uses chrome.tabs.getCurrent() to get the tab ID (works in content scripts).
   *
   * @returns {Promise<boolean>} True if injection succeeded
   */
  async function init() {
    if (initialized) return true;
    if (initInProgress) return false;
    if (!isAvailable()) return false;

    initInProgress = true;

    try {
      // Get current tab ID — this works in content scripts
      var tab = await new Promise(function (resolve) {
        chrome.tabs.getCurrent(function (tab) {
          resolve(tab || null);
        });
      });

      if (!tab || !tab.id) {
        initInProgress = false;
        return false;
      }

      // Inject into MAIN world — bypasses CSP (browser-level injection)
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: false },
        world: "MAIN",
        files: ["shared/network-interceptor.js"],
      });

      initialized = true;
      initInProgress = false;
      return true;
    } catch (e) {
      initInProgress = false;
      console.warn("[N13] Network interceptor injection failed:", e.message);
      return false;
    }
  }

  /**
   * Listen for intercepted network requests from the MAIN world interceptor.
   * CustomEvents dispatched on document in MAIN world ARE visible in the
   * content script's isolated world (same DOM node).
   *
   * @param {function} callback - Called with each intercepted request:
   *   { url, method, initiator, contentType, contentDisposition, contentLength, ... }
   */
  function onRequest(callback) {
    document.addEventListener(EVENT_NAME, function (e) {
      try {
        var detail = e.detail;
        if (detail && detail.url) {
          callback(detail);
        }
      } catch (err) { /* ignore */ }
    }, false);
  }

  /**
   * Set up PerformanceObserver-based resource timing detection.
   * Runs in the content script's isolated world.
   *
   * PerformanceObserver in isolated world CAN see page's performance entries
   * (tied to browsing context, not JS world). However:
   *   - buffered: false means only entries completing AFTER observer creation
   *   - Response headers NOT available from PerformanceResourceTiming
   *   - transferSize may be 0 for cross-origin without Timing-Allow-Origin
   *   - Useful for: URL, initiatorType, transferSize, timing data
   *
   * This supplements the MAIN world interceptor, not replaces it.
   *
   * @param {function} callback - Called with PerformanceResourceTiming-like data
   */
  function onPerformanceEntry(callback) {
    if (typeof PerformanceObserver === "undefined") return;

    try {
      var observer = new PerformanceObserver(function (list) {
        var entries = list.getEntries();
        for (var i = 0; i < entries.length; i++) {
          var entry = entries[i];
          if (entry.entryType === "resource") {
            callback({
              url: entry.name,
              initiatorType: entry.initiatorType || "",
              transferSize: entry.transferSize || 0,
              encodedBodySize: entry.encodedBodySize || 0,
              decodedBodySize: entry.decodedBodySize || 0,
              duration: entry.duration || 0,
              startTime: entry.startTime || 0,
              phase: "performance",
            });
          }
        }
      });

      observer.observe({ type: "resource", buffered: false });
    } catch (e) { /* PerformanceObserver not supported */ }
  }

  /**
   * Analyze a PerformanceResourceTiming entry for download signals.
   * Note: This does NOT have access to response headers.
   */
  function analyzePerformanceEntry(entry) {
    if (!entry || !entry.url) return { isDownloadCandidate: false };

    var result = {
      url: entry.url,
      initiatorType: entry.initiatorType || "",
      transferSize: entry.transferSize || 0,
      decodedBodySize: entry.decodedBodySize || 0,
      isDownloadCandidate: false,
      signals: [],
    };

    // Large transfer size suggests downloadable content
    if (entry.transferSize > 100 * 1024) {
      result.signals.push("large_transfer_size");
      result.isDownloadCandidate = true;
    }

    // Media initiator type
    if (entry.initiatorType === "media") {
      result.signals.push("media_initiator");
      result.isDownloadCandidate = true;
    }

    // fetch/xhr with large body
    if ((entry.initiatorType === "fetch" || entry.initiatorType === "xmlhttprequest") &&
        entry.decodedBodySize > 50 * 1024) {
      result.signals.push("fetch_xhr_large_body");
      result.isDownloadCandidate = true;
    }

    return result;
  }

  // Exports
  global.N13_NET = {
    isAvailable: isAvailable,
    init: init,
    onRequest: onRequest,
    onPerformanceEntry: onPerformanceEntry,
    analyzePerformanceEntry: analyzePerformanceEntry,
    EVENT_NAME: EVENT_NAME,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
