/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Feature: Settings
   ═══════════════════════════════════════════════════════════════════════════

   Settings page: schema definition, section rail, field renderers, the
   accumulating debounced save pipeline, update panel, and the rules
   manager.  Extracted verbatim from app.js (Phase 7) — all functions now
   receive the orchestrating `App` as their first parameter, and the
   `App._*` delegates keep the original call surface for callers and tests.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, API, I18N, Components */

const SettingsUI = {

/**
 * Settings schema, organised into the eight top-level groups shown in the
 * section rail.  Every field from the previous flat layout is preserved —
 * only the grouping changed — so no setting is lost in the redesign.
 */
_settingsDef() {
  const st = (key, fallback) => I18N.t("settings." + key, fallback);
  return [
    // ── General ───────────────────────────────────────────────────
    {
      id: "appearance", group: "general", icon: "sun", title: st("appearance", "Appearance"),
      fields: [
        { key: "_theme", label: "Theme", hint: "Interface color scheme", type: "theme" },
        { key: "_accent", label: "Accent color", hint: "Used for buttons, links and progress", type: "accent" },
      ],
    },
    {
      id: "general", group: "general", icon: "download", title: st("general", "General"),
      fields: [
        { key: "download_dir", label: "Download folder", hint: "Default location for new files", type: "dir" },
        { key: "duplicate_policy", label: "Duplicate handling", hint: "What to do when a URL or file already exists", type: "select", options: [
          { value: "ask", label: "Ask every time" },
          { value: "allow", label: "Allow duplicates" },
          { value: "rename", label: "Rename automatically" },
          { value: "replace", label: "Replace existing" },
        ] },
        { key: "language", label: "Language", hint: "Interface language", type: "select", options: [
          { value: "en", label: "English" },
          { value: "fa", label: "فارسی (Persian)" },
        ] },
      ],
    },
    {
      id: "startup", group: "general", icon: "power", title: st("startup", "Startup & clipboard"),
      fields: [
        { key: "resume_on_startup", label: "Resume on startup", hint: "Automatically continue downloads that were interrupted", type: "toggle" },
        { key: "start_minimized", label: "Start minimized", hint: "Launch the window minimized", type: "toggle" },
        { key: "minimize_to_tray", label: "Minimize to tray", hint: "Minimizing hides N13 to the system tray", type: "toggle" },
        { key: "close_to_tray", label: "Close to tray", hint: "Closing the window keeps N13 running in the tray", type: "toggle" },
        { key: "clipboard_monitor", label: "Clipboard monitoring", hint: "Offer to download URLs you copy", type: "toggle" },
        { key: "clipboard_autostart", label: "Auto-download copied links", hint: "Start the download without asking (when monitoring is on)", type: "toggle" },
      ],
    },

    // ── Downloads ─────────────────────────────────────────────────
    {
      id: "downloads", group: "downloads", icon: "sliders", title: st("downloads", "Download behaviour"),
      fields: [
        { key: "max_concurrent", label: "Simultaneous downloads", hint: "How many files download at once", type: "range", min: 1, max: 10 },
        { key: "num_threads", label: "Connections per download", hint: "Parallel segments per file (higher = faster on good networks)", type: "range", min: 1, max: 64 },
        { key: "connection_mode", label: "Connection mode", hint: "Smart picks the connection count automatically; Manual uses your fixed value", type: "select", options: [
          { value: "smart", label: "Smart (recommended)" },
          { value: "manual", label: "Manual" },
        ] },
        { key: "smart_max_connections", label: "Smart max connections", hint: "Ceiling Smart mode may use", type: "range", min: 1, max: 32 },
        { key: "smart_adaptive", label: "Adaptive scaling", hint: "Gradually increase connections while the server stays stable", type: "toggle" },
      ],
    },
    {
      id: "categories", group: "downloads", icon: "folder", title: st("categories", "Categories"),
      fields: [
        { key: "auto_categorize", label: "Auto-detect category", hint: "Assign a category from the file type", type: "toggle" },
        { key: "_category_dirs", label: "Category folders", hint: "Save each category to its own folder", type: "catdirs" },
        { key: "_category_exts", label: "Custom extensions", hint: "Extra extensions per category (advanced)", type: "catexts" },
      ],
    },
    {
      id: "rules", group: "downloads", icon: "link", title: st("rules", "Download Rules"),
      fields: [
        { key: "rules_enabled", label: "Enable rules", hint: "Automatically set category, folder and priority for new downloads", type: "toggle" },
        { key: "_rules_manager", label: "Rules", hint: "Match by extension, domain, URL text, size, or MIME type", type: "rules" },
      ],
    },
    {
      id: "scheduler", group: "downloads", icon: "calendar", title: st("scheduler", "Scheduler"),
      fields: [
        { key: "scheduler_enabled", label: "Enable scheduler", hint: "Gate the queue by time of day and apply a night speed cap", type: "toggle" },
        { key: "schedule_start_time", label: "Start at", hint: "Queue stays paused until this time (HH:MM)", type: "time" },
        { key: "schedule_stop_time", label: "Stop at", hint: "Queue pauses from this time (HH:MM)", type: "time" },
        { key: "schedule_days", label: "Active days", hint: "Days the window above applies to. None selected means every day.", type: "days", wide: true },
        { key: "_night_cap_enabled", label: "Night speed limit", hint: "Slow downloads during the night window", type: "toggle", of: "night_speed_limit_bps" },
        { key: "night_speed_limit_bps", label: "Night limit", hint: "Applied between night start and night end", type: "speed" },
        { key: "night_start_time", label: "Night starts at", hint: "e.g. 23:00", type: "time" },
        { key: "night_end_time", label: "Night ends at", hint: "e.g. 07:00", type: "time" },
      ],
    },

    // ── Connection ────────────────────────────────────────────────
    {
      id: "network", group: "connection", icon: "wifi", title: st("network", "Network"),
      fields: [
        { key: "proxy_url", label: "Proxy", hint: "e.g. http://127.0.0.1:8080 — empty disables", type: "text", placeholder: "http://host:port" },
        { key: "proxy_username", label: "Proxy username", hint: "", type: "text" },
        { key: "proxy_password", label: "Proxy password", hint: "", type: "password" },
        { key: "user_agent", label: "User agent", hint: "Sent with every request. Some hosts require a real browser string.", type: "uatext", wide: true },
      ],
    },
    {
      id: "reliability", group: "connection", icon: "retry", title: st("reliability", "Reliability"),
      fields: [
        { key: "max_retries", label: "Retry attempts", hint: "Times a failed segment is retried", type: "range", min: 0, max: 20 },
        { key: "retry_delay", label: "Retry delay", hint: "Base wait between attempts, in seconds", type: "range", min: 1, max: 60 },
        { key: "verify_ssl", label: "Verify SSL certificates", hint: "Keep enabled unless you know what you're doing", type: "toggle" },
        { key: "verify_size", label: "Verify file size", hint: "Check the saved file matches the server size", type: "toggle" },
        { key: "block_private_urls", label: "Block private addresses", hint: "Prevents downloads from local network targets (SSRF protection)", type: "toggle" },
      ],
    },

    // ── Speed ─────────────────────────────────────────────────────
    {
      id: "bandwidth", group: "speed", icon: "gauge", title: st("bandwidth", "Bandwidth"),
      fields: [
        { key: "_limit_enabled", label: "Limit download speed", hint: "Cap the total bandwidth N13 may use", type: "toggle", of: "max_speed_bps" },
        { key: "max_speed_bps", label: "Speed limit", hint: "Applies to all downloads combined", type: "speed" },
        { key: "_speed_presets", label: "Quick presets", hint: "256 KB/s · 512 KB/s · 1 MB/s · 2 MB/s · 5 MB/s · 10 MB/s", type: "speedpresets" },
      ],
    },

    // ── Browser Integration ───────────────────────────────────────
    {
      id: "integration", group: "browser", icon: "browser", title: st("integration", "Browser integration"),
      fields: [
        { key: "live_server_port", label: "Extension server port", hint: "Restart the live server after changing", type: "number", min: 1024, max: 65535 },
        { key: "_server_link", label: "Live server", hint: "Manage the browser bridge", type: "server" },
      ],
    },

    // ── Notifications ─────────────────────────────────────────────
    {
      id: "notifications", group: "notifications", icon: "bell", title: st("notifications", "Notifications"),
      fields: [
        { key: "notifications_enabled", label: "Desktop notifications", hint: "Balloon notifications via the system tray", type: "toggle" },
        { key: "notify_completed", label: "Notify on completion", hint: "When a download finishes", type: "toggle" },
        { key: "notify_failed", label: "Notify on failure", hint: "When a download fails", type: "toggle" },
        { key: "notify_started", label: "Notify on start", hint: "When a download begins (off by default)", type: "toggle" },
      ],
    },

    // ── Advanced ──────────────────────────────────────────────────
    {
      id: "updates", group: "advanced", icon: "refresh", title: st("updates", "Updates"),
      fields: [
        { key: "_update_panel", label: "", hint: "", type: "update" },
      ],
    },

    // ── Developer ─────────────────────────────────────────────────
    {
      id: "developer", group: "developer", icon: "code", title: st("developer", "Developer"),
      fields: [
        { key: "_devtools", label: "Diagnostics", hint: "Version, runtime and log access for bug reports", type: "devtools" },
      ],
    },
  ];
},

/** Top-level settings groups, in rail order. */
_settingsGroups() {
  return [
    { id: "general", label: I18N.t("settings.group.general", "General"), icon: "sliders" },
    { id: "downloads", label: I18N.t("settings.group.downloads", "Downloads"), icon: "download" },
    { id: "connection", label: I18N.t("settings.group.connection", "Connection"), icon: "wifi" },
    { id: "speed", label: I18N.t("settings.group.speed", "Speed"), icon: "gauge" },
    { id: "browser", label: I18N.t("settings.group.browser", "Browser Integration"), icon: "browser" },
    { id: "notifications", label: I18N.t("settings.group.notifications", "Notifications"), icon: "bell" },
    { id: "advanced", label: I18N.t("settings.group.advanced", "Advanced"), icon: "shield" },
    { id: "developer", label: I18N.t("settings.group.developer", "Developer"), icon: "code" },
  ];
},


async _buildSettings() {
  const container = Utils.$id("settingsBody");
  if (!this.state.settings) this.state.settings = await API.getSettings();
  const s = this.state.settings;
  if (!s) {
    container.innerHTML = `<p class="dim-note">Settings are unavailable right now.</p>`;
    return;
  }

  const speedMbps = {};
  ["max_speed_bps", "night_speed_limit_bps"].forEach((k) => {
    const bps = s[k] || 0;
    speedMbps[k] = bps > 0 ? +(bps / 1048576).toFixed(1) : 0;
  });
  const ctx = { speedMbps };

  const sections = this._settingsDef();
  const groups = this._settingsGroups();

  // Rail entries are grouped headings followed by their sections, so a long
  // settings tree stays navigable instead of being one endless scroll.
  const railHtml = groups.map((g) => {
    const kids = sections.filter((sec) => (sec.group || "general") === g.id);
    if (!kids.length) return "";
    return `
      <div class="set-rail-group">${Utils.escapeHtml(g.label)}</div>
      ${kids.map((sec) => `
        <button class="set-rail-btn" data-goto="${sec.id}">
          ${Utils.icon(sec.icon, 15)}<span>${Utils.escapeHtml(sec.title)}</span>
        </button>`).join("")}`;
  }).join("");

  // Each card is built defensively: a single malformed field must degrade to
  // a readable note instead of blanking the whole Settings page.
  const cardsHtml = sections.map((sec) => {
    let body = "";
    try {
      body = sec.fields.map((f) => this._fieldHtml(f, s, ctx)).join("");
    } catch (err) {
      API.logJs(`settings section ${sec.id}: ${err && err.message}`);
      body = `<p class="dim-note">${Utils.escapeHtml(I18N.t("app.section_unavailable", "These settings could not be displayed."))}</p>`;
    }
    return `
    <section class="set-card" id="set-${sec.id}">
      <header class="set-head">
        <span class="set-ico">${Utils.icon(sec.icon, 17)}</span>
        <h3>${sec.title}</h3>
        <span class="set-saved" data-saved="${sec.id}">${Utils.icon("check", 12)} ${I18N.t("settings.saved", "Saved")}</span>
      </header>
      <div class="set-body">${body}</div>
    </section>`;
  }).join("");

  container.innerHTML = `
    <div class="settings-shell">
      <nav class="set-rail" aria-label="${Utils.escapeHtml(I18N.t("settings.sections", "Settings sections"))}">${railHtml}</nav>
      <div class="settings-panes">${cardsHtml}</div>
    </div>`;

  this._wireSettings(container, s);
  this._wireSettingsRail(container);
},

/**
 * Rail navigation: scroll to a section and keep the highlight in sync.
 *
 * `card.offsetTop` is measured from the card's offsetParent (`#app`), not
 * from the `#content` scroller, so using it directly overshot by the
 * scroller's own distance from the top of the page — enough to land the
 * *next* section under the top edge (clicking General showed Startup &
 * clipboard).  Measure both rects instead; that is correct no matter which
 * ancestor happens to be positioned.
 */
_wireSettingsRail(container) {
  const rail = Utils.$q(".set-rail", container);
  const scroller = Utils.$id("content");
  if (!rail) return;

  // Settings can be rebuilt (language/theme change), so drop the previous
  // listener or they pile up and fight each other.
  if (this._railSpy && scroller) scroller.removeEventListener("scroll", this._railSpy);

  const btns = Utils.$qa(".set-rail-btn", rail);
  if (!btns.length) return;

  const setActive = (id) => btns.forEach((b) => b.classList.toggle("active", b.dataset.goto === id));

  /** Distance from the top of the scroller's content to the card's top. */
  const offsetInScroller = (card) => {
    if (!scroller) return card.offsetTop;
    const sr = scroller.getBoundingClientRect();
    return card.getBoundingClientRect().top - sr.top + scroller.scrollTop;
  };

  let suppressSpy = 0;   // ignore the spy while a click-scroll is animating
  btns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = Utils.$id("set-" + btn.dataset.goto);
      if (!card) return;
      setActive(btn.dataset.goto);
      suppressSpy = Date.now() + 700;
      if (scroller) {
        scroller.scrollTo({ top: Math.max(0, offsetInScroller(card) - 12), behavior: "smooth" });
      } else {
        card.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    });
  });

  // Scroll-spy: the highlight must follow manual scrolling too, otherwise the
  // rail and the visible panel drift apart.
  if (scroller) {
    this._railSpy = () => {
      if (Date.now() < suppressSpy) return;
      const sr = scroller.getBoundingClientRect();
      let current = btns[0].dataset.goto;
      for (const b of btns) {
        const card = Utils.$id("set-" + b.dataset.goto);
        if (card && card.getBoundingClientRect().top - sr.top <= 48) current = b.dataset.goto;
      }
      setActive(current);
    };
    scroller.addEventListener("scroll", this._railSpy, { passive: true });
  }

  setActive(btns[0].dataset.goto);
},

