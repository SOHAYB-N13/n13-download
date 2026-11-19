// Contrast guard for the two palettes.
//
// The 2026-11-19 audit found the light theme's tertiary text at 3.1:1 on
// white — the History table prints a row's duration and average speed in
// that colour, so real information was effectively unreadable.  Eyeballing a
// screenshot does not catch a 3.1:1 value; this test does.
//
// Every text token is checked against every surface it can land on, in both
// themes.  4.5:1 is the WCAG AA threshold for normal text, which is what all
// of these are (they are 11-13px).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CSS = path.resolve(__dirname, "../../ui/frontend/css/tokens.css");
const src = fs.readFileSync(CSS, "utf8");

/** Pull `--name: #hex` declarations out of one rule block. */
function block(selector) {
  const start = src.indexOf(selector);
  assert.notEqual(start, -1, `missing rule ${selector}`);
  const open = src.indexOf("{", start);
  const close = src.indexOf("}", open);
  const body = src.slice(open + 1, close);
  const out = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;/g)) {
    out[m[1]] = m[2];
  }
  return out;
}

function luminance(hex) {
  const ch = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = ch.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function ratio(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

// `:root` holds the dark palette; the light block only overrides some tokens,
// so it is layered on top rather than used alone.
const DARK = block(":root");
const LIGHT = { ...DARK, ...block('[data-theme="light"]') };
const THEMES = { dark: DARK, light: LIGHT };

const FOREGROUNDS = ["--text-1", "--text-2", "--text-3"];
const SURFACES = ["--bg-app", "--bg-secondary", "--bg-card", "--bg-elevated"];

for (const [name, tokens] of Object.entries(THEMES)) {
  test(`${name} theme: every text token clears 4.5:1 on every surface`, () => {
    const failures = [];
    for (const fg of FOREGROUNDS) {
      for (const bg of SURFACES) {
        const r = ratio(tokens[fg], tokens[bg]);
        if (r < 4.5) {
          failures.push(`${fg} ${tokens[fg]} on ${bg} ${tokens[bg]} = ${r.toFixed(2)}:1`);
        }
      }
    }
    assert.deepEqual(failures, [], `contrast below AA:\n  ${failures.join("\n  ")}`);
  });
}

test("the tertiary token stays visually distinct from the secondary one", () => {
  // The fix must not flatten the hierarchy into one grey.
  for (const [name, tokens] of Object.entries(THEMES)) {
    const d2 = luminance(tokens["--text-2"]);
    const d3 = luminance(tokens["--text-3"]);
    assert.ok(Math.abs(d2 - d3) > 0.02,
      `${name}: --text-2 and --text-3 are too close (${d2.toFixed(3)} vs ${d3.toFixed(3)})`);
  }
});
