/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Feature: Downloads (actions & callbacks)
   ═══════════════════════════════════════════════════════════════════════════

   Action dispatch for the Downloads page: the rowCallbacks table consumed by
   the shared download-row / context-menu components, plus the per-task
   dialogs (rename / speed limit / priority / properties).

   Extracted verbatim from app.js (Phase 4).  Every function receives the
   orchestrating `App` object, so this module holds no state of its own and
   `App.rowCallbacks` delegates here — keeping the original public surface
   (and the unit tests that exercise `App.rowCallbacks.*`) intact.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global API, Utils, I18N, Components */

const DownloadsActions = {
  /**
   * The callback table handed to Components.renderRow / rowMenu /
   * selectionActions.  Behavior identical to the pre-refactor table.
   */
  rowCallbacks(app) {
    return {
      onRowSelect(id, e) { app._onRowSelect(id, e); },
      // Resolve the action target list for a right-clicked task (context menu).
      targetsFor(id) { return app._actionTargetIds(id); },

      onPause(x) { app._forEachId(app._asIds(x), (id) => API.pauseDownload(id)); },
      onResume(x) { app._forEachId(app._asIds(x), (id) => API.resumeDownload(id)); },
      onStart(x) { app._forEachId(app._asIds(x), (id) => API.startTask(id)); },
      onRetry(x) { app._forEachId(app._asIds(x), (id) => API.retryDownload(id)); },
      async onCancel(x) {
        const ids = app._asIds(x);
        const n = ids.length;
        const ok = await Components.confirm({
          title: I18N.t("confirm.cancel_download", "Cancel download"),
          message: n > 1
            ? I18N.t("confirm.cancel_download_multi", "Cancel {n} downloads?").replace("{n}", n)
            : I18N.t("confirm.cancel_download_msg", "Stop this download? Progress is saved so you can resume later."),
          okText: I18N.t("act.cancel", "Cancel"), cancelText: I18N.t("confirm.keep", "Keep"), danger: true,
        });
        if (ok) app._forEachId(ids, (id) => API.cancelDownload(id));
      },
      async onRemove(x) {
        const ids = app._asIds(x);
        const n = ids.length;
        const ok = await Components.confirm({
          title: I18N.t("confirm.remove_download", "Remove download"),
          message: n > 1
            ? I18N.t("confirm.remove_download_multi", "Remove {n} selected downloads?").replace("{n}", n)
            : I18N.t("confirm.remove_download_msg", "Remove this entry from the list? The file on disk is kept."),
          okText: I18N.t("act.remove", "Remove"), cancelText: I18N.t("confirm.keep", "Keep"), danger: true,
        });
        if (ok) app._forEachId(ids, (id) => API.removeDownload(id));
      },
      onOpenFolder(id) { API.openFolder(id); },
      onOpenFile(id) { API.openFile(id); },
      onRedownload(id) { API.redownload(id); },
      onMove(x, delta) {
        const ids = app._asIds(x);
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
        app._ensureQueueOrder();
        app.rowCallbacks.onMove(x, delta);
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
        const ids = app._asIds(x);
        const urls = ids
          .map((id) => (app.state.downloads && app.state.downloads[id]) || null)
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
      onRename(id) { app.renameTask(id); },
      onSpeedLimit(id) { app.openSpeedLimit(id); },
      onPriority(x) { app.openPriority(x); },
      onProperties(x) { app.showProperties(x); },
    };
  },

  async renameTask(app, id) {
    const t = app.state.downloads[id];
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

  async openSpeedLimit(app, id) {
    const t = app.state.downloads[id];
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

  showProperties(app, x) {
    const ids = app._asIds(x);
    const tasks = ids.map((id) => app.state.downloads[id]).filter(Boolean);
    Components.propertiesDialog(tasks);
  },

  /**
   * Set the scheduling priority of one or several downloads.
   *
   * The stored scale runs 0 = highest … 10 = lowest; the dialog presents it
   * as High / Normal / Low so the direction is never ambiguous.
   */
  async openPriority(app, x) {
    const ids = app._asIds(x).filter((id) => app.state.downloads[id]);
    if (!ids.length) return;
    const first = app.state.downloads[ids[0]];
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
  ensureQueueOrder(app) {
    if (app.state.sortKey === "queue") return;
    app.state.sortKey = "queue";
    app.state.sortDir = 1;
    const sel = Utils.$id("sortSelect");
    if (sel) sel.value = "queue:1";
    app.state.listSig = "";
    app._renderDownloads(true);
    Components.toast(
      I18N.t("toast.queue_order_on", "Showing queue order"),
      I18N.t("toast.queue_order_on_msg", "Move up / down changes the order downloads start in."),
      "info", 3200);
  },

  /**
   * Close the queue gate without touching the downloads already in flight.
   *
   * Deliberately not the same action as pausing a download: this stops the
   * queue feeding the next task in, and the backend reports the new state so
   * the banner is never guessed.  See docs/QUEUE.md §1.
   */
  async pauseQueue(app) {
    try {
      const status = await API.pauseQueue();
      if (status) Events.applyQueueStatus(app, status);
      Components.toast(
        I18N.t("toast.queue_paused", "Queue paused"),
        I18N.t("toast.queue_paused_msg", "Running downloads continue; nothing new will start"),
        "info", 3000);
    } catch (e) {
      API.logJs("pause queue: " + String(e));
    }
  },

  async resumeQueue(app) {
    try {
      const status = await API.resumeQueue();
      if (status) Events.applyQueueStatus(app, status);
      Components.toast(
        I18N.t("toast.queue_resumed", "Queue resumed"),
        I18N.t("toast.queue_resumed_msg", "Waiting downloads can start again"),
        "info", 2600);
    } catch (e) {
      API.logJs("resume queue: " + String(e));
    }
  },

  /** Page-level toolbar bindings (chips, sort, bulk buttons, queue strip). */
  bindPage(app) {
    Utils.$qa("#filterChips .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        Utils.syncChipGroup("#filterChips .chip", chip);
        app.state.filter = chip.dataset.filter;
        app._renderDownloads(true);
      });
    });

    const sortSel = Utils.$id("sortSelect");
    sortSel.addEventListener("change", () => {
      const [key, dir] = sortSel.value.split(":");
      app.state.sortKey = key;
      app.state.sortDir = +dir;
      app._renderDownloads(true);
    });

    // "Pause everything" / "Resume everything" are the *whole-queue* actions:
    // they close/open the queue gate AND pause/resume every download.  Pausing
    // a single download is a different action with a different name, so the
    // two can never be confused.  The backend returns the resulting gate state
    // and that is what gets rendered — never an assumption about what the
    // click must have done.
    Utils.$id("btnPauseAll").addEventListener("click", async () => {
      const status = await API.pauseAll();
      if (status) Events.applyQueueStatus(app, status);
      Components.toast(I18N.t("toast.all_paused", "All paused"), I18N.t("toast.all_paused_msg", "Every active download was paused"), "info");
    });
    Utils.$id("btnResumeAll").addEventListener("click", async () => {
      const status = await API.resumeAll();
      if (status) Events.applyQueueStatus(app, status);
      Components.toast(I18N.t("toast.all_resumed", "All resumed"), I18N.t("toast.all_resumed_msg", "Paused downloads are running again"), "info");
    });
    Utils.$id("btnClearFinished").addEventListener("click", async () => {
      await API.clearFinished();
      Components.toast(I18N.t("toast.list_cleared", "List cleared"), I18N.t("toast.list_cleared_msg", "Finished entries were removed"), "info");
    });

    // Selection action bar
    const sbClear = Utils.$id("sbClear");
    if (sbClear) sbClear.addEventListener("click", () => app._clearSelection());

    // Queue strip controls
    const limitBtn = Utils.$id("qsLimitBtn");
    if (limitBtn) limitBtn.addEventListener("click", () => app._editGlobalLimit());
    const schedBtn = Utils.$id("qsSchedBtn");
    if (schedBtn) schedBtn.addEventListener("click", () => app._editScheduler());
    const shutBtn = Utils.$id("qsShutdownBtn");
    if (shutBtn) shutBtn.addEventListener("click", () => app._toggleShutdown());
    const shutCancel = Utils.$id("qsShutdownCancel");
    if (shutCancel) shutCancel.addEventListener("click", () => app._cancelShutdown());
    const queueResume = Utils.$id("qsQueueResume");
    if (queueResume) queueResume.addEventListener("click", () => app._resumeQueue());
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
};