_fieldHtml(f, s, ctx) {
  const tkey = f.key.replace(/^_/, "");
  const label = I18N.t("set." + tkey, f.label);
  const hint = I18N.t("set." + tkey + ".hint", f.hint);
  // Chip-style controls have no single focusable input, so a `for=` would
  // point at an id that never exists.
  const noFor = f.key.startsWith("_") || f.type === "days";
  const head = `
    <div class="field-info">
      <label class="field-label" ${noFor ? "" : `for="set-${f.key}"`}>${label}</label>
      ${hint ? `<p class="field-hint">${hint}</p>` : ""}
    </div>`;

  let ctl = "";
  if (f.type === "dir") {
    ctl = `<div class="input-join">
        <input class="input mono" id="set-${f.key}" data-key="${f.key}" value="${Utils.escapeHtml(s[f.key] || "")}" spellcheck="false">
        <button class="btn btn-ghost" data-browse="${f.key}">${Utils.icon("folder", 15)} ${I18N.t("dlg.browse", "Browse")}</button>
      </div>`;
  } else if (f.type === "range") {
    ctl = `<div class="slider-wrap">
        <input type="range" id="set-${f.key}" data-key="${f.key}" min="${f.min}" max="${f.max}" value="${s[f.key]}" aria-label="${Utils.escapeHtml(label)}">
        <output>${s[f.key]}</output>
      </div>`;
  } else if (f.type === "toggle") {
    const enabled = f.of ? (ctx.speedMbps?.[f.of] > 0) : !!s[f.key];
    ctl = `<span class="switch"><input type="checkbox" id="set-${f.key}" data-key="${f.key}" data-of="${f.of || ""}" ${enabled ? "checked" : ""}><span class="switch-track"></span></span>`;
  } else if (f.type === "text" || f.type === "password") {
    const ph = f.placeholder ? I18N.t("set." + tkey + ".placeholder", f.placeholder) : "";
    ctl = `<input class="input" type="${f.type}" id="set-${f.key}" data-key="${f.key}" value="${Utils.escapeHtml(s[f.key] || "")}" placeholder="${Utils.escapeHtml(ph)}" spellcheck="false">`;
  } else if (f.type === "uatext") {
    ctl = `<div class="ua-wrap">
      <input class="input mono" id="set-${f.key}" data-key="${f.key}" value="${Utils.escapeHtml(s[f.key] || "")}" spellcheck="false" autocomplete="off">
      <button class="btn btn-ghost btn-sm" data-reset="${f.key}" data-tip="${I18N.t("set.user_agent.reset", "Restore default browser string")}">${Utils.icon("retry", 13)} ${I18N.t("btn.reset", "Reset")}</button>
    </div>`;
  } else if (f.type === "number") {
    ctl = `<input class="input input-num" type="number" id="set-${f.key}" data-key="${f.key}" value="${s[f.key]}" min="${f.min}" max="${f.max}">`;
  } else if (f.type === "time") {
    ctl = `<input class="input mono" type="time" id="set-${f.key}" data-key="${f.key}" value="${Utils.escapeHtml(s[f.key] || "")}">`;
  } else if (f.type === "days") {
    // Multi-select weekday chips. The same control as the scheduler quick
    // dialog; an empty selection means "every day".
    const dayKeys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
    const picked = Array.isArray(s[f.key]) ? s[f.key] : [];
    ctl = `<div class="sched-days" data-days-key="${f.key}">
      ${dayKeys.map((d) => {
        const on = picked.includes(d);
        return `<button type="button" class="chip${on ? " active" : ""}" data-day="${d}" aria-pressed="${on}">${I18N.t("day." + d, d)}</button>`;
      }).join("")}
    </div>`;
  } else if (f.type === "select") {
    ctl = `<select class="input" id="set-${f.key}" data-key="${f.key}">
      ${(f.options || []).map((o) => {
        const optLabel = f.key === "language"
          ? I18N.t("lang." + o.value, o.label)
          : I18N.t("setopt." + o.value, o.label);
        return `<option value="${Utils.escapeHtml(o.value)}" ${String(s[f.key] || "") === o.value ? "selected" : ""}>${Utils.escapeHtml(optLabel)}</option>`;
      }).join("")}
    </select>`;
  } else if (f.type === "speed") {
    const mbps = (ctx.speedMbps?.[f.key] > 0) ? ctx.speedMbps[f.key] : 10;
    const off = !(ctx.speedMbps?.[f.key] > 0);
    ctl = `<div class="speed-wrap ${off ? "off" : ""}" id="speedwrap-${f.key}">
        <input type="range" data-speed-key="${f.key}" min="0.5" max="100" step="0.5" value="${mbps}" aria-label="${Utils.escapeHtml(I18N.t("set." + f.key + ".aria", "Speed limit in megabytes per second"))}">
        <div class="speed-val"><input class="input input-num" data-speed-in="${f.key}" type="number" min="0.5" max="100" step="0.5" value="${mbps}"><span>MB/s</span></div>
      </div>`;
  } else if (f.type === "speedpresets") {
    const presets = [256, 512, 1024, 2048, 5120, 10240].map((kb) => Math.round(kb * 1024));
    ctl = `<div class="preset-row">${presets.map((bps) => `
      <button class="chip" data-preset-bps="${bps}" data-tip="${(bps / 1024 / 1024).toFixed(1).replace(/\.0$/, "")} MB/s">${Utils.formatSpeed(bps)}</button>`).join("")}
      <button class="chip" data-preset-bps="0">${I18N.t("setopt.unlimited", "Unlimited")}</button>
    </div>`;
  } else if (f.type === "catdirs") {
    const cats = ["General", "Videos", "Music", "Images", "Documents", "Archives", "Programs", "Other"];
    const dirs = s.category_dirs || {};
    const catPlaceholder = I18N.t("set.category_dirs.placeholder", "Default folder");
    ctl = `<div class="catdirs">
      ${cats.map((c) => `
        <div class="catdir-row">
          <span class="catdir-name">${Utils.escapeHtml(I18N.t("category." + c, c))}</span>
          <input class="input mono" data-catdir="${c}" value="${Utils.escapeHtml(dirs[c] || "")}" placeholder="${Utils.escapeHtml(catPlaceholder)}">
          <button class="icon-btn btn-xs" data-catdir-browse="${c}" data-tip="${I18N.t("dlg.browse", "Browse")}" aria-label="${I18N.t("dlg.browse", "Browse")}">${Utils.icon("folder", 13)}</button>
        </div>`).join("")}
    </div>`;
  } else if (f.type === "catexts") {
    const txt = JSON.stringify(s.category_extensions || {}, null, 1);
    ctl = `<textarea class="input textarea mono" data-catexts rows="4" spellcheck="false" placeholder='{"${I18N.t("category.Videos", "Videos")}": ["mp4", "mkv"]}'>${Utils.escapeHtml(txt)}</textarea>`;
  } else if (f.type === "theme") {
    ctl = `<div class="seg" role="radiogroup" aria-label="${Utils.escapeHtml(label)}">
        <button class="seg-btn ${this.state.theme === "dark" ? "active" : ""}" data-theme="dark" role="radio" aria-checked="${this.state.theme === "dark"}">${Utils.icon("moon", 15)} ${I18N.t("setopt.dark", "Dark")}</button>
        <button class="seg-btn ${this.state.theme === "light" ? "active" : ""}" data-theme="light" role="radio" aria-checked="${this.state.theme === "light"}">${Utils.icon("sun", 15)} ${I18N.t("setopt.light", "Light")}</button>
      </div>`;
  } else if (f.type === "accent") {
    ctl = `<div class="swatches">${this.accents.map((c) => `
        <button class="swatch ${this.state.accent.toLowerCase() === c.toLowerCase() ? "active" : ""}" data-color="${c}" style="--sw:${c}" aria-label="${Utils.escapeHtml(I18N.t("set.accent.swatch", "Accent"))} ${c}" aria-pressed="${this.state.accent.toLowerCase() === c.toLowerCase()}"></button>`).join("")}
      </div>`;
  } else if (f.type === "server") {
    ctl = `<button class="btn btn-ghost" id="setGoBrowser">${Utils.icon("external", 15)} ${I18N.t("browser.open_page", "Open Browser page")}</button>`;
  } else if (f.type === "devtools") {
    // _propRow is a Components helper, not an App method.
    const propRow = Components._propRow.bind(Components);
    const rows = [
      propRow(I18N.t("dev.version", "App version"), Utils.escapeHtml(this.state.version || "—")),
      propRow(I18N.t("dev.platform", "Platform"), Utils.escapeHtml(navigator.userAgent.includes("Windows") ? "Windows" : navigator.platform || "—")),
      propRow(I18N.t("dev.ui_language", "UI language"), Utils.escapeHtml(I18N.lang)),
      propRow(I18N.t("dev.active_tasks", "Active tasks"), Utils.escapeHtml(String(this._taskArray().length))),
    ].join("");
    ctl = `<div class="dev-tools">
        <div class="prop-list">${rows}</div>
        <div class="dev-actions">
          <button class="btn btn-ghost btn-sm" data-dev="logs">${Utils.icon("logs", 13)} ${I18N.t("dev.open_logs", "Open logs")}</button>
          <button class="btn btn-ghost btn-sm" data-dev="copy">${Utils.icon("copy", 13)} ${I18N.t("dev.copy_diag", "Copy diagnostics")}</button>
          <button class="btn btn-ghost btn-sm" data-dev="settings">${Utils.icon("code", 13)} ${I18N.t("dev.copy_settings", "Copy settings JSON")}</button>
        </div>
      </div>`;
  } else if (f.type === "rules") {
    ctl = `<div class="rules-manager" id="rulesManager"></div>
      <div class="rules-actions">
        <button class="btn btn-ghost btn-sm" id="btnRuleAdd">${Utils.icon("plus", 13)} ${I18N.t("rules.add_rule", "Add rule")}</button>
        <button class="btn btn-ghost btn-sm" id="btnRuleTest">${Utils.icon("search", 13)} ${I18N.t("rules.test_rule", "Test rule")}</button>
      </div>`;
  } else if (f.type === "update") {
    ctl = `<div class="update-panel" id="updatePanel">
      <div class="update-meta">
        <div class="update-version-row"><span>${I18N.t("update.current_version", "Current version")}:</span> <strong id="upCurrentVersion">—</strong></div>
        <div class="update-version-row"><span>${I18N.t("update.latest_version", "Latest version")}:</span> <strong id="upLatestVersion">—</strong></div>
        <div class="update-status" id="upStatus">${I18N.t("update.check", "Check for updates")}</div>
        <div class="update-last" id="upLastChecked"></div>
        <div class="update-skipped" id="upSkippedRow" hidden>
          <span id="upSkippedText"></span>
          <button class="btn btn-ghost btn-xs" id="btnResetSkipped">${I18N.t("update.reset_skipped", "Reset skipped updates")}</button>
        </div>
      </div>
      <div class="update-progress-wrap" id="upProgressWrap" hidden>
        <div class="update-progress-bar" id="upProgressBar" style="--p:0%"></div>
      </div>
      <div class="update-progress-details" id="upProgressDetails" hidden></div>
      <div class="update-actions">
        <button class="btn btn-ghost" id="btnCheckUpdates">${Utils.icon("refresh", 14)} <span>${I18N.t("update.check", "Check for updates")}</span></button>
        <button class="btn btn-primary" id="btnDownloadUpdate" hidden>${Utils.icon("download", 14)} <span>${I18N.t("update.download", "Download Update")}</span></button>
        <button class="btn btn-primary" id="btnInstallUpdate" hidden>${Utils.icon("refresh", 14)} <span>${I18N.t("update.install", "Install Update")}</span></button>
        <button class="btn btn-ghost" id="btnCancelUpdate" hidden>${Utils.icon("x", 14)} <span>${I18N.t("update.cancel", "Cancel")}</span></button>
        <label class="switch-row update-auto">
          <span class="switch"><input type="checkbox" id="upAutoCheck"><span class="switch-track"></span></span>
          <span class="switch-text"><strong>${I18N.t("update.auto_check", "Automatic update checking")}</strong></span>
        </label>
      </div>
    </div>`;
  }

  return `<div class="set-row${f.wide ? " set-row-wide" : ""}" data-field="${f.key}">${head}<div class="field-ctl">${ctl}</div></div>`;
},

