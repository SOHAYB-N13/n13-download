// Focused tests for accent handling in prefs.js (theme application).
//
// A stored accent that is not a hex colour used to be written straight into
// `--accent`.  Every `linear-gradient(135deg, var(--accent), ...)` then became
// invalid at computed-value time and fell back to `none`, so `.cmd-primary`
// rendered as white text on a transparent background — the primary "New
// download" button was invisible, in both themes, with no error anywhere.
//
// These tests pin the guard: only a usable colour is applied, anything else
// leaves the stylesheet defaults in place, and the light theme darkens the
// gradient highlight instead of lightening it (white text on a light tint is
// ~2.6:1).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PREFS_JS = path.resolve(__dirname, "../../ui/frontend/js/core/prefs.js");

// Minimal document stub: apply() writes CSS custom properties onto the root
// element's inline style, so the test can read back exactly what was set.
function makeRoot() {
  const props = new Map();
  return {
    props,
    style: {
      setProperty(name, value) {
        props.set(name, value);
      },
    },
  };
}

globalThis.window = globalThis;
globalThis.localStorage = {
  _data: new Map(),
  getItem(k) {
    return this._data.has(k) ? this._data.get(k) : null;
  },
  setItem(k, v) {
    this._data.set(k, String(v));
  },
};
globalThis.API = { saveThemeConfig() {} };
globalThis.Utils = {};

const root = makeRoot();
globalThis.document = { documentElement: { dataset: {}, style: root.style } };

const code = fs.readFileSync(PREFS_JS, "utf8");
const Prefs = (0, eval)(code + "\n;Prefs");

function applyWith(theme, accent) {
  root.props.clear();
  const app = { state: { theme, accent, sidebarCollapsed: false } };
  Prefs.apply(app);
  return root.props;
}

test("normalizeAccent accepts 6- and 3-digit hex and normalises case-insensitively", () => {
  assert.equal(Prefs.normalizeAccent("#EF4444"), "#EF4444");
  assert.equal(Prefs.normalizeAccent("#ef4444"), "#ef4444");
  assert.equal(Prefs.normalizeAccent("#f00"), "#ff0000");
  assert.equal(Prefs.normalizeAccent("  #B91C1C  "), "#B91C1C");
});

test("normalizeAccent rejects anything that is not a hex colour", () => {
  for (const bad of ["", null, undefined, "indigo", "red", "#12", "#12345", "rgb(1,2,3)", "#GGGGGG"]) {
    assert.equal(Prefs.normalizeAccent(bad), "", `should reject ${JSON.stringify(bad)}`);
  }
});

test("an unusable accent leaves the stylesheet defaults in place", () => {
  // The regression: these used to be written through, producing an invalid
  // --accent and therefore a transparent primary button.
  for (const bad of ["indigo", "", null, "#12345"]) {
    const props = applyWith("dark", bad);
    assert.equal(props.has("--accent"), false, `wrote --accent for ${JSON.stringify(bad)}`);
    assert.equal(props.has("--accent-hi"), false);
  }
});

test("a valid accent is applied as a resolvable colour", () => {
  const props = applyWith("dark", "#EF4444");
  assert.equal(props.get("--accent"), "#EF4444");
  for (const key of ["--accent-hi", "--accent-soft", "--accent-ring"]) {
    assert.match(props.get(key), /^#[0-9a-f]{6}$|^rgba\(/, key);
  }
});

test("light theme darkens the gradient highlight instead of lightening it", () => {
  // `--accent-hi` is the light end of the primary-button gradient.  White text
  // on a lightened tint is ~2.6:1, so in light theme it must be darker than
  // --accent, not lighter.
  const props = applyWith("light", "#EF4444");
  const accent = props.get("--accent");
  const hi = props.get("--accent-hi");
  assert.match(hi, /^#[0-9a-f]{6}$/);
  assert.notEqual(hi, accent);
  const lum = (hex) => {
    const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  assert.ok(lum(hi) < lum(accent), `${hi} should be darker than ${accent}`);
  // White text must stay readable on both ends of the gradient.
  for (const hex of [accent, hi]) {
    const contrast = 1.05 / (lum(hex) + 0.05);
    assert.ok(contrast >= 4.5, `${hex} gives only ${contrast.toFixed(2)}:1 with white`);
  }
});

test("dark theme keeps the user's exact accent", () => {
  const props = applyWith("dark", "#EF4444");
  assert.equal(props.get("--accent"), "#EF4444");
});
