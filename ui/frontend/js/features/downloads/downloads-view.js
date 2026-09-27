/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Feature: Downloads (view: filtering, rendering,
   selection, action bar, queue strip)
   ═══════════════════════════════════════════════════════════════════════════

   Everything the Downloads page renders or derives from task state:
   filtered/sorted task list, list rendering with signature-based diffing,
   category strip, selection (Windows File Explorer style), the docked
   selection action bar, the queue strip and row add/update/remove.

   Extracted verbatim from app.js (Phase 4).  Functions receive the
   orchestrating `App` object; `App._*` delegates keep the original surface.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, API, I18N, Components */

const DownloadsView = {
  /** Canonical category order — matches the backend's routing defaults. */
  CAT_ORDER: ["General", "Compressed", "Videos", "Music", "Documents", "Programs", "Images"],

  taskArray(app) { return Object.values(app.state.downloads); },

  filteredTasks(app) {
    const { filter, search, sortKey, sortDir } = app.state;
    let list = this.taskArray(app);

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
    if (app.state.catFilter !== "all") {
      list = list.filter((t) => (t.category || "General") === app.state.catFilter);
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

  renderDownloads(app, structureChanged = false) {
    if (app.state.page !== "downloads" && !structureChanged) return;
    const listEl = Utils.$id("downloadList");
    const headEl = Utils.$id("downloadHead");
    const emptyEl = Utils.$id("downloadsEmpty");
    const tasks = this.filteredTasks(app);
    const sig = tasks.map((t) => t.id).join("|") + "::" + app.state.filter + app.state.sortKey + app.state.sortDir + app.state.search + "::" + app.state.catFilter;

    if (sig === app.state.listSig && !structureChanged) {
      tasks.forEach((t) => app._updateRow(t));
      // Task states may have changed, which changes the available actions.
      if (app.state.selectedIds.size) app._renderSelBar();
      return;
    }
    app.state.listSig = sig;
    this.renderCatStrip(app);

    if (!this.taskArray(app).length) {
      headEl.hidden = true;
      listEl.innerHTML = "";
      app._clearSelection();
      emptyEl.replaceChildren(Components.emptyState({
        icon: "download",
        title: I18N.t("empty.no_downloads", "No downloads yet"),
        desc: I18N.t("empty.no_downloads_desc", "Paste a link or drop it anywhere to start your first download."),
        actions: [
          {
            label: I18N.t("cmd.paste_url", "Paste URL"),
            icon: "paste",
            primary: true,
            onClick: () => app.pasteFromClipboard(),
          },
          {
            label: I18N.t("empty.install_extension", "Install Browser Extension"),
            icon: "browser",
            onClick: () => app._setupExtension(),
          },
          {
            label: I18N.t("empty.import", "Import Downloads"),
            icon: "batch",
            onClick: () => app.navigate("batch"),
          },
        ],
      }));
      emptyEl.hidden = false;
      return;
    }

    if (!tasks.length) {
      headEl.hidden = true;
      listEl.innerHTML = "";
      app._clearSelection();
      const cat = app.state.catFilter;
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
            onClick: () => app._setCatFilter("all"),
          }]
          : undefined,
      }));
      emptyEl.hidden = false;
      return;
    }

    emptyEl.hidden = true;
    headEl.hidden = false;
    const frag = document.createDocumentFragment();
    tasks.forEach((t) => frag.appendChild(Components.renderRow(t, app.rowCallbacks, app._elapsedFor(t))));
    listEl.replaceChildren(frag);
    app._syncSelection();

    if (app.state.highlightId) {
      const row = listEl.querySelector(`[data-id="${app.state.highlightId}"]`);
      if (row) {
        row.classList.add("flash");
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      app.state.highlightId = null;
    }
  },

  /**
   * Render the category filter strip above the list.
   *
   * Hidden unless the list actually spans more than one category: with a
   * single category (or none) the strip is pure noise and just eats a row.
   */
  renderCatStrip(app) {
    const strip = Utils.$id("catStrip");
    if (!strip) return;

    const all = this.taskArray(app);
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
      if (app.state.catFilter !== "all" && !counts.has(app.state.catFilter)) {
        app.state.catFilter = "all";
      }
      return;
    }

    const ordered = [
      ...this.CAT_ORDER.filter((c) => counts.has(c)),
      ...Array.from(counts.keys()).filter((c) => !this.CAT_ORDER.includes(c)).sort(),
    ];

    const chip = (key, label, n) => {
      const active = app.state.catFilter === key;
      return `<button class="cat-chip${active ? " active" : ""}" data-cat="${Utils.escapeHtml(key)}" role="tab" aria-selected="${active}">`
        + `<span class="cat-t">${Utils.escapeHtml(label)}</span><span class="cat-n">${n}</span></button>`;
    };

    strip.innerHTML =
      chip("all", I18N.t("cat.all", "All categories"), all.length) +
      ordered.map((c) => chip(c, I18N.t("category." + c, c), counts.get(c))).join("");
    strip.hidden = false;

    Utils.$qa(".cat-chip", strip).forEach((el) => {
      el.addEventListener("click", () => app._setCatFilter(el.dataset.cat));
    });
  },

  setCatFilter(app, cat) {
    app.state.catFilter = cat || "all";
    app.state.listSig = "";
    app._renderDownloads(true);
  },

  addRow(app) {
    // New task arrived — refresh structure cheaply.
    app.state.listSig = "";
    app._renderDownloads();
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
  elapsedFor(app, task) {
    const id = task.id;
    let e = app.state.elapsed[id];
    const active = ["Downloading", "Starting", "Analyzing", "Merging", "Verifying"].includes(task.state);
    if (!e) {
      e = { ms: 0, from: null };
      // Seed from the backend wall-clock start when we first observe an
      // already-active task (e.g. the app restarted mid-download).
      if (task.started_at) {
        const seed = Math.max(0, (Date.now() / 1000 - task.started_at) * 1000);
        if (seed > 0) e.ms = seed;
      }
      app.state.elapsed[id] = e;
    }
    if (active) {
      if (e.from === null) e.from = Date.now();
    } else if (e.from !== null) {
      e.ms += Date.now() - e.from;
      e.from = null;
    }
    return e.ms + (e.from !== null ? Date.now() - e.from : 0);
  },

  updateRow(app, task) {
    if (app.state.page !== "downloads") return;
    const row = Utils.$q(`#downloadList [data-id="${task.id}"]`);
    if (row) Components.updateRow(row, task, app._elapsedFor(task));
  },

  removeRow(app, id) {
    app.state.listSig = "";
    const row = Utils.$q(`#downloadList [data-id="${id}"]`);
    if (row) {
      row.classList.add("row-leave");
      setTimeout(() => app._renderDownloads(true), 180);
    } else {
      app._renderDownloads(true);
    }
  },

  async loadDownloads(app) {
    const downloads = await API.getDownloads();
    if (!downloads) return;
    app.state.downloads = {};
    downloads.forEach((t) => { app.state.downloads[t.id] = t; });
    app.state.listSig = "";
    app._renderDownloads(true);
    app._updateBadge();
    app._updateCounts();
  },

  updateBadge(app) {
    const activeStates = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Queued"];
    const n = this.taskArray(app).filter((t) => activeStates.includes(t.state)).length;
    const badge = Utils.$id("navBadge");
    badge.textContent = n;
    badge.hidden = n === 0;
  },

  updateCounts(app) {
    const all = this.taskArray(app);
    const count = (states) => all.filter((t) => states.includes(t.state)).length;
    const set = (f, v) => { const el = Utils.$q(`#filterChips [data-filter="${f}"] .chip-n`); if (el) el.textContent = v; };
    set("all", all.length);
    set("active", count(["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Stopping"]));
    set("queued", count(["Queued"]));
    set("paused", count(["Paused"]));
    set("completed", count(["Complete"]));
    set("failed", count(["Failed", "Cancelled", "Stopped"]));
    app._renderQueueStrip();
  },

  // ── Selection (Windows File Explorer style) ─────────────────────────

  selectedTasks(app) {
    const sel = app.state.selectedIds;
    if (!sel || !sel.size) return [];
    const order = Array.from(Utils.$qa("#downloadList .dl-row")).map((r) => r.dataset.id);
    const ids = order.filter((id) => sel.has(id));
    const rest = Array.from(sel).filter((id) => !order.includes(id));
    return [...ids, ...rest].map((id) => app.state.downloads[id]).filter(Boolean);
  },

  onRowSelect(app, id, e) {
    const sel = app.state.selectedIds;
    const ctrl = !!(e && (e.ctrlKey || e.metaKey));
    const shift = !!(e && e.shiftKey);

    // Current visual order of the rendered list (range selection follows it).
    const order = Array.from(Utils.$qa("#downloadList .dl-row")).map((r) => r.dataset.id);
    const clicked = order.indexOf(id);

    if (shift && app.state.selAnchor != null && clicked !== -1) {
      const a = order.indexOf(app.state.selAnchor);
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
        app.state.selAnchor = id;
      }
    } else if (ctrl) {
      // Ctrl+Click: toggle the item, keep the rest of the selection.
      if (sel.has(id)) sel.delete(id); else sel.add(id);
      app.state.selAnchor = id;
    } else {
      // Normal click: clear previous selection, select only this item.
      sel.clear(); sel.add(id);
      app.state.selAnchor = id;
    }
    if (!sel.size) app.state.selAnchor = null;
    app._syncSelection();
  },

  syncSelection(app) {
    const sel = app.state.selectedIds;
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
    if (pruned && !sel.size) app.state.selAnchor = null;
    if (app.state.selAnchor != null && !visible.has(app.state.selAnchor)) app.state.selAnchor = null;
    app._renderSelBar();
  },

  clearSelection(app) {
    app.state.selectedIds.clear();
    app.state.selAnchor = null;
    app._renderSelBar();
  },

  /**
   * Rebuild the docked action bar for the current selection.
   *
   * Only actions valid for the selected states are rendered, so the bar stays
   * short and never shows a button that cannot do anything.
   */
  renderSelBar(app) {
    const bar = Utils.$id("selBar");
    if (!bar) return;
    const tasks = this.selectedTasks(app);

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

    const actions = Components.selectionActions(tasks, app.rowCallbacks);
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

  // ── Context-menu action targeting (Windows File Explorer rule) ──────
  //
  // If the right-clicked task is part of the current multi-selection, the
  // action applies to the WHOLE selection.  Otherwise it applies only to the
  // right-clicked task.  This keeps `selectedIds` (selection) and `contextId`
  // (right-clicked task) as separate concepts.

  actionTargetIds(app, contextId) {
    const sel = app.state.selectedIds;
    if (contextId && sel.has(contextId) && sel.size > 1) return Array.from(sel);
    return contextId ? [contextId] : [];
  },

  asIds(app, x) {
    return Array.isArray(x) ? x : [x];
  },

  // Run a single-task async action over every id, never letting one failure
  // stop the rest.
  async forEachId(app, ids, fn) {
    for (const id of ids) {
      try { await fn(id); } catch (e) { API.logJs("batch action: " + String(e)); }
    }
  },

  showSkeletons(app) {
    Utils.$id("downloadList").innerHTML = Components.skeletonRows(4);
    const body = Utils.$q("#dashActive .panel-body");
    if (body) body.innerHTML = Components.skeletonRows(2);
  },

  // ── Queue strip ──────────────────────────────────────────────────────

  renderQueueStrip(app) {
    const all = this.taskArray(app);
    const ACTIVE = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    const active = all.filter((t) => ACTIVE.includes(t.state));
    const waiting = all.filter((t) => t.state === "Queued");
    const failed = all.filter((t) => ["Failed", "Cancelled", "Stopped"].includes(t.state));

    const set = (id, v) => { const el = Utils.$id(id); if (el) el.textContent = v; };
    set("qsActive", active.length);
    set("qsWaiting", waiting.length);
    set("qsSpeed", Utils.formatSpeed(active.reduce((s, t) => s + (t.speed_bps || 0), 0)));

    const limit = (app.state.settings && app.state.settings.max_speed_bps) || 0;
    const limitBtn = Utils.$id("qsLimitBtn");
    if (limitBtn) {
      limitBtn.textContent = limit > 0 ? Utils.formatSpeed(limit) : I18N.t("dlg.unlimited", "Unlimited");
      limitBtn.classList.toggle("warn", limit > 0);
    }

    const sched = app.state.settings || {};
    const schedBtn = Utils.$id("qsSchedBtn");
    if (schedBtn) {
      const on = !!sched.scheduler_enabled;
      schedBtn.textContent = app._scheduleLabel(sched);
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
  async editGlobalLimit(app) {
    const current = (app.state.settings && app.state.settings.max_speed_bps) || 0;
    const bps = await Components.speedLimitDialog(current, I18N.t("queue.speed_limit", "Global limit"));
    if (bps === null) return;
    try {
      await API.updateSettings({ max_speed_bps: bps });
      app.state.settings = { ...(app.state.settings || {}), max_speed_bps: bps };
      app._renderQueueStrip();
      Components.toast(
        I18N.t("toast.speed_limit_set", "Speed limit updated"),
        bps > 0 ? Utils.formatSpeed(bps) : I18N.t("dlg.unlimited", "Unlimited"),
        "success", 2400);
    } catch (e) {
      API.logJs("global limit: " + String(e));
    }
  },

  /** Label for the current schedule, shared by the queue strip and toasts. */
  scheduleLabel(app, s = app.state.settings || {}) {
    if (!s.scheduler_enabled) return I18N.t("queue.off", "Off");
    const win = `${s.schedule_start_time || "—"}–${s.schedule_stop_time || "—"}`;
    const days = Array.isArray(s.schedule_days) ? s.schedule_days : [];
    // Only mention days when they actually narrow the window.
    return days.length ? `${win} · ${days.length}/7` : win;
  },

  /** Quick scheduler editor driven from the queue strip (never leaves the page). */
  async editScheduler(app) {
    const s = app.state.settings || {};
    const res = await Components.schedulerDialog(s);
    if (res === null) return;   // cancelled or closed — discard everything
    try {
      await API.updateSettings({
        scheduler_enabled: res.enabled,
        schedule_start_time: res.start,
        schedule_stop_time: res.stop,
        schedule_days: res.days,
      });
      app.state.settings = {
        ...s,
        scheduler_enabled: res.enabled,
        schedule_start_time: res.start,
        schedule_stop_time: res.stop,
        schedule_days: res.days,
      };
      app._renderQueueStrip();
      Components.toast(
        I18N.t("toast.scheduler_set", "Scheduler updated"),
        app._scheduleLabel(),
        "success", 2400);
    } catch (e) {
      API.logJs("scheduler: " + String(e));
    }
  },
};
