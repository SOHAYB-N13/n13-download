// Guards against one class name being owned by two different components.
//
// The "Scheduled downloads" panel renders its rows as `.sched-row` and lays
// them out as a grid.  The scheduler *dialog* used the same class for a form
// row laid out as `display: flex`.  Because rtl.css is imported after
// downloads.css and both rules have the same specificity, the dialog's flex
// rule silently won: the panel's status column drifted into the middle of the
// row and the actions wrapped onto a second line.  Nothing errored — it just
// looked wrong, which is exactly the kind of defect a unit test should own.
//
// The check below is deliberately narrow: only plain single-class selectors
// (`.foo { … }`) count, so legitimate contextual overrides such as
// `[dir="rtl"] .foo` or `.card .foo` are ignored.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CSS_DIR = path.resolve(__dirname, "../../ui/frontend/css");

function scan() {
  const owned = new Map();   // class -> [{ file, display }]
  for (const file of fs.readdirSync(CSS_DIR).filter((f) => f.endsWith(".css"))) {
    const src = fs.readFileSync(path.join(CSS_DIR, file), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = /(^|\})\s*\.([a-zA-Z][\w-]*)\s*\{([^}]*)\}/g;
    let m;
    while ((m = rule.exec(src))) {
      const display = (m[3].match(/display\s*:\s*([^;]+)/) || [])[1];
      if (!owned.has(m[2])) owned.set(m[2], []);
      owned.get(m[2]).push({ file, display: display && display.trim() });
    }
  }
  return owned;
}

test("no class is given two different display modes by two stylesheets", () => {
  const offenders = [];
  for (const [cls, defs] of scan()) {
    if (defs.length < 2) continue;
    const modes = new Set(defs.map((d) => d.display).filter(Boolean));
    if (modes.size > 1) {
      offenders.push(`.${cls} → ${defs.map((d) => `${d.file}: ${d.display || "(none)"}`).join(" | ")}`);
    }
  }
  assert.deepEqual(offenders, [],
    `the same class is laid out differently by different files:\n  ${offenders.join("\n  ")}`);
});

test("the scheduled-downloads list keeps its own row class", () => {
  const css = (f) => fs.readFileSync(path.join(CSS_DIR, f), "utf8");
  // The list owns `.sched-row` (a grid cell inside `.sched-rows`).
  assert.match(css("downloads.css"), /\.sched-rows\s*\{[^}]*display:\s*grid/);
  assert.match(css("downloads.css"), /\.sched-row\s*\{\s*display:\s*contents/);
  // The dialog must not reach for it again.
  assert.doesNotMatch(css("rtl.css"), /(^|\})\s*\.sched-row\s*\{/);
});