_wireSettings(container, s) {
  const markSaved = (secId) => {
    const el = Utils.$q(`[data-saved="${secId}"]`, container);
    if (!el) return;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 1400);
  };

  // Accumulating save: never loses updates within the debounce window.
  const _pending = {};
  let _timer = null;
  const scheduleSave = (updates, secId) => {
    Object.assign(_pending, updates);
    clearTimeout(_timer);
    _timer = setTimeout(async () => {
      const batch = { ..._pending };
      Object.keys(_pending).forEach((k) => delete _pending[k]);
      const ids = Object.keys(batch);
      const ok = await API.updateSettings(batch);
      if (ok) {
        Object.assign(this.state.settings, batch);
        // Scheduler settings are surfaced in the Queue strip, which lives on
        // another page; refresh it now instead of waiting for the idle poll.
        if (ids.some((k) => k === "scheduler_enabled" || k.startsWith("schedule_"))) this._renderQueueStrip();
        ids.forEach((k) => markSaved(secId || (container.querySelector(`[data-key="${k}"]`) || {}).closest?.(".set-card")?.id?.replace("set-", "") || "general"));
      } else {
        Components.toast(I18N.t("toast.not_saved", "Not saved"), I18N.t("toast.not_saved_msg", "A setting could not be applied"), "error");
      }
    }, 300);
  };
  const secOf = (el) => el.closest?.(".set-card")?.id?.replace("set-", "") || "general";

  // Text / number inputs.
  Utils.$qa("input.input[data-key], input.input-num[data-key], input.mono[data-key]", container).forEach((inp) => {
    inp.addEventListener("change", () => {
      const key = inp.dataset.key;
      const val = inp.type === "number" ? Utils.clamp(+inp.value || 0, +inp.min || 0, +inp.max || 1e9) : inp.value.trim();
      scheduleSave({ [key]: val }, secOf(inp));
    });
  });

  // Range sliders — also paint the fill track.
  const paintSlider = (sl) => {
    const r = +sl.min || 0, m = +sl.max || 1;
    sl.style.setProperty("--fill", ((+sl.value - r) / (m - r)) * 100 + "%");
  };
  Utils.$qa('input[type="range"][data-key]', container).forEach((sl) => {
    paintSlider(sl);
    const out = sl.parentElement?.querySelector("output");
    sl.addEventListener("input", () => {
      if (out) out.textContent = sl.value;
      paintSlider(sl);
    });
    sl.addEventListener("change", () => scheduleSave({ [sl.dataset.key]: +sl.value }, secOf(sl)));
  });

  // Toggles — listen for clicks on the .switch area and also the native change event.
  // A toggle with `of` controls the enable/disable of a linked speed field.
  const handleToggle = (tg, checked) => {
    const key = tg.dataset.key;
    const ofKey = tg.dataset.of;
    if (ofKey) {
      const wrap = Utils.$id(`speedwrap-${ofKey}`);
      if (wrap) wrap.classList.toggle("off", !checked);
      const sl = Utils.$q(`input[data-speed-key="${ofKey}"]`, container);
      const mbps = checked ? (+(sl?.value || 0) || 10) : 0;
      scheduleSave({ [ofKey]: Math.round(mbps * 1048576) }, secOf(tg));
    } else {
      scheduleSave({ [key]: checked }, secOf(tg));
    }
  };
  Utils.$qa(".switch", container).forEach((sw) => {
    const inp = sw.querySelector('input[type="checkbox"]');
    if (!inp) return;
    sw.addEventListener("click", (e) => {
      if (e.target === inp) return; // let native change fire for actual input clicks
      inp.checked = !inp.checked;
      handleToggle(inp, inp.checked);
    });
    inp.addEventListener("change", () => handleToggle(inp, inp.checked));
  });

  // Speed slider + input pairs (generic, per key).
  const paintSpeed = (sl) => {
    const r = +sl.min || 0, m = +sl.max || 1;
    sl.style.setProperty("--fill", ((+sl.value - r) / (m - r)) * 100 + "%");
  };
  Utils.$qa("input[data-speed-key]", container).forEach((sl) => {
    const key = sl.dataset.speedKey;
    const input = Utils.$q(`input[data-speed-in="${key}"]`, container);
    paintSpeed(sl);
    let st;
    const flush = (v) => {
      clearTimeout(st);
      st = setTimeout(() => scheduleSave({ [key]: Math.round(v * 1048576) }, secOf(sl)), 320);
    };
    sl.addEventListener("input", () => {
      if (input) input.value = sl.value;
      paintSpeed(sl);
      flush(+sl.value);
    });
    if (input) {
      input.addEventListener("change", () => {
        input.value = Utils.clamp(+input.value || 0.5, 0.5, 100);
        sl.value = input.value;
        flush(+input.value);
      });
    }
  });

  // Speed presets.
  Utils.$qa("[data-preset-bps]", container).forEach((btn) => {
    btn.addEventListener("click", () => {
      const bps = +btn.dataset.presetBps;
      const slider = Utils.$q('input[data-speed-key="max_speed_bps"]', container);
      const wrap = Utils.$id("speedwrap-max_speed_bps");
      if (wrap) wrap.classList.toggle("off", bps === 0);
      if (slider) {
        slider.value = bps > 0 ? (bps / 1048576).toFixed(1) : 10;
        paintSpeed(slider);
        const input = Utils.$q('input[data-speed-in="max_speed_bps"]', container);
        if (input) input.value = slider.value;
      }
      scheduleSave({ max_speed_bps: bps }, "bandwidth");
      Components.toast(I18N.t("toast.speed_limit_set", "Speed limit set"), bps ? Utils.formatSpeed(bps) : I18N.t("toast.unlimited", "Unlimited"), "info", 1800);
    });
  });

  // Time inputs.
  Utils.$qa('input[type="time"][data-key]', container).forEach((inp) => {
    inp.addEventListener("change", () => {
      scheduleSave({ [inp.dataset.key]: inp.value || null }, secOf(inp));
    });
  });

  // Weekday chips (schedule_days). Multi-select; empty means every day.
  Utils.$qa("[data-days-key]", container).forEach((box) => {
    const key = box.dataset.daysKey;
    const chips = Utils.$qa(".chip", box);
    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        const on = chip.classList.toggle("active");
        chip.setAttribute("aria-pressed", String(on));
        scheduleSave({
          [key]: chips.filter((c) => c.classList.contains("active")).map((c) => c.dataset.day),
        }, secOf(box));
      });
    });
  });

  // Selects.
  Utils.$qa('select[data-key]', container).forEach((sel) => {
    sel.addEventListener("change", () => {
      const key = sel.dataset.key;
      scheduleSave({ [key]: sel.value }, secOf(sel));
      if (key === "language") {
        // Apply the new language immediately (the debounced save updates
        // state later); rebuild settings in the new language right away.
        this.state.settings = { ...(this.state.settings || {}), language: sel.value };
        requestAnimationFrame(() => this._applyLanguage());
      }
    });
  });

  // Category directory editor.
  const catdirInputs = Utils.$qa("input[data-catdir]", container);
  const saveCatDirs = () => {
    const dirs = {};
    catdirInputs.forEach((i) => {
      const v = i.value.trim();
      if (v) dirs[i.dataset.catdir] = v;
    });
    scheduleSave({ category_dirs: dirs }, "categories");
  };
  catdirInputs.forEach((i) => i.addEventListener("change", saveCatDirs));
  Utils.$qa("[data-catdir-browse]", container).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const dir = await API.selectDirectory();
      if (dir) {
        const inp = Utils.$q(`input[data-catdir="${btn.dataset.catdirBrowse}"]`, container);
        if (inp) { inp.value = dir; saveCatDirs(); }
      }
    });
  });

  // Category extensions editor (JSON textarea).
  Utils.$qa("textarea[data-catexts]", container).forEach((ta) => {
    ta.addEventListener("change", () => {
      try {
        const parsed = JSON.parse(ta.value || "{}");
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          scheduleSave({ category_extensions: parsed }, "categories");
        } else {
          Components.toast(I18N.t("toast.invalid_json", "Invalid JSON"), I18N.t("toast.invalid_json_msg", "Category extensions must be an object"), "error");
        }
      } catch {
        Components.toast(I18N.t("toast.json_syntax", "Invalid JSON"), I18N.t("toast.json_syntax_msg", "Check the category extensions syntax"), "error");
      }
    });
  });

  // Folder browse.
  Utils.$qa("[data-browse]", container).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const dir = await API.selectDirectory();
      if (dir) {
        const key = btn.dataset.browse;
        const inp = Utils.$id("set-" + key);
        if (inp) inp.value = dir;
        scheduleSave({ [key]: dir }, secOf(btn));
      }
    });
  });

  // User-agent reset.
  const DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
  Utils.$qa("[data-reset]", container).forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.reset;
      const inp = Utils.$id("set-" + key);
      if (inp) {
        inp.value = DEFAULT_UA;
        scheduleSave({ [key]: DEFAULT_UA }, secOf(btn));
        Components.toast(I18N.t("toast.user_agent_restored", "User agent restored"), I18N.t("toast.user_agent_restored_msg", "Default value applied"), "info");
      }
    });
  });

  // Theme segmented control.
  Utils.$qa(".seg-btn", container).forEach((btn) => {
    btn.addEventListener("click", () => {
      if (this.state.theme === btn.dataset.theme) return;
      this.toggleTheme();
      Utils.$qa(".seg-btn", container).forEach((b) => {
        const on = b.dataset.theme === this.state.theme;
        b.classList.toggle("active", on);
        b.setAttribute("aria-checked", String(on));
      });
      markSaved("appearance");
    });
  });

  // Accent swatches.
  Utils.$qa(".swatch", container).forEach((sw) => {
    sw.addEventListener("click", () => {
      this.setAccent(sw.dataset.color);
      Utils.$qa(".swatch", container).forEach((b) => {
        const on = b === sw;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", String(on));
      });
      markSaved("appearance");
    });
  });

  Utils.$id("setGoBrowser")?.addEventListener("click", () => this.navigate("browser"));

  // Developer → diagnostics.
  const copyText = async (text, okMsg) => {
    try {
      await navigator.clipboard.writeText(text);
      Components.toast(I18N.t("toast.copied", "Copied"), okMsg, "success", 2200);
    } catch {
      Components.toast(I18N.t("toast.copy_failed", "Copy failed"), I18N.t("toast.copy_failed_msg", "Clipboard is unavailable"), "error");
    }
  };
  Utils.$qa("[data-dev]", container).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const kind = btn.dataset.dev;
      if (kind === "logs") {
        this.navigate("logs");
      } else if (kind === "copy") {
        const diag = [
          `N13 Download Manager ${this.state.version || "unknown"}`,
          `Language: ${I18N.lang}`,
          `Theme: ${this.state.theme}`,
          `Tasks in list: ${this._taskArray().length}`,
          `Server running: ${!!this.state.serverRunning}`,
          `Extension connected: ${!!this.state.extConnected}`,
          `User agent: ${navigator.userAgent}`,
        ].join("\n");
        copyText(diag, I18N.t("dev.copied_diag", "Diagnostics copied to clipboard"));
      } else if (kind === "settings") {
        copyText(JSON.stringify(this.state.settings || {}, null, 2), I18N.t("dev.copied_settings", "Settings JSON copied to clipboard"));
      }
    });
  });
  if (Utils.$id("btnRuleAdd")) this._initRulesManager(container);
  this._wireUpdatePanel(container);
},

