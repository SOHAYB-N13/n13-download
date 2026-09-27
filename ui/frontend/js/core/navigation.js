/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Core: Navigation
   ═══════════════════════════════════════════════════════════════════════════

   Page switching: sidebar wiring, active-page classes, entrance animation,
   the nav indicator, page chrome (title/subtitle) and the per-page render
   dispatch.  The per-page render calls remain owned by App (they are feature
   entry points that will be handed to their features in later phases); this
   module only decides *when* they run.

   Extracted verbatim from app.js (Phase 2).  `App.navigate` delegates here.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, Queue */

const Nav = {
  /** Bind sidebar nav items + the collapse toggle. */
  bind(app) {
    Utils.$qa(".nav-item[data-page]").forEach((item) => {
      item.addEventListener("click", () => app.navigate(item.dataset.page));
    });
    Utils.$id("sidebarToggle")?.addEventListener("click", () => app.toggleSidebar());
  },

  toggleSidebar(app) {
    app.state.sidebarCollapsed = !app.state.sidebarCollapsed;
    this.applySidebar(app);
    Prefs.save(app);
  },

  applySidebar(app) {
    document.body.classList.toggle("sidebar-collapsed", app.state.sidebarCollapsed);
  },

  navigate(app, page) {
    if (!app.pages[page]) return;
    app.state.page = page;
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
    this.moveNavIndicator(app);
    this.renderPageChrome(app);

    if (page === "history") app._renderHistory();
    if (page === "settings") app._buildSettings();
    if (page === "browser") app._refreshServerStatus();
    if (page === "logs") app._renderLogs();
    if (page === "dashboard") app._renderDashboardLists();
    if (page === "downloads") app._renderDownloads(true);
    // The Queue page owns its own rendering (js/queue.js).
    if (page === "queue" && typeof Queue !== "undefined") Queue.render();
  },

  moveNavIndicator(app) {
    const active = Utils.$q(".nav-item[data-page].active");
    const ind = Utils.$id("navIndicator");
    if (!active || !ind) return;
    ind.style.transform = `translateY(${active.offsetTop}px)`;
    ind.style.height = active.offsetHeight + "px";
  },

  renderPageChrome(app) {
    const meta = app.pages[app.state.page];
    const title = I18N.t("page." + app.state.page, meta.title);
    Utils.$id("pageTitle").textContent = title;
    Utils.$id("pageSub").textContent = I18N.t("page." + app.state.page + ".sub", meta.sub);
    document.title = `${title} · N13 Download Manager`;
  },
};
