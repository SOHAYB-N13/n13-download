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
    }));
  },

  /** Push theme + accent into the document as data-attribute / CSS variables. */
  apply(app) {
    document.documentElement.dataset.theme = app.state.theme;
    const root = document.documentElement.style;
    root.setProperty("--accent", app.state.accent);
    root.setProperty("--accent-hi", this.mix(app.state.accent, 0.28));
    root.setProperty("--accent-soft", this.alpha(app.state.accent, 0.14));
    root.setProperty("--accent-ring", this.alpha(app.state.accent, 0.35));
    this.saveLocalOnly(app);
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
