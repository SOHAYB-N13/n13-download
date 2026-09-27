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
        } else if (app.state.sortKey === "queue" && prev && prev.queue_index !== t.queue_index) {
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
