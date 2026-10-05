import { test } from "node:test";
import assert from "node:assert/strict";
import { loadDetector, makeAnchor } from "./helpers.mjs";

const g = loadDetector();

const detected = (url, el) => g.N13_IS_DOWNLOAD(url, el);

test("strong file extensions are detected", () => {
  for (const ext of ["zip", "rar", "7z", "exe", "msi", "iso", "mp4"]) {
    assert.equal(detected(`https://example.com/f.${ext}`), true, `.${ext}`);
  }
});

test("download query param is detected", () => {
  assert.equal(detected("https://example.com/get?id=42&download=1"), true);
});

test("download.php route is detected", () => {
  assert.equal(detected("https://example.com/download.php?id=7"), true);
});

test("download route segments are detected", () => {
  for (const path of ["/download/x", "/downloads/x", "/dl/x", "/get/x", "/file/x"]) {
    assert.equal(detected(`https://example.com${path}`), true, path);
  }
});

test("download attribute is detected even without an extension", () => {
  const el = makeAnchor("https://example.com/generate/42", "Export", { download: true });
  assert.equal(detected("https://example.com/generate/42", el), true);
});

test("normal navigation links are NOT detected", () => {
  assert.equal(detected("https://example.com/about"), false);
  assert.equal(detected("https://example.com/docs/intro"), false);
});

test("social links are NOT detected", () => {
  assert.equal(detected("https://twitter.com/someone/status/123"), false);
  assert.equal(detected("https://www.facebook.com/somepage"), false);
});

test("\"Download\" text alone is NOT enough", () => {
  const el = makeAnchor("https://example.com/page", "Download");
  assert.equal(detected("https://example.com/page", el), false);
});

test("structured detection result has required fields", () => {
  const item = g.N13_DETECT_ITEM("https://example.com/f.zip", makeAnchor("https://example.com/f.zip", "file.zip"), "link");
  assert.equal(item.url, "https://example.com/f.zip");
  assert.ok(item.confidence >= 80);
  assert.equal(item.type, "download");
  assert.equal(item.source, "link");
  assert.ok(item.name);
});