// ── Update panel ──────────────────────────────────────────────────

async _wireUpdatePanel(container) {
  const panel = Utils.$id("updatePanel");
  if (!panel) return;

  // Load settings once; later events update state/progress.
  try {
    this.state.updateSettings = await API.getUpdateSettings();
  } catch {
    this.state.updateSettings = { current_version: "", auto_update_check: true, skipped_version: "", last_checked: "" };
  }
  const settings = this.state.updateSettings || {};
  Utils.$id("upCurrentVersion").textContent = settings.current_version || "—";
  this._renderSkippedRow();

  const autoInp = Utils.$id("upAutoCheck");
  if (autoInp) autoInp.checked = !!settings.auto_update_check;

  // Auto toggle
  autoInp?.addEventListener("change", async () => {
    await API.setAutoUpdateCheck(autoInp.checked);
    if (this.state.updateSettings) this.state.updateSettings.auto_update_check = autoInp.checked;
  });

  // Manual check
  Utils.$id("btnCheckUpdates")?.addEventListener("click", async () => {
    await API.checkForUpdates(true);
    const st = await API.getUpdateState();
    this._applyUpdateState(st, { manual: true });
  });

  Utils.$id("btnDownloadUpdate")?.addEventListener("click", async () => {
    await API.downloadUpdate();
  });

  Utils.$id("btnCancelUpdate")?.addEventListener("click", async () => {
    await API.cancelUpdateDownload();
  });

  Utils.$id("btnInstallUpdate")?.addEventListener("click", async () => {
    await this._confirmInstallUpdate();
  });

  Utils.$id("btnResetSkipped")?.addEventListener("click", async () => {
    await API.clearSkippedUpdate();
    if (this.state.updateSettings) this.state.updateSettings.skipped_version = "";
    this._renderSkippedRow();
    Components.toast(I18N.t("update.title", "Update available"), I18N.t("update.skipped_reset", "Skipped updates reset"), "info", 2000);
  });

  // Initial state
  const initial = await API.getUpdateState();
  this._applyUpdateState(initial, { manual: false });
},

