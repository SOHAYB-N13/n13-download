/**
 * N13 MIME Analyzer — MIME type intelligence for download detection.
 *
 * Maps Content-Type values to download categories and provides
 * confidence signals for the scoring engine.
 *
 * Categories:
 *   ARCHIVE, DOCUMENT, MEDIA, EXECUTABLE, EBOOK, CODE, WEB_ASSET, TRACKING, UNKNOWN
 */
(function (global) {
  "use strict";

  /**
   * MIME type → category mapping.
   * Specific types listed first; wildcard prefixes serve as fallback.
   */
  var MIME_CATEGORIES = {
    // Archives
    "application/zip": "ARCHIVE",
    "application/x-zip-compressed": "ARCHIVE",
    "application/x-7z-compressed": "ARCHIVE",
    "application/x-rar-compressed": "ARCHIVE",
    "application/vnd.rar": "ARCHIVE",
    "application/gzip": "ARCHIVE",
    "application/x-gzip": "ARCHIVE",
    "application/x-tar": "ARCHIVE",
    "application/x-bzip2": "ARCHIVE",
    "application/x-xz": "ARCHIVE",
    "application/x-iso9660-image": "ARCHIVE",
    "application/x-apple-diskimage": "ARCHIVE",
    "application/x-debian-package": "ARCHIVE",
    "application/x-rpm": "ARCHIVE",

    // Documents
    "application/pdf": "DOCUMENT",
    "application/msword": "DOCUMENT",
    "application/vnd.ms-word": "DOCUMENT",
    "application/vnd.oasis.opendocument.text": "DOCUMENT",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCUMENT",
    "application/vnd.ms-excel": "DOCUMENT",
    "application/vnd.oasis.opendocument.spreadsheet": "DOCUMENT",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "DOCUMENT",
    "application/vnd.ms-powerpoint": "DOCUMENT",
    "application/vnd.oasis.opendocument.presentation": "DOCUMENT",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "DOCUMENT",
    "application/rtf": "DOCUMENT",
    "text/csv": "DOCUMENT",
    "application/x-ole-storage": "DOCUMENT",

    // Executables
    "application/x-msdownload": "EXECUTABLE",
    "application/x-msdos-program": "EXECUTABLE",
    "application/x-executable": "EXECUTABLE",
    "application/x-mach-binary": "EXECUTABLE",
    "application/x-dmg": "EXECUTABLE",
    "application/vnd.android.package-archive": "EXECUTABLE",
    "application/x-sharedlib": "EXECUTABLE",

    // E-books
    "application/epub+zip": "EBOOK",
    "application/x-mobipocket-ebook": "EBOOK",
    "application/vnd.amazon.mobi8-ebook": "EBOOK",

    // Media — video
    "video/mp4": "MEDIA",
    "video/webm": "MEDIA",
    "video/ogg": "MEDIA",
    "video/x-matroska": "MEDIA",
    "video/x-flv": "MEDIA",
    "video/quicktime": "MEDIA",
    "video/x-msvideo": "MEDIA",
    "video/mpeg": "MEDIA",
    "video/3gpp": "MEDIA",
    "video/3gpp2": "MEDIA",
    "video/MP2T": "MEDIA",
    "application/x-mpegURL": "MEDIA",
    "application/dash+xml": "MEDIA",
    "application/vnd.apple.mpegurl": "MEDIA",
    "application/x-mpegurl": "MEDIA",

    // Media — audio
    "audio/mpeg": "MEDIA",
    "audio/mp3": "MEDIA",
    "audio/ogg": "MEDIA",
    "audio/wav": "MEDIA",
    "audio/webm": "MEDIA",
    "audio/flac": "MEDIA",
    "audio/x-m4a": "MEDIA",
    "audio/mp4": "MEDIA",
    "audio/aac": "MEDIA",
    "audio/x-wav": "MEDIA",
    "audio/x-flac": "MEDIA",

    // Media — images (downloadable, not UI)
    "image/jpeg": "MEDIA",
    "image/png": "MEDIA",
    "image/gif": "MEDIA",
    "image/webp": "MEDIA",
    "image/bmp": "MEDIA",
    "image/tiff": "MEDIA",
    "image/svg+xml": "MEDIA",
    "image/x-icon": "MEDIA",
    "image/avif": "MEDIA",
    "image/heic": "MEDIA",
    "image/heif": "MEDIA",
    "image/vnd.microsoft.icon": "MEDIA",

    // BitTorrent
    "application/x-bittorrent": "ARCHIVE",
    "application/x-torrent": "ARCHIVE",

    // Generic binary (strong download signal)
    "application/octet-stream": "ARCHIVE",
    "binary/octet-stream": "ARCHIVE",

    // Web assets (NOT downloads)
    "text/html": "WEB_ASSET",
    "text/css": "WEB_ASSET",
    "application/javascript": "WEB_ASSET",
    "text/javascript": "WEB_ASSET",
    "application/json": "WEB_ASSET",
    "application/ld+json": "WEB_ASSET",
    "application/xml": "WEB_ASSET",
    "text/xml": "WEB_ASSET",
    "image/svg+xml": "WEB_ASSET",  // SVG is both image and web asset; treated as web asset for scoring

    // Text
    "text/plain": "UNKNOWN",  // ambiguous
  };

  /**
   * MIME prefix fallback rules (checked after exact match).
   * Order matters: first match wins.
   */
  var MIME_PREFIX_RULES = [
    { prefix: "video/", category: "MEDIA" },
    { prefix: "audio/", category: "MEDIA" },
    { prefix: "image/", category: "MEDIA" },
    { prefix: "application/pdf", category: "DOCUMENT" },
    { prefix: "application/msword", category: "DOCUMENT" },
    { prefix: "application/vnd.", category: "DOCUMENT" },
    { prefix: "application/x-7z", category: "ARCHIVE" },
    { prefix: "application/x-rar", category: "ARCHIVE" },
    { prefix: "application/x-zip", category: "ARCHIVE" },
    { prefix: "application/x-tar", category: "ARCHIVE" },
    { prefix: "application/gzip", category: "ARCHIVE" },
    { prefix: "application/zip", category: "ARCHIVE" },
    { prefix: "application/x-msdownload", category: "EXECUTABLE" },
    { prefix: "application/x-dmg", category: "EXECUTABLE" },
    { prefix: "application/octet-stream", category: "ARCHIVE" },
    { prefix: "application/x-bittorrent", category: "ARCHIVE" },
    { prefix: "text/html", category: "WEB_ASSET" },
    { prefix: "text/css", category: "WEB_ASSET" },
    { prefix: "application/javascript", category: "WEB_ASSET" },
    { prefix: "text/javascript", category: "WEB_ASSET" },
    { prefix: "application/json", category: "WEB_ASSET" },
    { prefix: "font/", category: "WEB_ASSET" },
    { prefix: "application/font", category: "WEB_ASSET" },
  ];

  /**
   * Categories that are strong download signals.
   */
  var STRONG_DOWNLOAD_CATEGORIES = { ARCHIVE: true, DOCUMENT: true, EXECUTABLE: true, EBOOK: true };
  var MEDIA_CATEGORY = { MEDIA: true };
  var NEGATIVE_CATEGORIES = { WEB_ASSET: true, TRACKING: true };

  /**
   * Normalize a MIME type string: lowercase, strip parameters, trim.
   * "Application/PDF; charset=utf-8" → "application/pdf"
   */
  function normalizeMime(mime) {
    if (!mime || typeof mime !== "string") return "";
    var m = mime.split(";")[0].trim().toLowerCase();
    return m;
  }

  /**
   * Get the category of a MIME type.
   * @param {string} contentType - Raw Content-Type header value
   * @returns {string} Category: ARCHIVE, DOCUMENT, MEDIA, EXECUTABLE, EBOOK, WEB_ASSET, TRACKING, UNKNOWN
   */
  function getCategory(contentType) {
    var mime = normalizeMime(contentType);
    if (!mime) return "UNKNOWN";

    // Exact match first
    if (MIME_CATEGORIES[mime]) return MIME_CATEGORIES[mime];

    // Prefix fallback
    for (var i = 0; i < MIME_PREFIX_RULES.length; i++) {
      var rule = MIME_PREFIX_RULES[i];
      if (mime.indexOf(rule.prefix) === 0) return rule.category;
    }

    return "UNKNOWN";
  }

  /**
   * Check if a MIME type is a strong download signal.
   */
  function isDownloadable(contentType) {
    var cat = getCategory(contentType);
    return STRONG_DOWNLOAD_CATEGORIES[cat] === true || MEDIA_CATEGORY[cat] === true;
  }

  /**
   * Check if a MIME type is a negative signal (not a download).
   */
  function isNegativeSignal(contentType) {
    var cat = getCategory(contentType);
    return NEGATIVE_CATEGORIES[cat] === true;
  }

  /**
   * Check if a MIME type is a streaming manifest (HLS/DASH).
   */
  function isStreamManifest(contentType) {
    var mime = normalizeMime(contentType);
    return mime === "application/x-mpegURL" ||
           mime === "application/vnd.apple.mpegurl" ||
           mime === "application/x-mpegurl" ||
           mime === "application/dash+xml" ||
           mime === "application/vnd.ms-sstr+xml";
  }

  /**
   * Get a human-readable label for a MIME category.
   */
  function categoryLabel(cat) {
    var labels = {
      ARCHIVE: "Archive",
      DOCUMENT: "Document",
      MEDIA: "Media",
      EXECUTABLE: "Executable",
      EBOOK: "E-book",
      CODE: "Code",
      WEB_ASSET: "Web Asset",
      TRACKING: "Tracking",
      UNKNOWN: "Unknown",
    };
    return labels[cat] || "Unknown";
  }

  /**
   * Get the Content-Type header from a fetch Response-like object.
   * Returns empty string if unavailable.
   */
  function getContentTypeFromResponse(response) {
    if (!response) return "";
    try {
      if (typeof response.headers === "object" && response.headers !== null) {
        if (typeof response.headers.get === "function") {
          return response.headers.get("content-type") || "";
        }
        // Plain object fallback
        var ct = response.headers["content-type"] || response.headers["Content-Type"] || "";
        return ct;
      }
    } catch (e) { /* ignore */ }
    return "";
  }

  // Exports
  global.N13_MIME = {
    normalize: normalizeMime,
    getCategory: getCategory,
    isDownloadable: isDownloadable,
    isNegativeSignal: isNegativeSignal,
    isStreamManifest: isStreamManifest,
    categoryLabel: categoryLabel,
    getContentTypeFromResponse: getContentTypeFromResponse,
    CATEGORIES: {
      ARCHIVE: "ARCHIVE",
      DOCUMENT: "DOCUMENT",
      MEDIA: "MEDIA",
      EXECUTABLE: "EXECUTABLE",
      EBOOK: "EBOOK",
      WEB_ASSET: "WEB_ASSET",
      TRACKING: "TRACKING",
      UNKNOWN: "UNKNOWN",
    },
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
