/**
 * N13 Filename Resolver — extracts the best possible filename for a download.
 *
 * Priority chain:
 *   1. Content-Disposition filename* (RFC 5987)
 *   2. Content-Disposition filename (RFC 6266)
 *   3. URL path filename (decoded)
 *   4. HTML metadata (og:title, title, etc.)
 *   5. Generated fallback from URL hash/path
 *
 * All filenames are sanitized: invalid filesystem characters removed,
 * whitespace normalized, length capped, Unicode preserved.
 */
(function (global) {
  "use strict";

  var MAX_FILENAME_LENGTH = 255;
  var MIN_FILENAME_LENGTH = 1;

  /**
   * Characters that are invalid in filenames across OS.
   * Includes Windows reserved: \ / : * ? " < > |
   * Plus control characters and null bytes.
   */
  var INVALID_CHARS_RE = /[\\/:*?"<>|\x00-\x1f]/g;

  /**
   * Characters that are problematic but can be replaced with underscore.
   */
  var UNSAFE_CHARS_RE = /[\s]/g;

  /**
   * Sanitize a filename: remove invalid chars, normalize whitespace,
   * trim, and enforce length limits.
   *
   * @param {string} name - Raw filename
   * @returns {string} Sanitized filename
   */
  function sanitize(name) {
    if (!name || typeof name !== "string") return "";

    var result = name
      .replace(INVALID_CHARS_RE, "")     // Remove invalid chars
      .replace(UNSAFE_CHARS_RE, "_")     // Whitespace → underscore
      .replace(/_+/g, "_")               // Collapse multiple underscores
      .replace(/^[._\s]+/, "")           // Remove leading dots/underscores/spaces
      .replace(/[._\s]+$/, "");          // Remove trailing dots/underscores/spaces

    // Enforce length
    if (result.length > MAX_FILENAME_LENGTH) {
      // Preserve extension
      var lastDot = result.lastIndexOf(".");
      if (lastDot > 0) {
        var ext = result.substring(lastDot);
        var base = result.substring(0, lastDot);
        result = base.substring(0, MAX_FILENAME_LENGTH - ext.length) + ext;
      } else {
        result = result.substring(0, MAX_FILENAME_LENGTH);
      }
    }

    return result;
  }

  /**
   * Extract filename from a URL path.
   * Handles encoded characters, query strings, and fragments.
   *
   * @param {string} url - Full URL
   * @returns {string} Decoded filename from URL, or ""
   */
  function filenameFromUrl(url) {
    if (!url || typeof url !== "string") return "";

    try {
      var parsed = new URL(url);
      var pathname = parsed.pathname;

      // Get the last path segment
      var segments = pathname.split("/").filter(Boolean);
      if (segments.length === 0) return "";

      var lastSegment = segments[segments.length - 1];

      // Decode percent-encoding
      try {
        lastSegment = decodeURIComponent(lastSegment);
      } catch (e) { /* keep as-is */ }

      // Remove extension for display purposes? No — keep full filename
      return lastSegment || "";
    } catch (e) {
      // URL parsing failed — try regex extraction
      var match = url.match(/\/([^/?#]+)(?:[?#]|$)/);
      if (match && match[1]) {
        try { return decodeURIComponent(match[1]); } catch (e2) { return match[1]; }
      }
      return "";
    }
  }

  /**
   * Generate a filename from URL components when no filename is available.
   * Uses path segments, query parameters, or a timestamp-based fallback.
   *
   * @param {string} url - Full URL
   * @returns {string} Generated filename
   */
  function generateFromUrl(url) {
    if (!url || typeof url !== "string") return "download";

    try {
      var parsed = new URL(url);
      var segments = parsed.pathname.split("/").filter(Boolean);

      // Try second-to-last meaningful segment
      if (segments.length >= 2) {
        var candidate = segments[segments.length - 2] || segments[segments.length - 1];
        try { candidate = decodeURIComponent(candidate); } catch (e) { /* keep */ }
        if (candidate && candidate.length >= 2 && candidate.length <= 80) {
          return sanitize(candidate);
        }
      }

      // Try domain name
      var host = parsed.hostname.replace(/^www\./, "");
      if (host) return sanitize(host + "_download");

      return "download";
    } catch (e) {
      return "download";
    }
  }

  /**
   * Resolve the best filename from all available sources.
   *
   * @param {object} context - {
   *   url: string,
   *   dispositionFilename: string (from Content-Disposition),
   *   htmlTitle: string (from page meta/title),
   *   elementText: string (from anchor text),
   *   mime: string (MIME type, used to suggest extension if missing)
   * }
   * @returns {string} Best available filename, sanitized
   */
  function resolveFilename(context) {
    if (!context || typeof context !== "object") return "download";

    var name = "";

    // Priority 1: Content-Disposition filename (already decoded by header-analyzer)
    if (context.dispositionFilename) {
      name = sanitize(context.dispositionFilename);
      if (name) return name;
    }

    // Priority 2: URL filename
    if (context.url) {
      name = sanitize(filenameFromUrl(context.url));
      if (name) return name;
    }

    // Priority 3: HTML metadata
    if (context.htmlTitle) {
      name = sanitize(context.htmlTitle);
      if (name) return name;
    }

    // Priority 4: Element text
    if (context.elementText) {
      var txt = context.elementText.trim().replace(/\s+/g, " ");
      if (txt && txt.length <= 120 && txt.indexOf("://") === -1) {
        name = sanitize(txt);
        if (name) return name;
      }
    }

    // Priority 5: Generated fallback
    if (context.url) {
      name = generateFromUrl(context.url);
      if (name) return name;
    }

    return "download";
  }

  /**
   * Ensure a filename has a file extension.
   * If the filename lacks an extension but we know the MIME type,
   * append a common extension for that type.
   *
   * @param {string} filename - Current filename
   * @param {string} contentType - MIME type string
   * @returns {string} Filename with extension (or original if already has one)
   */
  function ensureExtension(filename, contentType) {
    if (!filename || typeof filename !== "string") return filename || "";

    // Check if filename already has an extension
    var lastDot = filename.lastIndexOf(".");
    if (lastDot > 0 && lastDot < filename.length - 1) {
      var ext = filename.substring(lastDot + 1).toLowerCase();
      // Valid extension: 1-10 alphanumeric chars
      if (/^[a-z0-9]{1,10}$/.test(ext)) return filename;
    }

    // MIME → extension mapping (common types)
    var mimeToExt = {
      "application/pdf": ".pdf",
      "application/zip": ".zip",
      "application/x-7z-compressed": ".7z",
      "application/x-rar-compressed": ".rar",
      "application/vnd.rar": ".rar",
      "application/gzip": ".gz",
      "application/x-tar": ".tar",
      "application/x-bzip2": ".bz2",
      "application/x-xz": ".xz",
      "application/msword": ".doc",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
      "application/vnd.ms-excel": ".xls",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
      "application/vnd.ms-powerpoint": ".ppt",
      "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
      "application/epub+zip": ".epub",
      "application/x-mobipocket-ebook": ".mobi",
      "application/x-msdownload": ".exe",
      "application/vnd.android.package-archive": ".apk",
      "application/x-bittorrent": ".torrent",
      "application/octet-stream": ".bin",
      "video/mp4": ".mp4",
      "video/webm": ".webm",
      "video/x-matroska": ".mkv",
      "video/quicktime": ".mov",
      "video/x-msvideo": ".avi",
      "audio/mpeg": ".mp3",
      "audio/ogg": ".ogg",
      "audio/wav": ".wav",
      "audio/flac": ".flac",
      "audio/x-m4a": ".m4a",
      "image/jpeg": ".jpg",
      "image/png": ".png",
      "image/gif": ".gif",
      "image/webp": ".webp",
      "image/bmp": ".bmp",
      "image/tiff": ".tiff",
      "image/avif": ".avif",
      "image/heic": ".heic",
      "text/csv": ".csv",
    };

    if (contentType && typeof N13_MIME !== "undefined") {
      var mime = N13_MIME.normalize(contentType);
      if (mimeToExt[mime]) {
        return filename + mimeToExt[mime];
      }
    }

    return filename;
  }

  // Exports
  global.N13_FILENAME = {
    sanitize: sanitize,
    fromUrl: filenameFromUrl,
    generateFromUrl: generateFromUrl,
    resolve: resolveFilename,
    ensureExtension: ensureExtension,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