_renderSkippedRow() {
  const row = Utils.$id("upSkippedRow");
  if (!row) return;
  const skipped = this.state.updateSettings?.skipped_version;
  row.hidden = !skipped;
  if (skipped) {
    const txt = Utils.$id("upSkippedText");
    if (txt) txt.textContent = I18N.t("update.skipped", "Skipped version") + ": v" + skipped;
  }
},

_updateErrorText(err) {
  const code = err?.code || "";
  const key = {
    network: "update.unavailable",
    rate_limited: "update.rate_limited",
    no_release: "update.no_release",
    no_installer: "update.no_installer",
    no_checksum: "update.verification_unavailable",
    checksum_mismatch: "update.verification_failed",
    download_failed: "update.download_failed",
    updater_failed: "update.updater_failed",
    dev_mode: "update.dev_mode",
  }[code];
  if (key) return I18N.t(key, err?.message || "Update failed.");
  return err?.message || I18N.t("update.failed", "Update failed.");
},

_updateProgressText(p) {
  const pct = p.percent || 0;
  const done = Utils.formatSize(p.downloaded_bytes || 0);
  const total = p.total_bytes ? Utils.formatSize(p.total_bytes) : "—";
  const speed = p.speed_bps ? Utils.formatSpeed(p.speed_bps) : "";
  const eta = p.eta_seconds ? Utils.formatETA(Math.max(0, Math.round(p.eta_seconds))) : "";
  let text = `${pct}% · ${done} / ${total}`;
  if (speed) text += ` · ${speed}`;
  if (eta) text += ` · ${I18N.t("update.eta", "ETA")} ${eta}`;
  return text;
},

