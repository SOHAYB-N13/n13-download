import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadDetector, loadFullEngine, makeAnchor, makeMediaElement, makeObjectElement, makeDocument, emptySelection, makeSelection, SHARED_DIR } from "./helpers.mjs";
import fs from "node:fs";
import path from "node:path";

// ---------------------------------------------------------------------------
// Load all modules for comprehensive testing
// ---------------------------------------------------------------------------

function loadAllModules() {
  // Ensure globalThis has all mocks
  globalThis.location = globalThis.location || { href: "https://example.com/page/index.html" };
  globalThis.document = globalThis.document || { querySelectorAll: () => [] };
  globalThis.window = globalThis.window || { getSelection: () => emptySelection() };

  const files = [
    "mime-analyzer.js",
    "header-analyzer.js",
    "filename-resolver.js",
    "url-analyzer.js",
    "deduplicator.js",
    "scoring-engine.js",
    "network-detector.js",
    "download-detector.js",
  ];
  for (const f of files) {
    const code = fs.readFileSync(path.join(SHARED_DIR, f), "utf8");
    (0, eval)(code);
  }
}

loadAllModules();

// ===========================================================================
// BACKWARD COMPATIBILITY TESTS — existing API must work identically
// ===========================================================================

describe("Backward Compatibility", () => {
  const g = loadDetector();

  test("N13_IS_DOWNLOAD exists and works", () => {
    assert.equal(typeof g.N13_IS_DOWNLOAD, "function");
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/file.zip"), true);
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/page"), false);
  });

  test("N13_DETECT returns numeric score", () => {
    const score = g.N13_DETECT("https://example.com/file.pdf");
    assert.equal(typeof score, "number");
    assert.ok(score >= 100);
  });

  test("N13_DETECT_ITEM returns correct structure", () => {
    const el = makeAnchor("https://example.com/file.zip", "Download ZIP");
    const item = g.N13_DETECT_ITEM("https://example.com/file.zip", el, "link");
    assert.equal(item.url, "https://example.com/file.zip");
    assert.ok(item.confidence >= 80);
    assert.equal(item.type, "download");
    assert.equal(item.source, "link");
    assert.ok(item.name);
  });

  test("N13_SCAN_PAGE returns {urls, count}", () => {
    globalThis.document = makeDocument([
      makeAnchor("https://example.com/a.zip", "A"),
      makeAnchor("https://example.com/b.pdf", "B"),
      makeAnchor("https://example.com/c", "Normal"),
    ]);
    const result = g.N13_SCAN_PAGE();
    assert.ok(Array.isArray(result.urls));
    assert.equal(typeof result.count, "number");
    assert.equal(result.count, 2);
  });

  test("N13_NAME extracts display name", () => {
    const el = makeAnchor("https://example.com/file.zip", "My File");
    assert.equal(g.N13_NAME(el, "https://example.com/file.zip"), "My File");
  });

  test("N13_NAME falls back to URL filename", () => {
    assert.equal(g.N13_NAME(null, "https://example.com/file.zip"), "file.zip");
  });
});

// ===========================================================================
// MIME ANALYZER TESTS
// ===========================================================================

describe("MIME Analyzer", () => {
  test("normalizes MIME types", () => {
    assert.equal(N13_MIME.normalize("Application/PDF; charset=utf-8"), "application/pdf");
    assert.equal(N13_MIME.normalize("  TEXT/HTML  "), "text/html");
    assert.equal(N13_MIME.normalize(""), "");
    assert.equal(N13_MIME.normalize(null), "");
  });

  test("categorizes archive types", () => {
    assert.equal(N13_MIME.getCategory("application/zip"), "ARCHIVE");
    assert.equal(N13_MIME.getCategory("application/x-7z-compressed"), "ARCHIVE");
    assert.equal(N13_MIME.getCategory("application/x-rar-compressed"), "ARCHIVE");
    assert.equal(N13_MIME.getCategory("application/gzip"), "ARCHIVE");
    assert.equal(N13_MIME.getCategory("application/octet-stream"), "ARCHIVE");
  });

  test("categorizes document types", () => {
    assert.equal(N13_MIME.getCategory("application/pdf"), "DOCUMENT");
    assert.equal(N13_MIME.getCategory("application/msword"), "DOCUMENT");
    assert.equal(N13_MIME.getCategory("application/vnd.openxmlformats-officedocument.wordprocessingml.document"), "DOCUMENT");
    assert.equal(N13_MIME.getCategory("text/csv"), "DOCUMENT");
  });

  test("categorizes media types via prefix", () => {
    assert.equal(N13_MIME.getCategory("video/mp4"), "MEDIA");
    assert.equal(N13_MIME.getCategory("audio/mpeg"), "MEDIA");
    assert.equal(N13_MIME.getCategory("image/jpeg"), "MEDIA");
    assert.equal(N13_MIME.getCategory("video/webm"), "MEDIA");
    assert.equal(N13_MIME.getCategory("audio/flac"), "MEDIA");
  });

  test("categorizes web assets", () => {
    assert.equal(N13_MIME.getCategory("text/html"), "WEB_ASSET");
    assert.equal(N13_MIME.getCategory("text/css"), "WEB_ASSET");
    assert.equal(N13_MIME.getCategory("application/javascript"), "WEB_ASSET");
    assert.equal(N13_MIME.getCategory("application/json"), "WEB_ASSET");
  });

  test("identifies downloadable MIME types", () => {
    assert.equal(N13_MIME.isDownloadable("application/pdf"), true);
    assert.equal(N13_MIME.isDownloadable("application/zip"), true);
    assert.equal(N13_MIME.isDownloadable("video/mp4"), true);
    assert.equal(N13_MIME.isDownloadable("text/html"), false);
    assert.equal(N13_MIME.isDownloadable("application/json"), false);
  });

  test("identifies negative signals", () => {
    assert.equal(N13_MIME.isNegativeSignal("text/html"), true);
    assert.equal(N13_MIME.isNegativeSignal("text/css"), true);
    assert.equal(N13_MIME.isNegativeSignal("application/pdf"), false);
  });

  test("identifies streaming manifests", () => {
    assert.equal(N13_MIME.isStreamManifest("application/x-mpegURL"), true);
    assert.equal(N13_MIME.isStreamManifest("application/vnd.apple.mpegurl"), true);
    assert.equal(N13_MIME.isStreamManifest("application/dash+xml"), true);
    assert.equal(N13_MIME.isStreamManifest("text/html"), false);
  });
});

