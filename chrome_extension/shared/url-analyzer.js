/**
 * N13 URL Analyzer — advanced URL intelligence for download detection.
 *
 * Goes beyond simple file extension matching to analyze:
 * - Known downloadable file extensions (with categories)
 * - Download-related URL paths and segments
 * - Download-related query parameters
 * - CDN / signed URL patterns
 * - Streaming manifest patterns (HLS/DASH)
 * - URLs without extensions (download path patterns)
 * - False positive filtering (analytics, tracking, assets)
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------------------
  // File extension classification
  // ---------------------------------------------------------------------------

  /**
   * Strong downloadable file extensions.
   * These are almost certainly downloads.
   */
  var STRONG_EXTENSIONS = {
    // Archives
    "zip": "ARCHIVE", "rar": "ARCHIVE", "7z": "ARCHIVE", "tar": "ARCHIVE",
    "gz": "ARCHIVE", "bz2": "ARCHIVE", "xz": "ARCHIVE", "lz": "ARCHIVE",
    "lzma": "ARCHIVE", "zst": "ARCHIVE",
    // Installers / executables
    "exe": "EXECUTABLE", "msi": "EXECUTABLE", "dmg": "EXECUTABLE",
    "apk": "EXECUTABLE", "deb": "EXECUTABLE", "rpm": "EXECUTABLE",
    "pkg": "EXECUTABLE", "app": "EXECUTABLE", "bin": "EXECUTABLE",
    "dll": "EXECUTABLE", "so": "EXECUTABLE", "dylib": "EXECUTABLE",
    // Disk images
    "iso": "ARCHIVE", "img": "ARCHIVE", "vmdk": "ARCHIVE", "vhd": "ARCHIVE",
    // Documents
    "pdf": "DOCUMENT", "doc": "DOCUMENT", "docx": "DOCUMENT",
    "xls": "DOCUMENT", "xlsx": "DOCUMENT", "ppt": "DOCUMENT", "pptx": "DOCUMENT",
    "odt": "DOCUMENT", "ods": "DOCUMENT", "odp": "DOCUMENT",
    "rtf": "DOCUMENT", "csv": "DOCUMENT",
    "epub": "EBOOK", "mobi": "EBOOK", "azw": "EBOOK", "azw3": "EBOOK",
    // Torrents
    "torrent": "ARCHIVE",
    // Text / data
    "txt": "DOCUMENT", "md": "DOCUMENT", "json": "DOCUMENT",
    "xml": "DOCUMENT", "yaml": "DOCUMENT", "yml": "DOCUMENT",
    "log": "DOCUMENT", "ini": "DOCUMENT", "cfg": "DOCUMENT",
  };

  /**
   * Media file extensions (downloadable but common in web pages).
   * Require additional context signals to avoid false positives.
   */
  var MEDIA_EXTENSIONS = {
    // Video
    "mp4": "VIDEO", "webm": "VIDEO", "mkv": "VIDEO", "avi": "VIDEO",
    "mov": "VIDEO", "flv": "VIDEO", "wmv": "VIDEO", "m4v": "VIDEO",
    "mpg": "VIDEO", "mpeg": "VIDEO", "3gp": "VIDEO", "ts": "VIDEO",
    // Audio
    "mp3": "AUDIO", "flac": "AUDIO", "wav": "AUDIO", "ogg": "AUDIO",
    "m4a": "AUDIO", "aac": "AUDIO", "wma": "AUDIO", "opus": "AUDIO",
    "aiff": "AUDIO",
    // Images (common in web, but some are downloads)
    "jpg": "IMAGE", "jpeg": "IMAGE", "png": "IMAGE", "gif": "IMAGE",
    "webp": "IMAGE", "svg": "IMAGE", "bmp": "IMAGE", "ico": "IMAGE",
    "tiff": "IMAGE", "tif": "IMAGE", "avif": "IMAGE",
    "heic": "IMAGE", "heif": "IMAGE", "raw": "IMAGE", "cr2": "IMAGE",
    "nef": "IMAGE", "psd": "IMAGE", "ai": "IMAGE",
  };

  /**
   * Streaming manifest extensions.
   */
  var STREAMING_EXTENSIONS = {
    "m3u8": "HLS",
    "mpd": "DASH",
    "m3u": "HLS",
  };

  /**
   * Extensions that are web assets (NOT downloads).
   */
  var WEB_ASSET_EXTENSIONS = {
    "html": true, "htm": true, "css": true, "js": true, "mjs": true,
    "jsx": true, "ts": true, "tsx": true, "vue": true, "svelte": true,
    "php": true, "asp": true, "aspx": true, "jsp": true, "cgi": true,
    "wasm": true, "map": true,
  };

  /**
   * Font extensions (NOT downloads unless explicitly triggered).
   */
  var FONT_EXTENSIONS = {
    "ttf": true, "otf": true, "woff": true, "woff2": true, "eot": true,
  };

  // ---------------------------------------------------------------------------
  // Download path patterns
  // ---------------------------------------------------------------------------

  /**
   * URL path segments that strongly indicate a download route.
   */
  var DOWNLOAD_PATH_SEGMENTS = [
    "download", "downloads", "dl", "dls",
    "get", "fetch", "retrieve",
    "file", "files",
    "attachment", "attachments",
    "export", "exports",
    "media", "mediafiles",
    "uploads", "upload",
    "documents", "docs",
    "archive", "archives",
    "backup", "backups",
    "assets", "static",
    "cdn", "content",
    "releases", "release",
    "packages", "dist",
  ];

  /**
   * Path segment regex pattern.
   */
  var PATH_SEGMENT_RE = new RegExp(
    "\\/(" + DOWNLOAD_PATH_SEGMENTS.join("|") + ")([./?#]|$)", "i"
  );

  /**
   * Download-related query parameters.
   */
  var DOWNLOAD_QUERY_PARAMS = [
    "download", "dl", "file", "url", "src", "link",
    "attachment", "filename", "name", "path", "token",
    "export", "get",
  ];

  var QUERY_PARAM_RE = new RegExp(
    "[?&](" + DOWNLOAD_QUERY_PARAMS.join("|") + ")=", "i"
  );

  // ---------------------------------------------------------------------------
  // False positive patterns
  // ---------------------------------------------------------------------------

  /**
   * URL patterns that should NOT be treated as downloads.
   */
  var FALSE_POSITIVE_PATTERNS = [
    // Analytics / tracking
    /google-analytics\.com/i,
    /googletagmanager\.com/i,
    /googleadservices\.com/i,
    /doubleclick\.net/i,
    /facebook\.com\/tr/i,
    /facebook\.net\/.*\/fbevents/i,
    /hotjar\.com/i,
    /mixpanel\.com/i,
    /amplitude\.com/i,
    /segment\.io/i,
    /segment\.com/i,
    /fullstory\.com/i,
    /sentry\.io/i,
    /bugsnag\.com/i,
    /newrelic\.com/i,
    /chartbeat\.com/i,
    /optimizely\.com/i,
    /clicky\.com/i,
    /matomo\./i,
    /piwik\./i,
    // Ad networks
    /adservice\.google/i,
    /pagead2/i,
    /ad.doubleclick/i,
    /advertising\.com/i,
    /adsrvr\.org/i,
    /demdex\.net/i,
    /amazon-adsystem/i,
    // Social widgets
    /connect\.facebook/i,
    /platform\.twitter/i,
    /widgets\.platform/i,
    // Fonts (CDN delivered)
    /fonts\.googleapis\.com/i,
    /fonts\.gstatic\.com/i,
    // CDN assets (JS/CSS bundles)
    /\.min\.js$/i,
    /\.min\.css$/i,
    /\.bundle\.js$/i,
    /\.chunk\.js$/i,
  ];

  /**
   * Known CDN URL patterns that are likely serving assets, not user downloads.
   */
  var CDN_ASSET_PATTERNS = [
    /cloudfront\.net\/.*\.(js|css|woff2?)$/i,
    /cloudflare\.com\/cdn-cgi\//i,
    /akamai\.net\/.*\.(js|css)$/i,
    /fastly\.net\/.*\.(js|css)$/i,
    /jsdelivr\.net\/.*\.(js|css|woff2?)$/i,
    /unpkg\.com\/.*\.(js|css|woff2?)$/i,
    /cdnjs\..*\/.*\.(js|css|woff2?)$/i,
  ];

  // ---------------------------------------------------------------------------
  // URL extension mapping
  // ---------------------------------------------------------------------------

  /**
   * Regex to extract file extension from URL (before query/fragment).
   */
  var EXT_EXTRACT_RE = /\.([a-z0-9]{1,10})(?:[?#]|$)/i;

  /**
   * Get the file extension from a URL path.
   * @param {string} url - Full URL
   * @returns {string} Lowercase extension without dot, or ""
   */
  function getExtension(url) {
    if (!url || typeof url !== "string") return "";
    var match = url.match(EXT_EXTRACT_RE);
    return match ? match[1].toLowerCase() : "";
  }

  /**
   * Classify a URL extension.
   * @param {string} ext - Lowercase extension without dot
   * @returns {{ category: string, isDownloadable: boolean, isMedia: boolean, isStreaming: boolean, isWebAsset: boolean }}
   */
  function classifyExtension(ext) {
    if (!ext) return { category: "NONE", isDownloadable: false, isMedia: false, isStreaming: false, isWebAsset: false };

    if (STRONG_EXTENSIONS[ext]) {
      return { category: STRONG_EXTENSIONS[ext], isDownloadable: true, isMedia: false, isStreaming: false, isWebAsset: false };
    }
    if (MEDIA_EXTENSIONS[ext]) {
      return { category: MEDIA_EXTENSIONS[ext], isDownloadable: false, isMedia: true, isStreaming: false, isWebAsset: false };
    }
    if (STREAMING_EXTENSIONS[ext]) {
      return { category: STREAMING_EXTENSIONS[ext], isDownloadable: false, isMedia: false, isStreaming: true, isWebAsset: false };
    }
    if (WEB_ASSET_EXTENSIONS[ext]) {
      return { category: "WEB_ASSET", isDownloadable: false, isMedia: false, isStreaming: false, isWebAsset: true };
    }
    if (FONT_EXTENSIONS[ext]) {
      return { category: "FONT", isDownloadable: false, isMedia: false, isStreaming: false, isWebAsset: true };
    }

    return { category: "UNKNOWN", isDownloadable: false, isMedia: false, isStreaming: false, isWebAsset: false };
  }

  /**
   * Check if a URL has a download-related path segment.
   */
  function hasDownloadPath(url) {
    if (!url || typeof url !== "string") return false;
    try {
      var pathname = new URL(url).pathname;
      return PATH_SEGMENT_RE.test(pathname);
    } catch (e) {
      return PATH_SEGMENT_RE.test(url);
    }
  }

  /**
   * Check if a URL has a download-related query parameter.
   */
  function hasDownloadQueryParam(url) {
    if (!url || typeof url !== "string") return false;
    return QUERY_PARAM_RE.test(url);
  }

  /**
   * Check if a URL matches known false positive patterns.
   */
  function isFalsePositive(url) {
    if (!url || typeof url !== "string") return false;
    for (var i = 0; i < FALSE_POSITIVE_PATTERNS.length; i++) {
      if (FALSE_POSITIVE_PATTERNS[i].test(url)) return true;
    }
    return false;
  }

  /**
   * Check if a URL is a CDN asset (JS/CSS/font bundle).
   */
  function isCdnAsset(url) {
    if (!url || typeof url !== "string") return false;
    for (var i = 0; i < CDN_ASSET_PATTERNS.length; i++) {
      if (CDN_ASSET_PATTERNS[i].test(url)) return true;
    }
    return false;
  }

  /**
   * Check if a URL is a signed/temporary URL (common in CDNs).
   * Signed URLs typically have query params like: sig, signature, token, expires, expiry, policy, key, Key-Pair-Id
   */
  function isSignedUrl(url) {
    if (!url || typeof url !== "string") return false;
    try {
      var params = new URL(url).searchParams;
      var signParams = ["sig", "signature", "token", "expires", "expiry", "policy", "key", "Key-Pair-Id", "X-Amz-Signature", "X-Amz-Credential"];
      for (var i = 0; i < signParams.length; i++) {
        if (params.has(signParams[i])) return true;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  /**
   * Check if URL matches blob: protocol.
   */
  function isBlobUrl(url) {
    return url && typeof url === "string" && url.indexOf("blob:") === 0;
  }

  /**
   * Check if URL is a streaming manifest (HLS/DASH).
   */
  function isStreamingUrl(url) {
    if (!url || typeof url !== "string") return false;
    var ext = getExtension(url);
    if (STREAMING_EXTENSIONS[ext]) return true;
    // Check for common manifest URL patterns without extension
    try {
      var pathname = new URL(url).pathname;
      if (/\.m3u8(\?|$)/i.test(pathname)) return true;
      if (/\.mpd(\?|$)/i.test(pathname)) return true;
    } catch (e) { /* ignore */ }
    return false;
  }

  /**
   * Full URL analysis — returns all signals for the scoring engine.
   *
   * @param {string} url - Full URL to analyze
   * @returns {object} URL analysis results
   */
  function analyzeUrl(url) {
    if (!url || typeof url !== "string") {
      return { valid: false };
    }

    var result = {
      valid: false,
      url: url,
      extension: "",
      extCategory: "NONE",
      isStrongExtension: false,
      isMediaExtension: false,
      isStreamingManifest: false,
      isWebAsset: false,
      hasDownloadPath: false,
      hasDownloadQueryParam: false,
      isFalsePositive: false,
      isCdnAsset: false,
      isSignedUrl: false,
      isBlobUrl: false,
      isStreamingUrl: false,
    };

    // Validate URL
    try {
      var parsed = new URL(url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return result;
      result.valid = true;
    } catch (e) {
      return result;
    }

    // Extension analysis
    var ext = getExtension(url);
    result.extension = ext;
    var extInfo = classifyExtension(ext);
    result.extCategory = extInfo.category;
    result.isStrongExtension = extInfo.isDownloadable;
    result.isMediaExtension = extInfo.isMedia;
    result.isStreamingManifest = extInfo.isStreaming;
    result.isWebAsset = extInfo.isWebAsset;

    // Path analysis
    result.hasDownloadPath = hasDownloadPath(url);
    result.hasDownloadQueryParam = hasDownloadQueryParam(url);

    // False positive / negative checks
    result.isFalsePositive = isFalsePositive(url);
    result.isCdnAsset = isCdnAsset(url);

    // Special URL types
    result.isSignedUrl = isSignedUrl(url);
    result.isBlobUrl = isBlobUrl(url);
    result.isStreamingUrl = isStreamingUrl(url);

    return result;
  }

  // Exports
  global.N13_URL = {
    getExtension: getExtension,
    classifyExtension: classifyExtension,
    hasDownloadPath: hasDownloadPath,
    hasDownloadQueryParam: hasDownloadQueryParam,
    isFalsePositive: isFalsePositive,
    isCdnAsset: isCdnAsset,
    isSignedUrl: isSignedUrl,
    isBlobUrl: isBlobUrl,
    isStreamingUrl: isStreamingUrl,
    analyzeUrl: analyzeUrl,
    STRONG_EXTENSIONS: STRONG_EXTENSIONS,
    MEDIA_EXTENSIONS: MEDIA_EXTENSIONS,
    STREAMING_EXTENSIONS: STREAMING_EXTENSIONS,
    WEB_ASSET_EXTENSIONS: WEB_ASSET_EXTENSIONS,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