_applyUpdateState(state, { manual = false } = {}) {
  if (!state) return;
  this.state.updateState = state;
  const panel = Utils.$id("updatePanel");
  if (!panel) { this._syncUpdateDialog(state); return; }

  const statusEl = Utils.$id("upStatus");
  const btnCheck = Utils.$id("btnCheckUpdates");
  const btnDownload = Utils.$id("btnDownloadUpdate");
  const btnInstall = Utils.$id("btnInstallUpdate");
  const btnCancel = Utils.$id("btnCancelUpdate");
  const progressWrap = Utils.$id("upProgressWrap");
  const progressBar = Utils.$id("upProgressBar");
  const progressDetails = Utils.$id("upProgressDetails");
  const latestEl = Utils.$id("upLatestVersion");
  const lastEl = Utils.$id("upLastChecked");

  const setStatus = (text) => { if (statusEl) statusEl.textContent = text; };
  const show = (el, on) => { if (el) el.hidden = !on; };
  const setCheckBusy = (busy) => {
    if (btnCheck) {
      btnCheck.disabled = busy;
      const span = btnCheck.querySelector("span");
      if (span) span.textContent = busy ? I18N.t("update.checking", "Checking for updates…") : I18N.t("update.check", "Check for updates");
    }
  };

  const release = state.release || null;
  if (latestEl) latestEl.textContent = release?.version ? "v" + release.version : "—";
  if (lastEl) {
    const lc = this.state.updateSettings?.last_checked;
    lastEl.textContent = lc
      ? I18N.t("update.last_checked", "Last checked") + ": " + new Date(lc).toLocaleString()
      : I18N.t("update.last_checked", "Last checked") + ": " + I18N.t("update.never", "Never");
  }

  const p = state.progress || {};
  // Reset button visibility; each state enables what it needs.
  show(btnDownload, false); show(btnInstall, false); show(btnCancel, false);
  show(progressWrap, false); show(progressDetails, false);

  switch (state.state) {
    case "checking":
      setCheckBusy(true);
      setStatus(I18N.t("update.checking", "Checking for updates…"));
      break;
    case "downloading":
      setCheckBusy(false);
      setStatus(I18N.t("update.downloading", "Downloading update…"));
      show(progressWrap, true); show(progressDetails, true); show(btnCancel, true);
      if (progressBar) progressBar.style.setProperty("--p", (p.percent || 0) + "%");
      if (progressDetails) progressDetails.textContent = this._updateProgressText(p);
      break;
    case "verifying":
      setStatus(I18N.t("update.verifying", "Verifying update…"));
      show(progressWrap, true);
      if (progressBar) progressBar.style.setProperty("--p", "100%");
      break;
    case "ready_to_install":
      setCheckBusy(false);
      setStatus(I18N.t("update.ready", "Update ready to install."));
      show(btnInstall, true);
      break;
    case "available":
      setCheckBusy(false);
      setStatus(I18N.t("update.available", "New update available") + (release?.version ? ": v" + release.version : ""));
      show(btnDownload, true);
      if (manual) this._showUpdateDialog(release);
      break;
    case "installing":
      setStatus(I18N.t("update.installing", "Installing update…"));
      break;
    case "failed": {
      setCheckBusy(false);
      const msg = this._updateErrorText(state.error);
      setStatus(I18N.t("update.failed", "Update failed.") + " " + msg);
      if (release) show(btnDownload, true);
      if (manual) Components.toast(I18N.t("update.failed", "Update failed."), msg, "error");
      break;
    }
    case "cancelled":
      setCheckBusy(false);
      setStatus(I18N.t("update.cancelled", "Download cancelled."));
      if (release) show(btnDownload, true);
      break;
    case "up_to_date":
      setCheckBusy(false);
      setStatus(I18N.t("update.up_to_date", "You're up to date."));
      if (manual) Components.toast(I18N.t("update.up_to_date", "You're up to date."), "v" + (state.current_version || ""), "success", 2200);
      break;
    default: // idle
      setCheckBusy(false);
      setStatus(I18N.t("update.check", "Check for updates"));
      break;
  }
  this._syncUpdateDialog(state);
},

