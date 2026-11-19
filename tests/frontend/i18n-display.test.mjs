// Regression tests for the localization / display defects found in the
// 2026-11-19 UX audit.
//
// Each case pins a defect that a user could see:
//
//  1. `statusLabel("Downloading")` had no entry in the key map, so the most
//     common state on screen stayed English in a Persian interface while every
//     neighbouring state was translated.
//  2. `formatDateTime` assumed a string and threw on anything else; because the
//     History table renders inside a single `map`, one bad row blanked the
//     whole page.
//  3. Settings pseudo-fields look up `set.<key without leading underscore>`.
//     Two of them pointed at keys that do not exist, so those rows silently
//     fell back to English. This test fails the moment a pseudo-field stops
//     matching its dictionary entry.
//  4. The scheduled row's time came from the backend already formatted in
//     English ("tomorrow 09:30"), which leaked into the Persian UI.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const JS = path.resolve(__dirname, "../../ui/frontend/js");

const read = (rel) => fs.readFileSync(path.join(JS, rel), "utf8");

// ── Shared stubs ────────────────────────────────────────────────────────────
let lang = "en";
const dict = new Map();
globalThis.I18N = {
  get lang() { return lang; },
  t(key, fallback) { return dict.has(key) ? dict.get(key) : (fallback !== undefined ? fallback : key); },
  fmt(key, vars, fallback) {
    let s = this.t(key, fallback);
    Object.entries(vars || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); });
    return s;
  },
};
globalThis.document = {
  documentElement: { dataset: {}, style: {} },
  body: { classList: { toggle() {} } },
  // `Utils.escapeHtml` goes through a detached div. Without it every renderer
  // test throws, so the stub implements the one behaviour that matters:
  // textContent in, entity-escaped innerHTML out.
  createElement() {
    return {
      _text: "",
      set textContent(v) { this._text = v == null ? "" : String(v); },
      get textContent() { return this._text; },
      get innerHTML() {
        return this._text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      },
    };
  },
};
globalThis.window = globalThis;

const Utils = (0, eval)(read("utils.js") + "\n;Utils");
// `scheduled-panel.js` is evaluated as a separate script, so the module-local
// `Utils` binding is not in scope for it. Publish it the way the browser does.
globalThis.Utils = Utils;
const ScheduledPanel = (0, eval)(read("features/scheduling/scheduled-panel.js") + "\n;ScheduledPanel");
const SettingsUI = (0, eval)(read("features/settings/settings.js") + "\n;SettingsUI");

// ── 1. status labels ────────────────────────────────────────────────────────
test("every task state has a translation key, including Downloading", () => {
  // The states the row renderer can display. If one is missing from the key
  // map it silently falls back to the English word.
  const states = [
    "Downloading", "Pausing", "Analyzing", "Starting", "Merging", "Verifying",
    "Paused", "Queued", "Complete", "Completed", "Failed", "Cancelled",
    "Stopped", "Stopping", "Removed",
  ];
  dict.clear();
  states.forEach((s) => dict.set(`status.${s.toLowerCase()}`, `T:${s}`));
  lang = "fa";
  for (const state of states) {
    assert.equal(Utils.statusLabel(state), `T:${state}`,
      `${state} is not translated — statusLabel fell back to English`);
  }
});

test("an unknown state still degrades to its own name", () => {
  lang = "fa";
  assert.equal(Utils.statusLabel("SomethingNew"), "SomethingNew");
});

// ── 2. date/time formatting ─────────────────────────────────────────────────
test("formatDateTime never throws on a non-string timestamp", () => {
  // History rows are rendered in one map pass; a throw here blanked the page.
  for (const bad of [1763500000, null, undefined, {}, [], true]) {
    assert.doesNotThrow(() => Utils.formatDateTime(bad));
    assert.equal(Utils.formatDateTime(bad), "");
  }
});

test("formatDateTime returns an unparseable string unchanged", () => {
  assert.equal(Utils.formatDateTime("not a date"), "not a date");
});

test("formatDateTime localizes the time and isolates it from RTL reordering", () => {
  const stamp = "2026-11-19 23:07:00";
  lang = "en";
  const en = Utils.formatDateTime(stamp);
  assert.ok(/\b23:07\b/.test(en), `expected a 24-hour clock, got ${en}`);
  // U+2066 LRI … U+2069 PDI keeps "23:07" from being reordered by RTL text.
  assert.ok(en.includes("\u2066") && en.includes("\u2069"), "time run is not bidi-isolated");

  lang = "fa";
  const fa = Utils.formatDateTime(stamp);
  assert.ok(fa.includes("\u2066"), "Persian time run is not bidi-isolated");
  assert.ok(!/AM|PM/.test(fa), `Persian should not use an AM/PM marker, got ${fa}`);
});

