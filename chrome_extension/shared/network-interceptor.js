/**
 * N13 Network Interceptor v2 — injected into the page's MAIN world.
 *
 * Monkey-patches fetch() and XMLHttpRequest to:
 *   1. Capture request URL + method
 *   2. Read response headers (Content-Type, Content-Disposition, Content-Length)
 *      WITHOUT consuming the response body
 *   3. Emit CustomEvents with full request+response metadata
 *
 * This runs in the page's JavaScript context (world: "MAIN"), giving it
 * full access to the page's own fetch/XHR responses including headers.
 *
 * Safety:
 *   - Original fetch/XHR behavior is NEVER modified
 *   - Response bodies are NEVER read, cloned, or consumed
 *   - response.headers.get() only reads metadata, not body bytes
 *   - All original Promises/Responses are returned untouched
 */
(function () {
  "use strict";

  if (window.__n13NetInterceptor) return;
  window.__n13NetInterceptor = true;

  var EVENT_NAME = "n13-net-request";

  function emit(detail) {
    try {
      document.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: detail }));
    } catch (e) { /* ignore */ }
  }

  /**
   * Build a request event object (pre-response).
   */
  function buildRequestEvent(url, method, initiator) {
    return {
      url: url,
      method: (method || "GET").toUpperCase(),
      initiator: initiator,
      resourceType: initiator,
      timestamp: Date.now(),
      // Response fields populated later
      contentType: "",
      contentDisposition: "",
      contentLength: 0,
      acceptsRanges: false,
      phase: "request",
    };
  }

  /**
   * Read response headers from a fetch Response object.
   * response.headers.get() does NOT consume the body.
   */
  function readFetchHeaders(response, requestEvent) {
    if (!response || !response.headers) return requestEvent;
    try {
      var ct = response.headers.get("content-type") || "";
      var cd = response.headers.get("content-disposition") || "";
      var cl = response.headers.get("content-length") || "";
      var ar = response.headers.get("accept-ranges") || "";
      var cr = response.headers.get("content-range") || "";

      requestEvent.contentType = ct;
      requestEvent.contentDisposition = cd;
      requestEvent.contentLength = parseInt(cl, 10) || 0;
      requestEvent.acceptsRanges = ar.toLowerCase() === "bytes";
      requestEvent.contentRange = cr;
      requestEvent.phase = "response";
    } catch (e) { /* ignore — CORS or restricted headers */ }
    return requestEvent;
  }

  /**
   * Lightweight URL analysis (page-side, avoids loading full module).
   */
  function quickAnalyzeUrl(url) {
    if (!url || typeof url !== "string") return { isDownloadCandidate: false, signals: [] };
    var result = { isDownloadCandidate: false, signals: [] };

    if (/\.(zip|rar|7z|tar|gz|bz2|xz|iso|exe|msi|apk|dmg|deb|rpm|pdf|docx?|xlsx?|pptx?|epub|mobi|torrent)(\?|#|$)/i.test(url)) {
      result.isDownloadCandidate = true;
      result.signals.push("strong_extension");
    }
    if (/\.(mp4|webm|mkv|avi|mov|flv|mp3|flac|wav|ogg|m4a|aac)(\?|#|$)/i.test(url)) {
      result.isDownloadCandidate = true;
      result.signals.push("media_extension");
    }
    if (/\.(m3u8|mpd|m3u)(\?|#|$)/i.test(url)) {
      result.isDownloadCandidate = true;
      result.signals.push("streaming_manifest");
    }
    if (/\/(download|downloads|dl|get|file|files|attachment|attachments|export|uploads?|media|documents|releases?|packages|dist)\//i.test(url)) {
      result.signals.push("download_path");
    }
    return result;
  }

  /**
   * Check if response MIME indicates a download.
   */
  function isDownloadMime(contentType) {
    if (!contentType) return false;
    var ct = contentType.split(";")[0].trim().toLowerCase();
    // Strong download signals
    if (/^(application\/(zip|x-7z|x-rar|gzip|x-tar|x-bzip2|x-xz|pdf|msword|vnd\.|x-msdownload|x-dmg|octet-stream|x-bittorrent)|audio\/|video\/)/.test(ct)) return true;
    return false;
  }

  // ===========================================================================
  // Monkey-patch fetch()
  // ===========================================================================

  var _originalFetch = window.fetch;

  window.fetch = function () {
    var url = "";
    var method = "GET";

    try {
      if (arguments[0] instanceof Request) {
        url = arguments[0].url;
        method = arguments[0].method || "GET";
      } else if (typeof arguments[0] === "string") {
        url = arguments[0];
        if (arguments[1] && arguments[1].method) method = arguments[1].method;
      } else if (arguments[0] && arguments[0].url) {
        url = arguments[0].url;
        method = arguments[0].method || "GET";
      }
    } catch (e) { /* ignore */ }

    if (!url || !/^https?:\/\//i.test(url)) {
      return _originalFetch.apply(this, arguments);
    }

    // Emit request event immediately
    var requestEvent = buildRequestEvent(url, method, "fetch");
    var urlAnalysis = quickAnalyzeUrl(url);
    requestEvent.isDownloadCandidate = urlAnalysis.isDownloadCandidate;
    requestEvent.signals = urlAnalysis.signals;
    emit(requestEvent);

    // Chain onto the response Promise to read headers
    var originalPromise = _originalFetch.apply(this, arguments);
    return originalPromise.then(function (response) {
      try {
        // Only read headers for non-opaque responses
        if (response.type !== "opaque") {
          var responseEvent = buildRequestEvent(url, method, "fetch");
          readFetchHeaders(response, responseEvent);
          responseEvent.isDownloadCandidate = urlAnalysis.isDownloadCandidate || isDownloadMime(responseEvent.contentType);
          responseEvent.signals = urlAnalysis.signals.slice();
          if (responseEvent.contentDisposition) responseEvent.signals.push("content_disposition");
          if (responseEvent.contentType) responseEvent.signals.push("content_type:" + responseEvent.contentType.split(";")[0].trim());
          emit(responseEvent);
        }
      } catch (e) { /* ignore */ }
      return response; // Return original response untouched
    }).catch(function (err) {
      // Re-throw the original error — never swallow it
      throw err;
    });
  };

  // ===========================================================================
  // Monkey-patch XMLHttpRequest
  // ===========================================================================

  var _OriginalXHR = window.XMLHttpRequest;
  var _originalOpen = _OriginalXHR.prototype.open;
  var _originalSend = _OriginalXHR.prototype.send;

  _OriginalXHR.prototype.open = function (method, url) {
    // Store metadata for the load handler
    this._n13Url = String(url || "");
    this._n13Method = (method || "GET").toUpperCase();

    // Emit request event
    if (/^https?:\/\//i.test(this._n13Url)) {
      var requestEvent = buildRequestEvent(this._n13Url, this._n13Method, "xhr");
      var urlAnalysis = quickAnalyzeUrl(this._n13Url);
      requestEvent.isDownloadCandidate = urlAnalysis.isDownloadCandidate;
      requestEvent.signals = urlAnalysis.signals;
      emit(requestEvent);
    }

    return _originalOpen.apply(this, arguments);
  };

  _OriginalXHR.prototype.send = function () {
    var self = this;

    // Add load listener to read response headers
    if (/^https?:\/\//i.test(this._n13Url)) {
      this.addEventListener("load", function () {
        try {
          var responseEvent = buildRequestEvent(self._n13Url, self._n13Method, "xhr");
          // getResponseHeader works for same-origin and CORS-enabled responses
          var ct = self.getResponseHeader("content-type") || "";
          var cd = self.getResponseHeader("content-disposition") || "";
          var cl = self.getResponseHeader("content-length") || "";
          var ar = self.getResponseHeader("accept-ranges") || "";

          responseEvent.contentType = ct;
          responseEvent.contentDisposition = cd;
          responseEvent.contentLength = parseInt(cl, 10) || 0;
          responseEvent.acceptsRanges = ar.toLowerCase() === "bytes";
          responseEvent.phase = "response";

          var urlAnalysis = quickAnalyzeUrl(self._n13Url);
          responseEvent.isDownloadCandidate = urlAnalysis.isDownloadCandidate || isDownloadMime(ct);
          responseEvent.signals = urlAnalysis.signals.slice();
          if (cd) responseEvent.signals.push("content_disposition");
          if (ct) responseEvent.signals.push("content_type:" + ct.split(";")[0].trim());

          emit(responseEvent);
        } catch (e) { /* ignore — restricted headers */ }
      });
    }

    return _originalSend.apply(this, arguments);
  };
})();