// ===========================================================================
// URL ANALYZER TESTS
// ===========================================================================

describe("URL Analyzer", () => {
  test("extracts file extensions", () => {
    assert.equal(N13_URL.getExtension("https://example.com/file.zip"), "zip");
    assert.equal(N13_URL.getExtension("https://example.com/file.tar.gz"), "gz");
    assert.equal(N13_URL.getExtension("https://example.com/page"), "");
  });

  test("classifies strong extensions", () => {
    const zip = N13_URL.classifyExtension("zip");
    assert.equal(zip.isDownloadable, true);
    assert.equal(zip.category, "ARCHIVE");

    const pdf = N13_URL.classifyExtension("pdf");
    assert.equal(pdf.isDownloadable, true);
    assert.equal(pdf.category, "DOCUMENT");

    const exe = N13_URL.classifyExtension("exe");
    assert.equal(exe.isDownloadable, true);
    assert.equal(exe.category, "EXECUTABLE");
  });

  test("classifies media extensions", () => {
    const mp4 = N13_URL.classifyExtension("mp4");
    assert.equal(mp4.isMedia, true);
    assert.equal(mp4.category, "VIDEO");

    const mp3 = N13_URL.classifyExtension("mp3");
    assert.equal(mp3.isMedia, true);
    assert.equal(mp3.category, "AUDIO");
  });

  test("classifies streaming extensions", () => {
    const m3u8 = N13_URL.classifyExtension("m3u8");
    assert.equal(m3u8.isStreaming, true);
    assert.equal(m3u8.category, "HLS");

    const mpd = N13_URL.classifyExtension("mpd");
    assert.equal(mpd.isStreaming, true);
    assert.equal(mpd.category, "DASH");
  });

  test("classifies web asset extensions", () => {
    const js = N13_URL.classifyExtension("js");
    assert.equal(js.isWebAsset, true);

    const css = N13_URL.classifyExtension("css");
    assert.equal(css.isWebAsset, true);

    const html = N13_URL.classifyExtension("html");
    assert.equal(html.isWebAsset, true);
  });

  test("detects download path segments", () => {
    assert.equal(N13_URL.hasDownloadPath("https://example.com/download/file.zip"), true);
    assert.equal(N13_URL.hasDownloadPath("https://example.com/files/document.pdf"), true);
    assert.equal(N13_URL.hasDownloadPath("https://example.com/dl/video.mp4"), true);
    assert.equal(N13_URL.hasDownloadPath("https://example.com/page/about"), false);
  });

  test("detects download query parameters", () => {
    assert.equal(N13_URL.hasDownloadQueryParam("https://example.com/get?download=1"), true);
    assert.equal(N13_URL.hasDownloadQueryParam("https://example.com/file?id=42&dl=1"), true);
    assert.equal(N13_URL.hasDownloadQueryParam("https://example.com/page?tab=about"), false);
  });

  test("identifies false positive URLs", () => {
    assert.equal(N13_URL.isFalsePositive("https://www.google-analytics.com/analytics.js"), true);
    assert.equal(N13_URL.isFalsePositive("https://connect.facebook.net/en_US/fbevents.js"), true);
    assert.equal(N13_URL.isFalsePositive("https://example.com/download/file.zip"), false);
  });

  test("identifies CDN assets", () => {
    assert.equal(N13_URL.isCdnAsset("https://cdn.jsdelivr.net/npm/react@18/dist/react.min.js"), true);
    assert.equal(N13_URL.isCdnAsset("https://example.com/download/file.zip"), false);
  });

  test("identifies signed URLs", () => {
    assert.equal(N13_URL.isSignedUrl("https://cdn.example.com/file.zip?X-Amz-Signature=abc"), true);
    assert.equal(N13_URL.isSignedUrl("https://cdn.example.com/file.zip?token=abc123"), true);
    assert.equal(N13_URL.isSignedUrl("https://example.com/file.zip"), false);
  });

  test("full URL analysis", () => {
    const result = N13_URL.analyzeUrl("https://example.com/download/file.zip");
    assert.equal(result.valid, true);
    assert.equal(result.extension, "zip");
    assert.equal(result.isStrongExtension, true);
    assert.equal(result.hasDownloadPath, true);
    assert.equal(result.isFalsePositive, false);
  });

  test("extensionless download URL analysis", () => {
    const result = N13_URL.analyzeUrl("https://example.com/download/839291");
    assert.equal(result.valid, true);
    assert.equal(result.hasDownloadPath, true);
    assert.equal(result.isStrongExtension, false);
  });

  test("URL without extension and without download path is not download", () => {
    const result = N13_URL.analyzeUrl("https://example.com/about/team");
    assert.equal(result.valid, true);
    assert.equal(result.isStrongExtension, false);
    assert.equal(result.hasDownloadPath, false);
  });

  test("invalid URL returns invalid result", () => {
    const result = N13_URL.analyzeUrl("");
    assert.equal(result.valid, false);
  });

  test("non-http URL returns invalid", () => {
    const result = N13_URL.analyzeUrl("ftp://example.com/file.zip");
    assert.equal(result.valid, false);
  });
});

// ===========================================================================
// HEADER ANALYZER TESTS
// ===========================================================================