async _showUpdateDialog(release) {
  if (!release || !release.version) return;
  // Already open for this flow: just refresh its content.
  if (this._updateDialog) { this._syncUpdateDialog(this.state.updateState); return; }

  const notesHtml = Utils.escapeHtml(release.notes || I18N.t("update.no_notes", "No release notes provided.")).replace(/\n/g, "<br>");
  const dlg = Components.showModal(`
    <div class="update-dialog-body">
      <div class="update-dialog-row"><span>${I18N.t("update.current_version", "Current version")}:</span> <strong>v${Utils.escapeHtml(this.state.updateState?.current_version || this.state.updateSettings?.current_version || "")}</strong></div>
      <div class="update-dialog-row"><span>${I18N.t("update.new_version", "New version")}:</span> <strong>v${Utils.escapeHtml(release.version)}</strong></div>
      <div class="update-dialog-label">${I18N.t("update.release_notes", "Release notes")}</div>
      <div class="update-notes">${notesHtml}</div>
      <div class="update-dialog-progress" id="upDlgProgress" hidden>
        <div class="update-progress-wrap"><div class="update-progress-bar" id="upDlgProgressBar" style="--p:0%"></div></div>
        <div class="update-progress-details" id="upDlgProgressDetails"></div>
      </div>
      <div class="update-dialog-error" id="upDlgError" hidden></div>
    </div>`, {
    title: I18N.t("update.title", "Update available"), width: 480,
    onClose: () => { this._updateDialog = null; },
  });
  this._updateDialog = dlg;
  dlg._renderedState = null;
  this._syncUpdateDialog(this.state.updateState);
},

_syncUpdateDialog(state) {
  const dlg = this._updateDialog;
  if (!dlg || !state || !state.release) return;
  const release = state.release;
  const p = state.progress || {};
  const progressBox = dlg.qs("#upDlgProgress");
  const bar = dlg.qs("#upDlgProgressBar");
  const details = dlg.qs("#upDlgProgressDetails");
  const errorBox = dlg.qs("#upDlgError");
  const st = state.state;

  // Live progress updates don't rebuild the footer (would break clicks).
  if (st === "downloading" || st === "verifying") {
    if (progressBox) progressBox.hidden = false;
    if (bar) bar.style.setProperty("--p", (st === "verifying" ? 100 : (p.percent || 0)) + "%");
    if (details) details.textContent = st === "verifying"
      ? I18N.t("update.verifying", "Verifying update…")
      : this._updateProgressText(p);
    if (dlg._renderedState !== "progress") {
      dlg._renderedState = "progress";
      if (errorBox) errorBox.hidden = true;
      dlg.setFooter(`<button class="btn btn-ghost" id="upDlgCancel">${I18N.t("update.cancel", "Cancel")}</button>`);
      dlg.qs("#upDlgCancel")?.addEventListener("click", async () => { await API.cancelUpdateDownload(); });
    }
    return;
  }

  if (dlg._renderedState === st) return;
  dlg._renderedState = st;
  if (progressBox) progressBox.hidden = true;

  if (st === "ready_to_install") {
    if (errorBox) errorBox.hidden = true;
    dlg.setFooter(`
      <button class="btn btn-ghost" id="upLater">${I18N.t("update.later", "Later")}</button>
      <button class="btn btn-primary" id="upNow">${I18N.t("update.install", "Install Update")}</button>`);
    dlg.qs("#upLater").addEventListener("click", () => dlg.close());
    dlg.qs("#upNow").addEventListener("click", async () => { dlg.close(); await this._confirmInstallUpdate(); });
  } else if (st === "failed") {
    if (errorBox) { errorBox.hidden = false; errorBox.textContent = this._updateErrorText(state.error); }
    dlg.setFooter(`
      <button class="btn btn-ghost" id="upLater">${I18N.t("update.later", "Later")}</button>
      <button class="btn btn-primary" id="upNow">${I18N.t("update.download", "Download Update")}</button>`);
    dlg.qs("#upLater").addEventListener("click", () => dlg.close());
    dlg.qs("#upNow").addEventListener("click", async () => { await API.downloadUpdate(); });
  } else if (st === "cancelled") {
    dlg.setFooter(`
      <button class="btn btn-ghost" id="upLater">${I18N.t("update.later", "Later")}</button>
      <button class="btn btn-primary" id="upNow">${I18N.t("update.download", "Download Update")}</button>`);
    dlg.qs("#upLater").addEventListener("click", () => dlg.close());
    dlg.qs("#upNow").addEventListener("click", async () => { await API.downloadUpdate(); });
  } else if (st === "installing") {
    dlg.setFooter(`<button class="btn btn-primary" disabled>${I18N.t("update.installing", "Installing update…")}</button>`);
  } else {
    // available / default
    dlg.setFooter(`
      <button class="btn btn-ghost" id="upLater">${I18N.t("update.later", "Later")}</button>
      <button class="btn btn-ghost" id="upSkip">${I18N.t("update.skip", "Skip this version")}</button>
      <button class="btn btn-primary" id="upNow">${I18N.t("update.download", "Download Update")}</button>`);
    dlg.qs("#upLater").addEventListener("click", () => dlg.close());
    dlg.qs("#upSkip").addEventListener("click", async () => {
      await API.skipUpdateVersion(release.version);
      if (this.state.updateSettings) this.state.updateSettings.skipped_version = release.version;
      this._renderSkippedRow();
      dlg.close();
      Components.toast(I18N.t("update.title", "Update available"), I18N.t("update.skipped", "Skipped version") + " v" + release.version, "info", 2000);
    });
    dlg.qs("#upNow").addEventListener("click", async () => { await API.downloadUpdate(); });
  }
},

