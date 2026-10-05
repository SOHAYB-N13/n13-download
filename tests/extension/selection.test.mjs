import { test } from "node:test";
import assert from "node:assert/strict";
import { loadDetector, makeAnchor, makeSelection, makeDocument, emptySelection } from "./helpers.mjs";

// ---------------------------------------------------------------------------
// THE MOST IMPORTANT TEST: a page has 25 downloadable links, the user selects
// only 5 (Part 1..5).  The selection scan MUST return exactly 5, never 25.
// ---------------------------------------------------------------------------

test("25 links on page, select 5 -> returns exactly the 5 selected", () => {
  const parts = [];
  for (let i = 1; i <= 5; i++) {
    parts.push(makeAnchor(`https://example.com/files/part${i}.zip`, `Part ${i}`));
  }
  const unrelated = [];
  for (let i = 1; i <= 20; i++) {
    unrelated.push(makeAnchor(`https://example.com/other/file${i}.rar`, `Other ${i}`));
  }

  // Whole page contains all 25 anchors.
  const g = loadDetector({
    document: makeDocument([...parts, ...unrelated]),
    window: { getSelection: () => makeSelection(parts) },
    location: { href: "https://example.com/page/index.html" },
  });

  const result = g.N13_SCAN_SELECTION();
  const urls = result.urls.map((u) => u.url);

  assert.equal(result.count, 5);
  assert.equal(urls.length, 5);
  for (let i = 1; i <= 5; i++) {
    assert.ok(urls.includes(`https://example.com/files/part${i}.zip`), `part${i} present`);
  }
  for (let i = 1; i <= 20; i++) {
    assert.ok(!urls.includes(`https://example.com/other/file${i}.rar`), `unrelated file${i} absent`);
  }
});

test("selection is lenient: plain links (no extension) inside a selection are kept", () => {
  const parts = [];
  for (let i = 1; i <= 5; i++) {
    parts.push(makeAnchor(`https://example.com/parts/${i}`, `Part ${i}`));
  }
  const g = loadDetector({
    document: makeDocument(parts),
    window: { getSelection: () => makeSelection(parts) },
    location: { href: "https://example.com/page/index.html" },
  });
  const result = g.N13_SCAN_SELECTION();
  assert.equal(result.count, 5);
});

test("selection with nested/anchored text still resolves", () => {
  // An anchor that wraps a span (the selected range's cloneContents returns the
  // anchor via querySelectorAll, and the common-ancestor walk handles text).
  const a = makeAnchor("https://example.com/dl/thing.bin", "Thing");
  const g = loadDetector({
    document: makeDocument([a]),
    window: { getSelection: () => makeSelection([a]) },
    location: { href: "https://example.com/page/index.html" },
  });
  assert.equal(g.N13_SCAN_SELECTION().count, 1);
});

test("empty / collapsed selection returns nothing", () => {
  const g = loadDetector({
    document: makeDocument([makeAnchor("https://example.com/a.zip", "a")]),
    window: { getSelection: () => emptySelection() },
    location: { href: "https://example.com/page/index.html" },
  });
  const result = g.N13_SCAN_SELECTION();
  assert.equal(result.count, 0);
});

test("selection deduplicates repeated URLs", () => {
  const dupes = [
    makeAnchor("https://example.com/a.zip", "a"),
    makeAnchor("https://example.com/a.zip", "a again"),
    makeAnchor("https://example.com/b.zip", "b"),
  ];
  const g = loadDetector({
    document: makeDocument(dupes),
    window: { getSelection: () => makeSelection(dupes) },
    location: { href: "https://example.com/page/index.html" },
  });
  assert.equal(g.N13_SCAN_SELECTION().count, 2);
});

test("javascript:/mailto: links inside a selection are dropped", () => {
  const mixed = [
    makeAnchor("https://example.com/a.zip", "a"),
    makeAnchor("javascript:void(0)", "click"),
    makeAnchor("mailto:x@y.com", "mail"),
    makeAnchor("#anchor", "same-page"),
  ];
  const g = loadDetector({
    document: makeDocument(mixed),
    window: { getSelection: () => makeSelection(mixed) },
    location: { href: "https://example.com/page/index.html" },
  });
  const urls = g.N13_SCAN_SELECTION().urls.map((u) => u.url);
  assert.deepEqual(urls, ["https://example.com/a.zip"]);
});
