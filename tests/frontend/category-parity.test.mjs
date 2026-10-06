// The frontend and the backend must agree on one thing above all others: which
// category a file belongs to.
//
// This is not a cosmetic label.  The New-download dialog uses the category to
// choose the destination folder, the Downloads strip uses it to filter, and
// History prints it as a pill.  When the two sides disagreed, the failure was
// silent and confusing:
//
//   * the dialog offered a chip ("Compressed") the backend could never emit, so
//     a `.rar` download arrived with *no* chip highlighted while its folder
//     changed to `…\Archives` behind the user's back;
//   * an `.iso` was offered as "Programs" and then recorded as "Archives" —
//     the same file described two different ways in one app.
//
// Both had the same root cause: a second, hand-maintained copy of the
// extension→category table.  These tests pin the three copies that remain
// (Python's `DEFAULT_CATEGORY_EXTENSIONS`, `Utils.CATEGORY_EXTENSIONS`, and the
// display order used by the two views) against each other, so the next edit to
// any one of them fails here instead of shipping.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const UTILS_JS = path.join(ROOT, "ui/frontend/js/utils.js");
const VIEW_JS = path.join(ROOT, "ui/frontend/js/features/downloads/downloads-view.js");
const ANALYZER_PY = path.join(ROOT, "core/analyzer.py");

globalThis.window = globalThis;
globalThis.document = { createElement: () => ({ innerHTML: "" }), querySelectorAll: () => [] };
globalThis.API = { logJs() {} };
globalThis.I18N = { t: (k, f) => (f !== undefined ? f : k), fmt: (k, v, f) => f, onChange() {} };
globalThis.Components = {};
globalThis.Utils = {};
globalThis.console = console;

const loadUtils = () => (0, eval)(fs.readFileSync(UTILS_JS, "utf8") + "\n;Utils");
const loadView = () => (0, eval)(fs.readFileSync(VIEW_JS, "utf8") + "\n;DownloadsView");

/**
 * Read the backend's extension table straight out of `core/analyzer.py`.
 *
 * Parsed rather than imported because the whole point is to compare two
 * independent sources; importing a Python mirror of the JS table would only
 * prove the mirror equals itself.
 */
function backendTable() {
  const src = fs.readFileSync(ANALYZER_PY, "utf8");
  const block = src.match(/DEFAULT_CATEGORY_EXTENSIONS\s*=\s*\{([\s\S]*?)\n\}/);
  assert.ok(block, "DEFAULT_CATEGORY_EXTENSIONS not found in core/analyzer.py");
  const table = {};
  for (const m of block[1].matchAll(/"([A-Za-z]+)"\s*:\s*\[([^\]]*)\]/g)) {
    table[m[1]] = [...m[2].matchAll(/"([^"]+)"/g)].map((e) => e[1]);
  }
  return table;
}

const Utils = loadUtils();
const DownloadsView = loadView();
const BACKEND = backendTable();

// ── The vocabulary itself ───────────────────────────────────────────────────

test("the backend table parses into the seven categories it documents", () => {
  assert.deepEqual(Object.keys(BACKEND).sort(),
    ["Archives", "Documents", "Images", "Music", "Other", "Programs", "Videos"]);
});

test("every backend category appears exactly once in the frontend table", () => {
  const js = Object.keys(Utils.CATEGORY_EXTENSIONS).sort();
  assert.deepEqual(js, Object.keys(BACKEND).sort());
});

test("the extension lists are identical on both sides", () => {
  for (const [cat, exts] of Object.entries(BACKEND)) {
    assert.deepEqual(
      [...Utils.CATEGORY_EXTENSIONS[cat]].sort(),
      [...exts].sort(),
      `extension list for ${cat} differs between analyzer.py and utils.js`,
    );
  }
});

test("no extension is claimed by two categories on either side", () => {
  for (const [label, table] of [["python", BACKEND], ["js", Utils.CATEGORY_EXTENSIONS]]) {
    const seen = new Map();
    for (const [cat, exts] of Object.entries(table)) {
      for (const e of exts) {
        assert.equal(seen.has(e), false,
          `${label}: .${e} is listed under both ${seen.get(e)} and ${cat}`);
        seen.set(e, cat);
      }
    }
  }
});

// ── Display order ───────────────────────────────────────────────────────────

test("the two views share one display order", () => {
  assert.deepEqual(DownloadsView.CAT_ORDER, Utils.CATEGORY_ORDER);
});

test("the display order covers every real category, General first", () => {
  assert.equal(Utils.CATEGORY_ORDER[0], "General");
  const shown = Utils.CATEGORY_ORDER.filter((c) => c !== "General").sort();
  assert.deepEqual(shown, Object.keys(BACKEND).sort());
  assert.equal(new Set(Utils.CATEGORY_ORDER).size, Utils.CATEGORY_ORDER.length, "duplicate entry");
});

// ── categoryFor agrees with detect_category, extension by extension ─────────

test("categoryFor matches the backend for every single extension", () => {
  for (const [cat, exts] of Object.entries(BACKEND)) {
    for (const ext of exts) {
      assert.equal(Utils.categoryFor(`file.${ext}`), cat,
        `.${ext} should be ${cat}`);
    }
  }
});

test("categoryFor falls back to General, not to a category", () => {
  // `General` is the backend's "we could not tell" bucket, and it is also the
  // download root rather than a subfolder — so an unknown file must never be
  // filed under a real category.
  assert.equal(Utils.categoryFor("noextension"), "General");
  assert.equal(Utils.categoryFor("archive.xyzzy"), "General");
  assert.equal(Utils.categoryFor(""), "General");
  assert.equal(Utils.categoryFor(null), "General");
  assert.equal(Utils.categoryFor(undefined), "General");
});

test("categoryFor is case-insensitive, like the backend", () => {
  assert.equal(Utils.categoryFor("PACK.RAR"), "Archives");
  assert.equal(Utils.categoryFor("Movie.MKV"), "Videos");
});

test("the regressions that motivated this file stay fixed", () => {
  // A disc image is an archive, not a program — the dialog and History used to
  // disagree about this exact file.
  assert.equal(Utils.categoryFor("ubuntu.iso"), "Archives");
  assert.equal(Utils.categoryFor("disk.img"), "Other");
  // Archives must resolve to a category the dialog actually offers.
  assert.equal(Utils.categoryFor("pack.rar"), "Archives");
  assert.ok(Utils.CATEGORY_ORDER.includes(Utils.categoryFor("pack.rar")));
  assert.ok(Utils.CATEGORY_ORDER.includes(Utils.categoryFor("ubuntu.iso")));
  assert.ok(Utils.CATEGORY_ORDER.includes(Utils.categoryFor("disk.img")));
});

test("every category categoryFor can return is offered as a chip", () => {
  const reachable = new Set(Object.values(BACKEND).map((exts) => Utils.categoryFor(`f.${exts[0]}`)));
  reachable.add("General");
  for (const cat of reachable) {
    assert.ok(Utils.CATEGORY_ORDER.includes(cat),
      `categoryFor can return "${cat}" but the dialog has no chip for it`);
  }
});