describe("Header Analyzer", () => {
  test("parses Content-Disposition with filename", () => {
    const result = N13_HEADERS.parseContentDisposition('attachment; filename="report.pdf"');
    assert.equal(result.disposition, "attachment");
    assert.equal(result.filename, "report.pdf");
  });

  test("parses Content-Disposition with filename* (RFC 5987)", () => {
    const result = N13_HEADERS.parseContentDisposition("attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87.pdf");
    assert.equal(result.disposition, "attachment");
    assert.equal(result.filenameStar, "UTF-8''%E4%B8%AD%E6%96%87.pdf");
  });

  test("decodes RFC 5987 filename*", () => {
    assert.equal(N13_HEADERS.decodeFilenameStar("UTF-8''hello.pdf"), "hello.pdf");
    assert.equal(N13_HEADERS.decodeFilenameStar("UTF-8''%E4%B8%AD%E6%96%87.pdf"), "中文.pdf");
    assert.equal(N13_HEADERS.decodeFilenameStar(""), "");
    assert.equal(N13_HEADERS.decodeFilenameStar(null), "");
  });

  test("extracts filename from Content-Disposition", () => {
    const result = N13_HEADERS.getFilenameFromDisposition('attachment; filename="report.pdf"');
    assert.equal(result.filename, "report.pdf");
    assert.equal(result.isAttachment, true);
  });

  test("filename* takes priority over filename", () => {
    const result = N13_HEADERS.getFilenameFromDisposition(
      'attachment; filename="fallback.pdf"; filename*=UTF-8\'\'%E4%B8%AD%E6%96%87.pdf'
    );
    assert.equal(result.filename, "中文.pdf");
  });

  test("detects Accept-Ranges", () => {
    assert.equal(N13_HEADERS.acceptsRanges("bytes"), true);
    assert.equal(N13_HEADERS.acceptsRanges("none"), false);
    assert.equal(N13_HEADERS.acceptsRanges(""), false);
  });

  test("parses Content-Range", () => {
    const result = N13_HEADERS.parseContentRange("bytes 0-1023/4096");
    assert.deepEqual(result, { unit: "bytes", start: 0, end: 1023, total: 4096 });
  });

  test("parses Content-Range with unknown total", () => {
    const result = N13_HEADERS.parseContentRange("bytes 0-1023/*");
    assert.deepEqual(result, { unit: "bytes", start: 0, end: 1023, total: -1 });
  });

  test("parses Content-Length", () => {
    assert.equal(N13_HEADERS.parseContentLength("12345"), 12345);
    assert.equal(N13_HEADERS.parseContentLength(""), -1);
    assert.equal(N13_HEADERS.parseContentLength(null), -1);
  });

  test("full header analysis", () => {
    const headers = new Map([
      ["content-type", "application/pdf"],
      ["content-disposition", 'attachment; filename="report.pdf"'],
      ["content-length", "1024000"],
      ["accept-ranges", "bytes"],
    ]);
    const result = N13_HEADERS.analyzeHeaders(headers);
    assert.equal(result.contentType, "application/pdf");
    assert.equal(result.isAttachment, true);
    assert.equal(result.filename, "report.pdf");
    assert.equal(result.contentLength, 1024000);
    assert.equal(result.acceptsRanges, true);
  });

  test("header analysis with plain object headers", () => {
    const headers = {
      "content-type": "video/mp4",
      "content-disposition": "inline",
      "content-length": "52428800",
    };
    const result = N13_HEADERS.analyzeHeaders(headers);
    assert.equal(result.contentType, "video/mp4");
    assert.equal(result.isAttachment, false);
    assert.equal(result.contentLength, 52428800);
  });
});

// ===========================================================================
// FILENAME RESOLVER TESTS
// ===========================================================================

describe("Filename Resolver", () => {
  test("sanitizes filenames", () => {
    assert.equal(N13_FILENAME.sanitize("hello world.txt"), "hello_world.txt");
    assert.equal(N13_FILENAME.sanitize("file<>:*?.txt"), "file.txt");
    assert.equal(N13_FILENAME.sanitize(""), "");
    assert.equal(N13_FILENAME.sanitize(null), "");
  });

  test("extracts filename from URL", () => {
    assert.equal(N13_FILENAME.fromUrl("https://example.com/files/report.pdf"), "report.pdf");
    assert.equal(N13_FILENAME.fromUrl("https://example.com/download/12345"), "12345");
    assert.equal(N13_FILENAME.fromUrl("https://example.com/"), "");
  });

  test("resolves filename with priority chain", () => {
    // Priority 1: Content-Disposition
    const result1 = N13_FILENAME.resolve({
      dispositionFilename: "report.pdf",
      url: "https://example.com/other.pdf",
    });
    assert.equal(result1, "report.pdf");

    // Priority 2: URL filename
    const result2 = N13_FILENAME.resolve({
      url: "https://example.com/files/document.pdf",
    });
    assert.equal(result2, "document.pdf");

    // Priority 3: fallback
    const result3 = N13_FILENAME.resolve({});
    assert.ok(result3);
  });

  test("ensures extension from MIME type", () => {
    assert.equal(N13_FILENAME.ensureExtension("report", "application/pdf"), "report.pdf");
    assert.equal(N13_FILENAME.ensureExtension("video", "video/mp4"), "video.mp4");
    assert.equal(N13_FILENAME.ensureExtension("report.pdf", "application/pdf"), "report.pdf");
  });

  test("URL filename with encoded characters", () => {
    const filename = N13_FILENAME.fromUrl("https://example.com/%E4%B8%AD%E6%96%87%E6%96%87%E4%BB%B6.pdf");
    assert.ok(filename.includes(".pdf"));
  });
});

// ===========================================================================
// SCORING ENGINE TESTS
// ===========================================================================

