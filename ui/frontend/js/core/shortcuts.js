/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Core: Global keyboard shortcuts
   ═══════════════════════════════════════════════════════════════════════════

   Owns the *global* keyboard shortcuts only.  Feature-owned shortcuts stay
   with their features (e.g. the Queue page's Ctrl+↑/↓ lives in queue.js).

   Extracted verbatim from app.js (Phase 2) — `App._bindGlobalEvents`
   delegates here; the drag & drop link handling and the context-menu
   dismissal that were interleaved in the same binder moved to
   `bindLinkDnd` / `bindContextMenuDismissal` in this same module so app.js
   keeps one delegated call and the behavior is byte-for-byte identical.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, API, I18N, Components */

const Shortcuts = {
  /**
   * Global keyboard shortcuts:
   *   Ctrl+N        new download
   *   Ctrl+V        paste URL from clipboard into a new download
   *   Ctrl+,        settings
   *   / or Ctrl+F   focus the global search
   *   Space         pause/resume the current downloads selection
   *   Delete        remove selected tasks (Shift+Delete also deletes files)
   *   Escape        close the context menu / clear the downloads selection
   *   Ctrl+A        select all visible downloads
   */
  bind(app) {
    document.addEventListener("keydown", (e) => this.onKeydown(app, e));
  },

  onKeydown(app, e) {
    const ae = document.activeElement;
    const typing = !!(ae && (
      /^(input|textarea|select)$/i.test(ae.tagName || "") ||
      ae.isContentEditable ||
      ae.getAttribute?.("contenteditable") === "true"));
    // Never hijack a shortcut while the user is typing in a field — Ctrl+V
    // in particular must keep its normal "paste into this input" meaning.
    if (e.ctrlKey && !e.shiftKey && (e.key === "n" || e.key === "N") && !typing) {
      e.preventDefault(); app.openNewDownload();
    } else if (e.ctrlKey && !e.shiftKey && (e.key === "v" || e.key === "V") && !typing) {
      // Paste a URL from the clipboard straight into a new download.
      e.preventDefault(); app.pasteFromClipboard();
    } else if (e.ctrlKey && e.key === ",") {
      e.preventDefault(); app.navigate("settings");
    } else if ((e.key === "/" && !typing) || (e.ctrlKey && (e.key === "f" || e.key === "F"))) {
      e.preventDefault(); Utils.$id("globalSearch").focus();
    } else if (e.key === " " && !typing && app.state.page === "downloads") {
      // Space toggles pause/resume for the current selection.
      const tasks = app._selectedTasks();
      if (tasks.length) {
        e.preventDefault();
        const anyRunning = tasks.some((t) => t.state === "Downloading");
        const ids = tasks.map((t) => t.id);
        if (anyRunning) app._forEachId(ids, (id) => API.pauseDownload(id));
        else app._forEachId(ids, (id) => API.resumeDownload(id));
      }
    } else if (e.key === "Delete" && !typing && app.state.page === "downloads") {
      const tasks = app._selectedTasks();
      if (tasks.length) {
        e.preventDefault();
        // Shift+Delete also removes the file on disk; Delete only removes
        // the task, matching the confirm copy shown by onRemove.
        if (e.shiftKey) {
          const done = tasks.filter((t) => t.state === "Complete");
          if (done.length) {
            done.forEach((t) => app.rowCallbacks.onDeleteFile(t.id, t.filename || Utils.fileName(t)));
          } else {
            app.rowCallbacks.onRemove(tasks.map((t) => t.id));
          }
        } else {
          app.rowCallbacks.onRemove(tasks.map((t) => t.id));
        }
      }
    } else if (e.key === "Escape" && !typing) {
      Components.hideContextMenu();
      if (app.state.page === "downloads" && app.state.selectedIds.size) {
        app.state.selectedIds.clear();
        app.state.selAnchor = null;
        app._syncSelection();
      }
    } else if (e.ctrlKey && (e.key === "a" || e.key === "A") && !typing && app.state.page === "downloads") {
      e.preventDefault();
      const vis = app._filteredTasks();
      app.state.selectedIds.clear();
      vis.forEach((t) => app.state.selectedIds.add(t.id));
      app.state.selAnchor = vis.length ? vis[0].id : null;
      app._syncSelection();
    }
  },

  /** Hide the shared context menu on outside click / blur / resize. */
  bindContextMenuDismissal() {
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#contextMenu")) Components.hideContextMenu();
    });
    window.addEventListener("blur", () => Components.hideContextMenu());
    window.addEventListener("resize", () => Components.hideContextMenu());
  },

  /** Drag & drop a link anywhere on the window. */
  bindLinkDnd(app) {
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
      const urls = await app._extractDroppedUrls(e.dataTransfer);
      if (!urls.length) return;
      if (urls.length === 1) {
        app.openNewDownload(urls[0]);
      } else {
        // No explicit folder: the backend routes each URL to its category dir.
        const n = await API.addBatch(urls, "");
        Components.toast(I18N.t("toast.batch_queued", "Batch queued"), `${n} ${I18N.t("batch.queued", "queued")}`, "success");
        app.navigate("downloads");
      }
    });
  },

  /** Report uncaught JS errors to the backend log. */
  bindErrorReporting() {
    window.addEventListener("error", (e) => {
      API.logJs(`${e.message} @ ${e.filename}:${e.lineno}`);
    });
  },
};
