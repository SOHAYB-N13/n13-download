/**
 * N13 Deduplicator — prevents duplicate download candidates.
 *
 * A single resource may be discovered via multiple detection layers:
 * DOM, network, click, media, MutationObserver.  This module ensures
 * each resource appears at most once in the candidate list.
 *
 * Deduplication strategy:
 *   1. Normalize URLs (strip tracking params, lowercase host, etc.)
 *   2. Compare final URLs (after redirects)
 *   3. Use filename + content-type as secondary key (for signed URLs)
 *   4. Maintain a time-based cache with TTL to handle page navigation
 *
 * Important: signed URLs with different tokens may point to the same
 * resource.  The deduplicator handles this by also matching on
 * filename + MIME + approximate size when available.
 */
(function (global) {
  "use strict";

  var DEFAULT_TTL_MS = 30 * 60 * 1000; // 30 minutes
  var MAX_CACHE_SIZE = 5000;

  /**
   * Query parameters that should be stripped for normalization.
   * These are typically tracking/session parameters, not part of the resource identity.
   */
  var TRACKING_PARAMS = [
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid",
    "ref", "referer", "referrer", "source", "src",
    "ts", "t", "v", "ver", "version",
    "rand", "random", "nocache", "_cache", "cb",
    "timestamp", "_", "tk", "sig",
    "X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date",
    "X-Amz-Expires", "X-Amz-SignedHeaders",
  ];

  /**
   * Normalize a URL for deduplication comparison.
   * - Lowercase scheme + host
   * - Remove default ports (80/443)
   * - Remove trailing slash from path
   * - Sort query parameters
   * - Remove tracking parameters
   */
  function normalizeUrl(url) {
    if (!url || typeof url !== "string") return "";

    try {
      var parsed = new URL(url);

      // Lowercase scheme + host
      parsed.protocol = parsed.protocol.toLowerCase();
      parsed.hostname = parsed.hostname.toLowerCase();

      // Remove default ports
      if ((parsed.protocol === "http:" && parsed.port === "80") ||
          (parsed.protocol === "https:" && parsed.port === "443")) {
        parsed.port = "";
      }

      // Remove tracking params
      var params = parsed.searchParams;
      for (var i = 0; i < TRACKING_PARAMS.length; i++) {
        params.delete(TRACKING_PARAMS[i]);
      }

      // Sort remaining params
      params.sort();

      // Rebuild URL
      var normalized = parsed.origin + parsed.pathname;

      // Remove trailing slash (except for root)
      if (normalized.length > parsed.origin.length) {
        normalized = normalized.replace(/\/+$/, "");
      }

      // Add sorted params
      var paramString = params.toString();
      if (paramString) {
        normalized += "?" + paramString;
      }

      // Add fragment (not part of resource identity)
      // Fragment is intentionally excluded

      return normalized;
    } catch (e) {
      // URL parsing failed — return as-is (lowercased)
      return url.toLowerCase();
    }
  }

  /**
   * Extract the "resource identity key" from a URL.
   * For signed URLs, this extracts the path portion without signing params.
   */
  function resourceKey(url) {
    if (!url || typeof url !== "string") return "";

    try {
      var parsed = new URL(url);
      return parsed.hostname.toLowerCase() + parsed.pathname;
    } catch (e) {
      return normalizeUrl(url);
    }
  }

  /**
   * Create a deduplication cache.
   *
   * @param {object} options - { ttlMs, maxSize }
   * @returns {object} Cache API
   */
  function createCache(options) {
    options = options || {};
    var ttlMs = options.ttlMs || DEFAULT_TTL_MS;
    var maxSize = options.maxSize || MAX_CACHE_SIZE;

    var entries = new Map(); // key → { time, candidate }

    /**
     * Evict expired entries and enforce max size.
     */
    function evict() {
      var now = Date.now();

      // Remove expired
      for (var key of entries.keys()) {
        var entry = entries.get(key);
        if (now - entry.time > ttlMs) {
          entries.delete(key);
        }
      }

      // Enforce max size (FIFO)
      while (entries.size > maxSize) {
        var firstKey = entries.keys().next().value;
        entries.delete(firstKey);
      }
    }

    return {
      /**
       * Check if a candidate is a duplicate.  If not, add it to the cache.
       * @param {object} candidate - { url, filename, mime, finalUrl }
       * @returns {{ isDuplicate: boolean, existingCandidate: object|null }}
       */
      check: function (candidate) {
        evict();

        var normUrl = normalizeUrl(candidate.url || "");
        var resKey = resourceKey(candidate.url || "");
        var finalNorm = candidate.finalUrl ? normalizeUrl(candidate.finalUrl) : "";

        // Check 1: Exact normalized URL match
        if (normUrl && entries.has(normUrl)) {
          return { isDuplicate: true, existingCandidate: entries.get(normUrl).candidate };
        }

        // Check 2: Final URL match (after redirect)
        if (finalNorm && finalNorm !== normUrl && entries.has(finalNorm)) {
          return { isDuplicate: true, existingCandidate: entries.get(finalNorm).candidate };
        }

        // Check 3: Resource key + filename + MIME (for signed URLs)
        if (candidate.filename && candidate.mime) {
          var compositeKey = resKey + "|" + (candidate.filename || "").toLowerCase() + "|" + (candidate.mime || "").toLowerCase();
          if (entries.has(compositeKey)) {
            return { isDuplicate: true, existingCandidate: entries.get(compositeKey).candidate };
          }
        }

        // Not a duplicate — add to cache
        var now = Date.now();
        if (normUrl) entries.set(normUrl, { time: now, candidate: candidate });
        if (finalNorm && finalNorm !== normUrl) entries.set(finalNorm, { time: now, candidate: candidate });
        if (candidate.filename && candidate.mime) {
          var compositeKey2 = resKey + "|" + (candidate.filename || "").toLowerCase() + "|" + (candidate.mime || "").toLowerCase();
          entries.set(compositeKey2, { time: now, candidate: candidate });
        }

        return { isDuplicate: false, existingCandidate: null };
      },

      /**
       * Check without adding (read-only).
       */
      has: function (candidate) {
        evict();
        var normUrl = normalizeUrl(candidate.url || "");
        if (normUrl && entries.has(normUrl)) return true;
        var finalNorm = candidate.finalUrl ? normalizeUrl(candidate.finalUrl) : "";
        if (finalNorm && entries.has(finalNorm)) return true;
        return false;
      },

      /**
       * Manually add a candidate to the cache.
       */
      add: function (candidate) {
        evict();
        var normUrl = normalizeUrl(candidate.url || "");
        var now = Date.now();
        if (normUrl) entries.set(normUrl, { time: now, candidate: candidate });
        var finalNorm = candidate.finalUrl ? normalizeUrl(candidate.finalUrl) : "";
        if (finalNorm && finalNorm !== normUrl) entries.set(finalNorm, { time: now, candidate: candidate });
      },

      /**
       * Clear all entries.
       */
      clear: function () {
        entries.clear();
      },

      /**
       * Get cache size.
       */
      size: function () {
        return entries.size;
      },

      /**
       * Get stats.
       */
      stats: function () {
        evict();
        return { size: entries.size, maxSize: maxSize, ttlMs: ttlMs };
      },
    };
  }

  // Exports
  global.N13_DEDUP = {
    normalizeUrl: normalizeUrl,
    resourceKey: resourceKey,
    createCache: createCache,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
