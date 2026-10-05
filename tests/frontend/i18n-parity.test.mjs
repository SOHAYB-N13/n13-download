// i18n parity guard for ui/frontend/js/i18n.js.
//
// The app ships two locales (en, fa) in one file. Nothing at runtime notices a
// key that exists only in `en` — the lookup silently falls back to English, so
// a missing Persian string looks like "the translation is just English here"
// rather than a bug. This test makes that class of mistake loud.
//
// Duplicate keys are checked too: a repeated key inside one dict is valid
// JavaScript and silently wins with its last value, which makes an edit look
// applied when it was actually overwritten.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const I18N_JS = path.resolve(__dirname, "../../ui/frontend/js/i18n.js");
const src = fs.readFileSync(I18N_JS, "utf8");

// Evaluate just the I18N object literal. The parentheses matter: a bare
// `{...}` is parsed as a block statement, not an object.
const start = src.indexOf("const I18N = {");
assert.ok(start !== -1, "could not find `const I18N = {` in i18n.js");
const body = src.slice(start);
const literal = body.slice(body.indexOf("{"), body.lastIndexOf("};") + 1);
const I18N = eval("(" + literal + ")");

const en = Object.keys(I18N.dict.en);
const fa = Object.keys(I18N.dict.fa);

test("both locales define the same keys", () => {
  const missingFa = en.filter((k) => !(k in I18N.dict.fa));
  const extraFa = fa.filter((k) => !(k in I18N.dict.en));
  assert.deepEqual(missingFa, [], "keys present in en but missing from fa");
  assert.deepEqual(extraFa, [], "keys present in fa but missing from en");
});

test("no locale has an empty value", () => {
  const empty = [
    ...en.filter((k) => !String(I18N.dict.en[k]).trim()).map((k) => `en:${k}`),
    ...fa.filter((k) => !String(I18N.dict.fa[k]).trim()).map((k) => `fa:${k}`),
  ];
  assert.deepEqual(empty, []);
});

test("no key is declared twice inside one locale", () => {
  const enBlock = src.slice(src.indexOf("en: {"), src.indexOf("fa: {"));
  const faBlock = src.slice(src.indexOf("fa: {"));
  const occurrences = (block, k) =>
    (block.match(new RegExp('"' + k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + '"\\s*:', "g")) || []).length;
  const dupes = en.filter((k) => occurrences(enBlock, k) > 1 || occurrences(faBlock, k) > 1);
  assert.deepEqual(dupes, []);
});

test("lookup falls back to en and then to the caller's fallback", () => {
  // Mirrors I18N.t's documented behaviour, so the fallbacks used throughout
  // the frontend can be relied on.
  const t = (lang, key, fallback) => {
    const dict = I18N.dict[lang] || {};
    if (dict[key] !== undefined) return dict[key];
    if (lang !== "en" && I18N.dict.en[key] !== undefined) return I18N.dict.en[key];
    return fallback !== undefined ? fallback : key;
  };
  assert.equal(t("en", "nav.downloads"), "Downloads");
  assert.equal(t("fa", "nav.downloads"), "دانلودها");
  assert.equal(t("fa", "does.not.exist", "Fallback"), "Fallback");
  assert.equal(t("en", "does.not.exist"), "does.not.exist");
});

test("the queue page's strings exist in both locales", () => {
  // The Queue page is rendered entirely from JS, so a missing key there shows
  // up as untranslated English text rather than a visible error.
  const required = en.filter((k) => k.startsWith("queue."));
  assert.ok(required.length > 50, `expected a substantial queue vocabulary, got ${required.length}`);
  required.forEach((k) => {
    assert.ok(k in I18N.dict.fa, `queue key missing from fa: ${k}`);
    assert.ok(String(I18N.dict.fa[k]).trim().length > 0, `empty fa value: ${k}`);
  });
});
