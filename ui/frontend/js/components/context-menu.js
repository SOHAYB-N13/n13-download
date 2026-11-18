/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Component: Context menu
   ═══════════════════════════════════════════════════════════════════════════

   Owns the shared context menu (#contextMenu): rendering, viewport-aware
   positioning, item wiring and dismissal.  Outside-click / blur / resize
   dismissal is wired in core/shortcuts.js; Escape handling likewise.

   `rowMenu` and `selectionActions` are pure builders for the Downloads
   feature's menus — they take a task plus the feature's callback object and
   never touch App internals themselves.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N */

const ContextMenuUI = {
  showContextMenu(items, x, y) {
    const menu = Utils.$id("contextMenu");
    if (!menu) return;
    this.hideContextMenu();

    menu.innerHTML = items.map((item, i) => {
      if (item.separator) return '<div class="ctx-sep" role="separator"></div>';
      return `<button class="ctx-item${item.danger ? " danger" : ""}" data-i="${i}" role="menuitem">
        ${Utils.icon(item.icon || "chevronRight", 15)}
        <span>${Utils.escapeHtml(item.label)}</span>
        ${item.hint ? `<kbd>${Utils.escapeHtml(item.hint)}</kbd>` : ""}
      </button>`;
    }).join("");

    menu.classList.add("open");
    // Measure the menu we just rendered rather than guessing its size.
    //
    // The previous estimate (`items.length * 34 + 12`) treated every separator
    // as a full 34px row — a separator is 1px with 5px margins — so a long menu
    // was thought to be up to ~60px taller than it is and got pinned that much
    // higher whenever the user right-clicked near the bottom edge.  It also
    // assumed a fixed 224px width, which a long (or translated) label can
    // exceed.
    //
    // `offsetWidth`/`offsetHeight`, not `getBoundingClientRect()`: the menu is
    // still running its open transition, and `getBoundingClientRect()` would
    // report the *scaled* box (`transform: scale(0.96)`) instead of the layout
    // size we want to fit on screen.
    const w = menu.offsetWidth || 224;
    const h = menu.offsetHeight || items.length * 34 + 12;
    const px = Math.min(x, window.innerWidth - w - 8);
    const py = Math.min(y, window.innerHeight - h - 8);
    menu.style.left = Math.max(8, px) + "px";
    menu.style.top = Math.max(8, py) + "px";
    menu.style.transformOrigin = (x > window.innerWidth / 2 ? "right " : "left ") +
      (y > window.innerHeight / 2 ? "bottom" : "top");

    Utils.$qa(".ctx-item", menu).forEach((btn) => {
      btn.addEventListener("click", () => {
        const item = items[+btn.dataset.i];
        this.hideContextMenu();
        if (item && item.action) item.action();
      });
    });
  },

  hideContextMenu() {
    const menu = Utils.$id("contextMenu");
    if (menu) menu.classList.remove("open");
  },

  rowMenu(task, cb) {
    const s = task.state;
    const name = Utils.fileName(task);
    const L = (k, f) => (typeof I18N !== "undefined" ? I18N.t(k, f) : f);
    // Action targets follow the File Explorer rule: if the right-clicked task
    // is part of the current selection, act on the whole selection; otherwise
    // act on just the right-clicked task.
    const targets = (cb && typeof cb.targetsFor === "function") ? cb.targetsFor(task.id) : [task.id];
    // The clicked task decides *which* actions are offered, but the selection
    // decides *how many* tasks each action will touch.  In a mixed selection
    // those differ: a "Pause (2)" that only pauses one row is a lie.  Filter
    // per action and label the real count.  Fall back to the clicked task's
    // state when no lookup callback is supplied.
    const taskFor = (cb && typeof cb.taskFor === "function")
      ? cb.taskFor
      : (id) => (id === task.id ? task : null);
    const eligible = (states) => targets.filter((id) => {
      const t = taskFor(id);
      return t && states.includes(t.state);
    });
    const cntFor = (list) => (list.length > 1 ? ` (${list.length})` : "");
    const multi = targets.length > 1;
    const items = [];
    const active = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    const pauseIds = eligible(["Downloading"]);
    const resumeIds = eligible(["Paused"]);
    const startIds = eligible(["Queued"]);
    const cancelIds = eligible([...active, "Paused", "Queued"]);
    const retryIds = eligible(["Failed", "Cancelled", "Stopped"]);
    if (pauseIds.length) items.push({ label: L("act.pause", "Pause") + cntFor(pauseIds), icon: "pause", action: () => cb.onPause(pauseIds) });
    if (resumeIds.length) items.push({ label: L("act.resume", "Resume") + cntFor(resumeIds), icon: "play", action: () => cb.onResume(resumeIds) });
    if (startIds.length) items.push({ label: L("act.start_now", "Start now") + cntFor(startIds), icon: "play", action: () => cb.onStart(startIds) });
    if (cancelIds.length) items.push({ label: L("act.cancel", "Cancel") + cntFor(cancelIds), icon: "xCircle", action: () => cb.onCancel(cancelIds) });
    if (retryIds.length) items.push({ label: L("act.retry", "Retry") + cntFor(retryIds), icon: "retry", action: () => cb.onRetry(retryIds) });
    if (items.length)     items.push({ separator: true });
    items.push({ label: L("act.copy_url", "Copy URL") + cntFor(targets), icon: "copy", action: () => cb.onCopyUrl(targets) });
    if (s === "Complete") {
      items.push({ label: L("act.open_file", "Open file"), icon: "external", action: () => cb.onOpenFile(task.id) });
      items.push({ label: L("act.open_folder", "Open folder"), icon: "folderOpen", action: () => cb.onOpenFolder(task.id) });
      items.push({ label: L("act.copy_path", "Copy path"), icon: "copy", action: () => cb.onCopyPath(task) });
      items.push({ label: L("act.redownload", "Redownload"), icon: "retry", action: () => cb.onRedownload(task.id) });
      items.push({ label: L("act.delete_file", "Delete file"), icon: "trash", danger: true, action: () => cb.onDeleteFile(task.id, name) });
    }
    // Rename / per-download cap only make sense for a single task.
    if (!multi) {
      items.push({ separator: true });
      items.push({ label: L("act.rename", "Rename"), icon: "edit", action: () => cb.onRename(task.id) });
      items.push({ label: L("act.speed_limit", "Speed limit"), icon: "gauge", action: () => cb.onSpeedLimit(task.id) });
      items.push({ label: L("act.priority", "Priority"), icon: "flag", action: () => cb.onPriority(task.id) });
      items.push({ label: L("act.properties", "Properties"), icon: "info", action: () => cb.onProperties(task.id) });
    }
    // Move up/down only reorder tasks that are still waiting to start.
    if (startIds.length) {
      items.push({ separator: true });
      items.push({ label: L("act.move_up", "Move up") + cntFor(startIds), icon: "arrowUp", action: () => cb.onReorder(startIds, -1) });
      items.push({ label: L("act.move_down", "Move down") + cntFor(startIds), icon: "arrowDown", action: () => cb.onReorder(startIds, 1) });
    }
    items.push({ separator: true });
    items.push({ label: L("act.remove", "Remove from list") + cntFor(targets), icon: "x", danger: true, action: () => cb.onRemove(targets) });
    return items;
  },

  /**
   * Build the action list for the current selection.
   *
   * Returns only actions that are meaningful for the selected states, so the
   * action bar stays short and never shows a dead button.  Each entry is
   * ``{id, label, icon, kind, run}`` where ``kind`` drives the styling
   * (``"primary"`` | ``"danger"`` | ``""``).
   */
  selectionActions(tasks, cb) {
    const L = (k, f) => (typeof I18N !== "undefined" ? I18N.t(k, f) : f);
    const ids = tasks.map((t) => t.id);
    const single = tasks.length === 1 ? tasks[0] : null;
    const firstComplete = tasks.find((t) => t.state === "Complete") || null;
    const n = tasks.length;
    const out = [];

    const ACTIVE = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    // Each action targets only the rows it can actually affect. A mixed
    // selection must never offer "Cancel (3)" when only two rows are live.
    const eligible = (states) => tasks.filter((t) => states.includes(t.state)).map((t) => t.id);
    const count = (list) => (list.length > 1 && list.length < n ? ` (${list.length})` : "");
    const startIds = eligible(["Queued"]);
    const pauseIds = eligible(["Downloading"]);
    const resumeIds = eligible(["Paused"]);
    const retryIds = eligible(["Failed", "Cancelled", "Stopped"]);
    const cancelIds = eligible([...ACTIVE, "Paused", "Queued"]);

    if (startIds.length)
      out.push({ id: "start", label: L("act.start_now", "Start") + count(startIds), icon: "play", kind: "primary", run: () => cb.onStart(startIds) });
    if (pauseIds.length)
      out.push({ id: "pause", label: L("act.pause", "Pause") + count(pauseIds), icon: "pause", kind: "", run: () => cb.onPause(pauseIds) });
    if (resumeIds.length)
      out.push({ id: "resume", label: L("act.resume", "Resume") + count(resumeIds), icon: "play", kind: "primary", run: () => cb.onResume(resumeIds) });
    if (retryIds.length)
      out.push({ id: "retry", label: L("act.retry", "Retry") + count(retryIds), icon: "retry", kind: "primary", run: () => cb.onRetry(retryIds) });
    if (cancelIds.length)
      out.push({ id: "cancel", label: L("act.cancel", "Cancel") + count(cancelIds), icon: "xCircle", kind: "", run: () => cb.onCancel(cancelIds) });

    if (out.length) out.push({ separator: true });

    if (firstComplete)
      out.push({ id: "openfile", label: L("act.open_file", "Open file"), icon: "external", kind: "", run: () => cb.onOpenFile(firstComplete.id) });
    const folderTarget = firstComplete || tasks[0];
    if (folderTarget)
      out.push({ id: "folder", label: L("act.open_folder", "Open folder"), icon: "folderOpen", kind: "", run: () => cb.onOpenFolder(folderTarget.id) });
    out.push({ id: "copy", label: L("act.copy_url", "Copy link"), icon: "copy", kind: "", run: () => cb.onCopyUrl(ids) });
    if (single)
      out.push({ id: "rename", label: L("act.rename", "Rename"), icon: "edit", kind: "", run: () => cb.onRename(single.id) });
    if (single)
      out.push({ id: "speed", label: L("act.speed_limit", "Speed limit"), icon: "gauge", kind: "", run: () => cb.onSpeedLimit(single.id) });

    // Queue controls.  Reordering is only meaningful for tasks that are still
    // waiting, so Move up/down is offered only when *every* selected task is
    // queued — otherwise the buttons would appear to do nothing.
    if (tasks.length && tasks.every((t) => t.state === "Queued")) {
      out.push({ id: "moveup", label: L("act.move_up", "Move up"), icon: "arrowUp", kind: "", run: () => cb.onReorder(ids, -1) });
      out.push({ id: "movedown", label: L("act.move_down", "Move down"), icon: "arrowDown", kind: "", run: () => cb.onReorder(ids, 1) });
    }
    // Priority is a persisted property, so it stays available in any state.
    out.push({ id: "priority", label: L("act.priority", "Priority"), icon: "flag", kind: "", run: () => cb.onPriority(single ? single.id : ids) });

    out.push({ id: "props", label: L("act.properties", "Properties"), icon: "info", kind: "", run: () => cb.onProperties(single ? single.id : ids) });

    out.push({ separator: true });
    out.push({ id: "remove", label: n > 1 ? L("act.remove_many", "Remove") : L("act.remove", "Remove"), icon: "trash", kind: "danger", run: () => cb.onRemove(ids) });
    return out;
  },
};