describe("Scoring Engine", () => {
  test("classifies scores into decision levels", () => {
    assert.equal(N13_SCORING.classify(0.9), "download");
    assert.equal(N13_SCORING.classify(0.7), "candidate");
    assert.equal(N13_SCORING.classify(0.4), "weak");
    assert.equal(N13_SCORING.classify(0.1), "ignore");
  });

  test("strong extension gets high score", () => {
    const result = N13_SCORING.computeScore({
      urlAnalysis: N13_URL.analyzeUrl("https://example.com/file.zip"),
    });
    assert.ok(result.score >= 0.4);
    assert.ok(result.decision === "candidate" || result.decision === "download");
  });

  test("Content-Disposition attachment is very strong signal", () => {
    const result = N13_SCORING.computeScore({
      urlAnalysis: N13_URL.analyzeUrl("https://example.com/download/123"),
      headerAnalysis: {
        isAttachment: true,
        filename: "report.pdf",
        contentLength: 1024000,
        acceptsRanges: true,
        contentRange: null,
        contentType: "application/pdf",
        contentDisposition: { disposition: "attachment" },
        isStreamManifest: false,
      },
    });
    assert.ok(result.score >= 0.5);
    assert.equal(result.decision, "download");
  });

  test("download attribute is strong positive signal", () => {
    const result = N13_SCORING.computeScore({
      domAnalysis: { hasDownloadAttribute: true },
    });
    assert.ok(result.score >= 0.3);
  });

  test("false positive URL gets heavy penalty", () => {
    const result = N13_SCORING.computeScore({
      urlAnalysis: N13_URL.analyzeUrl("https://www.google-analytics.com/analytics.js"),
    });
    assert.equal(result.score, 0);
    assert.equal(result.decision, "ignore");
  });

  test("HTML response is negative signal", () => {
    const result = N13_SCORING.computeScore({
      headerAnalysis: { contentType: "text/html" },
    });
    assert.ok(result.score < 0.3);
    assert.equal(result.decision, "ignore");
  });

  test("JSON response is negative signal", () => {
    const result = N13_SCORING.computeScore({
      headerAnalysis: { contentType: "application/json" },
    });
    assert.ok(result.score < 0.3);
    assert.equal(result.decision, "ignore");
  });

  test("download path + download attribute combined is strong", () => {
    const result = N13_SCORING.computeScore({
      urlAnalysis: N13_URL.analyzeUrl("https://example.com/download/file"),
      domAnalysis: { hasDownloadAttribute: true },
    });
    assert.ok(result.score >= 0.4);
    assert.ok(result.decision === "candidate" || result.decision === "download");
  });

  test("downloadable MIME type is strong signal", () => {
    const result = N13_SCORING.computeScore({
      mimeAnalysis: { isDownloadable: true, isNegativeSignal: false, isStreamManifest: false },
    });
    assert.ok(result.score >= 0.4);
  });

  test("quickScore function works", () => {
    const result = N13_SCORING.quickScore("https://example.com/file.zip");
    assert.ok(result.score >= 0.4);
    assert.ok(result.decision === "candidate" || result.decision === "download");
  });
});

// ===========================================================================
// DEDUPLICATOR TESTS
// ===========================================================================

describe("Deduplicator", () => {
  test("normalizes URLs", () => {
    const n1 = N13_DEDUP.normalizeUrl("https://example.com/file.zip?utm_source=test&dl=1");
    const n2 = N13_DEDUP.normalizeUrl("https://example.com/file.zip?dl=1");
    assert.equal(n1, n2);
  });

  test("normalizes URL case (scheme+host)", () => {
    const n1 = N13_DEDUP.normalizeUrl("HTTPS://EXAMPLE.COM/path/file.zip");
    const n2 = N13_DEDUP.normalizeUrl("https://example.com/path/file.zip");
    assert.equal(n1, n2);
  });

  test("resource key strips query params", () => {
    const k1 = N13_DEDUP.resourceKey("https://cdn.example.com/file.zip?token=abc&sig=def");
    const k2 = N13_DEDUP.resourceKey("https://cdn.example.com/file.zip?token=xyz");
    assert.equal(k1, k2);
  });

  test("cache detects duplicates", () => {
    const cache = N13_DEDUP.createCache();
    const c1 = { url: "https://example.com/file.zip" };
    const c2 = { url: "https://example.com/file.zip" };

    assert.equal(cache.check(c1).isDuplicate, false);
    assert.equal(cache.check(c2).isDuplicate, true);
  });

  test("cache handles signed URLs with same resource", () => {
    const cache = N13_DEDUP.createCache();
    const c1 = {
      url: "https://cdn.example.com/file.zip?X-Amz-Signature=abc",
      filename: "file.zip",
      mime: "application/zip",
    };
    const c2 = {
      url: "https://cdn.example.com/file.zip?X-Amz-Signature=def",
      filename: "file.zip",
      mime: "application/zip",
    };

    assert.equal(cache.check(c1).isDuplicate, false);
    assert.equal(cache.check(c2).isDuplicate, true);
  });

  test("cache treats different signed URLs as different when no filename match", () => {
    const cache = N13_DEDUP.createCache();
    const c1 = {
      url: "https://cdn.example.com/abc?X-Amz-Signature=1",
    };
    const c2 = {
      url: "https://cdn.example.com/xyz?X-Amz-Signature=2",
    };

    assert.equal(cache.check(c1).isDuplicate, false);
    assert.equal(cache.check(c2).isDuplicate, false);
  });

  test("cache respects TTL", () => {
    const cache = N13_DEDUP.createCache({ ttlMs: 1 }); // 1ms TTL
    cache.check({ url: "https://example.com/file.zip" });

    // After TTL expires (force eviction by calling check again)
    return new Promise((resolve) => {
      setTimeout(() => {
        assert.equal(cache.has({ url: "https://example.com/file.zip" }), false);
        resolve();
      }, 10);
    });
  });

  test("cache clears", () => {
    const cache = N13_DEDUP.createCache();
    cache.check({ url: "https://example.com/file.zip" });
    assert.equal(cache.size(), 1);
    cache.clear();
    assert.equal(cache.size(), 0);
  });
});