test("every formatted time is 24-hour, like the scheduler and the queue strip", () => {
  // The History table used to print "04:10 PM" while the scheduled-downloads
  // panel printed "19:09" in the same window.
  lang = "en";
  for (const [stamp, want] of [
    ["2026-11-19 13:05:00", "13:05"],
    ["2026-11-19 00:10:00", "00:10"],   // not "24:10"
    ["2026-11-19 23:59:00", "23:59"],
  ]) {
    const out = Utils.formatDateTime(stamp);
    assert.ok(out.includes(want), `expected ${want} in ${out}`);
    assert.ok(!/AM|PM/.test(out), `12-hour marker leaked into ${out}`);
  }
});

test("dateLocale follows the interface language, not the OS", () => {
  lang = "fa";
  assert.equal(Utils.dateLocale(), "fa-IR-u-nu-latn");
  lang = "en";
  assert.equal(Utils.dateLocale(), undefined);
});

// ── 3. settings pseudo-field keys resolve ───────────────────────────────────
test("every settings pseudo-field has a matching dictionary key", () => {
  const src = read("i18n.js");
  const keys = new Set(
    [...src.matchAll(/"((?:set|settings)\.[\w.]+)"\s*:/g)].map((m) => m[1]),
  );
  const missing = [];
  for (const section of SettingsUI._settingsDef()) {
    for (const f of section.fields || []) {
      if (!f.key.startsWith("_") || !f.label) continue;   // only labelled pseudo-fields
      const tkey = f.key.replace(/^_/, "");
      if (!keys.has(`set.${tkey}`)) missing.push(`${f.key} (needs set.${tkey})`);
      if (f.hint && !keys.has(`set.${tkey}.hint`)) missing.push(`${f.key} (needs set.${tkey}.hint)`);
    }
  }
  assert.deepEqual(missing, [], `pseudo-fields without a translation:\n  ${missing.join("\n  ")}`);
});

test("the two previously-mismatched rows now resolve", () => {
  const src = read("i18n.js");
  for (const key of ["set.category_extensions", "set.limit_download_speed", "set.devtools"]) {
    assert.ok(src.includes(`"${key}"`), `${key} missing from the English dictionary`);
  }
});

// ── 4. scheduled time is built locally ──────────────────────────────────────
test("a scheduled row uses a localized day word and a 24h clock", () => {
  dict.clear();
  dict.set("sched.task.tomorrow", "فردا");
  const t = (k, f) => I18N.t(k, f);

  const now = Math.floor(new Date(2026, 10, 19, 10, 0, 0).getTime() / 1000);
  const laterToday = Math.floor(new Date(2026, 10, 19, 13, 57, 0).getTime() / 1000);
  const tomorrow = Math.floor(new Date(2026, 10, 20, 9, 30, 0).getTime() / 1000);

  assert.equal(ScheduledPanel._whenText({ due_at: laterToday }, now, t), "13:57");
  assert.equal(ScheduledPanel._whenText({ due_at: tomorrow }, now, t), "فردا 09:30");
});

test("a row without a usable timestamp falls back to the backend string", () => {
  const t = (k, f) => I18N.t(k, f);
  assert.equal(
    ScheduledPanel._whenText({ due_at: 0, local_time: "02:00" }, 1000, t),
    "02:00",
  );
  assert.equal(
    ScheduledPanel._whenText({ local_time: "whenever" }, 1000, t),
    "whenever",
  );
});

// ── 5. countdown never prints four-digit minutes ────────────────────────────
test("a countdown rolls minutes into hours and days", () => {
  // The old formatter was `Math.floor(s / 60)` + "m", so anything booked for
  // tomorrow read "1439m 59s" and the user had to divide by 60 to understand it.
  assert.equal(ScheduledPanel._duration(119), "1m 59s");
  assert.equal(ScheduledPanel._duration(7199), "1h 59m");
  assert.equal(ScheduledPanel._duration(86399), "23h 59m");
  assert.equal(ScheduledPanel._duration(90000), "1d 1h");
});

test("a scheduled row renders the rolled-up countdown, not raw minutes", () => {
  dict.clear();
  const t = (k, f) => I18N.t(k, f);
  const now = Math.floor(new Date(2026, 10, 19, 10, 0, 0).getTime() / 1000);
  const html = ScheduledPanel._row(
    { id: "s1", status: "scheduled", due_at: now + 7199, payload: { label: "x.tar.gz" } },
    now, t,
  );
  assert.ok(html.includes("1h 59m"), `countdown not rolled up: ${html}`);
  assert.ok(!/\b119m\b/.test(html), "countdown still reports raw minutes");
});