async _confirmInstallUpdate() {
  const stats = await API.getStats();
  const active = stats?.running || 0;
  if (active > 0) {
    const ok = await Components.confirm({
      title: I18N.t("update.title", "Update available"),
      message: I18N.fmt("update.install_blocked_active", { count: active }),
      okText: I18N.t("update.update_anyway", "Update Anyway"),
      cancelText: I18N.t("confirm.cancel", "Cancel"),
    });
    if (!ok) return;
  }
  Components.toast(I18N.t("update.title", "Update available"), I18N.t("update.installing", "Installing update…"), "info", 3000);
  const res = await API.installUpdate(true);
  if (res && res.status === "not_ready") {
    Components.toast(I18N.t("update.failed", "Update failed."), this._updateErrorText(res.state?.error), "error");
  }
},

async _maybeCheckUpdates() {
  if (this._updateCheckedSession) return;
  this._updateCheckedSession = true;
  try {
    const settings = await API.getUpdateSettings();
    this.state.updateSettings = settings;
    if (!settings.auto_update_check) return;
    // The check is asynchronous; results arrive via events.
    await API.checkForUpdates(false);
  } catch {}
},

// ── Download Rules manager ────────────────────────────────────────

async _initRulesManager(container) {
  const box = Utils.$id("rulesManager");
  if (!box) return;
  const rules = (await API.getRules()) || [];
  if (!rules.length) {
    box.innerHTML = `<div class="dim-note">${I18N.t("rules.no_rules", "No rules yet")}. ${I18N.t("rules.no_rules_desc", "Rules auto-set category, folder and priority for matching downloads")}.</div>`;
  } else {
    box.innerHTML = rules.map((r) => `
      <div class="rule-item" data-rid="${Utils.escapeHtml(r.id)}">
        <label class="switch"><input type="checkbox" data-ren="${Utils.escapeHtml(r.id)}" ${r.enabled === false ? "" : "checked"}><span class="switch-track"></span></label>
        <div class="rule-main">
          <div class="rule-name">${Utils.escapeHtml(r.name)}</div>
          <div class="rule-sub">${Utils.escapeHtml((r.conditions || []).map((c) => I18N.t("rules.condition_fields." + c.field, c.field.replace(/_/g, " ")) + "=" + c.value).join(" & ") || I18N.t("rules.no_conditions", "no conditions"))}${r.category ? " · " + Utils.escapeHtml(I18N.t("category." + r.category, r.category)) : ""}</div>
        </div>
        <span class="rule-pri">P${r.priority}</span>
        <div class="rule-ops">
          <button class="icon-btn btn-xs" data-act="edit" data-rid="${Utils.escapeHtml(r.id)}" data-tip="${I18N.t("rules.edit", "Edit")}" aria-label="${I18N.t("rules.edit_rule", "Edit rule")}">${Utils.icon("settings", 13)}</button>
          <button class="icon-btn btn-xs" data-act="dup" data-rid="${Utils.escapeHtml(r.id)}" data-tip="${I18N.t("rules.duplicate", "Duplicate")}" aria-label="${I18N.t("rules.duplicate_rule", "Duplicate rule")}">${Utils.icon("copy", 13)}</button>
          <button class="icon-btn btn-xs" data-act="del" data-rid="${Utils.escapeHtml(r.id)}" data-tip="${I18N.t("rules.delete", "Delete")}" aria-label="${I18N.t("rules.delete_rule", "Delete rule")}">${Utils.icon("trash", 13)}</button>
        </div>
      </div>`).join("");
  }

  const reload = () => this._initRulesManager(container);
  const ruleById = (id) => rules.find((r) => r.id === id);

  Utils.$qa(".rule-item [data-ren]", box).forEach((cb) => {
    cb.addEventListener("change", async () => {
      await API.updateRule(cb.dataset.ren, { enabled: cb.checked });
      reload();
    });
  });
  Utils.$qa(".rule-item [data-act]", box).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const r = ruleById(btn.dataset.rid);
      if (!r) return;
      if (btn.dataset.act === "edit") {
        const saved = await Components.ruleEditor(r);
        if (saved) { await API.updateRule(r.id, saved); reload(); }
      } else if (btn.dataset.act === "dup") {
        await API.duplicateRule(r.id); reload();
      } else if (btn.dataset.act === "del") {
        const ok = await Components.confirm({
          title: I18N.t("confirm.delete_rule", "Delete rule"),
          message: I18N.t("confirm.delete_rule_msg", "Delete “{name}”?").replace("{name}", r.name),
          okText: I18N.t("confirm.delete", "Delete"), cancelText: I18N.t("confirm.cancel", "Cancel"), danger: true });
        if (ok) {
          await API.deleteRule(r.id); reload();
          Components.toast(I18N.t("toast.rule_deleted", "Rule deleted"), r.name, "info");
        }
      }
    });
  });

  Utils.$id("btnRuleAdd").addEventListener("click", async () => {
    const saved = await Components.ruleEditor({});
    if (saved) { await API.addRule(saved); reload(); }
  });
  Utils.$id("btnRuleTest").addEventListener("click", async () => {
    const dlg = Components.showModal(`
      <div class="confirm-body">
        <span class="confirm-ico accent">${Utils.icon("search", 22)}</span>
        <p class="confirm-msg">${I18N.t("rules.test_rule", "Test rule")}</p>
        <input class="input mono" id="rtUrl" placeholder="https://example.com/movie.mp4" spellcheck="false">
      </div>`, { title: I18N.t("rules.test_rule", "Test rule"), width: 460 });
    dlg.setFooter(`<button class="btn btn-ghost" id="rtCancel">${I18N.t("rules.cancel", "Cancel")}</button><button class="btn btn-primary" id="rtGo">${Utils.icon("search", 14)} ${I18N.t("rules.test", "Test")}</button>`);
    const url = dlg.qs("#rtUrl");
    setTimeout(() => url.focus(), 60);
    dlg.qs("#rtCancel").addEventListener("click", () => dlg.close());
    dlg.qs("#rtGo").addEventListener("click", async () => {
      const res = await API.testRule(url.value.trim());
      dlg.close();
      if (!res || !res.matched) {
        Components.toast(I18N.t("toast.no_rule_matched", "No rule matched"), I18N.t("toast.no_rule_matched_msg", "This URL would use the default settings"), "info");
        return;
      }
      const a = res.actions || {};
      Components.toast(I18N.t("toast.rule_matched", "Rule matched") + ": " + (res.rule?.name || "?"),
        `${I18N.t("rules.category", "Category")}: ${I18N.t("category." + (a.category || "General"), a.category || I18N.t("rules.default", "default"))} · ${I18N.t("rules.folder", "Folder")}: ${a.folder || I18N.t("rules.default", "default")} · ${I18N.t("rules.priority", "Priority")}: ${a.priority} · ${I18N.t("rules.connection_mode", "Conn")}: ${I18N.t("setopt." + (a.connection_mode || "inherit"), a.connection_mode || I18N.t("setopt.inherit", "inherit"))}`,
        "success", 6000);
    });
  });
},
};