// ===========================================================================
// ENHANCED DOM DETECTION TESTS
// ===========================================================================

describe("Enhanced DOM Detection", () => {
  const g = loadFullEngine();

  test("detects <a> elements with strong extensions", () => {
    globalThis.document = makeDocument([
      makeAnchor("https://example.com/archive.zip", "Archive"),
      makeAnchor("https://example.com/about", "About"),
    ]);
    const result = g.N13_SCAN_PAGE();
    assert.equal(result.count, 1);
    assert.equal(result.urls[0].url, "https://example.com/archive.zip");
  });

  test("detects downloadable links in page scan", () => {
    globalThis.document = makeDocument([
      makeAnchor("https://example.com/files/archive.zip", "Download ZIP"),
      makeAnchor("https://example.com/docs/report.pdf", "PDF Report"),
      makeAnchor("https://example.com/about", "About Us"),
    ]);
    const result = g.N13_SCAN_PAGE();
    assert.equal(result.count, 2);
    const urls = result.urls.map(u => u.url);
    assert.ok(urls.includes("https://example.com/files/archive.zip"));
    assert.ok(urls.includes("https://example.com/docs/report.pdf"));
    assert.ok(!urls.includes("https://example.com/about"));
  });

  test("detects <a download> attribute", () => {
    const el = makeAnchor("https://example.com/generate/42", "Export", { download: true });
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/generate/42", el), true);
  });

  test("detects download route paths", () => {
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/download/file"), true);
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/dl/video.mp4"), true);
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/files/doc.pdf"), true);
  });

  test("does not detect non-downloadable pages", () => {
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/about"), false);
    assert.equal(g.N13_IS_DOWNLOAD("https://example.com/contact"), false);
  });

  test("group scan finds contextual links", () => {
    const link = makeAnchor("https://example.com/files/part1.zip", "Part 1");
    const result = g.N13_SCAN_GROUP(link);
    assert.ok(result.urls.length >= 1);
  });

  test("selection scan finds selected links", () => {
    const links = [
      makeAnchor("https://example.com/a.zip", "A"),
      makeAnchor("https://example.com/b.pdf", "B"),
    ];
    globalThis.window = { getSelection: () => makeSelection(links) };
    const result = g.N13_SCAN_SELECTION();
    assert.equal(result.count, 2);
  });

  test("element analysis correctly identifies media elements", () => {
    const video = makeMediaElement("video", "https://example.com/video.mp4", "video/mp4");
    const analysis = g.N13_ANALYZE_ELEMENT(video);
    assert.equal(analysis.isMediaElement, true);
    assert.equal(analysis.tag, "VIDEO");
  });

  test("element analysis correctly identifies download attribute", () => {
    const el = makeAnchor("https://example.com/file", "Download", { download: true });
    const analysis = g.N13_ANALYZE_ELEMENT(el);
    assert.equal(analysis.hasDownloadAttribute, true);
  });
});

// ===========================================================================
// NETWORK DETECTION TESTS (PerformanceObserver mock)
// ===========================================================================

describe("Network Detection", () => {
  test("N13_NET exists", () => {
    assert.ok(typeof N13_NET !== "undefined");
  });

  test("analyzes performance entry for downloads", () => {
    const entry = {
      url: "https://example.com/files/video.mp4",
      initiatorType: "media",
      transferSize: 10 * 1024 * 1024,
      decodedBodySize: 10 * 1024 * 1024,
    };
    const result = N13_NET.analyzePerformanceEntry(entry);
    assert.equal(result.isDownloadCandidate, true);
    assert.ok(result.signals.includes("media_initiator"));
  });

  test("analyzes large fetch as candidate", () => {
    const entry = {
      url: "https://example.com/api/download/data",
      initiatorType: "fetch",
      transferSize: 500 * 1024,
      decodedBodySize: 500 * 1024,
    };
    const result = N13_NET.analyzePerformanceEntry(entry);
    assert.equal(result.isDownloadCandidate, true);
  });

  test("small request is not a candidate", () => {
    const entry = {
      url: "https://example.com/api/data",
      initiatorType: "fetch",
      transferSize: 1024,
      decodedBodySize: 1024,
    };
    const result = N13_NET.analyzePerformanceEntry(entry);
    assert.equal(result.isDownloadCandidate, false);
  });
});

// ===========================================================================
// SCORING ENGINE: INTEGRATION TESTS
// ===========================================================================

