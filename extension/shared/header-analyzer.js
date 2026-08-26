/**
 * N13 Header Analyzer — HTTP header intelligence for download detection.
 *
 * Parses Content-Disposition (RFC 6266 / RFC 5987), Content-Length,
 * Accept-Ranges, Content-Range, and related headers to provide
 * download signals for the scoring engine.
 */
(function (global) {
  "use strict";

  /**
   * Parse a Content-Disposition header value.
   * Handles both RFC 6266 (filename) and RFC 5987 (filename*).
   *
   * @param {string} headerValue - Raw Content-Disposition value
   * @returns {{ disposition: string, filename: string, filenameStar: string, parameters: object }}
   */
  function parseContentDisposition(headerValue) {
    if (!headerValue || typeof headerValue !== "string") {
      return { disposition: "", filename: "", filenameStar: "", parameters: {} };
    }

    var result = { disposition: "", filename: "", filenameStar: "", parameters: {} };
    var parts = headerValue.split(";");
    result.disposition = (parts[0] || "").trim().toLowerCase();

    for (var i = 1; i < parts.length; i++) {
      var part = parts[i].trim();
      var eqIdx = part.indexOf("=");
      if (eqIdx === -1) continue;

      var key = part.substring(0, eqIdx).trim().toLowerCase();
      var value = part.substring(eqIdx + 1).trim();

      // Remove surrounding quotes
      if (value.length >= 2 && value.charAt(0) === '"' && value.charAt(value.length - 1) === '"') {
        value = value.substring(1, value.length - 1);
      }

      if (key === "filename*") {
        // RFC 5987: charset'lang'value
        result.filenameStar = value;
        result.parameters["filename*"] = value;
      } else if (key === "filename") {
        result.filename = value;
        result.parameters["filename"] = value;
      } else {
        result.parameters[key] = value;
      }
    }

    return result;
  }

  /**
   * Decode an RFC 5987 encoded filename* value.
   * Format: charset'language'value
   * Example: UTF-8''%E4%B8%AD%E6%96%87.pdf
   *
   * @param {string} encoded - The filename* value
   * @returns {string} Decoded filename, or "" on failure
   */
  function decodeFilenameStar(encoded) {
    if (!encoded || typeof encoded !== "string") return "";

    var parts = encoded.split("'");
    if (parts.length < 3) return "";

    var charset = (parts[0] || "").toUpperCase();
    // parts[1] is language tag (ignored for our purposes)
    var value = parts.slice(2).join("'");

    try {
      if (charset === "UTF-8" || charset === "UTF8") {
        // percent-encoded UTF-8
        return decodeURIComponent(value);
      } else if (charset === "ISO-8859-1" || charset === "ISO8859-1") {
        // percent-encoded ISO-8859-1 → decode as bytes → string
        var bytes = [];
        for (var i = 0; i < value.length; i++) {
          if (value.charAt(i) === "%" && i + 2 < value.length) {
            bytes.push(parseInt(value.substring(i + 1, i + 3), 16));
            i += 2;
          } else {
            bytes.push(value.charCodeAt(i));
          }
        }
        // ISO-8859-1 maps directly to Unicode code points 0-255
        return String.fromCharCode.apply(null, bytes);
      } else {
        // Unknown charset — try percent-decode as UTF-8
        return decodeURIComponent(value);
      }
    } catch (e) {
      // Fallback: return raw percent-decoded if possible
      try { return decodeURIComponent(value); } catch (e2) { return ""; }
    }
  }

  /**
   * Get the best filename from Content-Disposition header.
   * Priority: filename* (RFC 5987) → filename (RFC 6266)
   *
   * @param {string} headerValue - Raw Content-Disposition value
   * @returns {{ filename: string, isAttachment: boolean, disposition: string }}
   */
  function getFilenameFromDisposition(headerValue) {
    var parsed = parseContentDisposition(headerValue);
    var filename = "";

    // Priority 1: filename* (RFC 5987)
    if (parsed.filenameStar) {
      filename = decodeFilenameStar(parsed.filenameStar);
    }

    // Priority 2: filename (RFC 6266)
    if (!filename && parsed.filename) {
      filename = parsed.filename;
    }

    return {
      filename: filename || "",
      isAttachment: parsed.disposition === "attachment",
      disposition: parsed.disposition,
    };
  }

  /**
   * Extract Accept-Ranges header value.
   * Indicates if the server supports range requests (strong download signal).
   *
   * @param {string} headerValue - Raw Accept-Ranges value
   * @returns {boolean} True if server accepts byte ranges
   */
  function acceptsRanges(headerValue) {
    if (!headerValue || typeof headerValue !== "string") return false;
    return headerValue.trim().toLowerCase() === "bytes";
  }

  /**
   * Parse Content-Range header.
   * Format: bytes 0-1023/4096
   *
   * @param {string} headerValue - Raw Content-Range value
   * @returns {{ unit: string, start: number, end: number, total: number } | null}
   */
  function parseContentRange(headerValue) {
    if (!headerValue || typeof headerValue !== "string") return null;

    var match = headerValue.match(/^(\w+)\s+(\d+)-(\d+)\/(\d+|\*)$/);
    if (!match) return null;

    return {
      unit: match[1],
      start: parseInt(match[2], 10),
      end: parseInt(match[3], 10),
      total: match[4] === "*" ? -1 : parseInt(match[4], 10),
    };
  }

  /**
   * Parse Content-Length header to a number.
   *
   * @param {string} headerValue - Raw Content-Length value
   * @returns {number} Length in bytes, or -1 if unparseable
   */
  function parseContentLength(headerValue) {
    if (!headerValue || typeof headerValue !== "string") return -1;
    var n = parseInt(headerValue.trim(), 10);
    return isNaN(n) ? -1 : n;
  }

  /**
   * Analyze all relevant headers from a response-like object.
   *
   * @param {object} headers - Object with get() method or plain key-value
   * @returns {object} Analyzed header signals
   */
  function analyzeHeaders(headers) {
    var result = {
      contentType: "",
      contentDisposition: null,
      filename: "",
      isAttachment: false,
      contentLength: -1,
      acceptsRanges: false,
      contentRange: null,
      isStreamManifest: false,
    };

    function getHeader(name) {
      if (!headers) return "";
      try {
        if (typeof headers.get === "function") return headers.get(name) || "";
        return headers[name] || headers[name.toLowerCase()] || "";
      } catch (e) { return ""; }
    }

    // Content-Type
    result.contentType = getHeader("content-type");

    // Content-Disposition
    var cd = getHeader("content-disposition");
    if (cd) {
      result.contentDisposition = parseContentDisposition(cd);
      var fnResult = getFilenameFromDisposition(cd);
      result.filename = fnResult.filename;
      result.isAttachment = fnResult.isAttachment;
    }

    // Content-Length
    result.contentLength = parseContentLength(getHeader("content-length"));

    // Accept-Ranges
    result.acceptsRanges = acceptsRanges(getHeader("accept-ranges"));

    // Content-Range
    result.contentRange = parseContentRange(getHeader("content-range"));

    // Stream manifest check
    if (typeof N13_MIME !== "undefined") {
      result.isStreamManifest = N13_MIME.isStreamManifest(result.contentType);
    }

    return result;
  }

  // Exports
  global.N13_HEADERS = {
    parseContentDisposition: parseContentDisposition,
    decodeFilenameStar: decodeFilenameStar,
    getFilenameFromDisposition: getFilenameFromDisposition,
    acceptsRanges: acceptsRanges,
    parseContentRange: parseContentRange,
    parseContentLength: parseContentLength,
    analyzeHeaders: analyzeHeaders,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
