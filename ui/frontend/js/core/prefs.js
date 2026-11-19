/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Core: Preferences (theme / accent / sidebar)
   ═══════════════════════════════════════════════════════════════════════════

   Owns everything around UI preferences: local persistence ("n13-prefs" in
   localStorage), backend persistence (API.saveThemeConfig), and applying the
   theme/accent CSS variables.

   Extracted verbatim from app.js (Phase 2 modularization).  Every function
   takes the owning `app` object so the module keeps no import-time coupling;
   `App` still exposes the original method names as thin delegates, so tests
   and other modules see no difference.

   Load order: this file must load before app.js (it is referenced at call
   time only, but loading first keeps the core layer below the features).
   ═══════════════════════════════════════════════════════════════════════════ */

/* global API, Utils */

const Prefs = {
  /** localStorage key shared with the pre-paint snippet in index.html. */
  KEY: "n13-prefs",

  /** Read persisted prefs into app.state (localStorage only, never throws). */
  loadLocal(app) {
    try {
      const p = JSON.parse(localStorage.getItem(this.KEY) || "{}");
      if (p.theme) app.state.theme = p.theme;
      if (p.accent) app.state.accent = p.accent;
      if (typeof p.sidebarCollapsed === "boolean") app.state.sidebarCollapsed = p.sidebarCollapsed;
    } catch {}
  },

  /** Persist to localStorage AND the backend theme config. */
  save(app) {
    const prefs = {
      theme: app.state.theme,
      accent: app.state.accent,
      sidebarCollapsed: app.state.sidebarCollapsed,
      // Mirrored for the pre-paint script only; the backend setting stays
      // authoritative. This prevents an RTL flash on the next launch.
      lang: (app.state.settings && app.state.settings.language) || "en",
    };
    localStorage.setItem(this.KEY, JSON.stringify(prefs));
    API.saveThemeConfig(prefs).catch(() => {});
  },

  /** Persist to localStorage only (used while applying at boot). */
  saveLocalOnly(app) {
    localStorage.setItem(this.KEY, JSON.stringify({
      theme: app.state.theme,
      accent: app.state.accent,
      sidebarCollapsed: app.state.sidebarCollapsed,
      lang: (app.state.settings && app.state.settings.language) || "en",
    }));
  },

  /** Push theme + accent into the document as data-attribute / CSS variables. */
  apply(app) {
    document.documentElement.dataset.theme = app.state.theme;
    const root = document.documentElement.style;
    const accent = this.normalizeAccent(app.state.accent);
    // A stored accent that is not a hex colour (an empty value from a partial
    // config, a name left by another version, a hand-edited file) used to be
    // written straight into --accent.  Every `linear-gradient(... var(--accent))`
    // then became invalid at computed-value time and fell back to `none`, so the
    // primary "New download" button rendered as white text on a transparent
    // background — invisible.  Ignoring an unusable value leaves the stylesheet
    // defaults in place, which always render.
    if (!accent) {
      this.saveLocalOnly(app);
      return;
    }
    // A very light custom accent is unreadable as text on white cards. Keep
    // the user's hue but darken it only as far as needed for AA contrast in
    // the light theme; dark theme keeps their exact choice.
    const resolved = app.state.theme === "light"
      ? this.ensureTextContrast(accent)
      : accent;
    root.setProperty("--accent", resolved);
    // "Hi" is the light end of the primary-button gradient.  On a dark theme
    // that is a lighter tint; on a light theme a lighter tint puts white button
    // text at ~2.6:1, so the highlight has to go the other way.
    root.setProperty(
      "--accent-hi",
      app.state.theme === "light" ? this.shade(resolved, 0.18) : this.mix(resolved, 0.28),
    );
    root.setProperty("--accent-soft", this.alpha(resolved, 0.14));
    root.setProperty("--accent-ring", this.alpha(resolved, 0.35));
    this.saveLocalOnly(app);
  },

  /** ``#rgb``/``#rrggbb`` as a normalised ``#rrggbb``, or ``""`` when unusable. */
  normalizeAccent(value) {
    const text = String(value == null ? "" : value).trim();
    if (/^#[0-9a-f]{6}$/i.test(text)) return text;
    if (/^#[0-9a-f]{3}$/i.test(text)) {
      return "#" + text.slice(1).split("").map((c) => c + c).join("");
    }
    return "";
  },

  /** Relative luminance (WCAG) of a #rrggbb colour. */
  luminance(hex) {
    const [r, g, b] = this.hexToRgb(hex).map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  },

  /** Darken a light accent until it reads against a white card (>= 4.5:1). */
  ensureTextContrast(hex) {
    let [r, g, b] = this.hexToRgb(hex);
    // (L + 0.05) / 0.05 >= 4.5  =>  L <= 0.175
    const toHex = () => "#" + [r, g, b]
      .map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
    let guard = 0;
    while (this.luminance(toHex()) > 0.175 && guard++ < 40) {
      r = Math.max(0, r - 8);
      g = Math.max(0, g - 8);
      b = Math.max(0, b - 8);
    }
    return toHex();
  },

  hexToRgb(hex) {
    const h = hex.replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  },

  alpha(hex, a) {
    const [r, g, b] = this.hexToRgb(hex);
    return `rgba(${r},${g},${b},${a})`;
  },

  mix(hex, amt) {
    const [r, g, b] = this.hexToRgb(hex);
    const m = (c) => Math.round(c + (255 - c) * amt);
    return `#${[m(r), m(g), m(b)].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
  },

  /** Darken *hex* toward black by *amt* (0..1). */
  shade(hex, amt) {
    const [r, g, b] = this.hexToRgb(hex);
    const s = (c) => Math.round(c * (1 - amt));
    return `#${[s(r), s(g), s(b)].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
  },

  toggleTheme(app) {
    app.state.theme = app.state.theme === "dark" ? "light" : "dark";
    this.apply(app);
    this.save(app);
    if (app._spark) app._spark.redraw();
  },

  setAccent(app, color) {
    app.state.accent = color;
    this.apply(app);
    this.save(app);
    if (app._spark) app._spark.redraw();
  },
};
