// Static browser smoke test: load every frontend script in the exact order
// index.html uses, with minimal DOM/bridge mocks, and assert that the whole
// bundle evaluates without a load-time exception. This catches syntax errors,
// duplicate consts, and modules that touch the DOM too early.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(__dirname, "../../ui/frontend");
const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);

function el() {
  const node = {
    tagName: "DIV", dataset: {}, style: {}, hidden: false, textContent: "", innerHTML: "",
    value: "", disabled: false, checked: false, children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {},
    getAttribute: () => null, appendChild() {}, replaceChildren() {}, insertAdjacentHTML() {},
    querySelector: () => el(), querySelectorAll: () => [], focus() {}, remove() {},
    get scrollHeight() { return 0; }, get offsetWidth() { return 100; },
    get offsetHeight() { return 20; }, get offsetTop() { return 0; },
  };
  return node;
}

globalThis.console = console;
globalThis.document = {
  addEventListener() {}, readyState: "loading", hidden: false, activeElement: null,
  documentElement: { dataset: {}, style: { setProperty() {} }, setAttribute() {}, lang: "en", dir: "ltr" },
  body: el(), title: "",
  getElementById: () => el(), querySelector: () => el(), querySelectorAll: () => [],
  createElement: () => el(), createDocumentFragment: () => el(),
};
globalThis.window = globalThis;
globalThis.innerWidth = 1200;
globalThis.innerHeight = 800;
globalThis.screen = { availWidth: 1200, availHeight: 800 };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const nav = globalThis.navigator || {};
nav.clipboard = { writeText: async () => {}, readText: async () => "" };
globalThis.I18N = undefined;   // defined by i18n.js in the bundle
globalThis.Utils = undefined;
globalThis.API = undefined;

test("all frontend scripts load together without a runtime error", () => {
  assert.ok(scripts.length >= 20, "expected the full script list from index.html");
  const bundle = scripts.map((s) => fs.readFileSync(path.join(frontend, s), "utf8")).join("\n;\n");
  assert.doesNotThrow(() => {
    (0, eval)(bundle + "\n;({ Utils, I18N, API, App, Queue, Nav, ContextMenuUI, ToastUI })");
  });
});

test("the bundle exposes the public module surface", () => {
  const bundle = scripts.map((s) => fs.readFileSync(path.join(frontend, s), "utf8")).join("\n;\n");
  const surface = (0, eval)(bundle + "\n;({ Utils, I18N, API, App, Queue, Nav, ContextMenuUI, ToastUI })");
  for (const key of ["Utils", "I18N", "API", "App"]) {
    assert.ok(surface[key], `${key} must exist after load`);
  }
});
