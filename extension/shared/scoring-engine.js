/**
 * N13 Scoring Engine — weighted confidence scoring for download detection.
 *
 * Combines signals from URL analysis, MIME analysis, header analysis,
 * DOM analysis, and network context into a single confidence score (0.00–1.00).
 *
 * Signal weights are designed to balance:
 *   - Strong signals (Content-Disposition attachment, known extension)
 *   - Medium signals (download path, media MIME type)
 *   - Weak signals (link text, element type)
 *   - Negative signals (HTML response, tracking URL, CSS/JS)
 *
 * The scoring is deterministic and debuggable: each signal contributes
 * an explicit delta with a reason string.
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------------------
  // Confidence thresholds
  // ---------------------------------------------------------------------------

  var THRESHOLDS = {
    IGNORE: 0.29,          // Below this: not a candidate
    WEAK: 0.59,            // 0.30–0.59: weak candidate (might show in debug)
    CANDIDATE: 0.79,       // 0.60–0.79: valid candidate
    STRONG: 1.00,          // 0.80–1.00: strong download candidate
  };

  var DECISION = {
    IGNORE: "ignore",
    WEAK: "weak",
    CANDIDATE: "candidate",
    STRONG: "download",
  };

  /**
   * Classify a raw score into a decision level.
   */
  function classify(score) {
    if (score >= THRESHOLDS.CANDIDATE) return DECISION.STRONG;
    if (score >= THRESHOLDS.WEAK) return DECISION.CANDIDATE;
    if (score >= THRESHOLDS.IGNORE) return DECISION.WEAK;
    return DECISION.IGNORE;
  }

  // ---------------------------------------------------------------------------
  // Positive signals (weights: -1.0 to +1.0, representing contribution to final score)
  // ---------------------------------------------------------------------------

  /**
   * Compute positive score signals from all analysis layers.
   * Returns { score: number, signals: [{weight, reason}] }
   */
  function computePositiveSignals(context) {
    var signals = [];
    var score = 0;

    // --- URL Extension signals ---

    if (context.urlAnalysis) {
      var ua = context.urlAnalysis;

      // Strong file extension: .zip, .pdf, .exe, etc.
      // A known downloadable extension is the strongest URL-only signal.
      if (ua.isStrongExtension) {
        score += 0.80;
        signals.push({ weight: 0.80, reason: "known_download_extension" });
      }

      // Media extension: .mp4, .mp3, etc.
      // Common on web pages, so needs additional context to be a candidate.
      if (ua.isMediaExtension) {
        score += 0.35;
        signals.push({ weight: 0.35, reason: "media_extension" });
      }

      // Streaming manifest: .m3u8, .mpd
      if (ua.isStreamingManifest || ua.isStreamingUrl) {
        score += 0.30;
        signals.push({ weight: 0.30, reason: "streaming_manifest" });
      }

      // Download path segment: /download/, /files/, /dl/
      if (ua.hasDownloadPath) {
        score += 0.25;
        signals.push({ weight: 0.25, reason: "download_path_segment" });
      }

      // Download query parameter: ?download=, ?file=, ?dl=
      if (ua.hasDownloadQueryParam) {
        score += 0.25;
        signals.push({ weight: 0.25, reason: "download_query_parameter" });
      }

      // Signed URL (may need special handling but IS a download)
      if (ua.isSignedUrl && (ua.isStrongExtension || ua.hasDownloadPath)) {
        score += 0.05;
        signals.push({ weight: 0.05, reason: "signed_url_with_download_signal" });
      }
    }

    // --- DOM element signals ---

    if (context.domAnalysis) {
      var da = context.domAnalysis;

      // <a download> attribute
      if (da.hasDownloadAttribute) {
        score += 0.40;
        signals.push({ weight: 0.40, reason: "download_attribute" });
      }

      // Media element (video/audio src)
      if (da.isMediaElement) {
        score += 0.25;
        signals.push({ weight: 0.25, reason: "media_element_src" });
      }

      // Object/embed element
      if (da.isObjectElement) {
        score += 0.15;
        signals.push({ weight: 0.15, reason: "object_embed_element" });
      }

      // Element type attribute (MIME type)
      if (da.typeAttribute) {
        var typeCat = typeof N13_MIME !== "undefined" ? N13_MIME.getCategory(da.typeAttribute) : "UNKNOWN";
        if (typeCat === "ARCHIVE" || typeCat === "DOCUMENT" || typeCat === "EXECUTABLE") {
          score += 0.30;
          signals.push({ weight: 0.30, reason: "element_type_" + typeCat.toLowerCase() });
        } else if (typeCat === "MEDIA") {
          score += 0.20;
          signals.push({ weight: 0.20, reason: "element_type_media" });
        }
      }

      // Download-related link text
      if (da.downloadTextMatch) {
        score += 0.10;
        signals.push({ weight: 0.10, reason: "download_text_in_link" });
      }

      // Sibling/parent download context
      if (da.hasDownloadSibling) {
        score += 0.10;
        signals.push({ weight: 0.10, reason: "sibling_download_context" });
      }
    }

    // --- MIME type signals ---

    if (context.mimeAnalysis) {
      var ma = context.mimeAnalysis;

      if (ma.isDownloadable) {
        score += 0.45;
        signals.push({ weight: 0.45, reason: "downloadable_mime_type" });
      }

      if (ma.isStreamManifest) {
        score += 0.20;
        signals.push({ weight: 0.20, reason: "streaming_manifest_mime" });
      }
    }

    // --- Header signals ---

    if (context.headerAnalysis) {
      var ha = context.headerAnalysis;

      // Content-Disposition: attachment
      if (ha.isAttachment) {
        score += 0.50;
        signals.push({ weight: 0.50, reason: "content_disposition_attachment" });
      }

      // Content-Disposition present (inline with filename)
      if (ha.contentDisposition && !ha.isAttachment && ha.filename) {
        score += 0.20;
        signals.push({ weight: 0.20, reason: "content_disposition_with_filename" });
      }

      // Accept-Ranges: bytes
      if (ha.acceptsRanges) {
        score += 0.10;
        signals.push({ weight: 0.10, reason: "accept_ranges_bytes" });
      }

      // Content-Length > 0 (actual content)
      if (ha.contentLength > 0) {
        score += 0.05;
        signals.push({ weight: 0.05, reason: "content_length_present" });
      }

      // Content-Length > 100KB (significant file)
      if (ha.contentLength > 100 * 1024) {
        score += 0.05;
        signals.push({ weight: 0.05, reason: "content_length_large" });
      }

      // Content-Range present (partial download support)
      if (ha.contentRange) {
        score += 0.10;
        signals.push({ weight: 0.10, reason: "content_range_present" });
      }
    }

    // --- Network context signals ---

    if (context.networkAnalysis) {
      var na = context.networkAnalysis;

      if (na.resourceType === "media") {
        score += 0.15;
        signals.push({ weight: 0.15, reason: "network_resource_type_media" });
      }

      if (na.resourceType === "other" || na.resourceType === "xhr" || na.resourceType === "fetch") {
        score += 0.05;
        signals.push({ weight: 0.05, reason: "network_resource_type_" + na.resourceType });
      }
    }

    // --- Source bonus ---

    if (context.source) {
      if (context.source === "network") {
        score += 0.05;
        signals.push({ weight: 0.05, reason: "network_detection_source" });
      } else if (context.source === "click") {
        score += 0.10;
        signals.push({ weight: 0.10, reason: "user_click_source" });
      }
    }

    return { score: Math.min(score, 1.0), signals: signals };
  }

  /**
   * Compute negative signals that reduce confidence.
   * Returns { penalty: number, signals: [{weight, reason}] }
   */
  function computeNegativeSignals(context) {
    var signals = [];
    var penalty = 0;

    // --- URL negative signals ---

    if (context.urlAnalysis) {
      var ua = context.urlAnalysis;

      // False positive URL (analytics, tracking)
      if (ua.isFalsePositive) {
        penalty += 0.80;
        signals.push({ weight: -0.80, reason: "false_positive_url_pattern" });
      }

      // CDN asset (JS/CSS bundle)
      if (ua.isCdnAsset) {
        penalty += 0.60;
        signals.push({ weight: -0.60, reason: "cdn_asset_pattern" });
      }

      // Web asset extension
      if (ua.isWebAsset) {
        penalty += 0.50;
        signals.push({ weight: -0.50, reason: "web_asset_extension" });
      }

      // Blob URL (usually not directly downloadable)
      if (ua.isBlobUrl) {
        penalty += 0.30;
        signals.push({ weight: -0.30, reason: "blob_url" });
      }
    }

    // --- MIME negative signals ---

    if (context.mimeAnalysis) {
      var ma = context.mimeAnalysis;

      if (ma.isNegativeSignal) {
        penalty += 0.70;
        signals.push({ weight: -0.70, reason: "negative_mime_type" });
      }
    }

    // --- Header negative signals ---

    if (context.headerAnalysis) {
      var ha = context.headerAnalysis;

      // Content-Type is JSON
      if (ha.contentType && /json/i.test(ha.contentType)) {
        penalty += 0.60;
        signals.push({ weight: -0.60, reason: "json_response" });
      }

      // Content-Type is HTML
      if (ha.contentType && /html/i.test(ha.contentType)) {
        penalty += 0.70;
        signals.push({ weight: -0.70, reason: "html_response" });
      }
    }

    // --- DOM negative signals ---

    if (context.domAnalysis) {
      var da = context.domAnalysis;

      // Very small element (likely tracking pixel or icon)
      if (da.elementSize && da.elementSize.width < 5 && da.elementSize.height < 5) {
        penalty += 0.70;
        signals.push({ weight: -0.70, reason: "tiny_element_tracking_pixel" });
      }
    }

    return { penalty: Math.min(penalty, 1.0), signals: signals };
  }

  /**
   * Compute the final confidence score for a download candidate.
   *
   * @param {object} context - Combined analysis context containing:
   *   urlAnalysis, domAnalysis, mimeAnalysis, headerAnalysis, networkAnalysis, source
   * @returns {{ score: number, confidence: number, decision: string, signals: object[], positiveSignals: object[], negativeSignals: object[] }}
   */
  function computeScore(context) {
    var pos = computePositiveSignals(context);
    var neg = computeNegativeSignals(context);

    // Raw score: positive signals minus negative signals
    var rawScore = pos.score - neg.penalty;
    // Clamp to [0, 1]
    var score = Math.max(0, Math.min(1.0, rawScore));
    var decision = classify(score);

    return {
      score: Math.round(score * 100) / 100,  // 2 decimal places
      confidence: score,
      decision: decision,
      positiveSignals: pos.signals,
      negativeSignals: neg.signals,
      signals: pos.signals.concat(neg.signals),
    };
  }

  /**
   * Quick score for simple cases (URL only, no headers/MIME).
   * Used for initial DOM scan before network data is available.
   *
   * @param {string} url - URL to score
   * @param {object} domElement - DOM element (optional)
   * @param {string} source - Detection source (optional)
   * @returns {{ score: number, decision: string }}
   */
  function quickScore(url, domElement, source) {
    var urlAnalysis = typeof N13_URL !== "undefined" ? N13_URL.analyzeUrl(url) : { valid: false };
    var domAnalysis = analyzeDomElement(domElement);
    return computeScore({
      urlAnalysis: urlAnalysis,
      domAnalysis: domAnalysis,
      source: source || "link",
    });
  }

  /**
   * Quick DOM element analysis for scoring.
   */
  function analyzeDomElement(el) {
    if (!el) return {};

    var result = {
      hasDownloadAttribute: false,
      isMediaElement: false,
      isObjectElement: false,
      typeAttribute: "",
      downloadTextMatch: false,
      hasDownloadSibling: false,
      elementSize: null,
    };

    try {
      // Download attribute
      if (typeof el.hasAttribute === "function") {
        result.hasDownloadAttribute = el.hasAttribute("download");
      }

      // Tag-based classification
      var tag = el.tagName ? el.tagName.toUpperCase() : "";
      if (tag === "VIDEO" || tag === "AUDIO") {
        result.isMediaElement = true;
      }
      if (tag === "OBJECT" || tag === "EMBED") {
        result.isObjectElement = true;
      }

      // Type attribute
      if (typeof el.getAttribute === "function") {
        result.typeAttribute = el.getAttribute("type") || "";
      }

      // Download-related text
      if (el.textContent) {
        var txt = el.textContent.trim().replace(/\s+/g, " ").slice(0, 120);
        if (/download|دانلود|دریافت|بارگیری|direct link|get file|save file/i.test(txt)) {
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
    } catch (e) { /* ignore */ }

    return result;
  }

  // Exports
  global.N13_SCORING = {
    computeScore: computeScore,
    quickScore: quickScore,
    classify: classify,
    analyzeDomElement: analyzeDomElement,
    THRESHOLDS: THRESHOLDS,
    DECISION: DECISION,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