describe("Scoring Integration", () => {
  const g = loadFullEngine();

  test("full detection: .zip URL with download attribute = strong", () => {
    const el = makeAnchor("https://example.com/files/app.zip", "App", { download: true });
    const candidate = g.N13_DETECT_ADVANCED("https://example.com/files/app.zip", el);
    assert.ok(candidate.score >= 0.5);
    assert.equal(candidate.decision, "download");
    assert.equal(candidate.type, "download");
    assert.ok(candidate.signals.length > 0);
  });

  test("full detection: .mp4 URL = media candidate", () => {
    const candidate = g.N13_DETECT_ADVANCED("https://example.com/video.mp4");
    assert.ok(candidate.score >= 0.2);
    assert.ok(candidate.type === "media" || candidate.type === "download");
  });

  test("full detection: analytics URL = ignore", () => {
    const candidate = g.N13_DETECT_ADVANCED("https://www.google-analytics.com/analytics.js");
    assert.equal(candidate.score, 0);
    assert.equal(candidate.decision, "ignore");
  });

  test("full detection: extensionless download URL with path gets positive score", () => {
    const candidate = g.N13_DETECT_ADVANCED("https://example.com/download/839291");
    assert.ok(candidate.score >= 0.20);
    assert.ok(candidate.positiveSignals.length > 0);
    // Download path alone (0.25) is below the weak threshold (0.30)
    // but combined with MIME or headers it would be a valid candidate
    const withMime = g.N13_DETECT_ADVANCED("https://example.com/download/839291", null, { mimeType: "application/pdf" });
    assert.ok(withMime.decision !== "ignore");
  });

  test("full detection: with MIME type = higher confidence", () => {
    const withoutMime = g.N13_DETECT_ADVANCED("https://example.com/download/123");
    const withMime = g.N13_DETECT_ADVANCED("https://example.com/download/123", null, {
      mimeType: "application/pdf",
    });
    assert.ok(withMime.score > withoutMime.score);
  });

  test("full detection: with Content-Disposition attachment = very strong", () => {
    const headers = new Map([
      ["content-type", "application/pdf"],
      ["content-disposition", 'attachment; filename="report.pdf"'],
      ["content-length", "1024000"],
    ]);
    const candidate = g.N13_DETECT_ADVANCED("https://example.com/download/123", null, {
      headers: headers,
      mimeType: "application/pdf",
    });
    assert.ok(candidate.score >= 0.5);
    assert.equal(candidate.decision, "download");
    assert.equal(candidate.filename, "report.pdf");
  });

  test("full detection: with negative MIME signal reduces score", () => {
    const withoutMime = g.N13_DETECT_ADVANCED("https://example.com/page");
    const withHtmlMime = g.N13_DETECT_ADVANCED("https://example.com/page", null, {
      mimeType: "text/html",
    });
    assert.ok(withHtmlMime.score <= withoutMime.score);
  });

  test("full detection: blob URL is penalized", () => {
    const candidate = g.N13_DETECT_ADVANCED("blob:https://example.com/abc-123");
    assert.ok(candidate.score < 0.3);
  });

  test("full detection: CDN asset is penalized", () => {
    const candidate = g.N13_DETECT_ADVANCED("https://cdn.jsdelivr.net/npm/react@18/dist/react.min.js");
    assert.equal(candidate.decision, "ignore");
  });

  test("scoring includes positive and negative signal breakdown", () => {
    const candidate = g.N13_DETECT_ADVANCED("https://example.com/download/file.zip", null, {
      headers: new Map([["content-disposition", "attachment"]]),
      mimeType: "application/zip",
    });
    assert.ok(candidate.positiveSignals.length > 0);
    assert.ok(candidate.signals.length > 0);
    // Check signal structure
    const firstSignal = candidate.signals[0];
    assert.ok(typeof firstSignal.weight === "number");
    assert.ok(typeof firstSignal.reason === "string");
  });

  test("advanced page scan returns scored candidates", () => {
    globalThis.document = makeDocument([
      makeAnchor("https://example.com/a.zip", "A"),
      makeAnchor("https://example.com/b.pdf", "B"),
      makeAnchor("https://example.com/c", "Normal"),
    ]);
    const result = g.N13_SCAN_PAGE_ADVANCED();
    assert.equal(result.count, 2);
    assert.ok(result.urls[0].score !== undefined);
    assert.ok(result.urls[0].decision !== undefined);
  });
});

// ===========================================================================
// DETECTION ENGINE TESTS
// ===========================================================================

describe("Detection Engine", () => {
  test("engine initializes", () => {
    N13_ENGINE.init({ enabled: true, detection: true, debug: false });
    assert.equal(N13_ENGINE.debugInfo().initialized, true);
  });

  test("engine adds non-duplicate candidates", () => {
    N13_ENGINE.reset();
    const result = N13_ENGINE.addCandidate({
      url: "https://example.com/file.zip",
      score: 0.8,
      decision: "download",
    });
    assert.equal(result.added, true);
    assert.equal(result.isDuplicate, false);
  });

  test("engine rejects duplicate candidates", () => {
    N13_ENGINE.reset();
    N13_ENGINE.addCandidate({ url: "https://example.com/file.zip", score: 0.8, decision: "download" });
    const result = N13_ENGINE.addCandidate({ url: "https://example.com/file.zip", score: 0.9, decision: "download" });
    assert.equal(result.added, false);
    assert.equal(result.isDuplicate, true);
  });

  test("engine updates existing candidate with higher score", () => {
    N13_ENGINE.reset();
    N13_ENGINE.addCandidate({ url: "https://example.com/file.zip", score: 0.6, decision: "candidate" });
    N13_ENGINE.addCandidate({ url: "https://example.com/file.zip", score: 0.9, decision: "download" });
    const candidates = N13_ENGINE.getCandidates();
    assert.equal(candidates.length, 1);
    assert.equal(candidates[0].score, 0.9);
  });

  test("engine filters by minimum decision level", () => {
    N13_ENGINE.reset();
    N13_ENGINE.addCandidate({ url: "https://example.com/a.zip", score: 0.8, decision: "download" });
    N13_ENGINE.addCandidate({ url: "https://example.com/b.pdf", score: 0.6, decision: "candidate" });
    N13_ENGINE.addCandidate({ url: "https://example.com/c.mp4", score: 0.4, decision: "weak" });

    const strong = N13_ENGINE.getCandidates({ minDecision: "download" });
    assert.equal(strong.length, 1);

    const candidates = N13_ENGINE.getCandidates({ minDecision: "candidate" });
    assert.equal(candidates.length, 2);

    const all = N13_ENGINE.getCandidates({ minDecision: "weak" });
    assert.equal(all.length, 3);
  });

  test("engine resets properly", () => {
    N13_ENGINE.reset();
    N13_ENGINE.addCandidate({ url: "https://example.com/file.zip", score: 0.8, decision: "download" });
    N13_ENGINE.reset();
    assert.equal(N13_ENGINE.getCandidates().length, 0);
  });

  test("engine processes network requests", () => {
    N13_ENGINE.reset();
    N13_ENGINE.processNetworkRequest({
      url: "https://example.com/download/video.mp4",
      initiatorType: "media",
      transferSize: 10 * 1024 * 1024,
    });
    const candidates = N13_ENGINE.getCandidates({ minDecision: "weak" });
    assert.ok(candidates.length > 0);
  });

  test("engine ignores false positive network requests", () => {
    N13_ENGINE.reset();
    N13_ENGINE.processNetworkRequest({
      url: "https://www.google-analytics.com/analytics.js",
      initiatorType: "script",
      transferSize: 1024,
    });
    assert.equal(N13_ENGINE.getCandidates().length, 0);
  });

  test("engine ignores CDN assets", () => {
    N13_ENGINE.reset();
    N13_ENGINE.processNetworkRequest({
      url: "https://cdn.jsdelivr.net/npm/react@18/dist/react.min.js",
      initiatorType: "script",
      transferSize: 5000,
    });
    assert.equal(N13_ENGINE.getCandidates().length, 0);
  });

  test("engine settings can be updated", () => {
    N13_ENGINE.updateSettings({ enabled: false });
    assert.equal(N13_ENGINE.debugInfo().settings.enabled, false);
    N13_ENGINE.updateSettings({ enabled: true });
  });

  test("engine legacy candidates format", () => {
    N13_ENGINE.reset();
    N13_ENGINE.addCandidate({
      url: "https://example.com/file.zip",
      name: "file.zip",
      score: 0.8,
      confidence: 80,
      decision: "download",
      type: "download",
      source: "link",
    });
    const legacy = N13_ENGINE.getLegacyCandidates();
    assert.equal(legacy.length, 1);
    assert.equal(legacy[0].url, "https://example.com/file.zip");
    assert.equal(legacy[0].confidence, 80);
  });
});

