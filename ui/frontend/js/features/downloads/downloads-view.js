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
  /**
   * Canonical category order — the backend's own vocabulary.
   *
   * These are exactly the values `core/analyzer.detect_category` can return, so
   * every category a task can actually carry has a place in this order.  A name
   * that is not listed here still renders (it is appended, sorted) — that is how
   * a legacy task stored under an older name keeps working.
   */
  // Display order for the category strip.  Kept as a literal (rather than
  // read from `Utils.CATEGORY_ORDER`) so this module still loads on its own in
  // tests; `tests/frontend/category-parity.test.mjs` asserts the two lists —
  // and the backend's extension table — all agree, so the copy cannot drift.
  CAT_ORDER: ["General", "Archives", "Videos", "Music", "Documents", "Programs", "Images", "Other"],

  taskArray(app) { return Object.values(app.state.downloads); },

  /**
   * The tasks in the open group, before any filter or search is applied.
   *
   * This is the base list for everything that *describes* what the user is
   * looking at — the filter chips, the category strip and the list itself — so
   * those counts always agree with the rows.  The queue strip deliberately does
   * not use it: the queue is one app-wide thing, not a per-group one.
   *
   * `GroupsModel` is guarded because this module also loads in unit tests that
   * do not pull the group feature in.
   */
  groupTasks(app) {
    const all = this.taskArray(app);
    const group = app.state.activeProject;
    if (typeof GroupsModel !== "undefined" && !GroupsModel.isAll(group)) {
      return GroupsModel.tasksFor(all, group);
    }
    return all;
  },

  filteredTasks(app) {
    const { filter, search, sortKey, sortDir } = app.state;
    // Group scope comes first, so every filter below — the chips, the category
    // strip, the search and the counts — describes the group the user is
    // looking at rather than the whole workspace.
    let list = this.groupTasks(app);

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
      // Effective start order first — what the scheduler will really do — with
      // the manual position as a tiebreak for everything that is not waiting.
      // Sorting by `queue_index` alone was the lie: once priorities differ it
      // is not the order anything starts in.  See docs/QUEUE.md §2.
      queue: (t) => [
        t.queue_position > 0 ? t.queue_position : Number.MAX_SAFE_INTEGER,
        t.queue_index >= 0 ? t.queue_index : Number.MAX_SAFE_INTEGER,
      ],
    }[sortKey] || ((t) => t.created_at || 0);

    list = [...list].sort((a, b) => this.compareKeys(key(a), key(b)) * sortDir);
    return list;
  },

  /**
   * Compare two sort keys, which may be scalars or tuples.
   *
   * Tuple keys compare element by element so a secondary key can break a tie
   * without collapsing into a string comparison (`[10, 0] < [9, 0]` would be
   * true as strings and wrong as numbers).
   */
  compareKeys(a, b) {
    const aa = Array.isArray(a) ? a : [a];
    const bb = Array.isArray(b) ? b : [b];
    const n = Math.max(aa.length, bb.length);
    for (let i = 0; i < n; i++) {
      const x = aa[i];
      const y = bb[i];
      if (x < y) return -1;
      if (x > y) return 1;
    }
    return 0;
  },

  renderDownloads(app, structureChanged = false) {
    if (app.state.page !== "downloads" && !structureChanged) return;
    const listEl = Utils.$id("downloadList");
    const headEl = Utils.$id("downloadHead");
    const emptyEl = Utils.$id("downloadsEmpty");
    const tasks = this.filteredTasks(app);
    // `activeProject` is part of the signature: two different groups can hold
    // the same set of ids only if a task moved, and switching groups must
    // always rebuild the rows rather than diff against the previous group's.
    const sig = tasks.map((t) => t.id).join("|")
      + "::" + app.state.filter + app.state.sortKey + app.state.sortDir
      + app.state.search + "::" + app.state.catFilter
      + "::" + (app.state.activeProject || "");

    if (sig === app.state.listSig && !structureChanged) {
      tasks.forEach((t) => app._updateRow(t));
      // Task states may have changed, which changes the available actions.
      if (app.state.selectedIds.size) app._renderSelBar();
      return;
    }
    app.state.listSig = sig;
    this.renderCatStrip(app);
    // The tab row shows a live count per group and a state dot, so it is
    // repainted with the list rather than only when a group itself changes.
    if (typeof Groups !== "undefined") Groups.paint();
    // The chips are group-scoped, so switching groups has to renumber them.
    // (Task *state* changes go through the event path, which calls this too.)
    this.updateCounts(app);

    if (!this.taskArray(app).length) {
      headEl.hidden = true;
      listEl.innerHTML = "";
      app._clearSelection();
      // Nothing anywhere yet: offer the things a first-run user wants, and the
      // group affordance alongside them so groups are discoverable before the
      // first download rather than after it.
      //
      // `(key, vars, fallback)` is the same translator shape `I18N.fmt` has;
      // the group view layer is guarded because this module also loads in unit
      // tests that do not pull the group feature in.
      const t = (key, vars, fallback) => I18N.fmt(key, vars, fallback);
      const opts = (typeof GroupsView !== "undefined")
        ? GroupsView.emptyAllOptions(t, {
            add: () => app.openNewDownload(null, { paste: true }),
            newGroup: () => Groups.create(),
            setupExtension: () => app._setupExtension(),
            importBatch: () => app.navigate("batch"),
          })
        : {
            icon: "download",
            title: I18N.t("empty.no_downloads", "No downloads yet"),
            desc: I18N.t("empty.no_downloads_desc", "Paste a link or drop it anywhere to start your first download."),
            actions: [{
              label: I18N.t("cmd.paste_url", "Paste URL"),
              icon: "paste",
              primary: true,
              onClick: () => app.pasteFromClipboard(),
            }],
          };
      emptyEl.replaceChildren(Components.emptyState(opts));
      emptyEl.hidden = false;
      return;
    }

    if (!tasks.length) {
      headEl.hidden = true;
      listEl.innerHTML = "";
      app._clearSelection();
      const project = (typeof Groups !== "undefined") ? Groups.current() : null;
      // A group being *open* is not the same as the group being *empty*: with
      // a search or a filter active, a group full of downloads can produce an
      // empty row list.  Only the second case may claim "nothing in this
      // group yet"; the first is an ordinary "nothing matches".
      const groupEmpty = !!project && this.groupTasks(app).length === 0;
      const reason = this.emptyReason(app.state);
      const t = (key, vars, fallback) => I18N.fmt(key, vars, fallback);

      if (groupEmpty) {
        // The open group simply has nothing in it yet — a different situation
        // from "your filters hid everything", and it gets a different answer.
        emptyEl.replaceChildren(Components.emptyState(GroupsView.emptyGroupOptions(
          t,
          () => app.openNewDownload(),
        )));
      } else {
        // "Nothing matches" must always come with the way out.  Whatever is
        // hiding the rows — the status chip, the category chip or the search
        // box — the one action offered is the one that undoes *all* of them,
        // so a user can never be left staring at an empty list with no exit.
        emptyEl.replaceChildren(Components.emptyState({
          icon: "search",
          title: I18N.t("empty.nothing_matches", "Nothing matches"),
          desc: reason.categoryOnly
            ? I18N.t("empty.nothing_in_category", "No downloads in this category yet.")
            : I18N.t("empty.nothing_matches_desc", "Try a different filter or search term."),
          actions: reason.resettable
            ? [{
              label: reason.categoryOnly
                ? I18N.t("cat.show_all", "Show all categories")
                : I18N.t("empty.show_all", "Show all downloads"),
              icon: "list",
              primary: true,
              onClick: () => this.resetListFilters(app),
            }]
            : undefined,
        }));
      }
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

    const all = this.groupTasks(app);
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
      // A toggle button, not a tab: the strip re-filters one list rather than
      // switching between panels, so `aria-pressed` states the truth.
      return `<button class="cat-chip${active ? " active" : ""}" data-cat="${Utils.escapeHtml(key)}" aria-pressed="${active}">`
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

  /**
   * Why the visible list is empty, and whether a filter is responsible.
   *
   * Pure, so the "is the user stuck?" question is testable without a DOM.  The
   * view uses it for two decisions: which wording to show, and — the part that
   * matters — whether the empty state must offer a way back.  An empty list
   * with no exit is the failure this exists to prevent.
   */
  emptyReason(state) {
    const s = state || {};
    const filter = s.filter || "all";
    const catFilter = s.catFilter || "all";
    const search = String(s.search || "").trim();
    const byFilter = filter !== "all";
    const byCategory = catFilter !== "all";
    const bySearch = search.length > 0;
    return {
      filter,
      catFilter,
      search,
      byFilter,
      byCategory,
      bySearch,
      // Only the category chip is hiding things — the app already had precise
      // wording for that case, so keep it.
      categoryOnly: byCategory && !byFilter && !bySearch,
      resettable: byFilter || byCategory || bySearch,
    };
  },

  /**
   * Undo every filter that can hide a row, and put the controls back in sync.
   *
   * Clearing the state alone would leave the status chip and the search box
   * still looking active, so the widgets are reset too — otherwise the toolbar
   * and the list would disagree about what is being shown.
   */
  resetListFilters(app) {
    app.state.filter = "all";
    app.state.catFilter = "all";
    app.state.search = "";
    Utils.syncChipGroup("#filterChips .chip", Utils.$q('#filterChips [data-filter="all"]'));
    const search = Utils.$id("globalSearch");
    if (search) search.value = "";
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
    // A row is updated whenever it exists, whatever page is on screen.  The
    // list is rendered into the document even while its section is hidden, so
    // gating this on `state.page` did not avoid work — it dropped every state
    // and progress update for the visible list whenever `state.page` and the
    // active section disagreed, which left rows frozen on a stale label (see
    // `App._initialPage`).
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
    // Group-scoped, so the chip numbers always match the rows on screen.
    const all = this.groupTasks(app);
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
      // `data-tip` directly, never `data-i18n-tip`: that attribute is resolved
      // as a *dictionary key* on every language change, so handing it an
      // already-translated label made the lookup miss and blanked the tooltip.
      // The label is also the button's visible text, so repeating it in a
      // tooltip adds nothing — the tooltip is dropped on purpose.
      return `<button class="sb-btn${cls}" data-sb="${a.id}">${Utils.icon(a.icon, 15)}<span>${Utils.escapeHtml(a.label)}</span></button>`;
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

    const shutBtn = Utils.$id("qsShutdownBtn");
    if (shutBtn) {
      // Render the *runtime* state, never the preference alone: "the toggle is
      // On" and "a shutdown is counting down" are different facts.
      const as = app.state.autoShutdown;
      const enabled = as ? !!as.enabled : !!sched.shutdown_when_done;
      const state = as ? as.state : (enabled ? "Armed" : "Disabled");
      shutBtn.textContent = Events.autoShutdownStateLabel(state);
      shutBtn.classList.toggle("warn", state === "Countdown" || state === "Blocked");
      shutBtn.classList.toggle("good", state === "Armed" || state === "Waiting");
      const reason = as ? (as.cancel_reason || as.reason) : "";
      const reasonTxt = Events.autoShutdownReasonLabel(reason);
      shutBtn.setAttribute("data-tip", reasonTxt
        ? `${I18N.t("queue.auto_shutdown", "Auto shutdown")} · ${reasonTxt}`
        : I18N.t("queue.auto_shutdown_tip", "Shut down the computer when all downloads finish"));
    }
    // The countdown banner is driven entirely by the runtime state.
    Events.syncShutdownCountdown(app);

    // The queue-gate banner is driven entirely by the runtime state too.  A
    // paused queue must never be invisible: nothing starting with no
    // explanation reads as a hung app.
    const strip = Utils.$id("queueStrip");
    if (strip) strip.classList.toggle("paused", !!app.state.queuePaused);
    const pausedBanner = Utils.$id("qsQueuePausedBanner");
    if (pausedBanner) pausedBanner.hidden = !app.state.queuePaused;

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
    const start = s.schedule_start_time || "";
    const stop = s.schedule_stop_time || "";
    // End time is optional — without it the window runs from the start time
    // onwards (days selection still applies).
    const win = (start && stop) ? `${start}–${stop}`
      : start ? I18N.fmt("queue.from_time", { t: start }, `from ${start}`)
      : I18N.t("queue.on", "On");
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

  /**
   * Auto-shutdown toggle driven from the queue strip (persisted).
   *
   * Turning it off while a countdown is live cancels the pending Windows
   * shutdown — the backend owns that logic, this only asks for it.
   */
  async toggleShutdown(app) {
    const as = app.state.autoShutdown;
    const on = as ? !as.enabled : !(app.state.settings || {}).shutdown_when_done;
    try {
      const status = await API.setAutoShutdown(on);
      if (status) Events.applyAutoShutdown(app, status);
      else app._renderQueueStrip();
      Components.toast(
        I18N.t("queue.auto_shutdown", "Auto shutdown"),
        on ? I18N.t("toast.shutdown_on", "The computer will shut down when all downloads finish")
           : I18N.t("toast.shutdown_off", "Auto shutdown turned off"),
        on ? "warning" : "info", 3000);
    } catch (e) {
      API.logJs("auto shutdown: " + String(e));
    }
  },

  /**
   * Cancel a pending shutdown.  The backend aborts the Windows shutdown,
   * disarms the preference (so it cannot immediately re-arm) and reports the
   * resulting state.
   */
  async cancelShutdown(app) {
    try {
      const status = await API.cancelAutoShutdown();
      if (status) Events.applyAutoShutdown(app, status);
      else app._renderQueueStrip();
      Components.toast(
        I18N.t("toast.shutdown_cancelled", "Auto shutdown cancelled"),
        I18N.t("auto_shutdown.reason.user_cancelled", "Cancelled by you"),
        "info", 4000);
    } catch (e) {
      API.logJs("cancel shutdown: " + String(e));
    }
  },
};
