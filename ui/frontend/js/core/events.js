/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Core: Backend events & polling
   ═══════════════════════════════════════════════════════════════════════════

   Owns the backend event pipeline: the 200ms poll loop against
   API.pollEvents() and the dispatch of each event to the feature handlers
   that live on App (which will move to their features in later phases).

   Extracted verbatim from app.js (Phase 2).  `App._startPolling` and
   `App._handleEvent` delegate here, so call sites and tests see no change.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global API, Utils, I18N, Components */

const Events = {
  /**
   * Long-running poll loop.  One poll every 200ms; errors are swallowed so a
   * hiccup in the bridge never kills the loop (matches the original code).
   */
  startPolling(app) {
    const poll = async () => {
      try {
        const events = await API.pollEvents();
        if (events && events.length) {
          requestAnimationFrame(() => events.forEach((evt) => app._handleEvent(evt)));
        }
      } catch {}
      setTimeout(poll, 200);
    };
    poll();
  },

  handleEvent(app, evt) {
    if (evt.type === "task" && evt.task) {
      const t = evt.task;
      const had = !!app.state.downloads[t.id];
      if (evt.event === "removed") {
        delete app.state.downloads[t.id];
        delete app.state.elapsed[t.id];
        app.state.selectedIds.delete(t.id);
        if (!app.state.selectedIds.size) app.state.selAnchor = null;
        app._removeRow(t.id);
      } else {
        const prev = app.state.downloads[t.id];
        app.state.downloads[t.id] = t;
        app._elapsedFor(t); // keep the active-time tracker honest on every event
        if (!had) {
          app._addRow(t);
        } else if (app.state.sortKey === "queue" && prev
                   && (prev.queue_index !== t.queue_index
                       || prev.queue_position !== t.queue_position)) {
          // A queue reorder moved this row.  _updateRow only rewrites cells in
          // place, so rebuild the list to actually reorder it.
          app.state.listSig = "";
          app._renderDownloads(true);
        } else {
          app._updateRow(t);
        }
      }
      app._updateBadge();
      app._updateCounts();
      app._renderDashboardLists();
      if (evt.event === "finished") {
        if (t.state === "Complete") {
          Components.toast(I18N.t("toast.download_complete", "Download complete"), Utils.fileName(t) + " — " + Utils.formatSize(t.completed || t.total), "success");
          app._refreshHistory();
        } else if (t.state === "Failed") {
          Components.toast(I18N.t("toast.download_failed", "Download failed"), `${Utils.fileName(t)}${t.error ? " — " + t.error : ""}`, "error");
          app._refreshHistory();
        } else if (t.state === "Cancelled") {
          Components.toast(I18N.t("toast.download_cancelled", "Download cancelled"), Utils.fileName(t), "info");
        }
      }
    } else if (evt.type === "log") {
      app.state.logs.push(evt.message);
      if (app.state.logs.length > 800) app.state.logs.shift();
      if (app.state.page === "logs") app._appendLog(evt.message);
    } else if (evt.type === "browser_url") {
      // Proof the extension is installed and paired with our live server.
      app.state.extLastSeen = Date.now();
      app._renderExtPill();
      Components.toast(I18N.t("toast.link_captured", "Link captured"), I18N.t("toast.link_captured_msg", "Received from browser extension"), "info");
      app.openNewDownload(evt.url);
    } else if (evt.type === "clipboard_url") {
      app._onClipboardLink(evt.url);
    } else if (evt.type === "navigate") {
      app.navigate(evt.page || "dashboard");
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
      app.state.extInstalling = false;
      const btn = Utils.$id("btnInstallExt");
      if (btn) btn.disabled = false;
      if (evt.ok) {
        Components.toast(I18N.t("ext_install.done", "Extension installed successfully"), I18N.t("ext_install.done_msg", "N13 is now connected to Chrome"), "success", 8000);
      } else {
        Components.toast(I18N.t("ext_install.failed", "Extension install failed"), String(evt.error || ""), "error", 10000);
      }
    } else if (evt.type === "window") {
      app._setMaxState(!!evt.maximized);
    } else if (evt.type === "auto_shutdown_state") {
      Events.applyAutoShutdown(app, evt.status);
    } else if (evt.type === "queue_gate") {
      Events.applyQueueStatus(app, evt.status);
    } else if (evt.type === "projects_changed") {
      // The backend pushes this whenever a group is created, edited, paused,
      // resumed or deleted, and whenever a task moves between groups.  The
      // strip reloads its own data, so the event carries no payload — there is
      // exactly one response shape and one place that reads it.
      if (typeof Groups !== "undefined") {
        Promise.resolve(Groups.load())
          .then(() => app._syncProjectSelects())
          .catch(() => {});
      }
    } else if (evt.type === "update_state") {
      app._applyUpdateState(evt.state);
      const st = evt.state;
      if (st.state === "available" && st.release?.version) {
        const skipped = app.state.updateSettings?.skipped_version;
        if (st.release.version !== skipped && app._updateNotifiedFor !== st.release.version) {
          app._updateNotifiedFor = st.release.version;
          // Subtle notification — the user explicitly chooses to view or defer.
          Components.toast(
            I18N.t("update.notify_title", "N13 Update Available"),
            I18N.fmt("update.notify_body", { version: st.release.version }),
            "info", 10000,
            { label: I18N.t("update.view_update", "View Update"), onClick: () => app._showUpdateDialog(st.release) },
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
  /**
   * Human label for a runtime auto-shutdown state ("Off", "Waiting", …).
   * Shared by the queue strip and the toasts so both say the same thing.
   */
  autoShutdownStateLabel(state) {
    return I18N.t(`auto_shutdown.state.${state}`, state || "");
  },

  /** Human label for a structured auto-shutdown reason ("" when none). */
  autoShutdownReasonLabel(reason) {
    if (!reason) return "";
    return I18N.t(`auto_shutdown.reason.${reason}`, reason);
  },

  /**
   * Auto-shutdown runtime state from the backend controller.
   *
   * The backend owns the state machine; the UI renders what it is told and
   * never infers the runtime situation from `shutdown_when_done`.  The
   * preference is only mirrored so the Settings toggle stays in sync.
   *
   * Toasts fire on *transitions* only, so a repeated evaluation of the same
   * state cannot spam the user.
   */
  applyAutoShutdown(app, status) {
    if (!status) return;
    const prev = app.state.autoShutdown;
    const was = prev ? prev.state : null;
    const now = status.state;

    app.state.autoShutdown = status;
    app.state.settings = { ...(app.state.settings || {}), shutdown_when_done: !!status.enabled };
    app._renderQueueStrip();
    Events.syncShutdownCountdown(app);

    if (now === was) return;

    const reason = Events.autoShutdownReasonLabel(status.cancel_reason || status.reason);
    if (now === "Countdown") {
      Components.toast(
        I18N.t("toast.shutdown_firing", "Shutting down"),
        I18N.fmt("toast.shutdown_firing_msg", { n: status.countdown_seconds || 0 },
          "All downloads finished — the computer shuts down in 60 seconds"),
        "warning", 12000);
    } else if (was === "Countdown") {
      // The countdown was aborted (a new download, a resume, the user, …).
      Components.toast(
        I18N.t("toast.shutdown_cancelled", "Auto shutdown cancelled"),
        reason || I18N.t("auto_shutdown.reason.policy_not_eligible", "Downloads are still unfinished"),
        "info", 6000);
    } else if (now === "Blocked") {
      Components.toast(
        I18N.t("toast.shutdown_blocked", "Auto shutdown blocked"),
        reason || I18N.t("auto_shutdown.reason.policy_not_eligible", "Downloads are still unfinished"),
        "error", 8000);
    } else if (now === "Error") {
      Components.toast(
        I18N.t("toast.shutdown_error", "Auto shutdown error"),
        reason || status.last_error || "",
        "error", 10000);
    } else if (now === "Executed") {
      Components.toast(
        I18N.t("toast.shutdown_executed", "Shutting down now"),
        reason || "",
        "warning", 8000);
    }
  },

  /**
   * Apply the backend's queue-gate state.
   *
   * The gate is *runtime* state, so the UI renders what the backend reports and
   * never infers it from the last button the user pressed.  It is pushed on
   * every change (and seeded at boot), which is what keeps a paused queue from
   * looking like a stalled application.  See docs/QUEUE.md §1.
   */
  applyQueueStatus(app, status) {
    if (!status) return;
    app.state.queuePaused = !!status.paused;
    app.state.schedulerGate = !!status.scheduler_gate;
    app._renderQueueStrip();
    // The Queue page renders the same gate, so let it repaint if it is open.
    if (typeof Queue !== "undefined" && Queue) Queue.render();
  },

  /**
   * Drive the countdown banner.  The deadline comes from the backend (epoch
   * seconds) and is ticked locally, so the readout stays accurate without
   * polling the backend once per second.
   */
  syncShutdownCountdown(app) {
    const banner = Utils.$id("qsShutdownBanner");
    const s = app.state.autoShutdown;
    const active = !!s && s.state === "Countdown";
    if (banner) banner.hidden = !active;
    if (!active) {
      if (app._shutdownTicker) {
        clearInterval(app._shutdownTicker);
        app._shutdownTicker = null;
      }
      return;
    }
    Events.tickShutdownCountdown(app);
    if (!app._shutdownTicker) {
      app._shutdownTicker = setInterval(() => Events.tickShutdownCountdown(app), 1000);
    }
  },

  tickShutdownCountdown(app) {
    const s = app.state.autoShutdown;
    if (!s || s.state !== "Countdown" || !s.deadline) return;
    const left = Math.max(0, Math.ceil(s.deadline - Date.now() / 1000));
    const count = Utils.$id("qsShutdownCount");
    const msg = Utils.$id("qsShutdownMsg");
    if (count) count.textContent = I18N.fmt("queue.shutdown_remaining", { n: left }, `${left}s`);
    if (msg) msg.textContent = I18N.fmt("queue.shutdown_soon", { n: left },
      `The computer shuts down in ${left} seconds`);
  },

  async onClipboardLink(app, url) {
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
          onClick: () => app.openNewDownload(url),
        });
    } catch (e) {
      API.logJs("clipboard link: " + String(e));
    }
  },
};