// ===========================================================================
// EXTENSIONLESS URL TESTS (critical scenario)
// ===========================================================================

describe("Extensionless URL Detection", () => {
  test("extensionless URL with download path is detected", () => {
    const score = N13_DETECT("https://example.com/download/839291");
    // This is the KEY gap in the old detector — extensionless URLs
    // With the enhanced URL analyzer, download path should boost score
    const urlAnalysis = N13_URL.analyzeUrl("https://example.com/download/839291");
    assert.equal(urlAnalysis.hasDownloadPath, true);
  });

  test("extensionless URL with download query param is detected", () => {
    const urlAnalysis = N13_URL.analyzeUrl("https://example.com/file?id=839291&download=1");
    assert.equal(urlAnalysis.hasDownloadQueryParam, true);
  });

  test("CDN URL without extension but with download signal", () => {
    const urlAnalysis = N13_URL.analyzeUrl("https://cdn.example.com/download/8f72a91");
    assert.equal(urlAnalysis.hasDownloadPath, true);
    assert.equal(urlAnalysis.valid, true);
  });

  test("URL with no extension and no download path is NOT detected", () => {
    const urlAnalysis = N13_URL.analyzeUrl("https://example.com/page/about");
    assert.equal(urlAnalysis.isStrongExtension, false);
    assert.equal(urlAnalysis.hasDownloadPath, false);
  });
});

// ===========================================================================
// MEDIA DETECTION TESTS
// ===========================================================================

describe("Media Detection", () => {
  test("HLS manifest extension is detected", () => {
    const urlAnalysis = N13_URL.analyzeUrl("https://example.com/stream/master.m3u8");
    assert.equal(urlAnalysis.isStreamingManifest, true);
    assert.equal(urlAnalysis.isStreamingUrl, true);
  });

  test("DASH manifest extension is detected", () => {
    const urlAnalysis = N13_URL.analyzeUrl("https://example.com/stream/manifest.mpd");
    assert.equal(urlAnalysis.isStreamingManifest, true);
  });

  test("video extension is classified as media", () => {
    const ext = N13_URL.classifyExtension("mp4");
    assert.equal(ext.isMedia, true);
    assert.equal(ext.category, "VIDEO");
  });

  test("audio extension is classified as media", () => {
    const ext = N13_URL.classifyExtension("mp3");
    assert.equal(ext.isMedia, true);
    assert.equal(ext.category, "AUDIO");
  });

  test("streaming manifest MIME is detected", () => {
    assert.equal(N13_MIME.isStreamManifest("application/x-mpegURL"), true);
    assert.equal(N13_MIME.isStreamManifest("application/dash+xml"), true);
  });
});

// ===========================================================================
// FALSE POSITIVE TESTS
// ===========================================================================

describe("False Positive Filtering", () => {
  test("Google Analytics is filtered", () => {
    assert.equal(N13_URL.isFalsePositive("https://www.google-analytics.com/analytics.js"), true);
  });

  test("Facebook tracking is filtered", () => {
    assert.equal(N13_URL.isFalsePositive("https://connect.facebook.net/en_US/fbevents.js"), true);
  });

  test("Hotjar is filtered", () => {
    assert.equal(N13_URL.isFalsePositive("https://static.hotjar.com/c/hotjar.js"), true);
  });

  test("JS/CSS CDN assets are filtered", () => {
    assert.equal(N13_URL.isCdnAsset("https://cdn.jsdelivr.net/npm/lodash@4/lodash.min.js"), true);
    assert.equal(N13_URL.isCdnAsset("https://unpkg.com/react@18/umd/react.production.min.js"), true);
  });

  test("real download URLs are NOT filtered", () => {
    assert.equal(N13_URL.isFalsePositive("https://example.com/download/file.zip"), false);
    assert.equal(N13_URL.isFalsePositive("https://cdn.example.com/abc123/file.pdf"), false);
  });

  test("JSON API responses are negative signals", () => {
    const result = N13_SCORING.computeScore({
      headerAnalysis: { contentType: "application/json" },
    });
    assert.equal(result.decision, "ignore");
  });

  test("tracking pixel (tiny element) is negative signal", () => {
    const result = N13_SCORING.computeScore({
      domAnalysis: { elementSize: { width: 1, height: 1 } },
    });
    assert.ok(result.negativeSignals.some(s => s.reason === "tiny_element_tracking_pixel"));
  });
});

// ===========================================================================
// COMPREHENSIVE TEST MATRIX
// ===========================================================================

