/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Application
   ═══════════════════════════════════════════════════════════════════════════ */

const App = {
  state: {
    // The landing page.  `App._initialPage()` overwrites this at boot with the
    // section the document actually marks `active`, so this default is only a
    // fallback for a document with no active section at all.
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
    // Runtime auto-shutdown state from the backend controller (null until the
    // first status arrives).  Deliberately separate from `settings` — the
    // `shutdown_when_done` preference says nothing about whether a countdown
    // is currently live.
    autoShutdown: null,
    // Queue gate: runtime state pushed by the backend (`queue_gate` events,
    // plus a boot-time seed).  Deliberately separate from any task's Paused
    // status — the gate blocks the whole queue, a paused task stops one
    // download, and neither implies the other.  See docs/QUEUE.md §1.
    queuePaused: false,
    schedulerGate: false,
    maximized: false,
    highlightId: null,
    listSig: "",
    // Explorer-style multi-selection for the Downloads list.
    selectedIds: new Set(),   // Set of selected task ids
    selAnchor: null,          // anchor task id for Shift+Click range selection
    // Download groups.  `projects` holds the flat payloads the backend sends
    // (one shape only — see ui/projects_api.py) and `activeProject` is the id
    // of the group the Downloads workspace is currently scoped to.  The
    // sentinel `"all"` means "no group filter"; see GroupsModel.ALL_ID.
    projects: [],
    activeProject: "all",
  },

  pages: {
    dashboard: { title: "Dashboard", sub: "Overview of your download activity" },
    downloads: { title: "Downloads", sub: "Manage and monitor your files" },
    queue:     { title: "Queue", sub: "What is running and what starts next" },
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
    // The page the document is really showing, before anything else reads
    // `state.page`.  Every page-gated render (the row updater, the dashboard
    // panels, the log view) decides whether to paint from this value, so it has
    // to agree with the markup from the very first frame.
    this.state.page = this._initialPage();
    this.rowCallbacks = DownloadsActions.rowCallbacks(this);
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
    // Bind the group strip containers before any render, so a
    // `projects_changed` event that arrives during boot has somewhere to paint.
    if (typeof Groups !== "undefined") Groups.init(this);
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

  /**
   * The landing page: the section the markup marks `active`.
   *
   * `index.html` and `App.state` used to be two independent sources of truth
   * for which page is open, and they disagreed: the document activated
   * `#page-downloads` while the state said `"dashboard"`.  The visible
   * Downloads list was therefore treated as "not the current page": its rows
   * were rendered once and then never updated (the updater is page-gated), so a
   * live download stayed frozen on whatever label it had at first paint —
   * typically "Analyzing" — until a navigation repainted the list.  Deriving the
   * value from the document removes the second source of truth instead of
   * keeping the two in sync by hand.
   */
  _initialPage() {
    const active = Utils.$q(".page.active");
    const id = active && active.id ? String(active.id).replace(/^page-/, "") : "";
    return this.pages[id] ? id : this.state.page;
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

    // Seed the auto-shutdown runtime state so the queue strip is honest from
    // the first paint (the event stream only reports *changes*).
    try {
      const as = await API.getAutoShutdownStatus();
      if (as) this.state.autoShutdown = as;
    } catch {}

    // Same for the queue gate: without this seed a queue paused before the
    // app was restarted would look open until the next change event.
    try {
      const qs = await API.getQueueStatus();
      if (qs) Events.applyQueueStatus(this, qs);
    } catch {}

    try {
      this.state.version = (await API.getVersion()) || "";
    } catch {}

    this._applyLanguage();

    try { await this._loadDownloads(); } catch {}
    try { await this._refreshHistory(); } catch {}
    // Seed the group strip so the tab row and the active group's header are
    // honest on the first paint (the event stream only reports changes).
    try { await Groups.load(); } catch {}
    this._syncProjectSelects();
    try { await this._refreshServerStatus(); } catch {}
    this._renderDashboardLists();
    this._maybeCheckUpdates();
    await API.ready();
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Theme & preferences
  // ══════════════════════════════════════════════════════════════════════

  // Theme/prefs live in js/core/prefs.js; these delegates keep the original
  // App API intact for the rest of the codebase and the tests.
  _loadLocalPrefs() { Prefs.loadLocal(this); },
  _savePrefs() { Prefs.save(this); },
  _savePrefsLocalOnly() { Prefs.saveLocalOnly(this); },
  _applyTheme() { Prefs.apply(this); },
  _hexToRgb(hex) { return Prefs.hexToRgb(hex); },
  _alpha(hex, a) { return Prefs.alpha(hex, a); },
  _mix(hex, amt) { return Prefs.mix(hex, amt); },
  toggleTheme() { Prefs.toggleTheme(this); },
  setAccent(color) { Prefs.setAccent(this, color); },

  // ══════════════════════════════════════════════════════════════════════
  //  Navigation
  // ══════════════════════════════════════════════════════════════════════

  // Navigation lives in js/core/navigation.js; delegates keep the App API.
  _bindNavigation() { Nav.bind(this); },
  toggleSidebar() { Nav.toggleSidebar(this); },
  _applySidebar() { Nav.applySidebar(this); },
  navigate(page) { Nav.navigate(this, page); },
  _moveNavIndicator() { Nav.moveNavIndicator(this); },
  _renderPageChrome() { Nav.renderPageChrome(this); },

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
    // The group strip is part of the Downloads workspace, so it is repainted
    // whenever the page is — its labels are translated text too.
    if (typeof Groups !== "undefined") Groups.paint();
  },

  // ══════════════════════════════════════════════════════════════════════
  //  Titlebar (search, quick actions, theme)
  // ══════════════════════════════════════════════════════════════════════

  _bindTitlebar() {
    const search = Utils.$id("globalSearch");
    if (search) {
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
    }

    // The titlebar keeps only the shell-level shortcuts.  "New download" and
    // "Paste URL" live in the command bar directly below, which is the one
    // documented primary-action surface — repeating them here put two identical
    // primary buttons on screen at once and split the hierarchy between them.
    // (The two paste entries also behaved differently: the one here silently did
    // nothing when the clipboard held no link.)
    const on = (id, fn, tag) => {
      const el = Utils.$id(id);
      if (el) el.addEventListener("click", () => fn().catch((e) => API.logJs(tag + ": " + String(e))));
    };
    on("btnTheme", async () => this.toggleTheme(), "btnTheme");
    on("btnTopSettings", async () => this.navigate("settings"), "btnTopSettings");

    // Prevent pywebview drag-region from swallowing interactive presses.
    const bar = Utils.$id("titlebar");
    if (!bar) return;
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
    if (!btn) return;
    btn.innerHTML = Utils.icon(max ? "restore" : "max", 14);
    // Translated, not a literal: writing an English string here overwrote the
    // `data-i18n-aria-label` the language switch had already applied, so after
    // one maximise/restore the button announced itself in English for the rest
    // of the session even in Persian.
    const key = max ? "title.restore" : "title.maximize";
    btn.setAttribute("aria-label", I18N.t(key));
    btn.setAttribute("data-i18n-aria-label", key);
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

  // Global events live in js/core/shortcuts.js; delegate kept for compat.
  _bindGlobalEvents() {
    Shortcuts.bindContextMenuDismissal();
    Shortcuts.bind(this);
    Shortcuts.bindLinkDnd(this);
    Shortcuts.bindErrorReporting();
  },

  _onKeydown(e) { Shortcuts.onKeydown(this, e); },

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

  // Backend event pipeline lives in js/core/events.js; delegates for compat.
  _startPolling() { Events.startPolling(this); },

  _handleEvent(evt) { Events.handleEvent(this, evt); },

  /**
   * Clipboard URL detected.
   *
   * A non-blocking toast with a one-click "Download" action replaces the old
   * modal, so copying a link never interrupts what the user is doing and the
   * most common response is a single click.
   */
  async _onClipboardLink(url) { return Events.onClipboardLink(this, url); },

  //  Downloads page
  // ══════════════════════════════════════════════════════════════════════
  //
  // The Downloads feature lives in js/features/downloads/ (view + actions).
  // The members below are thin delegates kept for compatibility with the
  // existing callers and the unit tests that exercise App.* directly.

  rowCallbacks: null,  // built via _buildRowCallbacks() at init time

  _buildRowCallbacks() { return DownloadsActions.rowCallbacks(this); },

  _bindDownloadsPage() { DownloadsActions.bindPage(this); },

  _selectedTasks() { return DownloadsView.selectedTasks(this); },
  _renderSelBar() { DownloadsView.renderSelBar(this); },
  _onRowSelect(id, e) { DownloadsView.onRowSelect(this, id, e); },
  _syncSelection() { DownloadsView.syncSelection(this); },
  _clearSelection() { DownloadsView.clearSelection(this); },
  _actionTargetIds(contextId) { return DownloadsView.actionTargetIds(this, contextId); },
  _asIds(x) { return DownloadsView.asIds(this, x); },
  async _forEachId(ids, fn) { return DownloadsView.forEachId(this, ids, fn); },

  renameTask(id) { return DownloadsActions.renameTask(this, id); },
  openSpeedLimit(id) { return DownloadsActions.openSpeedLimit(this, id); },
  showProperties(x) { DownloadsActions.showProperties(this, x); },
  openPriority(x) { return DownloadsActions.openPriority(this, x); },
  _ensureQueueOrder() { DownloadsActions.ensureQueueOrder(this); },

  _taskArray() { return DownloadsView.taskArray(this); },
  _filteredTasks() { return DownloadsView.filteredTasks(this); },
  _renderDownloads(structureChanged = false) { DownloadsView.renderDownloads(this, structureChanged); },
  _renderCatStrip() { DownloadsView.renderCatStrip(this); },
  _setCatFilter(cat) { DownloadsView.setCatFilter(this, cat); },
  _addRow(task) { DownloadsView.addRow(this); },
  _elapsedFor(task) { return DownloadsView.elapsedFor(this, task); },
  _updateRow(task) { DownloadsView.updateRow(this, task); },
  _removeRow(id) { DownloadsView.removeRow(this, id); },
  async _loadDownloads() { return DownloadsView.loadDownloads(this); },
  _updateBadge() { DownloadsView.updateBadge(this); },
  _updateCounts() { DownloadsView.updateCounts(this); },
  _showSkeletons() { DownloadsView.showSkeletons(this); },
  _renderQueueStrip() { DownloadsView.renderQueueStrip(this); },
  async _editGlobalLimit() { return DownloadsView.editGlobalLimit(this); },
  _scheduleLabel(s) { return DownloadsView.scheduleLabel(this, s); },
  async _editScheduler() { return DownloadsView.editScheduler(this); },
  async _toggleShutdown() { return DownloadsView.toggleShutdown(this); },
  async _cancelShutdown() { return DownloadsView.cancelShutdown(this); },

  // Queue gate — the "stop the queue" / "resume the queue" pair, distinct from
  // pausing individual downloads.
  async _pauseQueue() { return DownloadsActions.pauseQueue(this); },
  async _resumeQueue() { return DownloadsActions.resumeQueue(this); },
  async _loadQueueStatus() {
    try { Events.applyQueueStatus(this, await API.getQueueStatus()); } catch {}
  },

  //  New Download dialog
  // ══════════════════════════════════════════════════════════════════════
  //
  // The dialog lives in js/features/add-download/add-download.js; the two
  // delegates below keep the original App API for callers and tests.

  async openNewDownload(prefillUrl = null, { paste = false } = {}) {
    return AddDownload.open(this, prefillUrl, { paste });
  },

  async _addDownloadResolvingConflict(url, directory, name, checksum, autostart, category) {
    return AddDownload.resolveConflict(this, url, directory, name, checksum, autostart, category);
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
        // A group's derived state (a window opening, a slot freeing) changes
        // with no project mutation behind it, so it is re-read on the same
        // cadence rather than waiting for an event that will never arrive.
        if (typeof Groups !== "undefined") Groups.refresh();
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
        Utils.syncChipGroup("#historyFilterChips .chip", chip);
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

  /**
   * Fill every `select[data-project-select]` from the current group list.
   *
   * Rebuilds only when the list or the open group actually changed, so
   * re-entering a page never steals focus from a select the user is already
   * using.  A download always belongs to a group, so the whole row stays hidden
   * until the list has loaded rather than offering an empty dropdown.
   *
   * The default follows the open group, because that is what the user is
   * looking at — unless they have picked something else on this control, which
   * `data-touched` records.
   */
  _syncProjectSelects() {
    const projects = this.state.projects || [];
    const active = this.state.activeProject;
    const sig = projects.map((p) => p.id + ":" + p.name).join("|") + "::" + (active || "");
    Utils.$qa("select[data-project-select]").forEach((sel) => {
      const row = sel.closest("[id$='ProjectRow']");
      if (row) row.hidden = !projects.length;
      if (sel.dataset.sig === sig) return;
      sel.dataset.sig = sig;
      sel.innerHTML = projects
        .map((p) => `<option value="${Utils.escapeHtml(p.id)}">${Utils.escapeHtml(p.name)}</option>`)
        .join("");
      const wanted = sel.dataset.touched === "1" ? sel.value : active;
      if (wanted && projects.some((p) => p.id === wanted)) sel.value = wanted;
      if (!sel.dataset.bound) {
        sel.dataset.bound = "1";
        sel.addEventListener("change", () => { sel.dataset.touched = "1"; });
      }
    });
  },

  _bindBatchPage() {
    // A real tab widget: two mutually exclusive panels behind two tabs.  The
    // class swap alone left `aria-selected="true"` frozen on the first tab, so
    // a screen reader announced "URL list, selected" even while Pattern scan
    // was the panel on screen.  Select through one function so the visual and
    // the announced state cannot drift apart, and make the strip navigable
    // from the keyboard the way a tablist is expected to be.
    const tabs = Utils.$qa("#page-batch .tab-btn");
    const select = (btn, focus = false) => {
      if (!btn) return;
      tabs.forEach((b) => {
        const on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", String(on));
        b.tabIndex = on ? 0 : -1;
      });
      Utils.$qa("#page-batch .tab-panel").forEach((p) => p.classList.remove("active"));
      const panel = Utils.$id("tab-" + btn.dataset.tab);
      if (panel) panel.classList.add("active");
      if (focus) btn.focus();
    };
    tabs.forEach((btn, i) => {
      btn.addEventListener("click", () => select(btn));
      btn.addEventListener("keydown", (e) => {
        const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (step) {
          e.preventDefault();
          select(tabs[(i + step + tabs.length) % tabs.length], true);
        } else if (e.key === "Home" || e.key === "End") {
          e.preventDefault();
          select(e.key === "Home" ? tabs[0] : tabs[tabs.length - 1], true);
        }
      });
    });
    select(tabs.find((b) => b.classList.contains("active")) || tabs[0]);

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
      const projectId = (Utils.$id("batchProject") || {}).value || "";
      const count = await API.addBatch(urls, dir, projectId);
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
          const count = await API.addBatch(res.urls, dir, (Utils.$id("batchProject") || {}).value || "");
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

    // The project list may not have arrived yet at bind time; this also runs on
    // every `projects_changed` and on navigation, so the selector fills in.
    this._syncProjectSelects();
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

  //  Settings
  // ══════════════════════════════════════════════════════════════════════
  //
  // The Settings feature lives in js/features/settings/settings.js.  The
  // delegates below invoke it with `this` bound to App so every internal
  // `this.*` reference inside the module keeps working unchanged.

  _settingsDef() { return SettingsUI._settingsDef.call(this); },
  _settingsGroups() { return SettingsUI._settingsGroups.call(this); },
  async _buildSettings() { return SettingsUI._buildSettings.call(this); },
  _wireSettingsRail(container) { return SettingsUI._wireSettingsRail.call(this, container); },
  _fieldHtml(f, s, ctx) { return SettingsUI._fieldHtml.call(this, f, s, ctx); },
  _wireSettings(container, s) { return SettingsUI._wireSettings.call(this, container, s); },
  async _wireUpdatePanel(container) { return SettingsUI._wireUpdatePanel.call(this, container); },
  _renderSkippedRow() { return SettingsUI._renderSkippedRow.call(this); },
  _updateErrorText(err) { return SettingsUI._updateErrorText.call(this, err); },
  _updateProgressText(p) { return SettingsUI._updateProgressText.call(this, p); },
  _applyUpdateState(state, opts) { return SettingsUI._applyUpdateState.call(this, state, opts); },
  async _showUpdateDialog(release) { return SettingsUI._showUpdateDialog.call(this, release); },
  _syncUpdateDialog(state) { return SettingsUI._syncUpdateDialog.call(this, state); },
  async _confirmInstallUpdate() { return SettingsUI._confirmInstallUpdate.call(this); },
  async _maybeCheckUpdates() { return SettingsUI._maybeCheckUpdates.call(this); },
  async _initRulesManager(container) { return SettingsUI._initRulesManager.call(this, container); },

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
