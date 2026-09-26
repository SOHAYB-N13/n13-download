/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Application
   ═══════════════════════════════════════════════════════════════════════════ */

const App = {
  state: {
    page: "dashboard",
    downloads: {},
    history: [],
    logs: [],
    elapsed: {},           // taskId -> { ms, from } active-time accumulator
    filter: "all",
    // Category filter for the Downloads list ("all" or a category name).
    catFilter: "all",
    sortKey: "newest",
    sortDir: -1,
    search: "",
    theme: "dark",
    accent: "#EF4444",
    sidebarCollapsed: false,
    serverRunning: false,
    // Timestamp of the last link received from the browser extension.  The
    // live server exposes no "extension installed" flag, so an actual capture
    // is the only honest proof of a working pairing.
    extLastSeen: 0,
    extConnected: false,
    version: "",
    settings: null,
    maximized: false,
    highlightId: null,
    listSig: "",
    // Explorer-style multi-selection for the Downloads list.
    selectedIds: new Set(),   // Set of selected task ids
    selAnchor: null,          // anchor task id for Shift+Click range selection
  },

  pages: {
    dashboard: { title: "Dashboard", sub: "Overview of your download activity" },
    downloads: { title: "Downloads", sub: "Manage and monitor your files" },
    history:   { title: "History", sub: "Previously completed downloads" },
    batch:     { title: "Batch", sub: "Queue many downloads at once" },
    browser:   { title: "Browser", sub: "Capture downloads from your browser" },
    settings:  { title: "Settings", sub: "Tune N13 to your workflow" },
    logs:      { title: "Logs", sub: "Application activity" },
  },

  accents: ["#3B82F6", "#8B5CF6", "#14B8A6", "#22C55E", "#F59E0B", "#EC4899", "#EF4444"],

  // ══════════════════════════════════════════════════════════════════════
  //  Boot
  // ══════════════════════════════════════════════════════════════════════

  async init() {
    this._loadLocalPrefs();
    this._applyTheme();
    Components.initRipple();
    I18N.onChange(() => this._onLanguageChange());
    this._bindNavigation();
    this._bindTitlebar();
    this._bindWindowControls();
    this._bindResizeHandles();
    this._bindGlobalEvents();
    this._bindCommandBar();
    this._bindDownloadsPage();
    this._bindBatchPage();
    this._bindBrowserPage();
    this._bindHistoryPage();
    this._bindLogsPage();
    Utils.$qa("[data-nav]").forEach((el) =>
      el.addEventListener("click", () => this.navigate(el.dataset.nav)));
    this._renderPageChrome();
    this._showSkeletons();

    const start = async () => {
      await this._initBackend();
      this._startPolling();
      this._startStatsPolling();
      this._startSparkline();
    };

    if (API.available) {
      await start();
    } else {
      const onReady = async () => {
        window.removeEventListener("pywebviewready", onReady);
        window.removeEventListener("_pywebviewready", onReady);
        await start();
      };
      window.addEventListener("pywebviewready", onReady);
      window.addEventListener("_pywebviewready", onReady);
      setTimeout(async () => { if (!this.state.booted) await start(); }, 2500);
    }
  },

  async _initBackend() {
    if (this.state.booted) return;
    this.state.booted = true;

    try {
      const prefs = await API.getThemeConfig();
      if (prefs) {
        if (prefs.theme) this.state.theme = prefs.theme;
        if (prefs.accent) this.state.accent = prefs.accent;
        if (typeof prefs.sidebarCollapsed === "boolean") this.state.sidebarCollapsed = prefs.sidebarCollapsed;
        this._applyTheme();
        this._applySidebar();
      }
    } catch {}

    try {
      this.state.settings = await API.getSettings();
    } catch {}

    try {
      this.state.version = (await API.getVersion()) || "";
    } catch {}

    this._applyLanguage();

    try { await this._loadDownloads(); } catch {}
    try { await this._refreshHistory(); } catch {}
    try { await this._refreshServerStatus(); } catch {}
    this._renderDashboardLists();
    this._maybeCheckUpdates();
    await API.ready();
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Theme & preferences
  // ══════════════════════════════════════════════════════════════════════

  _loadLocalPrefs() {
    try {
      const p = JSON.parse(localStorage.getItem("n13-prefs") || "{}");
      if (p.theme) this.state.theme = p.theme;
      if (p.accent) this.state.accent = p.accent;
      if (typeof p.sidebarCollapsed === "boolean") this.state.sidebarCollapsed = p.sidebarCollapsed;
    } catch {}
  },

  _savePrefs() {
    const prefs = {
      theme: this.state.theme,
      accent: this.state.accent,
      sidebarCollapsed: this.state.sidebarCollapsed,
    };
    localStorage.setItem("n13-prefs", JSON.stringify(prefs));
    API.saveThemeConfig(prefs).catch(() => {});
  },

  _applyTheme() {
    document.documentElement.dataset.theme = this.state.theme;
    const root = document.documentElement.style;
    root.setProperty("--accent", this.state.accent);
    root.setProperty("--accent-hi", this._mix(this.state.accent, 0.28));
    root.setProperty("--accent-soft", this._alpha(this.state.accent, 0.14));
    root.setProperty("--accent-ring", this._alpha(this.state.accent, 0.35));
    this._savePrefsLocalOnly();
  },

  _savePrefsLocalOnly() {
    localStorage.setItem("n13-prefs", JSON.stringify({
      theme: this.state.theme,
      accent: this.state.accent,
      sidebarCollapsed: this.state.sidebarCollapsed,
    }));
  },

  _hexToRgb(hex) {
    const h = hex.replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  },

  _alpha(hex, a) {
    const [r, g, b] = this._hexToRgb(hex);
    return `rgba(${r},${g},${b},${a})`;
  },

  _mix(hex, amt) {
    const [r, g, b] = this._hexToRgb(hex);
    const m = (c) => Math.round(c + (255 - c) * amt);
    return `#${[m(r), m(g), m(b)].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
  },

  toggleTheme() {
    this.state.theme = this.state.theme === "dark" ? "light" : "dark";
    this._applyTheme();
    this._savePrefs();
    if (this._spark) this._spark.redraw();
  },

  setAccent(color) {
    this.state.accent = color;
    this._applyTheme();
    this._savePrefs();
    if (this._spark) this._spark.redraw();
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Navigation
  // ══════════════════════════════════════════════════════════════════════

  _bindNavigation() {
    Utils.$qa(".nav-item[data-page]").forEach((item) => {
      item.addEventListener("click", () => this.navigate(item.dataset.page));
    });
    Utils.$id("sidebarToggle")?.addEventListener("click", () => this.toggleSidebar());
  },

  toggleSidebar() {
    this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
    this._applySidebar();
    this._savePrefs();
  },

  _applySidebar() {
    document.body.classList.toggle("sidebar-collapsed", this.state.sidebarCollapsed);
  },

  navigate(page) {
    if (!this.pages[page]) return;
    this.state.page = page;
    Utils.$qa(".page").forEach((p) => p.classList.remove("active"));
    const target = Utils.$id("page-" + page);
    if (target) {
      target.classList.add("active");
      // Re-trigger the entrance animation.
      target.classList.remove("page-enter");
      void target.offsetWidth;
      target.classList.add("page-enter");
    }
    Utils.$qa(".nav-item[data-page]").forEach((n) => {
      const on = n.dataset.page === page;
      n.classList.toggle("active", on);
      if (on) n.setAttribute("aria-current", "page");
      else n.removeAttribute("aria-current");
    });
    this._moveNavIndicator();
    this._renderPageChrome();

    if (page === "history") this._renderHistory();
    if (page === "settings") this._buildSettings();
    if (page === "browser") this._refreshServerStatus();
    if (page === "logs") this._renderLogs();
    if (page === "dashboard") this._renderDashboardLists();
    if (page === "downloads") this._renderDownloads(true);
  },

  _moveNavIndicator() {
    const active = Utils.$q(".nav-item[data-page].active");
    const ind = Utils.$id("navIndicator");
    if (!active || !ind) return;
    ind.style.transform = `translateY(${active.offsetTop}px)`;
    ind.style.height = active.offsetHeight + "px";
  },

  _renderPageChrome() {
    const meta = this.pages[this.state.page];
    const title = I18N.t("page." + this.state.page, meta.title);
    Utils.$id("pageTitle").textContent = title;
    Utils.$id("pageSub").textContent = I18N.t("page." + this.state.page + ".sub", meta.sub);
    document.title = `${title} · N13 Download Manager`;
  },

  _applyLanguage() {
    const lang = (this.state.settings && this.state.settings.language) || "en";
    I18N.setLang(lang);
  },

  _onLanguageChange() {
    I18N.apply(document);
    this._renderPageChrome();
    this._updateCounts();
    if (this.state.page === "dashboard") this._renderDashboardLists();
    if (this.state.page === "downloads") this._renderDownloads(true);
    if (this.state.page === "history") this._renderHistory();
    if (this.state.page === "settings") this._buildSettings();
    if (this.state.page === "browser" && this._lastServerStatus) this._renderBrowser(this._lastServerStatus);
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Titlebar (search, quick actions, theme)
  // ══════════════════════════════════════════════════════════════════════

  _bindTitlebar() {
    const search = Utils.$id("globalSearch");
    search.addEventListener("input", Utils.debounce(() => {
      this.state.search = search.value.trim().toLowerCase();
      if (this.state.search && this.state.page !== "downloads" && this.state.page !== "history") {
        this.navigate("downloads");
      }
      if (this.state.page === "downloads") this._renderDownloads(true);
      if (this.state.page === "history") this._renderHistory();
    }, 160));
    search.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { search.value = ""; this.state.search = ""; this._renderDownloads(true); search.blur(); }
      if (e.key === "Enter" && this.state.page !== "downloads") this.navigate("downloads");
    });

    Utils.$id("btnNewDownload").addEventListener("click", () => { this.openNewDownload().catch((e) => API.logJs("btnNewDownload: " + String(e))); });
    Utils.$id("btnPasteQuick").addEventListener("click", () => { this.openNewDownload(null, { paste: true }).catch((e) => API.logJs("btnPasteQuick: " + String(e))); });
    Utils.$id("btnTheme").addEventListener("click", () => this.toggleTheme());
    Utils.$id("btnTopSettings").addEventListener("click", () => this.navigate("settings"));

    // Prevent pywebview drag-region from swallowing interactive presses.
    const bar = Utils.$id("titlebar");
    Utils.$qa("button, input, a, select, .tb-nodrag", bar).forEach((el) => {
      el.addEventListener("mousedown", (e) => e.stopPropagation());
    });
    bar.addEventListener("dblclick", (e) => {
      if (e.target.closest("button, input, a, select")) return;
      API.winToggleMaximize();
    });
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Window controls & frameless resize
  // ══════════════════════════════════════════════════════════════════════

  _bindWindowControls() {
    Utils.$id("winMin").addEventListener("click", () => API.winMinimize());
    Utils.$id("winMax").addEventListener("click", () => API.winToggleMaximize());
    Utils.$id("winClose").addEventListener("click", () => API.winClose());
    window.addEventListener("resize", Utils.throttle(() => this._syncMaxState(), 250));
    this._syncMaxState();
  },

  _syncMaxState() {
    const max = window.outerWidth >= screen.availWidth - 4 && window.outerHeight >= screen.availHeight - 4;
    this._setMaxState(max);
  },

  _setMaxState(max) {
    if (this.state.maximized === max) return;
    this.state.maximized = max;
    document.body.classList.toggle("maximized", max);
    const btn = Utils.$id("winMax");
    btn.innerHTML = Utils.icon(max ? "restore" : "max", 14);
    btn.setAttribute("aria-label", max ? "Restore window" : "Maximize window");
  },

  _bindResizeHandles() {
    const MIN_W = 1120, MIN_H = 680;
    Utils.$qa(".rz").forEach((handle) => {
      handle.addEventListener("pointerdown", (e) => {
        if (this.state.maximized || !API.available || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        try { handle.setPointerCapture(e.pointerId); } catch {}
        const dir = handle.dataset.dir;
        const start = {
          mx: e.screenX, my: e.screenY,
          x: window.screenX, y: window.screenY,
          w: window.outerWidth, h: window.outerHeight,
        };
        let queued = false;
        let pending = null;
        const flush = () => {
          queued = false;
          if (pending) API.winSetBounds(...pending);
        };
        const onMove = (ev) => {
          const dx = ev.screenX - start.mx;
          const dy = ev.screenY - start.my;
          let { x, y, w, h } = start;
          if (dir.includes("e")) w = start.w + dx;
          if (dir.includes("s")) h = start.h + dy;
          if (dir.includes("w")) { w = start.w - dx; x = start.x + dx; }
          if (dir.includes("n")) { h = start.h - dy; y = start.y + dy; }
          if (w < MIN_W) { if (dir.includes("w")) x -= MIN_W - w; w = MIN_W; }
          if (h < MIN_H) { if (dir.includes("n")) y -= MIN_H - h; h = MIN_H; }
          pending = [Math.round(x), Math.round(y), Math.round(w), Math.round(h)];
          if (!queued) {
            queued = true;
            requestAnimationFrame(flush);
          }
        };
        const onUp = () => {
          handle.removeEventListener("pointermove", onMove);
          handle.removeEventListener("pointerup", onUp);
          handle.removeEventListener("pointercancel", onUp);
          document.body.classList.remove("resizing");
          if (pending) API.winSetBounds(...pending);
          this._syncMaxState();
        };
        document.body.classList.add("resizing");
        handle.addEventListener("pointermove", onMove);
        handle.addEventListener("pointerup", onUp);
        handle.addEventListener("pointercancel", onUp);
      });
    });
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Global events (shortcuts, drag & drop, context dismissal)
  // ══════════════════════════════════════════════════════════════════════

  _bindGlobalEvents() {
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#contextMenu")) Components.hideContextMenu();
    });
    window.addEventListener("blur", () => Components.hideContextMenu());
    window.addEventListener("resize", () => Components.hideContextMenu());

    document.addEventListener("keydown", (e) => {
      const ae = document.activeElement;
      const typing = !!(ae && (
        /^(input|textarea|select)$/i.test(ae.tagName || "") ||
        ae.isContentEditable ||
        ae.getAttribute?.("contenteditable") === "true"));
      // Never hijack a shortcut while the user is typing in a field — Ctrl+V
      // in particular must keep its normal "paste into this input" meaning.
      if (e.ctrlKey && !e.shiftKey && (e.key === "n" || e.key === "N") && !typing) {
        e.preventDefault(); this.openNewDownload();
      } else if (e.ctrlKey && !e.shiftKey && (e.key === "v" || e.key === "V") && !typing) {
        // Paste a URL from the clipboard straight into a new download.
        e.preventDefault(); this.pasteFromClipboard();
      } else if (e.ctrlKey && e.key === ",") {
        e.preventDefault(); this.navigate("settings");
      } else if ((e.key === "/" && !typing) || (e.ctrlKey && (e.key === "f" || e.key === "F"))) {
        e.preventDefault(); Utils.$id("globalSearch").focus();
      } else if (e.key === " " && !typing && this.state.page === "downloads") {
        // Space toggles pause/resume for the current selection.
        const tasks = this._selectedTasks();
        if (tasks.length) {
          e.preventDefault();
          const anyRunning = tasks.some((t) => t.state === "Downloading");
          const ids = tasks.map((t) => t.id);
          if (anyRunning) this._forEachId(ids, (id) => API.pauseDownload(id));
          else this._forEachId(ids, (id) => API.resumeDownload(id));
        }
      } else if (e.key === "Delete" && !typing && this.state.page === "downloads") {
        const tasks = this._selectedTasks();
        if (tasks.length) {
          e.preventDefault();
          // Shift+Delete also removes the file on disk; Delete only removes
          // the task, matching the confirm copy shown by onRemove.
          if (e.shiftKey) {
            const done = tasks.filter((t) => t.state === "Complete");
            if (done.length) {
              done.forEach((t) => this.rowCallbacks.onDeleteFile(t.id, t.filename || Utils.fileName(t)));
            } else {
              this.rowCallbacks.onRemove(tasks.map((t) => t.id));
            }
          } else {
            this.rowCallbacks.onRemove(tasks.map((t) => t.id));
          }
        }
      } else if (e.key === "Escape" && !typing) {
        Components.hideContextMenu();
        if (this.state.page === "downloads" && this.state.selectedIds.size) {
          this.state.selectedIds.clear();
          this.state.selAnchor = null;
          this._syncSelection();
        }
      } else if (e.ctrlKey && (e.key === "a" || e.key === "A") && !typing && this.state.page === "downloads") {
        e.preventDefault();
        const vis = this._filteredTasks();
        this.state.selectedIds.clear();
        vis.forEach((t) => this.state.selectedIds.add(t.id));
        this.state.selAnchor = vis.length ? vis[0].id : null;
        this._syncSelection();
      }
    });

    // Drag & drop a link anywhere.
    let depth = 0;
    const overlay = Utils.$id("dropOverlay");
    window.addEventListener("dragenter", (e) => {
      if (![...(e.dataTransfer?.types || [])].some((t) => t.includes("text"))) return;
      depth++;
      overlay.classList.add("open");
    });
    window.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (!depth) overlay.classList.remove("open");
    });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", async (e) => {
      e.preventDefault();
      depth = 0;
      overlay.classList.remove("open");
      const urls = await this._extractDroppedUrls(e.dataTransfer);
      if (!urls.length) return;
      if (urls.length === 1) {
        this.openNewDownload(urls[0]);
      } else {
        // No explicit folder: the backend routes each URL to its category dir.
        const n = await API.addBatch(urls, "");
        Components.toast(I18N.t("toast.batch_queued", "Batch queued"), `${n} ${I18N.t("batch.queued", "queued")}`, "success");
        this.navigate("downloads");
      }
    });

    window.addEventListener("error", (e) => {
      API.logJs(`${e.message} @ ${e.filename}:${e.lineno}`);
    });
  },

  async _extractDroppedUrls(dt) {
    const extract = (text) => (text.match(/https?:\/\/[^\s"'<>]+/g) || []).filter(Boolean);
    let urls = [];
    const uriList = (dt.getData("text/uri-list") || "").trim();
    if (uriList) {
      urls = uriList.split("\n").map((l) => l.trim()).filter((l) => /^https?:\/\//i.test(l));
    }
    const text = (dt.getData("text/plain") || "").trim();
    if (!urls.length && text) urls = extract(text);
    // Local text files containing URLs (e.g. a .txt/.csv/.url dropped from Explorer).
    if (!urls.length && (dt.files && dt.files.length)) {
      const tf = Array.from(dt.files).find((f) => /\.(txt|csv|list|url)$/i.test(f.name));
      if (tf) {
        try {
          urls = extract(await tf.text());
        } catch (e) { API.logJs("drop file read: " + String(e)); }
      }
    }
    return [...new Set(urls)];
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Backend event polling
  // ══════════════════════════════════════════════════════════════════════

  _startPolling() {
    const poll = async () => {
      try {
        const events = await API.pollEvents();
        if (events && events.length) {
          requestAnimationFrame(() => events.forEach((evt) => this._handleEvent(evt)));
        }
      } catch {}
      setTimeout(poll, 200);
    };
    poll();
  },

  _handleEvent(evt) {
    if (evt.type === "task" && evt.task) {
      const t = evt.task;
      const had = !!this.state.downloads[t.id];
      if (evt.event === "removed") {
        delete this.state.downloads[t.id];
        delete this.state.elapsed[t.id];
        this.state.selectedIds.delete(t.id);
        if (!this.state.selectedIds.size) this.state.selAnchor = null;
        this._removeRow(t.id);
      } else {
        const prev = this.state.downloads[t.id];
        this.state.downloads[t.id] = t;
        this._elapsedFor(t); // keep the active-time tracker honest on every event
        if (!had) {
          this._addRow(t);
        } else if (this.state.sortKey === "queue" && prev && prev.queue_index !== t.queue_index) {
          // A queue reorder moved this row.  _updateRow only rewrites cells in
          // place, so rebuild the list to actually reorder it.
          this.state.listSig = "";
          this._renderDownloads(true);
        } else {
          this._updateRow(t);
        }
      }
      this._updateBadge();
      this._updateCounts();
      this._renderDashboardLists();
      if (evt.event === "finished") {
        if (t.state === "Complete") {
          Components.toast(I18N.t("toast.download_complete", "Download complete"), Utils.fileName(t) + " — " + Utils.formatSize(t.completed || t.total), "success");
          this._refreshHistory();
        } else if (t.state === "Failed") {
          Components.toast(I18N.t("toast.download_failed", "Download failed"), `${Utils.fileName(t)}${t.error ? " — " + t.error : ""}`, "error");
          this._refreshHistory();
        } else if (t.state === "Cancelled") {
          Components.toast(I18N.t("toast.download_cancelled", "Download cancelled"), Utils.fileName(t), "info");
        }
      }
    } else if (evt.type === "log") {
      this.state.logs.push(evt.message);
      if (this.state.logs.length > 800) this.state.logs.shift();
      if (this.state.page === "logs") this._appendLog(evt.message);
    } else if (evt.type === "browser_url") {
      // Proof the extension is installed and paired with our live server.
      this.state.extLastSeen = Date.now();
      this._renderExtPill();
      Components.toast(I18N.t("toast.link_captured", "Link captured"), I18N.t("toast.link_captured_msg", "Received from browser extension"), "info");
      this.openNewDownload(evt.url);
    } else if (evt.type === "clipboard_url") {
      this._onClipboardLink(evt.url);
    } else if (evt.type === "navigate") {
      this.navigate(evt.page || "dashboard");
    } else if (evt.type === "toast") {
      Components.toast(evt.title || "Notice", evt.message || "", evt.kind || "info");
    } else if (evt.type === "ext_install") {
      const stages = {
        "Locating extension...": "ext_install.stage.locating",
        "Opening Chrome...": "ext_install.stage.opening",
        "Opening chrome://extensions/...": "ext_install.stage.page",
        "Checking Developer Mode...": "ext_install.stage.developer",
        "Clicking Load unpacked...": "ext_install.stage.load",
        "Waiting for folder picker...": "ext_install.stage.picker",
        "Selecting extension directory...": "ext_install.stage.selecting",
        "Clicking Select Folder...": "ext_install.stage.folder",
        "Verifying installation...": "ext_install.stage.verifying",
      };
      const key = stages[evt.stage] || "ext_install.stage.locating";
      Components.toast(I18N.t("ext_install.title", "Installing extension…"), I18N.t(key, evt.stage), "info", 2600);
    } else if (evt.type === "ext_install_done") {
      this.state.extInstalling = false;
      const btn = Utils.$id("btnInstallExt");
      if (btn) btn.disabled = false;
      if (evt.ok) {
        Components.toast(I18N.t("ext_install.done", "Extension installed successfully"), I18N.t("ext_install.done_msg", "N13 is now connected to Chrome"), "success", 8000);
      } else {
        Components.toast(I18N.t("ext_install.failed", "Extension install failed"), String(evt.error || ""), "error", 10000);
      }
    } else if (evt.type === "window") {
      this._setMaxState(!!evt.maximized);
    } else if (evt.type === "update_state") {
      this._applyUpdateState(evt.state);
      const st = evt.state;
      if (st.state === "available" && st.release?.version) {
        const skipped = this.state.updateSettings?.skipped_version;
        if (st.release.version !== skipped && this._updateNotifiedFor !== st.release.version) {
          this._updateNotifiedFor = st.release.version;
          // Subtle notification — the user explicitly chooses to view or defer.
          Components.toast(
            I18N.t("update.notify_title", "N13 Update Available"),
            I18N.fmt("update.notify_body", { version: st.release.version }),
            "info", 10000,
            { label: I18N.t("update.view_update", "View Update"), onClick: () => this._showUpdateDialog(st.release) },
          );
        }
      }
    }
  },

  /**
   * Clipboard URL detected.
   *
   * A non-blocking toast with a one-click "Download" action replaces the old
   * modal, so copying a link never interrupts what the user is doing and the
   * most common response is a single click.
   */
  async _onClipboardLink(url) {
    try {
      let host = url;
      try { host = new URL(url).hostname || url; } catch { /* keep raw url */ }

      Components.toast(
        I18N.t("clip.detected", "Download detected"),
        host,
        "info",
        9000,
        {
          label: I18N.t("clip.download_now", "Download"),
          onClick: () => this.openNewDownload(url),
        });
    } catch (e) {
      API.logJs("clipboard link: " + String(e));
    }
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Downloads page
  // ══════════════════════════════════════════════════════════════════════

  rowCallbacks: {
    onRowSelect(id, e) { App._onRowSelect(id, e); },
    // Resolve the action target list for a right-clicked task (context menu).
    targetsFor(id) { return App._actionTargetIds(id); },

    onPause(x) { App._forEachId(App._asIds(x), (id) => API.pauseDownload(id)); },
    onResume(x) { App._forEachId(App._asIds(x), (id) => API.resumeDownload(id)); },
    onStart(x) { App._forEachId(App._asIds(x), (id) => API.startTask(id)); },
    onRetry(x) { App._forEachId(App._asIds(x), (id) => API.retryDownload(id)); },
    async onCancel(x) {
      const ids = App._asIds(x);
      const n = ids.length;
      const ok = await Components.confirm({
        title: I18N.t("confirm.cancel_download", "Cancel download"),
        message: n > 1
          ? I18N.t("confirm.cancel_download_multi", "Cancel {n} downloads?").replace("{n}", n)
          : I18N.t("confirm.cancel_download_msg", "Stop this download? Progress is saved so you can resume later."),
        okText: I18N.t("act.cancel", "Cancel"), cancelText: I18N.t("confirm.keep", "Keep"), danger: true,
      });
      if (ok) App._forEachId(ids, (id) => API.cancelDownload(id));
    },
    async onRemove(x) {
      const ids = App._asIds(x);
      const n = ids.length;
      const ok = await Components.confirm({
        title: I18N.t("confirm.remove_download", "Remove download"),
        message: n > 1
          ? I18N.t("confirm.remove_download_multi", "Remove {n} selected downloads?").replace("{n}", n)
          : I18N.t("confirm.remove_download_msg", "Remove this entry from the list? The file on disk is kept."),
        okText: I18N.t("act.remove", "Remove"), cancelText: I18N.t("confirm.keep", "Keep"), danger: true,
      });
      if (ok) App._forEachId(ids, (id) => API.removeDownload(id));
    },
    onOpenFolder(id) { API.openFolder(id); },
    onOpenFile(id) { API.openFile(id); },
    onRedownload(id) { API.redownload(id); },
    onMove(x, delta) {
      const ids = App._asIds(x);
      if (ids.length <= 1) { API.moveTask(ids[0], delta); return; }
      // Move the selected group together, preserving its internal order:
      // forward for up, reverse for down (relative to the displayed order).
      const order = Array.from(Utils.$qa("#downloadList .dl-row")).map((r) => r.dataset.id);
      const sorted = ids.filter((id) => order.includes(id)).sort((a, b) => order.indexOf(a) - order.indexOf(b));
      const seq = delta < 0 ? sorted : sorted.slice().reverse();
      seq.forEach((id) => API.moveTask(id, delta));
    },
    // UI-facing wrapper: queue reordering is invisible under a date sort, so
    // flip to queue order first.  Kept separate from onMove so the pure
    // dispatch logic above stays unit-testable.
    onReorder(x, delta) {
      App._ensureQueueOrder();
      App.rowCallbacks.onMove(x, delta);
    },
    async onCopyPath(task) {
      const p = `${task.directory}\\${task.filename || Utils.fileName(task)}`;
      try {
        await navigator.clipboard.writeText(p);
        Components.toast(I18N.t("toast.copied", "Copied"), I18N.t("toast.copied_path", "File path copied to clipboard"), "info", 2200);
      } catch {
        Components.toast(I18N.t("toast.copy_failed", "Copy failed"), I18N.t("toast.copy_failed_msg", "Clipboard is unavailable"), "error");
      }
    },
    async onDeleteFile(id, name) {
      const ok = await Components.confirm({
        title: I18N.t("confirm.delete_file", "Delete file"),
        message: I18N.t("confirm.delete_file_msg", "Permanently delete “{name}” from disk? This cannot be undone.").replace("{name}", name),
        okText: I18N.t("confirm.delete", "Delete"), cancelText: I18N.t("confirm.keep", "Keep"), danger: true, icon: "trash",
      });
      if (ok) {
        const done = await API.deleteFile(id);
        if (done) Components.toast(I18N.t("toast.delete_file_done", "File deleted"), name, "success");
        else Components.toast(I18N.t("toast.delete_failed", "Delete failed"), I18N.t("toast.delete_failed_msg", "The file could not be deleted"), "error");
      }
    },
    async onCopyUrl(x) {
      const ids = App._asIds(x);
      const urls = ids
        .map((id) => (App.state.downloads && App.state.downloads[id]) || null)
        .filter((t) => t && t.url)
        .map((t) => t.url);
      if (!urls.length) return;
      const text = urls.join("\n");
      try {
        await navigator.clipboard.writeText(text);
        if (urls.length > 1) {
          Components.toast(I18N.t("toast.copied", "Copied"), I18N.t("toast.copied_urls", "{n} URLs copied to clipboard").replace("{n}", urls.length), "info", 2200);
        } else {
          Components.toast(I18N.t("toast.copied", "Copied"), I18N.t("toast.copied_url", "Download URL copied to clipboard"), "info", 2200);
        }
      } catch {
        Components.toast(I18N.t("toast.copy_failed", "Copy failed"), I18N.t("toast.copy_failed_msg", "Clipboard is unavailable"), "error");
      }
    },
    onRename(id) { App.renameTask(id); },
    onSpeedLimit(id) { App.openSpeedLimit(id); },
    onPriority(x) { App.openPriority(x); },
    onProperties(x) { App.showProperties(x); },
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Selection action bar
  // ══════════════════════════════════════════════════════════════════════

  /** Currently selected task snapshots, in displayed order. */
  _selectedTasks() {
    const sel = this.state.selectedIds;
    if (!sel || !sel.size) return [];
    const order = Array.from(Utils.$qa("#downloadList .dl-row")).map((r) => r.dataset.id);
    const ids = order.filter((id) => sel.has(id));
    const rest = Array.from(sel).filter((id) => !order.includes(id));
    return [...ids, ...rest].map((id) => this.state.downloads[id]).filter(Boolean);
  },

  /**
   * Rebuild the docked action bar for the current selection.
   *
   * Only actions valid for the selected states are rendered, so the bar stays
   * short and never shows a button that cannot do anything.
   */
  _renderSelBar() {
    const bar = Utils.$id("selBar");
    if (!bar) return;
    const tasks = this._selectedTasks();

    if (!tasks.length) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;

    const n = tasks.length;
    const countEl = Utils.$id("sbCount");
    if (countEl) countEl.textContent = n;
    const lblEl = Utils.$id("sbCountLabel");
    if (lblEl) {
      lblEl.textContent = n > 1
        ? I18N.t("sel.selected_plural", "selected")
        : I18N.t("sel.selected", "selected");
    }

    const actions = Components.selectionActions(tasks, this.rowCallbacks);
    const host = Utils.$id("sbActions");
    if (!host) return;

    host.innerHTML = actions.map((a) => {
      if (a.separator) return '<span class="sb-divider" aria-hidden="true"></span>';
      const cls = a.kind === "primary" ? " primary" : (a.kind === "danger" ? " danger" : "");
      return `<button class="sb-btn${cls}" data-sb="${a.id}" data-i18n-tip="${a.label}">${Utils.icon(a.icon, 15)}<span>${Utils.escapeHtml(a.label)}</span></button>`;
    }).join("");

    // Wire fresh handlers each render (the list is short and changes with state).
    Utils.$qa("[data-sb]", host).forEach((btn) => {
      const id = btn.dataset.sb;
      const a = actions.find((x) => x.id === id);
      if (!a || !a.run) return;
      btn.addEventListener("click", () => a.run());
    });
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Rename / speed limit / properties
  // ══════════════════════════════════════════════════════════════════════

  async renameTask(id) {
    const t = this.state.downloads[id];
    if (!t) return;
    const active = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Stopping"];
    if (active.includes(t.state)) {
      Components.toast(
        I18N.t("toast.rename_blocked", "Cannot rename while downloading"),
        I18N.t("toast.rename_blocked_msg", "Pause or cancel the download first."),
        "warning");
      return;
    }
    const current = t.filename || Utils.fileName(t);
    const next = await Components.renameDialog(current);
    if (!next || next === current) return;

    try {
      const res = await API.renameDownload(id, next);
      if (res && res.ok) {
        Components.toast(I18N.t("toast.renamed", "Renamed"), res.name || next, "success", 2400);
      } else {
        const err = (res && res.error) || "";
        const msg = err === "task_active"
          ? I18N.t("toast.rename_blocked_msg", "Pause or cancel the download first.")
          : err === "target_exists"
            ? I18N.t("toast.rename_exists", "A file with that name already exists.")
            : err === "invalid_name"
              ? I18N.t("toast.rename_invalid", "That name is not valid.")
              : err;
        Components.toast(I18N.t("toast.rename_failed", "Rename failed"), msg, "error");
      }
    } catch (e) {
      API.logJs("rename: " + String(e));
      Components.toast(I18N.t("toast.rename_failed", "Rename failed"), String(e), "error");
    }
  },

  async openSpeedLimit(id) {
    const t = this.state.downloads[id];
    if (!t) return;
    const bps = await Components.speedLimitDialog(t.speed_limit_bps || 0, t.filename || Utils.fileName(t));
    if (bps === null) return;
    try {
      await API.setTaskSpeedLimit(id, bps);
      Components.toast(
        I18N.t("toast.speed_limit_set", "Speed limit updated"),
        bps > 0 ? Utils.formatSpeed(bps) : I18N.t("dlg.unlimited", "Unlimited"),
        "success", 2400);
    } catch (e) {
      API.logJs("speed limit: " + String(e));
    }
  },

  showProperties(x) {
    const ids = this._asIds(x);
    const tasks = ids.map((id) => this.state.downloads[id]).filter(Boolean);
    Components.propertiesDialog(tasks);
  },

  /**
   * Set the scheduling priority of one or several downloads.
   *
   * The stored scale runs 0 = highest … 10 = lowest; the dialog presents it
   * as High / Normal / Low so the direction is never ambiguous.
   */
  async openPriority(x) {
    const ids = this._asIds(x).filter((id) => this.state.downloads[id]);
    if (!ids.length) return;
    const first = this.state.downloads[ids[0]];
    const current = ids.length > 1 ? 5 : (first.priority ?? 5);
    const value = await Components.priorityDialog(
      current,
      ids.length > 1 ? "" : (first.filename || Utils.fileName(first)));
    if (value === null) return;

    const label = value <= 3
      ? I18N.t("dlg.pri_high", "High")
      : value >= 8 ? I18N.t("dlg.pri_low", "Low") : I18N.t("dlg.pri_normal", "Normal");
    try {
      for (const id of ids) await API.setPriority(id, value);
      Components.toast(
        ids.length > 1
          ? I18N.t("toast.priority_set_many", "Priority updated for {n} downloads").replace("{n}", ids.length)
          : I18N.t("toast.priority_set", "Priority updated"),
        `${label} (${value})`, "success", 2400);
    } catch (e) {
      API.logJs("priority: " + String(e));
      Components.toast(I18N.t("toast.priority_failed", "Could not set priority"), String(e), "error");
    }
  },

  /**
   * Switch the list to queue order so Move up/down is actually visible.
   *
   * Reordering the backend queue does nothing you can see while the list is
   * sorted by date, so the first move flips the sort — and says so, rather
   * than silently changing the order behind the user's back.
   */
  _ensureQueueOrder() {
    if (this.state.sortKey === "queue") return;
    this.state.sortKey = "queue";
    this.state.sortDir = 1;
    const sel = Utils.$id("sortSelect");
    if (sel) sel.value = "queue:1";
    this.state.listSig = "";
    this._renderDownloads(true);
    Components.toast(
      I18N.t("toast.queue_order_on", "Showing queue order"),
      I18N.t("toast.queue_order_on_msg", "Move up / down changes the order downloads start in."),
      "info", 3200);
  },

  _bindDownloadsPage() {
    Utils.$qa("#filterChips .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        Utils.$qa("#filterChips .chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        this.state.filter = chip.dataset.filter;
        this._renderDownloads(true);
      });
    });

    const sortSel = Utils.$id("sortSelect");
    sortSel.addEventListener("change", () => {
      const [key, dir] = sortSel.value.split(":");
      this.state.sortKey = key;
      this.state.sortDir = +dir;
      this._renderDownloads(true);
    });

    Utils.$id("btnPauseAll").addEventListener("click", async () => {
      await API.pauseAll();
      Components.toast(I18N.t("toast.all_paused", "All paused"), I18N.t("toast.all_paused_msg", "Every active download was paused"), "info");
    });
    Utils.$id("btnResumeAll").addEventListener("click", async () => {
      await API.resumeAll();
      Components.toast(I18N.t("toast.all_resumed", "All resumed"), I18N.t("toast.all_resumed_msg", "Paused downloads are running again"), "info");
    });
    Utils.$id("btnClearFinished").addEventListener("click", async () => {
      await API.clearFinished();
      Components.toast(I18N.t("toast.list_cleared", "List cleared"), I18N.t("toast.list_cleared_msg", "Finished entries were removed"), "info");
    });

    // Selection action bar
    const sbClear = Utils.$id("sbClear");
    if (sbClear) sbClear.addEventListener("click", () => this._clearSelection());

    // Queue strip controls
    const limitBtn = Utils.$id("qsLimitBtn");
    if (limitBtn) limitBtn.addEventListener("click", () => this._editGlobalLimit());
    const schedBtn = Utils.$id("qsSchedBtn");
    if (schedBtn) schedBtn.addEventListener("click", () => this._editScheduler());
    const retryBtn = Utils.$id("qsRetryFailed");
    if (retryBtn) {
      retryBtn.addEventListener("click", async () => {
        const n = await API.retryFailed();
        Components.toast(
          I18N.t("toast.retrying_failed", "Retrying failed downloads"),
          I18N.fmt("toast.n_requeued", { n }, "{n} re-queued").replace("{n}", n),
          "success");
      });
    }
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Command bar (always-visible primary actions)
  // ══════════════════════════════════════════════════════════════════════

  _bindCommandBar() {
    const on = (id, fn) => {
      const el = Utils.$id(id);
      if (el) el.addEventListener("click", fn);
    };
    on("cmdNewDownload", () => this.openNewDownload());
    on("cmdPasteUrl", () => this.pasteFromClipboard());
    on("cmdBatch", () => this.navigate("batch"));
    on("extPill", () => {
      // A connected extension has nothing to fix — jump to the Browser page
      // so the user can manage it.  Otherwise start the guided setup.
      if (this.state.extConnected) this.navigate("browser");
      else this._setupExtension();
    });

    this._renderExtPill();
  },

  /** How long a browser capture keeps the extension marked as "connected". */
  _EXT_FRESH_MS: 5 * 60 * 1000,

  /**
   * Reflect live extension/server state in the always-visible status pill.
   *
   * The live server exposes no "extension installed" flag, so connectivity is
   * inferred honestly: the extension is considered connected once it has
   * actually sent us a link within the freshness window.  That is the only
   * signal that proves the extension is installed *and* paired.
   */
  _renderExtPill() {
    const pill = Utils.$id("extPill");
    if (!pill) return;
    const running = !!this.state.serverRunning;
    const fresh = !!this.state.extLastSeen &&
      (Date.now() - this.state.extLastSeen) < this._EXT_FRESH_MS;
    const connected = running && fresh;
    this.state.extConnected = connected;

    pill.classList.toggle("on", connected);
    pill.classList.toggle("off", !connected);

    const title = Utils.$id("extPillTitle");
    const sub = Utils.$id("extPillSub");
    const act = Utils.$id("extPillAction");
    if (!title || !sub || !act) return;

    if (connected) {
      title.textContent = I18N.t("browser.ext_on", "Extension connected");
      sub.textContent = I18N.t("browser.ext_on_sub", "Right-click any link to send it to N13");
      act.textContent = I18N.t("browser.manage", "Manage");
    } else if (running) {
      title.textContent = I18N.t("browser.ext_waiting", "Waiting for extension");
      sub.textContent = I18N.t("browser.ext_waiting_sub", "Server is running — install the extension");
      act.textContent = I18N.t("browser.setup", "Set up");
    } else {
      title.textContent = I18N.t("browser.ext_off", "Extension not connected");
      sub.textContent = I18N.t("browser.ext_off_sub", "Click to set up");
      act.textContent = I18N.t("browser.setup", "Set up");
    }
  },

  /** Start the live server and open the guided install flow. */
  async _setupExtension() {
    this.navigate("browser");
    try {
      if (!this.state.serverRunning) {
        await API.startLiveServer();
        await this._refreshServerStatus();
      }
      Components.toast(
        I18N.t("browser.setup_started", "Browser setup"),
        I18N.t("browser.setup_started_msg", "Press “Install Extension” on the Browser page to finish."),
        "info", 6000);
    } catch (e) {
      API.logJs("setup extension: " + String(e));
    }
  },

  /**
   * Paste a URL straight from the clipboard into the New Download dialog.
   *
   * Falls back to an empty dialog (prefilled nothing) when the clipboard is
   * unreadable, so Ctrl+V never dead-ends the user.
   */
  async pasteFromClipboard() {
    const url = await this._readClipboardUrl();
    if (!url) {
      Components.toast(
        I18N.t("toast.no_link", "No link in clipboard"),
        I18N.t("toast.no_link_msg", "Copy a download link first, or type the address manually."),
        "warning");
    }
    this.openNewDownload(url || null, { paste: true });
  },

  /**
   * Resolve a download URL from the clipboard.
   *
   * Tries the WebView clipboard first (fast, no IPC) and falls back to the
   * native reader in the Python shell, because WebView2 may deny
   * ``navigator.clipboard`` for local-origin documents.  Returns "" when
   * neither yields a link.
   */
  async _readClipboardUrl() {
    const pick = (text) => ((text || "").match(/https?:\/\/[^\s"'<>]+/) || [])[0] || "";
    try {
      const fromJs = pick(await navigator.clipboard.readText());
      if (fromJs) return fromJs;
    } catch (e) {
      API.logJs("clipboard read (js): " + String(e));
    }
    try {
      return pick(await API.readClipboardUrl());
    } catch (e) {
      API.logJs("clipboard read (native): " + String(e));
      return "";
    }
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Queue strip
  // ══════════════════════════════════════════════════════════════════════

  _renderQueueStrip() {
    const all = this._taskArray();
    const ACTIVE = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    const active = all.filter((t) => ACTIVE.includes(t.state));
    const waiting = all.filter((t) => t.state === "Queued");
    const failed = all.filter((t) => ["Failed", "Cancelled", "Stopped"].includes(t.state));

    const set = (id, v) => { const el = Utils.$id(id); if (el) el.textContent = v; };
    set("qsActive", active.length);
    set("qsWaiting", waiting.length);
    set("qsSpeed", Utils.formatSpeed(active.reduce((s, t) => s + (t.speed_bps || 0), 0)));

    const limit = (this.state.settings && this.state.settings.max_speed_bps) || 0;
    const limitBtn = Utils.$id("qsLimitBtn");
    if (limitBtn) {
      limitBtn.textContent = limit > 0 ? Utils.formatSpeed(limit) : I18N.t("dlg.unlimited", "Unlimited");
      limitBtn.classList.toggle("warn", limit > 0);
    }

    const sched = this.state.settings || {};
    const schedBtn = Utils.$id("qsSchedBtn");
    if (schedBtn) {
      const on = !!sched.scheduler_enabled;
      schedBtn.textContent = this._scheduleLabel(sched);
      schedBtn.classList.toggle("good", on);
    }

    const retryBtn = Utils.$id("qsRetryFailed");
    if (retryBtn) {
      retryBtn.hidden = failed.length === 0;
      const lbl = Utils.$id("qsRetryLabel");
      if (lbl) lbl.textContent = `${I18N.t("queue.retry_failed", "Retry failed")} (${failed.length})`;
    }
  },

  /** Quick global bandwidth-cap editor driven from the queue strip. */
  async _editGlobalLimit() {
    const current = (this.state.settings && this.state.settings.max_speed_bps) || 0;
    const bps = await Components.speedLimitDialog(current, I18N.t("queue.speed_limit", "Global limit"));
    if (bps === null) return;
    try {
      await API.updateSettings({ max_speed_bps: bps });
      this.state.settings = { ...(this.state.settings || {}), max_speed_bps: bps };
      this._renderQueueStrip();
      Components.toast(
        I18N.t("toast.speed_limit_set", "Speed limit updated"),
        bps > 0 ? Utils.formatSpeed(bps) : I18N.t("dlg.unlimited", "Unlimited"),
        "success", 2400);
    } catch (e) {
      API.logJs("global limit: " + String(e));
    }
  },

  /** Label for the current schedule, shared by the queue strip and toasts. */
  _scheduleLabel(s = this.state.settings || {}) {
    if (!s.scheduler_enabled) return I18N.t("queue.off", "Off");
    const win = `${s.schedule_start_time || "—"}–${s.schedule_stop_time || "—"}`;
    const days = Array.isArray(s.schedule_days) ? s.schedule_days : [];
    // Only mention days when they actually narrow the window.
    return days.length ? `${win} · ${days.length}/7` : win;
  },

  /** Quick scheduler editor driven from the queue strip (never leaves the page). */
  async _editScheduler() {
    const s = this.state.settings || {};
    const res = await Components.schedulerDialog(s);
    if (res === null) return;   // cancelled or closed — discard everything
    try {
      await API.updateSettings({
        scheduler_enabled: res.enabled,
        schedule_start_time: res.start,
        schedule_stop_time: res.stop,
        schedule_days: res.days,
      });
      this.state.settings = {
        ...s,
        scheduler_enabled: res.enabled,
        schedule_start_time: res.start,
        schedule_stop_time: res.stop,
        schedule_days: res.days,
      };
      this._renderQueueStrip();
      Components.toast(
        I18N.t("toast.scheduler_set", "Scheduler updated"),
        this._scheduleLabel(),
        "success", 2400);
    } catch (e) {
      API.logJs("scheduler: " + String(e));
    }
  },

  _taskArray() { return Object.values(this.state.downloads); },

  // ══════════════════════════════════════════════════════════════════════
  //  Downloads list multi-selection (Windows File Explorer style)
  // ══════════════════════════════════════════════════════════════════════

  _onRowSelect(id, e) {
    const sel = this.state.selectedIds;
    const ctrl = !!(e && (e.ctrlKey || e.metaKey));
    const shift = !!(e && e.shiftKey);

    // Current visual order of the rendered list (range selection follows it).
    const order = Array.from(Utils.$qa("#downloadList .dl-row")).map((r) => r.dataset.id);
    const clicked = order.indexOf(id);

    if (shift && this.state.selAnchor != null && clicked !== -1) {
      const a = order.indexOf(this.state.selAnchor);
      if (a !== -1) {
        const [lo, hi] = a <= clicked ? [a, clicked] : [clicked, a];
        const range = new Set(order.slice(lo, hi + 1));
        if (ctrl) {
          // Ctrl+Shift: add the range to the current selection (no duplicates).
          range.forEach((x) => sel.add(x));
        } else {
          // Shift: replace the selection with the range.
          sel.clear();
          range.forEach((x) => sel.add(x));
        }
        // Anchor stays the same across Shift ranges.
      } else {
        sel.clear(); sel.add(id);
        this.state.selAnchor = id;
      }
    } else if (ctrl) {
      // Ctrl+Click: toggle the item, keep the rest of the selection.
      if (sel.has(id)) sel.delete(id); else sel.add(id);
      this.state.selAnchor = id;
    } else {
      // Normal click: clear previous selection, select only this item.
      sel.clear(); sel.add(id);
      this.state.selAnchor = id;
    }
    if (!sel.size) this.state.selAnchor = null;
    this._syncSelection();
  },

  _syncSelection() {
    const sel = this.state.selectedIds;
    const visible = new Set();
    Utils.$qa("#downloadList .dl-row").forEach((row) => {
      const id = row.dataset.id;
      visible.add(id);
      const on = sel.has(id);
      row.classList.toggle("sel", on);
      if (on) row.setAttribute("aria-selected", "true");
      else row.removeAttribute("aria-selected");
    });
    // Drop stale ids / anchor that are no longer in the current view.
    let pruned = false;
    sel.forEach((id) => { if (!visible.has(id)) { sel.delete(id); pruned = true; } });
    if (pruned && !sel.size) this.state.selAnchor = null;
    if (this.state.selAnchor != null && !visible.has(this.state.selAnchor)) this.state.selAnchor = null;
    this._renderSelBar();
  },

  _clearSelection() {
    this.state.selectedIds.clear();
    this.state.selAnchor = null;
    this._renderSelBar();
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Context-menu action targeting (Windows File Explorer rule)
  // ══════════════════════════════════════════════════════════════════════
  //
  // If the right-clicked task is part of the current multi-selection, the
  // action applies to the WHOLE selection.  Otherwise it applies only to the
  // right-clicked task.  This keeps `selectedIds` (selection) and `contextId`
  // (right-clicked task) as separate concepts.

  _actionTargetIds(contextId) {
    const sel = this.state.selectedIds;
    if (contextId && sel.has(contextId) && sel.size > 1) return Array.from(sel);
    return contextId ? [contextId] : [];
  },

  _asIds(x) {
    return Array.isArray(x) ? x : [x];
  },

  // Run a single-task async action over every id, never letting one failure
  // stop the rest.
  async _forEachId(ids, fn) {
    for (const id of ids) {
      try { await fn(id); } catch (e) { API.logJs("batch action: " + String(e)); }
    }
  },

  _filteredTasks() {
    const { filter, search, sortKey, sortDir } = this.state;
    let list = this._taskArray();

    if (filter !== "all") {
      const map = {
        active: ["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Stopping"],
        queued: ["Queued"],
        paused: ["Paused"],
        completed: ["Complete"],
        failed: ["Failed", "Cancelled", "Stopped"],
      };
      const states = map[filter] || [];
      list = list.filter((t) => states.includes(t.state));
    }

    if (search) {
      list = list.filter((t) =>
        Utils.fileName(t).toLowerCase().includes(search) ||
        (t.url || "").toLowerCase().includes(search) ||
        Utils.hostOf(t.url).includes(search));
    }

    // Category view.  Tasks with no category are treated as "General", which
    // is exactly what the backend defaults them to.
    if (this.state.catFilter !== "all") {
      list = list.filter((t) => (t.category || "General") === this.state.catFilter);
    }

    const key = {
      newest: (t) => t.created_at || 0,
      name: (t) => Utils.fileName(t).toLowerCase(),
      size: (t) => t.total || 0,
      progress: (t) => (t.total > 0 ? t.completed / t.total : 0),
      speed: (t) => t.speed_bps || 0,
      status: (t) => t.state,
      // Backend queue position.  Tasks the manager no longer tracks (-1) sort
      // to the bottom rather than jumping to the top.
      queue: (t) => (t.queue_index >= 0 ? t.queue_index : Number.MAX_SAFE_INTEGER),
    }[sortKey] || ((t) => t.created_at || 0);

    list = [...list].sort((a, b) => {
      const ka = key(a), kb = key(b);
      if (ka < kb) return -sortDir;
      if (ka > kb) return sortDir;
      return 0;
    });
    return list;
  },

  _renderDownloads(structureChanged = false) {
    if (this.state.page !== "downloads" && !structureChanged) return;
    const listEl = Utils.$id("downloadList");
    const headEl = Utils.$id("downloadHead");
    const emptyEl = Utils.$id("downloadsEmpty");
    const tasks = this._filteredTasks();
    const sig = tasks.map((t) => t.id).join("|") + "::" + this.state.filter + this.state.sortKey + this.state.sortDir + this.state.search + "::" + this.state.catFilter;

    if (sig === this.state.listSig && !structureChanged) {
      tasks.forEach((t) => this._updateRow(t));
      // Task states may have changed, which changes the available actions.
      if (this.state.selectedIds.size) this._renderSelBar();
      return;
    }
    this.state.listSig = sig;
    this._renderCatStrip();

    if (!this._taskArray().length) {
      headEl.hidden = true;
      listEl.innerHTML = "";
      this._clearSelection();
      emptyEl.replaceChildren(Components.emptyState({
        icon: "download",
        title: I18N.t("empty.no_downloads", "No downloads yet"),
        desc: I18N.t("empty.no_downloads_desc", "Paste a link or drop it anywhere to start your first download."),
        actions: [
          {
            label: I18N.t("cmd.paste_url", "Paste URL"),
            icon: "paste",
            primary: true,
            onClick: () => this.pasteFromClipboard(),
          },
          {
            label: I18N.t("empty.install_extension", "Install Browser Extension"),
            icon: "browser",
            onClick: () => this._setupExtension(),
          },
          {
            label: I18N.t("empty.import", "Import Downloads"),
            icon: "batch",
            onClick: () => this.navigate("batch"),
          },
        ],
      }));
      emptyEl.hidden = false;
      return;
    }

    if (!tasks.length) {
      headEl.hidden = true;
      listEl.innerHTML = "";
      this._clearSelection();
      const cat = this.state.catFilter;
      emptyEl.replaceChildren(Components.emptyState({
        icon: "search",
        title: I18N.t("empty.nothing_matches", "Nothing matches"),
        desc: cat !== "all"
          ? I18N.t("empty.nothing_in_category", "No downloads in this category yet.")
          : I18N.t("empty.nothing_matches_desc", "Try a different filter or search term."),
        actions: cat !== "all"
          ? [{
            label: I18N.t("cat.show_all", "Show all categories"),
            icon: "list",
            primary: true,
            onClick: () => this._setCatFilter("all"),
          }]
          : undefined,
      }));
      emptyEl.hidden = false;
      return;
    }

    emptyEl.hidden = true;
    headEl.hidden = false;
    const frag = document.createDocumentFragment();
    tasks.forEach((t) => frag.appendChild(Components.renderRow(t, this.rowCallbacks, this._elapsedFor(t))));
    listEl.replaceChildren(frag);
    this._syncSelection();

    if (this.state.highlightId) {
      const row = listEl.querySelector(`[data-id="${this.state.highlightId}"]`);
      if (row) {
        row.classList.add("flash");
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      this.state.highlightId = null;
    }
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Category filter
  // ══════════════════════════════════════════════════════════════════════

  /** Canonical category order — matches the backend's routing defaults. */
  _CAT_ORDER: ["General", "Compressed", "Videos", "Music", "Documents", "Programs", "Images"],

  /**
   * Render the category filter strip above the list.
   *
   * Hidden unless the list actually spans more than one category: with a
   * single category (or none) the strip is pure noise and just eats a row.
   */
  _renderCatStrip() {
    const strip = Utils.$id("catStrip");
    if (!strip) return;

    const all = this._taskArray();
    const counts = new Map();
    all.forEach((t) => {
      const c = t.category || "General";
      counts.set(c, (counts.get(c) || 0) + 1);
    });

    if (counts.size < 2) {
      strip.hidden = true;
      strip.innerHTML = "";
      // A filter pointing at a category that no longer exists would hide
      // every row with no way back — drop it.
      if (this.state.catFilter !== "all" && !counts.has(this.state.catFilter)) {
        this.state.catFilter = "all";
      }
      return;
    }

    const ordered = [
      ...this._CAT_ORDER.filter((c) => counts.has(c)),
      ...Array.from(counts.keys()).filter((c) => !this._CAT_ORDER.includes(c)).sort(),
    ];

    const chip = (key, label, n) => {
      const active = this.state.catFilter === key;
      return `<button class="cat-chip${active ? " active" : ""}" data-cat="${Utils.escapeHtml(key)}" role="tab" aria-selected="${active}">`
        + `<span class="cat-t">${Utils.escapeHtml(label)}</span><span class="cat-n">${n}</span></button>`;
    };

    strip.innerHTML =
      chip("all", I18N.t("cat.all", "All categories"), all.length) +
      ordered.map((c) => chip(c, I18N.t("category." + c, c), counts.get(c))).join("");
    strip.hidden = false;

    Utils.$qa(".cat-chip", strip).forEach((el) => {
      el.addEventListener("click", () => this._setCatFilter(el.dataset.cat));
    });
  },

  _setCatFilter(cat) {
    this.state.catFilter = cat || "all";
    this.state.listSig = "";
    this._renderDownloads(true);
  },

  _addRow(task) {
    // New task arrived — refresh structure cheaply.
    this.state.listSig = "";
    this._renderDownloads();
  },
  /**
   * Active-time accumulator for the unified progress component.
   *
   * Elapsed counts only genuinely active phases (Downloading/Starting/
   * Analyzing/Merging/Verifying).  Pausing freezes the value, Resuming
   * continues from the same point, and terminal states (Complete/Failed/
   * Cancelled) freeze it permanently.  Uses state-transition timestamps, so
   * timer jitter never inflates the count.
   */
  _elapsedFor(task) {
    const id = task.id;
    let e = this.state.elapsed[id];
    const active = ["Downloading", "Starting", "Analyzing", "Merging", "Verifying"].includes(task.state);
    if (!e) {
      e = { ms: 0, from: null };
      // Seed from the backend wall-clock start when we first observe an
      // already-active task (e.g. the app restarted mid-download).
      if (task.started_at) {
        const seed = Math.max(0, (Date.now() / 1000 - task.started_at) * 1000);
        if (seed > 0) e.ms = seed;
      }
      this.state.elapsed[id] = e;
    }
    if (active) {
      if (e.from === null) e.from = Date.now();
    } else if (e.from !== null) {
      e.ms += Date.now() - e.from;
      e.from = null;
    }
    return e.ms + (e.from !== null ? Date.now() - e.from : 0);
  },

  _updateRow(task) {
    if (this.state.page !== "downloads") return;
    const row = Utils.$q(`#downloadList [data-id="${task.id}"]`);
    if (row) Components.updateRow(row, task, this._elapsedFor(task));
  },

  _removeRow(id) {
    this.state.listSig = "";
    const row = Utils.$q(`#downloadList [data-id="${id}"]`);
    if (row) {
      row.classList.add("row-leave");
      setTimeout(() => this._renderDownloads(true), 180);
    } else {
      this._renderDownloads(true);
    }
  },

  async _loadDownloads() {
    const downloads = await API.getDownloads();
    if (!downloads) return;
    this.state.downloads = {};
    downloads.forEach((t) => { this.state.downloads[t.id] = t; });
    this.state.listSig = "";
    this._renderDownloads(true);
    this._updateBadge();
    this._updateCounts();
  },

  _updateBadge() {
    const activeStates = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Queued"];
    const n = this._taskArray().filter((t) => activeStates.includes(t.state)).length;
    const badge = Utils.$id("navBadge");
    badge.textContent = n;
    badge.hidden = n === 0;
  },

  _updateCounts() {
    const all = this._taskArray();
    const count = (states) => all.filter((t) => states.includes(t.state)).length;
    const set = (f, v) => { const el = Utils.$q(`#filterChips [data-filter="${f}"] .chip-n`); if (el) el.textContent = v; };
    set("all", all.length);
    set("active", count(["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Stopping"]));
    set("queued", count(["Queued"]));
    set("paused", count(["Paused"]));
    set("completed", count(["Complete"]));
    set("failed", count(["Failed", "Cancelled", "Stopped"]));
    this._renderQueueStrip();
  },

  _showSkeletons() {
    Utils.$id("downloadList").innerHTML = Components.skeletonRows(4);
    const body = Utils.$q("#dashActive .panel-body");
    if (body) body.innerHTML = Components.skeletonRows(2);
  },

  // ══════════════════════════════════════════════════════════════════════
  //  New Download dialog
  // ══════════════════════════════════════════════════════════════════════

  async openNewDownload(prefillUrl = null, { paste = false } = {}) {
    try {
    const L = (k, f) => I18N.t(k, f);
    const settings = this.state.settings || (await API.getSettings()) || {};
    const baseDir = settings.download_dir || "";
    const categories = ["General", "Compressed", "Videos", "Music", "Documents", "Programs", "Images"];

    const dlg = Components.showModal(`
      <div class="nd">
        <div class="nd-url-wrap">
          <span class="nd-url-ico">${Utils.icon("link", 17)}</span>
          <input id="ndUrl" class="input nd-url" type="url" placeholder="${L("dlg.url_placeholder", "Paste a download link…  https://")}" autocomplete="off" spellcheck="false" aria-label="${L("dlg.url_placeholder", "Download URL")}">
          <button class="icon-btn nd-paste" id="ndPaste" data-tip="${L("dlg.paste", "Paste from clipboard")}" aria-label="${L("dlg.paste", "Paste from clipboard")}">${Utils.icon("paste", 15)}</button>
        </div>
        <p class="nd-error" id="ndError" hidden></p>

        <div class="nd-detect" id="ndDetect" hidden>
          <div class="nd-detect-ico" id="ndDetectIco">${Utils.icon("file", 20)}</div>
          <div class="nd-detect-info">
            <div class="nd-detect-name" id="ndDetectName"></div>
            <div class="nd-detect-meta" id="ndDetectMeta"></div>
          </div>
          <span class="nd-resume" id="ndResume" hidden>${Utils.icon("bolt", 13)} ${L("dlg.resumable", "Resumable")}</span>
        </div>
        <div class="nd-detect nd-probing" id="ndProbing" hidden>
          <span class="sk sk-box" style="width:40px;height:40px;border-radius:12px"></span>
          <div style="flex:1;display:flex;flex-direction:column;gap:8px">
            <span class="sk sk-line w60"></span><span class="sk sk-line w35"></span>
          </div>
        </div>

        <div class="nd-grid">
          <div class="field">
            <label class="field-label" for="ndName">${L("dlg.file_name", "File name")}</label>
            <input id="ndName" class="input" type="text" placeholder="${L("dlg.file_name_placeholder", "Detected automatically")}" autocomplete="off">
          </div>
          <div class="field">
            <label class="field-label">${L("dlg.category", "Category")}</label>
            <div class="nd-cats" id="ndCats">
              ${categories.map((c, i) => `<button class="cat-chip${i === 0 ? " active" : ""}" data-cat="${c}">${L("category." + c, c)}</button>`).join("")}
            </div>
          </div>
          <div class="field">
            <label class="field-label" for="ndDir">${L("dlg.save_to", "Save to")}</label>
            <div class="input-join">
              <input id="ndDir" class="input" type="text" value="${Utils.escapeHtml(baseDir)}" autocomplete="off">
              <button class="btn btn-ghost" id="ndBrowse">${Utils.icon("folder", 15)} ${L("dlg.browse", "Browse")}</button>
            </div>
          </div>
        </div>

        <button class="nd-adv-toggle" id="ndAdvToggle" aria-expanded="false">
          ${Utils.icon("chevronRight", 14)} ${L("dlg.advanced", "Advanced options")}
        </button>
        <div class="nd-adv" id="ndAdv" hidden>
          <div class="field">
            <label class="field-label" for="ndChecksum">${L("dlg.checksum", "Checksum")} <span class="field-opt">${L("dlg.checksum_hint", "MD5 or SHA-256, optional")}</span></label>
            <input id="ndChecksum" class="input mono" type="text" placeholder="e.g. 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" autocomplete="off" spellcheck="false">
          </div>
          <label class="switch-row">
            <span class="switch"><input type="checkbox" id="ndAutostart" checked><span class="switch-track"></span></span>
            <span class="switch-text">${L("dlg.start_immediately", "Start immediately")}<span class="switch-hint">${L("dlg.start_off", "Off — add to the queue without starting")}</span></span>
          </label>
        </div>
      </div>`,
      { title: L("dlg.add_download", "New download"), subtitle: L("dlg.add_file_by_url", "Add a file by URL"), width: 620 });

    dlg.setFooter(`
      <button class="btn btn-ghost" id="ndCancel">${L("dlg.cancel_btn", "Cancel")}</button>
      <button class="btn btn-primary btn-lg" id="ndGo" disabled>${Utils.icon("download", 16)} ${L("dlg.download_btn", "Download")}</button>`);

    const el = {
      url: dlg.qs("#ndUrl"), name: dlg.qs("#ndName"), dir: dlg.qs("#ndDir"),
      checksum: dlg.qs("#ndChecksum"), autostart: dlg.qs("#ndAutostart"),
      err: dlg.qs("#ndError"), detect: dlg.qs("#ndDetect"), probing: dlg.qs("#ndProbing"),
      detectName: dlg.qs("#ndDetectName"), detectMeta: dlg.qs("#ndDetectMeta"),
      detectIco: dlg.qs("#ndDetectIco"), resume: dlg.qs("#ndResume"),
      go: dlg.qs("#ndGo"), cats: dlg.qs("#ndCats"),
    };

    const model = {
      valid: false, normalized: "", nameTouched: false, dirTouched: false,
      category: "General", baseDir, probing: 0,
    };

    const setValid = (ok, msg = "") => {
      model.valid = ok;
      el.go.disabled = !ok;
      el.err.hidden = ok || !msg;
      el.err.textContent = msg;
      el.url.classList.toggle("invalid", !ok && !!msg);
    };

    const applyCategory = (cat) => {
      model.category = cat;
      Utils.$qa(".cat-chip", el.cats).forEach((c) => c.classList.toggle("active", c.dataset.cat === cat));
      if (!model.dirTouched) {
        el.dir.value = cat === "General" ? model.baseDir : model.baseDir.replace(/[\\/]+$/, "") + "\\" + cat;
      }
    };

    const probe = Utils.debounce(async () => {
      const raw = el.url.value.trim();
      el.detect.hidden = true;
      if (!raw) { setValid(false); el.probing.hidden = true; return; }
      const v = await API.validateUrl(raw);
      if (!v || !v.valid) { setValid(false, L("dlg.valid_url_error", "Enter a valid http:// or https:// link")); return; }
      model.normalized = v.normalized;
      setValid(true);

      const ticket = ++model.probing;
      el.probing.hidden = false;
      const res = await API.probeUrl(v.normalized);
      if (ticket !== model.probing) return;   // stale response
      el.probing.hidden = true;
      if (res && res.ok) {
        const fname = res.filename || Utils.fileName({ url: v.normalized });
        el.detect.hidden = false;
        el.detectName.textContent = fname;
        el.detectMeta.textContent =
          (res.size_display ? res.size_display : L("dlg.unknown_size", "Unknown size")) +
          (Utils.hostOf(v.normalized) ? " · " + Utils.hostOf(v.normalized) : "");
        el.detectIco.innerHTML = Utils.fileIcon(fname, 20);
        el.resume.hidden = !res.range;
        if (!model.nameTouched) el.name.value = fname;
        applyCategory(Utils.categoryFor(fname));
      } else {
        el.detect.hidden = false;
        el.detectName.textContent = Utils.fileName({ url: v.normalized });
        el.detectMeta.textContent = (res && res.error) ? res.error : L("dlg.could_not_inspect", "Could not inspect this link");
        el.detectIco.innerHTML = Utils.icon("file", 20);
        el.resume.hidden = true;
        if (!model.nameTouched) el.name.value = Utils.fileName({ url: v.normalized });
      }
    }, 550);

    el.url.addEventListener("input", probe);
    el.url.addEventListener("keydown", (e) => { if (e.key === "Enter" && !el.go.disabled) el.go.click(); });
    el.name.addEventListener("input", () => { model.nameTouched = true; });
    el.dir.addEventListener("input", () => { model.dirTouched = true; model.baseDir = el.dir.value; });
    el.name.addEventListener("keydown", (e) => { if (e.key === "Enter" && !el.go.disabled) el.go.click(); });

    el.cats.addEventListener("click", (e) => {
      const chip = e.target.closest(".cat-chip");
      if (chip) applyCategory(chip.dataset.cat);
    });

    dlg.qs("#ndPaste").addEventListener("click", async () => {
      try {
        const text = (await navigator.clipboard.readText() || "").trim();
        if (text) { el.url.value = text.split(/\s/)[0]; probe(); }
      } catch {
        Components.toast(L("toast.clipboard_blocked", "Clipboard blocked"), L("toast.clipboard_blocked_msg", "Allow clipboard access or paste manually"), "warning");
      }
    });

    dlg.qs("#ndBrowse").addEventListener("click", async () => {
      const dir = await API.selectDirectory();
      if (dir) {
        model.baseDir = dir;
        model.dirTouched = false;
        applyCategory(model.category);
      }
    });

    dlg.qs("#ndAdvToggle").addEventListener("click", () => {
      const panel = dlg.qs("#ndAdv");
      panel.hidden = !panel.hidden;
      dlg.qs("#ndAdvToggle").setAttribute("aria-expanded", String(!panel.hidden));
      dlg.qs("#ndAdvToggle").classList.toggle("open", !panel.hidden);
    });

    dlg.qs("#ndCancel").addEventListener("click", () => dlg.close());
    el.go.addEventListener("click", async () => {
      const url = (model.normalized || el.url.value).trim();
      const dir = el.dir.value.trim();
      const name = el.name.value.trim();
      el.go.disabled = true;
      el.go.classList.add("busy");
      try {
        const id = await this._addDownloadResolvingConflict(
          url, dir, name, el.checksum.value.trim(), el.autostart.checked, model.category);
        if (id) {
          this.state.highlightId = id;
          dlg.close();
          Components.toast(I18N.t("toast.download_added", "Download added"), name || Utils.fileName({ url }), "success");
          if (this.state.page !== "downloads") this.navigate("downloads");
        } else {
          setValid(false, L("dlg.add_error", "Could not add this download"));
        }
      } finally {
        el.go.classList.remove("busy");
        el.go.disabled = false;
      }
    });

    // Prefill flows.
    if (prefillUrl) {
      el.url.value = prefillUrl;
      probe();
    } else if (paste) {
      try {
        const text = (await navigator.clipboard.readText() || "").trim();
        if (text && /^https?:\/\//i.test(text)) {
          el.url.value = text.split(/\s/)[0];
          probe();
        }
      } catch {}
    }
    el.url.focus();
    } catch (e) {
      Components.toast(I18N.t("toast.open_dialog_failed", "Could not open dialog"), String(e), "error");
      API.logJs("openNewDownload: " + String(e));
    }
  },

  async _addDownloadResolvingConflict(url, directory, name, checksum, autostart, category) {
    const policy = (this.state.settings && this.state.settings.duplicate_policy) || "ask";
    let conflict = null;
    try { conflict = await API.checkDuplicate(url, directory, name); } catch (e) {}
    const hasConflict = conflict && (conflict.reason || conflict.has_active);
    if (!hasConflict) {
      return API.addDownload(url, directory, name, checksum, autostart, category);
    }
    if (policy === "allow") return API.addDownload(url, directory, name, checksum, autostart, category, true);
    if (policy === "replace") return API.addDownload(url, directory, name, checksum, autostart, category, false, "replace");
    if (policy === "rename") return API.addDownload(url, directory, name, checksum, autostart, category);
    // "ask" — show the conflict dialog.
    const choice = await Components.conflictPrompt({
      reason: conflict.reason || (conflict.has_active ? "same_url" : ""),
      filePath: conflict.file_path, name,
    });
    if (choice === "cancel") return "";
    if (choice === "open_task") {
      this.navigate("downloads");
      this.state.highlightId = conflict.active_task_id;
      this.state.listSig = "";
      this._renderDownloads(true);
      return "";
    }
    if (choice === "open") {
      API.openFileAt(conflict.file_path);
      return "";
    }
    if (choice === "replace") return API.addDownload(url, directory, name, checksum, autostart, category, false, "replace");
    if (choice === "again") return API.addDownload(url, directory, name, checksum, autostart, category, true);
    return API.addDownload(url, directory, name, checksum, autostart, category);
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Dashboard
  // ══════════════════════════════════════════════════════════════════════

  _startStatsPolling() {
    let tick = 0;
    const poll = async () => {
      try {
        const [stats, sys] = await Promise.all([API.getStats(), API.getSystemStats()]);
        if (stats) this._renderStats(stats, sys || {});
        this._lastStats = stats;
      } catch {}
      // Refresh the extension pill and queue strip roughly every 10s so a
      // stale "connected" state decays even while the app sits idle.
      if (++tick % 5 === 0) {
        this._renderExtPill();
        this._renderQueueStrip();
      }
      setTimeout(poll, 2000);
    };
    poll();
  },

  _startSparkline() {
    const canvas = Utils.$id("speedSpark");
    if (!canvas) return;
    this._spark = Components.sparkline(canvas);
    setInterval(() => {
      if (this._lastStats) this._spark.push(this._lastStats.total_speed_bps || 0);
    }, 1000);
  },

  _renderStats(stats, sys) {
    const today = this.state.history.filter((h) => Utils.isToday(h.finished));
    const todayBytes = today.reduce((s, h) => s + (h.size_bytes || 0), 0);
    const set = (id, val) => { const el = Utils.$id(id); if (el && el.textContent !== String(val)) el.textContent = val; };

    if (!this._statsAnimated) {
      this._statsAnimated = true;
      Components.countUp(Utils.$id("stToday"), today.length);
      Components.countUp(Utils.$id("stActive"), stats.running || 0);
      Components.countUp(Utils.$id("stDone"), stats.completed || 0);
      Components.countUp(Utils.$id("stFailed"), stats.failed || 0);
    } else {
      set("stToday", today.length);
      set("stActive", stats.running || 0);
      set("stDone", stats.completed || 0);
      set("stFailed", stats.failed || 0);
    }
    set("stTodaySub", I18N.fmt("fmt.downloaded", { size: Utils.formatSize(todayBytes) }));
    set("stActiveSub", I18N.fmt("fmt.in_queue", { n: stats.queued || 0 }));
    set("stSpeed", Utils.formatSpeed(stats.total_speed_bps));
    set("stSpeedPeak", I18N.fmt("fmt.connections_live", { n: stats.running || 0, s: stats.running === 1 ? "" : "s" }));
    set("stNetwork", sys.session_downloaded_display || "0 B");
    set("stDisk", sys.disk_free_display || I18N.t("fmt.unknown", "—"));
    set("stDiskSub", sys.disk_total ? I18N.fmt("fmt.of_used", { used: sys.disk_used_display, total: sys.disk_total_display }) : "");

    const ring = Utils.$id("diskRing");
    if (ring && sys.disk_total) {
      const pct = Utils.clamp(sys.disk_percent || 0, 0, 100);
      ring.style.setProperty("--p", pct);
      ring.classList.toggle("warn", pct > 90);
    }
    const sbSpeed = Utils.$id("sidebarSpeedVal");
    if (sbSpeed) sbSpeed.textContent = Utils.formatSpeed(stats.total_speed_bps);
  },

  _renderDashboardLists() {
    if (this.state.page !== "dashboard") return;
    const active = this._taskArray()
      .filter((t) => ["Downloading", "Paused", "Queued", "Stopping"].includes(t.state))
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0));

    const activeEl = Utils.$id("dashActive");
    const body = activeEl.querySelector(".panel-body");
    if (!active.length) {
      body.replaceChildren(Components.emptyState({
        icon: "bolt",
        title: I18N.t("dash.all_quiet", "All quiet"),
        desc: I18N.t("dash.all_quiet_desc", "Active downloads will show up here in real time."),
        actionLabel: I18N.t("title.new_download", "New download"),
        onAction: () => this.openNewDownload(null, { paste: true }),
      }));
    } else {
      const frag = document.createDocumentFragment();
      active.slice(0, 5).forEach((t) => frag.appendChild(this._miniRow(t)));
      body.replaceChildren(frag);
    }
    Utils.$id("dashActiveCount").textContent = active.length || "";

    const recent = this.state.history.slice(0, 6);
    const recentEl = Utils.$id("dashRecent");
    const rbody = recentEl.querySelector(".panel-body");
    if (!recent.length) {
      rbody.replaceChildren(Components.emptyState({
        icon: "history", title: I18N.t("dash.no_history", "No history yet"),
        desc: I18N.t("dash.no_history_desc", "Finished downloads appear here."),
      }));
    } else {
      const frag = document.createDocumentFragment();
      recent.forEach((h) => {
        const row = document.createElement("div");
        row.className = "mini-row";
        const ok = h.status === "Complete";
        row.innerHTML = `
          <span class="mini-ico" data-type="${Utils.fileType(h.name)}">${Utils.fileIcon(h.name, 16)}</span>
          <div class="mini-main">
            <span class="mini-name" title="${Utils.escapeHtml(h.name)}">${Utils.escapeHtml(h.name)}</span>
            <span class="mini-sub">${Utils.escapeHtml(h.size || "")} · ${Utils.formatDateTime(h.finished)}</span>
          </div>
          <span class="mini-status ${ok ? "ok" : "bad"}" title="${Utils.escapeHtml(Utils.statusLabel(h.status))}">${Utils.icon(ok ? "check" : "x", 13)}</span>`;
        frag.appendChild(row);
      });
      rbody.replaceChildren(frag);
    }
  },

  _miniRow(t) {
    const pct = t.total > 0 ? Utils.clamp((t.completed / t.total) * 100, 0, 100) : 0;
    const stCls = Utils.statusClass(t.state);
    const row = document.createElement("div");
    row.className = "mini-row";
    row.innerHTML = `
      <span class="mini-ico" data-type="${Utils.fileType(Utils.fileName(t))}">${Utils.fileIcon(Utils.fileName(t), 16)}</span>
      <div class="mini-main">
        <span class="mini-name" title="${Utils.escapeHtml(Utils.fileName(t))}">${Utils.escapeHtml(Utils.fileName(t))}</span>
        <span class="mini-progress"><span class="mini-fill p-${stCls}" style="width:${pct}%"></span></span>
      </div>
      <span class="mini-speed">${t.state === "Downloading" ? Utils.formatSpeed(t.speed_bps) : Utils.statusLabel(t.state)}</span>`;
    row.addEventListener("click", () => {
      this.navigate("downloads");
      this.state.highlightId = t.id;
      this.state.listSig = "";
      this._renderDownloads(true);
    });
    return row;
  },

  // ══════════════════════════════════════════════════════════════════════
  //  History
  // ══════════════════════════════════════════════════════════════════════

  async _refreshHistory() {
    try {
      this.state.history = (await API.getHistory()) || [];
    } catch {}
    if (this.state.page === "history") this._renderHistory();
  },

  _bindHistoryPage() {
    Utils.$qa("#historyFilterChips .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        Utils.$qa("#historyFilterChips .chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        this.state.hFilter = chip.dataset.hfilter;
        this._renderHistory();
      });
    });
    Utils.$id("btnClearHistory").addEventListener("click", async () => {
      const ok = await Components.confirm({
        title: I18N.t("confirm.clear_history", "Clear history"),
        message: I18N.t("confirm.clear_history_msg", "Remove all history entries? Downloaded files are not affected."),
        okText: I18N.t("history.clear", "Clear history"), cancelText: I18N.t("confirm.keep", "Keep"), danger: true,
      });
      if (ok) {
        await API.clearHistory();
        this.state.history = [];
        this._renderHistory();
        Components.toast(I18N.t("toast.history_cleared", "History cleared"), "", "info");
      }
    });
  },

  _historyStatusClass(status) {
    if (status === "Complete") return "complete";
    if (status === "Cancelled") return "cancelled";
    return "failed";
  },

  async _historyAction(action, h) {
    if (action === "folder") { await API.openPath(h.directory); return; }
    if (action === "file") {
      const ok = await API.openFileFromHistory(h);
      if (!ok) Components.toast(I18N.t("toast.file_not_found_title", "File not found"), I18N.t("toast.file_not_found", "{name} is no longer on disk").replace("{name}", h.name), "error");
      return;
    }
    if (action === "copypath") {
      try { await navigator.clipboard.writeText(`${h.directory}\\${h.name}`); Components.toast(I18N.t("toast.copied", "Copied"), I18N.t("toast.copied_path", "File path copied to clipboard"), "info", 2000); } catch {}
      return;
    }
    if (action === "redownload") {
      // Smart re-download: check the destination / existing file first and
      // offer conflict handling instead of blindly creating a new task.
      const id = await this._addDownloadResolvingConflict(h.url, h.directory, h.name, "", true, "");
      if (id) { this.state.highlightId = id; Components.toast(I18N.t("act.redownload", "Redownload"), h.name, "success"); this.navigate("downloads"); }
      return;
    }
    if (action === "remove") {
      await API.removeHistoryEntry(h.task_id);
      this.state.history = this.state.history.filter((x) => x.task_id !== h.task_id);
      this._renderHistory();
    }
  },

  _renderHistory() {
    const body = Utils.$id("historyBody");
    const empty = Utils.$id("historyEmpty");
    const table = Utils.$id("historyTable");
    this._renderHistoryStats();
    const q = this.state.search;
    const hf = this.state.hFilter || "all";
    let items = this.state.history;
    if (q) items = items.filter((h) => (h.name || "").toLowerCase().includes(q) || (h.url || "").toLowerCase().includes(q));
    if (hf !== "all") items = items.filter((h) => (h.status || "") === hf);

    if (!items.length) {
      table.hidden = true;
      empty.hidden = false;
      empty.replaceChildren(Components.emptyState({
        icon: "history",
        title: this.state.history.length ? I18N.t("empty.nothing_matches", "Nothing matches")
                                        : I18N.t("empty.no_history", "No history yet"),
        desc: this.state.history.length ? I18N.t("empty.nothing_matches_desc", "Try a different filter or search term.")
                                        : I18N.t("empty.no_history_desc", "Completed and failed downloads are listed here."),
      }));
      return;
    }
    empty.hidden = true;
    table.hidden = false;
    body.innerHTML = items.slice(0, 300).map((h) => {
      const stCls = this._historyStatusClass(h.status);
      const sizeTxt = h.size || "";
      const metaBits = [];
      if (h.duration) metaBits.push(`⏱ ${h.duration.toFixed ? h.duration.toFixed(0) : h.duration}s`);
      if (h.avg_speed) metaBits.push(`~${Utils.formatSpeed(h.avg_speed)}`);
      const meta = metaBits.length ? `<span class="h-meta">${metaBits.join(" · ")}</span>` : "";
      const dir = h.directory || "";
      const path = `${dir}\\${h.name || ""}`;
      return `
      <tr>
        <td class="h-date">${Utils.formatDateTime(h.finished)}</td>
        <td class="h-name">
          <span class="h-ico" data-type="${Utils.fileType(h.name)}">${Utils.fileIcon(h.name, 15)}</span>
          <span class="h-name-t" title="${Utils.escapeHtml(h.name || "")}">${Utils.escapeHtml(h.name || "")}</span>
        </td>
        <td class="h-size">${Utils.escapeHtml(sizeTxt)}${meta}</td>
        <td class="h-cat"><span class="cat-pill">${Utils.escapeHtml(I18N.t("category." + (h.category || "General"), h.category || "General"))}</span></td>
        <td><span class="badge badge-${stCls}"><i class="badge-dot"></i>${Utils.statusLabel(h.status || "Failed")}</span></td>
        <td class="h-dir">
          <span class="h-dir-t" title="${Utils.escapeHtml(dir)}">${Utils.escapeHtml(dir || "")}</span>
        </td>
        <td class="h-actions">
          <button class="icon-btn btn-xs" data-hact="file" data-tip="${I18N.t("act.open_file", "Open file")}" aria-label="${I18N.t("act.open_file", "Open file")}">${Utils.icon("external", 13)}</button>
          <button class="icon-btn btn-xs" data-hact="folder" data-tip="${I18N.t("act.open_folder", "Open folder")}" aria-label="${I18N.t("act.open_folder", "Open folder")}">${Utils.icon("folderOpen", 13)}</button>
          <button class="icon-btn btn-xs" data-hact="copypath" data-tip="${I18N.t("act.copy_path", "Copy path")}" aria-label="${I18N.t("act.copy_path", "Copy path")}">${Utils.icon("copy", 13)}</button>
          <button class="icon-btn btn-xs" data-hact="redownload" data-tip="${I18N.t("act.redownload", "Redownload")}" aria-label="${I18N.t("act.redownload", "Redownload")}">${Utils.icon("retry", 13)}</button>
          <button class="icon-btn btn-xs" data-hact="remove" data-tip="${I18N.t("act.remove", "Remove")}" aria-label="${I18N.t("act.remove", "Remove")}">${Utils.icon("x", 13)}</button>
        </td>
      </tr>`;
    }).join("");

    Utils.$qa("[data-hact]", body).forEach((btn) =>
      btn.addEventListener("click", () => {
        const tr = btn.closest("tr");
        const idx = Array.from(body.children).indexOf(tr);
        const h = items[idx];
        if (h) this._historyAction(btn.dataset.hact, h);
      }));
  },

  async _renderHistoryStats() {
    const box = Utils.$id("historyStats");
    if (!box) return;
    const a = await API.getAnalytics();
    if (!a || !a.total_downloads) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    const fmtDur = (s) => {
      if (!s) return "0s";
      s = Math.round(s);
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
      return h ? I18N.fmt("fmt.duration", { h, m }) : I18N.fmt("fmt.duration_short", { m, s: sec });
    };
    const cards = [
      [I18N.t("analytics.downloads", "Downloads"), String(a.total_downloads)],
      [I18N.t("analytics.completed", "Completed"), String(a.completed)],
      [I18N.t("analytics.failed", "Failed"), String(a.failed)],
      [I18N.t("analytics.cancelled", "Cancelled"), String(a.cancelled)],
      [I18N.t("analytics.data", "Data"), a.total_bytes_display],
      [I18N.t("analytics.avg_speed", "Avg speed"), Utils.formatSpeed(a.avg_speed)],
      [I18N.t("analytics.peak_speed", "Peak speed"), Utils.formatSpeed(a.peak_speed)],
      [I18N.t("analytics.time", "Time"), fmtDur(a.total_duration)],
    ];
    const catList = Object.entries(a.by_category || {}).slice(0, 8)
      .map(([k, v]) => `<span class="an-pill">${Utils.escapeHtml(I18N.t("category." + k, k))} ${v}</span>`).join("");
    const typeList = Object.entries(a.by_type || {}).slice(0, 8)
      .map(([k, v]) => `<span class="an-pill">.${Utils.escapeHtml(k)} ${v}</span>`).join("");
    const mode = a.by_mode || {};
    box.innerHTML = `
      <div class="an-cards">${cards.map(([l, v]) =>
        `<div class="an-card"><div class="an-val">${Utils.escapeHtml(v)}</div><div class="an-lbl">${Utils.escapeHtml(l)}</div></div>`).join("")}</div>
      <div class="an-rows">
        ${catList ? `<div class="an-row"><span class="an-lbl">${I18N.t("analytics.categories", "Categories")}</span><div class="an-pills">${catList}</div></div>` : ""}
        ${typeList ? `<div class="an-row"><span class="an-lbl">${I18N.t("analytics.types", "Types")}</span><div class="an-pills">${typeList}</div></div>` : ""}
        <div class="an-row"><span class="an-lbl">${I18N.t("analytics.connections", "Connections")}</span><div class="an-pills">
          <span class="an-pill">${I18N.t("analytics.smart", "Smart")} ${mode.smart || 0}</span><span class="an-pill">${I18N.t("analytics.manual", "Manual")} ${mode.manual || 0}</span></div></div>
      </div>`;
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Batch
  // ══════════════════════════════════════════════════════════════════════

  _bindBatchPage() {
    Utils.$qa("#page-batch .tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        Utils.$qa("#page-batch .tab-btn").forEach((b) => b.classList.remove("active"));
        Utils.$qa("#page-batch .tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        Utils.$id("tab-" + btn.dataset.tab).classList.add("active");
      });
    });

    const area = Utils.$id("batchUrls");
    const counter = Utils.$id("batchCount");
    const updateCount = () => {
      const n = area.value.split("\n").map((l) => l.trim()).filter((l) => /^https?:\/\//i.test(l)).length;
      counter.textContent = n ? I18N.fmt("batch.valid_urls", { n }) : "";
    };
    area.addEventListener("input", updateCount);

    Utils.$id("btnBatchFile").addEventListener("click", async () => {
      const path = await API.selectFile();
      if (!path) return;
      const res = await API.readTextFile(path);
      if (res && res.ok) {
        area.value = res.text;
        updateCount();
        Components.toast(I18N.t("toast.file_loaded", "File loaded"), `${path.split(/[\\/]/).pop()}`, "success");
      } else {
        Components.toast(I18N.t("toast.file_not_read", "Could not read file"), res?.error || "", "error");
      }
    });

    Utils.$id("btnBatchBrowse").addEventListener("click", async () => {
      const dir = await API.selectDirectory();
      if (dir) Utils.$id("batchDir").value = dir;
    });

    Utils.$id("btnBatchQueue").addEventListener("click", async () => {
      const urls = area.value.split("\n").map((l) => l.trim()).filter((l) => /^https?:\/\//i.test(l));
      if (!urls.length) {
        Components.toast(I18N.t("toast.no_urls", "No URLs"), I18N.t("batch.no_urls_msg", "Paste at least one valid http(s) link"), "warning");
        return;
      }
      // Empty folder => backend applies per-file category routing.
      const dir = Utils.$id("batchDir").value.trim();
      const count = await API.addBatch(urls, dir);
      Components.toast(I18N.t("toast.batch_queued", "Batch queued"), `${count} ${I18N.t("batch.queued", "queued")}`, "success");
      area.value = "";
      updateCount();
      this.navigate("downloads");
    });

    // Pattern scan.
    Utils.$id("btnPatternBrowse").addEventListener("click", async () => {
      const dir = await API.selectDirectory();
      if (dir) Utils.$id("patternDir").value = dir;
    });

    Utils.$id("btnPatternScan").addEventListener("click", async () => {
      const pattern = Utils.$id("patternUrl").value.trim();
      if (!pattern.includes("*")) {
        Components.toast(I18N.t("toast.invalid_pattern", "Invalid pattern"), I18N.t("batch.invalid_pattern_msg", "Use * where the number goes, e.g. file-*.zip"), "warning");
        return;
      }
      const dir = Utils.$id("patternDir").value.trim();
      const start = parseInt(Utils.$id("patternStart").value, 10) || 1;
      const padding = parseInt(Utils.$id("patternPadding").value, 10) || 2;
      const btn = Utils.$id("btnPatternScan");
      const results = Utils.$id("patternResults");
      btn.disabled = true;
      btn.classList.add("busy");
      results.innerHTML = `<div class="pattern-note"><span class="sk sk-line w35"></span></div>`;
      try {
        const res = await API.scanPattern(pattern, dir, start, padding);
        if (res && res.urls && res.urls.length) {
          results.innerHTML = `
            <div class="pattern-note ok">${Utils.icon("check", 14)} ${res.urls.length} ${I18N.t("batch.files_found", "files found — queued for download")}</div>
            <div class="pattern-list">${res.urls.slice(0, 40).map((u) => `<div class="pattern-item">${Utils.escapeHtml(u)}</div>`).join("")}
            ${res.urls.length > 40 ? `<div class="pattern-item dim">… ${res.urls.length - 40} ${I18N.t("batch.more", "more")}</div>` : ""}</div>`;
          const count = await API.addBatch(res.urls, dir);
          Components.toast(I18N.t("toast.scan_complete", "Scan complete"), `${count} ${I18N.t("batch.queued", "queued")}`, "success");
        } else {
          results.innerHTML = `<div class="pattern-note">${Utils.icon("info", 14)} ${I18N.t("batch.nothing_matched", "No reachable files matched this pattern")}</div>`;
        }
      } catch {
        results.innerHTML = `<div class="pattern-note bad">${Utils.icon("alert", 14)} ${I18N.t("toast.scan_failed", "Scan failed")}</div>`;
      }
      btn.disabled = false;
      btn.classList.remove("busy");
    });
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Browser integration
  // ══════════════════════════════════════════════════════════════════════

  async _refreshServerStatus() {
    try {
      const st = await API.liveServerStatus();
      if (!st) return;
      this._lastServerStatus = st;
      this.state.serverRunning = st.running;
      if (this.state.page === "browser") this._renderBrowser(st);
      this._renderExtPill();
    } catch {}
  },

  _renderBrowser(st) {
    const orb = Utils.$id("serverOrb");
    const status = Utils.$id("serverStatusText");
    const addr = Utils.$id("serverAddr");
    const token = Utils.$id("serverToken");
    const btn = Utils.$id("btnServerToggle");
    orb.className = "server-orb " + (st.running ? "on" : "off");
    status.textContent = st.running ? I18N.t("browser.server_running", "Live server is running") : I18N.t("browser.server_stopped", "Live server is stopped");
    addr.textContent = st.running ? `http://${st.host}:${st.port}` : "—";
    token.textContent = st.running ? st.token : "—";
    btn.innerHTML = st.running
      ? `${Utils.icon("stop", 15)} ${I18N.t("browser.stop_server", "Stop server")}`
      : `${Utils.icon("power", 15)} ${I18N.t("browser.start_server", "Start server")}`;
    btn.classList.toggle("btn-danger", st.running);
    btn.classList.toggle("btn-primary", !st.running);
  },

  _bindBrowserPage() {
    Utils.$id("btnServerToggle").addEventListener("click", async () => {
      const btn = Utils.$id("btnServerToggle");
      btn.disabled = true;
      if (this.state.serverRunning) {
        await API.stopLiveServer();
        Components.toast(I18N.t("toast.server_stopped", "Server stopped"), "", "info");
      } else {
        const ok = await API.startLiveServer();
        Components.toast(ok ? I18N.t("toast.server_started", "Server started") : I18N.t("toast.server_start_failed", "Start failed"),
          ok ? I18N.t("toast.server_started_msg", "The extension can now connect") : I18N.t("toast.server_start_failed_msg", "The port may already be in use"), ok ? "success" : "error");
      }
      btn.disabled = false;
      await this._refreshServerStatus();
    });

    Utils.$id("btnCopyAddr").addEventListener("click", async () => {
      const text = Utils.$id("serverAddr").textContent;
      if (text && text !== "—") {
        try { await navigator.clipboard.writeText(text); Components.toast(I18N.t("toast.copied", "Copied"), text, "info", 2000); } catch {}
      }
    });

    Utils.$id("btnCreateExt").addEventListener("click", async () => {
      const path = await API.createExtension();
      if (path) Components.toast(I18N.t("toast.extension_created", "Extension created"), path, "success", 6500);
    });

    Utils.$id("btnInstallExt").addEventListener("click", async () => {
      const btn = Utils.$id("btnInstallExt");
      btn.disabled = true;
      const res = await API.installExtension();
      if (res && res.status === "busy") {
        Components.toast(I18N.t("ext_install.busy", "Already installing…"), "", "info", 2000);
        btn.disabled = false;
        return;
      }
      Components.toast(I18N.t("ext_install.title", "Installing extension…"), I18N.t("ext_install.stage.locating", "Locating the N13 extension…"), "info", 2600);
      this.state.extInstalling = true;
      setTimeout(() => { if (!this.state.extInstalling) btn.disabled = false; }, 30000);
    });

    Utils.$id("btnRepairExt").addEventListener("click", async () => {
      const btn = Utils.$id("btnRepairExt");
      btn.disabled = true;
      Components.toast(
        I18N.t("ext_repair.title", "Repairing extension…"),
        I18N.t("ext_repair.msg", "Rebuilding the extension folder from the bundled template."),
        "info", 2400);
      try {
        const res = await API.repairExtension();
        if (res && res.ok) {
          Components.toast(I18N.t("toast.ext_repaired", "Extension repaired"), res.path || "", "success", 6500);
        } else {
          Components.toast(
            I18N.t("toast.ext_repair_failed", "Repair failed"),
            (res && res.reason) || I18N.t("toast.ext_repair_failed_msg", "The extension folder could not be rebuilt."),
            "error", 6500);
        }
      } catch (e) {
        API.logJs("repair extension: " + String(e));
        Components.toast(I18N.t("toast.ext_repair_failed", "Repair failed"), String(e), "error", 6500);
      } finally {
        btn.disabled = false;
      }
    });

    Utils.$id("btnRegProtocol").addEventListener("click", async () => {
      const ok = await API.registerProtocol();
      Components.toast(ok ? I18N.t("toast.protocol_registered", "Protocol registered") : I18N.t("toast.protocol_failed", "Registration failed"),
        ok ? I18N.t("toast.protocol_registered_msg", "dldm:// links now open in N13") : I18N.t("toast.protocol_failed_msg", "Try running as administrator"), ok ? "success" : "error");
    });
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Settings
  // ══════════════════════════════════════════════════════════════════════

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
    const head = `
      <div class="field-info">
        <label class="field-label" ${f.key.startsWith("_") ? "" : `for="set-${f.key}"`}>${label}</label>
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

  // ══════════════════════════════════════════════════════════════════════
  //  Logs
  // ══════════════════════════════════════════════════════════════════════

  _bindLogsPage() {
    Utils.$id("btnClearLogs").addEventListener("click", () => {
      this.state.logs = [];
      Utils.$id("logList").innerHTML = "";
    });
    Utils.$id("btnCopyLogs").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(this.state.logs.join("\n"));
        Components.toast(I18N.t("logs.copied", "Logs copied"), "", "info", 2000);
      } catch {
        Components.toast(I18N.t("logs.copy_failed", "Copy failed"), I18N.t("logs.copy_failed_msg", "Clipboard is unavailable"), "error");
      }
    });
  },

  _logLine(msg) {
    const div = document.createElement("div");
    div.className = "log-line";
    if (/\b(ERROR|CRITICAL)\b/.test(msg)) div.classList.add("err");
    else if (/\b(WARNING|WARN)\b/.test(msg)) div.classList.add("warn");
    div.textContent = msg;
    return div;
  },

  _renderLogs() {
    const list = Utils.$id("logList");
    const frag = document.createDocumentFragment();
    this.state.logs.forEach((m) => frag.appendChild(this._logLine(m)));
    list.replaceChildren(frag);
    list.scrollTop = list.scrollHeight;
  },

  _appendLog(msg) {
    const list = Utils.$id("logList");
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
    list.appendChild(this._logLine(msg));
    while (list.children.length > 800) list.firstElementChild.remove();
    if (nearBottom) list.scrollTop = list.scrollHeight;
  },
};

// ── Bootstrap ─────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => App.init());