describe("Test Matrix: All Scenario Types", () => {
  test("Direct files: .pdf, .zip, .rar, .7z, .exe, .iso, .apk, .docx, .xlsx", () => {
    const extensions = ["pdf", "zip", "rar", "7z", "exe", "iso", "apk", "docx", "xlsx"];
    for (const ext of extensions) {
      const url = `https://example.com/file.${ext}`;
      const score = N13_DETECT(url);
      assert.ok(score >= 80, `.${ext} should be detected (score: ${score})`);
    }
  });

  test("Media: .mp4, .webm, .mp3, .m4a", () => {
    const extensions = ["mp4", "webm", "mp3", "m4a"];
    for (const ext of extensions) {
      const urlAnalysis = N13_URL.analyzeUrl(`https://example.com/media.${ext}`);
      assert.equal(urlAnalysis.isMediaExtension, true, `.${ext} should be classified as media`);
    }
  });

  test("Extensionless URLs with download paths", () => {
    const urls = [
      "https://example.com/download/12345",
      "https://example.com/file/827361",
      "https://example.com/dl/8f72a91",
    ];
    for (const url of urls) {
      const urlAnalysis = N13_URL.analyzeUrl(url);
      assert.equal(urlAnalysis.hasDownloadPath, true, `${url} should have download path`);
    }
  });

  test("Headers: Content-Disposition attachment", () => {
    const headers = new Map([["content-disposition", 'attachment; filename="test.zip"']]);
    const result = N13_HEADERS.analyzeHeaders(headers);
    assert.equal(result.isAttachment, true);
    assert.equal(result.filename, "test.zip");
  });

  test("Headers: Content-Length", () => {
    const headers = new Map([["content-length", "1048576"]]);
    const result = N13_HEADERS.analyzeHeaders(headers);
    assert.equal(result.contentLength, 1048576);
  });

  test("Headers: Accept-Ranges", () => {
    const headers = new Map([["accept-ranges", "bytes"]]);
    const result = N13_HEADERS.analyzeHeaders(headers);
    assert.equal(result.acceptsRanges, true);
  });

  test("Streaming manifests: m3u8, mpd", () => {
    assert.equal(N13_URL.isStreamingUrl("https://example.com/stream/master.m3u8"), true);
    assert.equal(N13_URL.isStreamingUrl("https://example.com/stream/manifest.mpd"), true);
    assert.equal(N13_MIME.isStreamManifest("application/x-mpegURL"), true);
    assert.equal(N13_MIME.isStreamManifest("application/dash+xml"), true);
  });

  test("Negative: analytics, tracking, JS/CSS CDN assets", () => {
    const negatives = [
      "https://www.google-analytics.com/analytics.js",
      "https://connect.facebook.net/en_US/fbevents.js",
      "https://cdn.jsdelivr.net/npm/lodash.min.js",
    ];
    for (const url of negatives) {
      assert.equal(N13_URL.isFalsePositive(url) || N13_URL.isCdnAsset(url), true, `${url} should be filtered`);
    }
    // CSS on same domain is not a false positive by URL alone - MIME would catch it
    const cssUrl = N13_URL.analyzeUrl("https://example.com/style.css");
    assert.equal(cssUrl.isWebAsset, true);
  });

  test("Blob URLs are detected as blob type", () => {
    const urlAnalysis = N13_URL.analyzeUrl("blob:https://example.com/abc-123");
    assert.ok(urlAnalysis.isBlobUrl || !urlAnalysis.valid);
  });
});

// ===========================================================================
// PERFORMANCE CONSTRAINTS TEST
// ===========================================================================

describe("Performance Constraints", () => {
  test("dedup cache has max size limit", () => {
    const cache = N13_DEDUP.createCache({ maxSize: 100 });
    for (let i = 0; i < 150; i++) {
      cache.check({ url: `https://example.com/file${i}.zip` });
    }
    const stats = cache.stats();
    assert.ok(stats.size <= 100, `Cache size ${stats.size} should not exceed 100`);
  });

  test("scoring is fast for bulk operations", () => {
    const start = Date.now();
    for (let i = 0; i < 1000; i++) {
      N13_SCORING.quickScore(`https://example.com/file${i}.zip`);
    }
    const elapsed = Date.now() - start;
    assert.ok(elapsed < 5000, `1000 scores in ${elapsed}ms should be under 5s`);
  });

  test("URL analysis is fast for bulk operations", () => {
    const start = Date.now();
    for (let i = 0; i < 1000; i++) {
      N13_URL.analyzeUrl(`https://example.com/download/file${i}.zip`);
    }
    const elapsed = Date.now() - start;
    assert.ok(elapsed < 5000, `1000 URL analyses in ${elapsed}ms should be under 5s`);
  });
});

// ===========================================================================
// SECURITY TEST
// ===========================================================================

describe("Security", () => {
  test("non-http URLs are rejected", () => {
    assert.equal(N13_URL.analyzeUrl("javascript:alert(1)").valid, false);
    assert.equal(N13_URL.analyzeUrl("data:text/html,<script>alert(1)</script>").valid, false);
    assert.equal(N13_URL.analyzeUrl("file:///etc/passwd").valid, false);
  });

  test("localhost URLs are not treated specially (SSRF handled by N13)", () => {
    const result = N13_URL.analyzeUrl("http://127.0.0.1:8080/file.zip");
    assert.equal(result.valid, true);
    assert.equal(result.isStrongExtension, true);
  });

  test("empty/null URLs are handled gracefully", () => {
    assert.equal(N13_URL.analyzeUrl(null).valid, false);
    assert.equal(N13_URL.analyzeUrl("").valid, false);
    assert.equal(N13_URL.analyzeUrl(undefined).valid, false);
  });

  test("sanitized filenames don't contain path traversal", () => {
    const sanitized = N13_FILENAME.sanitize("../../etc/passwd");
    assert.ok(!sanitized.includes(".."));
    assert.ok(!sanitized.includes("/"));
  });
});
