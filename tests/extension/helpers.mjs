// Shared test helpers for the N13 extension JS tests (Node built-in runner).
//
// The extension's shared modules are plain IIFEs that attach to `globalThis`,
// so we evaluate them in global scope (indirect eval) after installing minimal
// browser mocks (document / location / window / chrome / fetch) on globalThis.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const SHARED_DIR = path.resolve(__dirname, "../../chrome_extension/shared");

/** Evaluate a shared module in global scope. Returns globalThis. */
export function loadScript(file, mocks = {}) {
  const code = fs.readFileSync(file, "utf8");
  const saved = {};
  for (const k of Object.keys(mocks)) {
    saved[k] = globalThis[k];
    globalThis[k] = mocks[k];
  }
  try {
    (0, eval)(code);
  } finally {
    for (const k of Object.keys(mocks)) {
      globalThis[k] = saved[k];
    }
  }
  return globalThis;
}

/** Load the detector with browser globals that persist for the suite. */
export function loadDetector(overrides = {}) {
  const base = {
    location: { href: "https://example.com/page/index.html" },
    document: makeDocument([]),
    window: { getSelection: () => emptySelection() },
  };
  const mocks = { ...base, ...overrides };
  for (const k of Object.keys(mocks)) globalThis[k] = mocks[k];
  const g = loadScript(path.join(SHARED_DIR, "download-detector.js"), {});
  return g;
}

/** Load the full detection engine with all modules. */
export function loadFullEngine(overrides = {}) {
  const base = {
    location: { href: "https://example.com/page/index.html" },
    document: makeDocument([]),
    window: { getSelection: () => emptySelection() },
  };
  const mocks = { ...base, ...overrides };
  for (const k of Object.keys(mocks)) globalThis[k] = mocks[k];

  // Load modules in dependency order
  loadScript(path.join(SHARED_DIR, "mime-analyzer.js"), {});
  loadScript(path.join(SHARED_DIR, "header-analyzer.js"), {});
  loadScript(path.join(SHARED_DIR, "filename-resolver.js"), {});
  loadScript(path.join(SHARED_DIR, "url-analyzer.js"), {});
  loadScript(path.join(SHARED_DIR, "deduplicator.js"), {});
  loadScript(path.join(SHARED_DIR, "scoring-engine.js"), {});
  loadScript(path.join(SHARED_DIR, "network-detector.js"), {});
  const g = loadScript(path.join(SHARED_DIR, "download-detector.js"), {});
  return g;
}

/** Load the bridge with chrome/fetch mocks that persist for the suite. */
export function loadBridge(overrides = {}) {
  const mocks = {
    chrome: {
      runtime: { getURL: (p) => "chrome-extension://test/" + p },
      tabs: { create: (o, cb) => cb && cb({ id: 1 }), remove: (id, cb) => cb && cb() },
    },
    fetch: async () => ({ status: 200, ok: true, text: async () => "{}" }),
    ...overrides,
  };
  for (const k of Object.keys(mocks)) globalThis[k] = mocks[k];
  const g = loadScript(path.join(SHARED_DIR, "n13-bridge.js"), {});
  return g;
}

// ---------------------------------------------------------------------------
// Minimal DOM mocks
// ---------------------------------------------------------------------------

export function makeAnchor(href, text, opts = {}) {
  const el = {
    tagName: "A",
    nodeType: 1,
    parentNode: null,
    textContent: text != null ? text : "",
    _attrs: { href },
    getAttribute(name) {
      if (name === "href") return this._attrs.href;
      if (name === "type") return this._attrs.type || null;
      if (name === "download") return this._attrs.download || null;
      if (name === "src") return this._attrs.src || null;
      return null;
    },
    hasAttribute(name) {
      if (name === "download") return !!opts.download;
      return false;
    },
  };
  if (opts.type) el._attrs.type = opts.type;
  if (opts.download) el._attrs.download = "";
  if (opts.src) el._attrs.src = opts.src;
  return el;
}

/** Create a mock video/audio element */
export function makeMediaElement(tag, src, type) {
  return {
    tagName: tag.toUpperCase(),
    nodeType: 1,
    textContent: "",
    _attrs: { src: src || "", type: type || "" },
    getAttribute(name) {
      return this._attrs[name] || null;
    },
    hasAttribute(name) {
      return name in this._attrs && this._attrs[name] !== null;
    },
  };
}

/** Create a mock object/embed element */
export function makeObjectElement(data, type) {
  return {
    tagName: "OBJECT",
    nodeType: 1,
    textContent: "",
    _attrs: { data: data || "", type: type || "" },
    getAttribute(name) {
      return this._attrs[name] || null;
    },
    hasAttribute(name) {
      return name in this._attrs;
    },
  };
}

export function makeFragment(anchors) {
  return {
    querySelectorAll(sel) {
      if (sel === "a[href]") return anchors;
      return [];
    },
  };
}

export function makeSelection(anchors, common = null) {
  const range = {
    commonAncestorContainer: common || { nodeType: 1, tagName: "DIV", parentNode: null, getAttribute: () => null },
    cloneContents() {
      return makeFragment(anchors);
    },
  };
  return {
    rangeCount: anchors.length ? 1 : 0,
    isCollapsed: anchors.length === 0,
    getRangeAt() { return range; },
  };
}

export function emptySelection() {
  return { rangeCount: 0, isCollapsed: true, getRangeAt: () => null };
}

export function makeDocument(anchors, extraSelectors = {}) {
  return {
    querySelectorAll(sel) {
      if (sel === "a[href]") return anchors;
      if (extraSelectors[sel]) return extraSelectors[sel];
      return [];
    },
  };
}
