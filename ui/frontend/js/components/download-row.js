/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Component: Download row renderer
   ═══════════════════════════════════════════════════════════════════════════

   Pure view layer for a Downloads-list row: render, in-place update, and
   the row's internal DOM event wiring.  All actions are delegated through
   the caller's callback object (`cb`) — this module never touches App or
   the backend directly.  Row "more" / contextmenu events open the shared
   context menu built from `ContextMenuUI.rowMenu`.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, ContextMenuUI */

const DownloadRowUI = {
  _rowActions(task) {
    const s = task.state;
    const active = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    let html = "";
    const L = (k, f) => I18N.t(k, f);
    if (s === "Downloading")
      html += `<button class="icon-btn" data-act="pause" data-tip="${L("act.pause", "Pause")}" aria-label="${L("act.pause", "Pause download")}">${Utils.icon("pause", 15)}</button>`;
    if (s === "Paused")
      html += `<button class="icon-btn accent" data-act="resume" data-tip="${L("act.resume", "Resume")}" aria-label="${L("act.resume", "Resume download")}">${Utils.icon("play", 15)}</button>`;
    if (s === "Queued")
      html += `<button class="icon-btn accent" data-act="start" data-tip="${L("act.start_now", "Start now")}" aria-label="${L("act.start_now", "Start download now")}">${Utils.icon("play", 15)}</button>`;
    if (s === "Failed" || s === "Cancelled" || s === "Stopped")
      html += `<button class="icon-btn accent" data-act="retry" data-tip="${L("act.retry", "Retry")}" aria-label="${L("act.retry", "Retry download")}">${Utils.icon("retry", 15)}</button>`;
    if (active.includes(s) || s === "Paused" || s === "Queued")
      html += `<button class="icon-btn" data-act="cancel" data-tip="${L("act.cancel", "Cancel")}" aria-label="${L("act.cancel", "Cancel download")}">${Utils.icon("x", 15)}</button>`;
    if (s === "Complete")
      html += `<button class="icon-btn" data-act="openfile" data-tip="${L("act.open_file", "Open file")}" aria-label="${L("act.open_file", "Open file")}">${Utils.icon("external", 15)}</button>`;
    if (s === "Complete")
      html += `<button class="icon-btn" data-act="folder" data-tip="${L("act.open_folder", "Open folder")}" aria-label="${L("act.open_folder", "Open containing folder")}">${Utils.icon("folderOpen", 15)}</button>`;
    html += `<button class="icon-btn" data-act="more" data-tip="${L("act.more", "More actions")}" aria-label="${L("act.more", "More actions")}">${Utils.icon("more", 15)}</button>`;
    return html;
  },

  _badge(task) {
    const cls = Utils.statusClass(task.state);
    const lbl = Utils.statusLabel(task.state);
    return `<span class="badge badge-${cls}"><i class="badge-dot"></i>${lbl}</span>`;
  },

  _etaText(task) {
    if (task.state === "Complete") return "00:00";
    if (task.state !== "Downloading") return "--:--";
    const eta = task.eta_seconds;
    if (eta == null || !isFinite(eta) || eta < 0) return "--:--";
    return Utils.formatDuration(eta);
  },

  /** Live connection count for a row ("—" when the task is not transferring). */
  _connText(task) {
    const live = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    if (!live.includes(task.state)) return "—";
    const n = task.connections;
    if (!n || n < 1) return "—";
    return String(n);
  },

  _connHtml(task) {
    const live = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    const isLive = live.includes(task.state);
    const txt = this._connText(task);
    const icon = Utils.icon("bolt", 13);
    return `<div class="dl-conn${isLive && txt !== "—" ? " live" : ""}" title="${I18N.t("col.connections", "Connections")}">${icon}<span>${txt}</span></div>`;
  },

  /** Small badge shown when a per-download bandwidth cap is configured. */
  _capHtml(task) {
    const bps = task.speed_limit_bps || 0;
    if (bps <= 0) return "";
    return `<span class="dl-cap" title="${I18N.t("dlg.speed_limit", "Per-download speed limit")}">${Utils.formatSpeed(bps)}</span>`;
  },

  renderRow(task, cb, elapsedMs = 0) {
    const name = Utils.fileName(task);
    const pct = task.total > 0 ? Utils.clamp((task.completed / task.total) * 100, 0, 100) : 0;
    const stCls = Utils.statusClass(task.state);
    const row = document.createElement("div");
    row.className = "dl-row row-enter";
    row.dataset.id = task.id;
    row.dataset.state = task.state;
    row.setAttribute("role", "listitem");
    row.tabIndex = 0;

    const done = Utils.formatSize(task.completed);
    const total = task.total > 0 ? Utils.formatSize(task.total) : "—";
    const remaining = task.total > 0 ? Utils.formatSize(Math.max(0, task.total - task.completed)) : "—";
    const pctText = task.total > 0 ? pct.toFixed(0) + "%" : "—";

    row.innerHTML = `
      <div class="dl-ico" data-type="${Utils.fileType(name)}">${Utils.fileIcon(name, 19)}</div>
      <div class="dl-main">
        <div class="dl-name" title="${Utils.escapeHtml(name)}">${Utils.escapeHtml(name)}</div>
        <div class="dl-sub" title="${Utils.escapeHtml(task.url)}">${Utils.escapeHtml(Utils.hostOf(task.url))}<span class="dl-smart">${task.smart_status ? I18N.fmt("fmt.smart_connections", { status: task.smart_status }) : ""}</span></div>
      </div>
      <div class="dl-progress">
        <span class="dl-time dl-elapsed">${Utils.formatDuration(elapsedMs / 1000)}</span>
        <div class="progress" role="progressbar" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-fill p-${stCls}" style="width:${pct}%"></div>
          <span class="dl-pct">${pctText}</span>
        </div>
        <span class="dl-time dl-eta">${this._etaText(task)}</span>
      </div>
      <div class="dl-cell dl-size" title="${done} of ${total} · ${remaining} left">
        <span class="dl-cell-main">${done}<span class="dl-cell-dim"> / ${total}</span></span>
      </div>
      <div class="dl-cell dl-speed">${task.state === "Downloading" ? Utils.formatSpeed(task.speed_bps) : "—"}${this._capHtml(task)}</div>
      ${this._connHtml(task)}
      <div class="dl-status">${this._badge(task)}${task.error ? `<span class="dl-err" title="${Utils.escapeHtml(task.error)}">${Utils.icon("info", 13)}</span>` : ""}</div>
      <div class="dl-actions">${this._rowActions(task)}</div>`;

    this._wireRow(row, task, cb);
    return row;
  },

  _wireRow(row, task, cb) {
    row.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) {
        // Plain row click (not a control): hand it to the selection handler.
        if (cb && typeof cb.onRowSelect === "function") cb.onRowSelect(task.id, e);
        return;
      }
      const act = btn.dataset.act;
      if (act === "pause") cb.onPause(task.id);
      else if (act === "resume") cb.onResume(task.id);
      else if (act === "start") cb.onStart(task.id);
      else if (act === "cancel") cb.onCancel(task.id);
      else if (act === "retry") cb.onRetry(task.id);
      else if (act === "openfile") cb.onOpenFile(task.id);
      else if (act === "folder") cb.onOpenFolder(task.id);
      else if (act === "more") {
        // Stop the click from bubbling to the document handler, which would
        // immediately close the just-opened context menu.
        e.stopPropagation();
        const r = btn.getBoundingClientRect();
        ContextMenuUI.showContextMenu(ContextMenuUI.rowMenu(task, cb), r.right - 190, r.bottom + 6);
      }
    });
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      ContextMenuUI.showContextMenu(ContextMenuUI.rowMenu(task, cb), e.clientX, e.clientY);
    });
    row.addEventListener("keydown", (e) => {
      if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
        e.preventDefault();
        const r = row.getBoundingClientRect();
        ContextMenuUI.showContextMenu(ContextMenuUI.rowMenu(task, cb), r.left + 60, r.bottom);
      }
    });
  },

  updateRow(row, task, elapsedMs = 0) {
    const pct = task.total > 0 ? Utils.clamp((task.completed / task.total) * 100, 0, 100) : 0;
    const stCls = Utils.statusClass(task.state);
    const stateChanged = row.dataset.state !== task.state;
    row.dataset.state = task.state;

    const fill = row.querySelector(".progress-fill");
    if (fill) {
      fill.style.width = pct + "%";
      if (stateChanged) fill.className = `progress-fill p-${stCls}`;
    }
    const bar = row.querySelector(".progress");
    if (bar) bar.setAttribute("aria-valuenow", pct.toFixed(0));

    const pctEl = row.querySelector(".dl-pct");
    if (pctEl) pctEl.textContent = task.total > 0 ? pct.toFixed(0) + "%" : "—";

    const elapsedEl = row.querySelector(".dl-elapsed");
    if (elapsedEl) elapsedEl.textContent = Utils.formatDuration(elapsedMs / 1000);
    const etaEl = row.querySelector(".dl-eta");
    if (etaEl) etaEl.textContent = this._etaText(task);

    const sizeEl = row.querySelector(".dl-size .dl-cell-main");
    if (sizeEl) {
      const total = task.total > 0 ? Utils.formatSize(task.total) : "—";
      sizeEl.innerHTML = `${Utils.formatSize(task.completed)}<span class="dl-cell-dim"> / ${total}</span>`;
    }
    const speedEl = row.querySelector(".dl-speed");
    if (speedEl) {
      speedEl.innerHTML = (task.state === "Downloading" ? Utils.formatSpeed(task.speed_bps) : "—") + this._capHtml(task);
    }

    const connEl = row.querySelector(".dl-conn");
    if (connEl) {
      const live = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"].includes(task.state);
      const txt = this._connText(task);
      connEl.classList.toggle("live", live && txt !== "—");
      const span = connEl.querySelector("span");
      if (span) span.textContent = txt;
    }

    const smartEl = row.querySelector(".dl-smart");
    if (smartEl) smartEl.textContent = task.smart_status ? I18N.fmt("fmt.smart_connections", { status: task.smart_status }) : "";

    if (stateChanged) {
      const status = row.querySelector(".dl-status");
      if (status) {
        status.innerHTML = this._badge(task) +
          (task.error ? `<span class="dl-err" title="${Utils.escapeHtml(task.error)}">${Utils.icon("info", 13)}</span>` : "");
      }
      const actions = row.querySelector(".dl-actions");
      if (actions) actions.innerHTML = this._rowActions(task);
    }
  },
};
