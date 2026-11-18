/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Scheduled downloads panel

   Reads the authoritative backend rows. Countdowns are derived from a stored
   UTC timestamp, never from a JS timeout, so closing the page cannot change
   when a download fires.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, Components, API */

const ScheduledPanel = {
  _timer: null,
  _seq: 0,

  mount() {
    if (this._timer) return;
    // Countdown text needs a slow refresh; the schedule itself is backend owned.
    this._timer = setInterval(() => {
      if (App.state.page === "downloads") this.render(App);
    }, 10000);
  },

  async render(app) {
    const root = Utils.$id("scheduledPanel");
    if (!root) return;
    let rows = [];
    try { rows = (await API.listScheduled()) || []; } catch { rows = []; }
    // A slower earlier fetch must never repaint over a newer state.
    const seq = ++this._seq;
    if (seq !== this._seq || !root.isConnected) return;
    const open = rows.filter((r) => ["scheduled", "paused", "starting", "missed", "failed"].includes(r.status));
    if (!open.length) {
      root.hidden = true;
      root.replaceChildren();
      return;
    }
    root.hidden = false;
    const t = (k, f) => I18N.t(k, f);
    const now = Date.now() / 1000;
    const missed = open.filter((r) => r.status === "missed").length;

    root.innerHTML = `
      <div class="sched-head">
        <strong>${Utils.escapeHtml(t("sched.task.title", "Scheduled downloads"))}</strong>
        <span class="sched-count">${open.length}</span>
        ${missed ? `<span class="sched-missed">${missed} ${Utils.escapeHtml(t("sched.task.missed", "missed while closed"))}</span>` : ""}
        <span class="sched-note">${Utils.escapeHtml(t("sched.task.note", "Times are shown in your local timezone."))}</span>
      </div>
      <div class="sched-rows">
        ${open.map((r) => this._row(r, now, t)).join("")}
      </div>`;

    root.querySelectorAll("[data-sched-act]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.schedId;
        const action = btn.dataset.schedAct;
        btn.disabled = true;
        try {
          if (action === "cancel") await API.cancelScheduled(id);
          else if (action === "start") await API.startScheduledNow(id);
          else if (action === "pause") await API.pauseScheduled(id);
          else if (action === "resume") await API.resumeScheduled(id);
          else if (action === "reschedule") await this._reschedule(id, t);
        } finally {
          await this.render(app);
          if (typeof app._refreshHistory === "function") app._refreshHistory();
        }
      });
    });
  },

  _row(row, now, t) {
    const payload = row.payload || {};
    const name = payload.label || (payload.url || "").split("/").pop() || row.id;
    const statusLabel = t("sched.task.status." + row.status, row.status);
    let when = row.local_time || "";
    if (row.status === "scheduled" || row.status === "paused") {
      const remaining = Math.ceil(row.due_at - now);
      if (remaining > 0) {
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;
        when += ` · ${mins}m ${String(secs).padStart(2, "0")}s`;
      } else {
        when += ` · ${t("sched.task.due", "due")}`;
      }
    }
    const canStart = ["scheduled", "paused", "missed", "failed"].includes(row.status);
    const canCancel = ["scheduled", "paused", "missed", "failed"].includes(row.status);
    return `<div class="sched-row">
      <div class="sched-main">
        <span class="sched-name" title="${Utils.escapeHtml(payload.url || "")}">${Utils.escapeHtml(name)}</span>
        <span class="sched-when">${Utils.escapeHtml(when)}</span>
        ${row.last_error ? `<span class="sched-err">${Utils.escapeHtml(row.last_error)}</span>` : ""}
      </div>
      <span class="sched-state s-${Utils.escapeHtml(row.status)}">${Utils.escapeHtml(statusLabel)}</span>
      <div class="sched-ops">
        ${canStart ? `<button class="btn btn-ghost btn-xs" data-sched-act="start" data-sched-id="${Utils.escapeHtml(row.id)}">${Utils.escapeHtml(t("act.start_now", "Start now"))}</button>` : ""}
        ${canCancel ? `<button class="btn btn-ghost btn-xs" data-sched-act="reschedule" data-sched-id="${Utils.escapeHtml(row.id)}">${Utils.escapeHtml(t("act.reschedule", "Reschedule"))}</button>` : ""}
        ${row.status === "scheduled" || row.status === "paused"
          ? `<button class="btn btn-ghost btn-xs" data-sched-act="${row.status === "scheduled" ? "pause" : "resume"}" data-sched-id="${Utils.escapeHtml(row.id)}">${Utils.escapeHtml(t(row.status === "scheduled" ? "act.pause" : "act.resume", row.status === "scheduled" ? "Pause" : "Resume"))}</button>`
          : ""}
        ${canCancel ? `<button class="btn btn-ghost btn-xs danger" data-sched-act="cancel" data-sched-id="${Utils.escapeHtml(row.id)}">${Utils.escapeHtml(t("act.cancel", "Cancel"))}</button>` : ""}
      </div>
    </div>`;
  },

  async _reschedule(id, t) {
    const row = (await API.listScheduled()).find((r) => r.id === id);
    if (!row) return;
    const preset = new Date(row.due_at * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    const dateVal = `${preset.getFullYear()}-${pad(preset.getMonth() + 1)}-${pad(preset.getDate())}`;
    const timeVal = `${pad(preset.getHours())}:${pad(preset.getMinutes())}`;
    let settled = false;
    const dlg = Components.showModal(`
      <div class="sched-edit">
        <p>${Utils.escapeHtml(t("sched.task.reschedule_hint", "Choose a new local date and time."))}</p>
        <input class="input" type="date" id="rsDate" value="${dateVal}">
        <input class="input" type="time" id="rsTime" value="${timeVal}">
        <p class="sched-edit-err" id="rsErr" hidden></p>
      </div>`, {
      title: t("act.reschedule", "Reschedule"),
      width: 420,
      onClose: () => { settled = true; },
    });
    dlg.setFooter(`
      <button class="btn btn-ghost" id="rsCancel">${Utils.escapeHtml(t("groups.cancel", "Cancel"))}</button>
      <button class="btn btn-primary" id="rsGo">${Utils.escapeHtml(t("act.reschedule", "Reschedule"))}</button>`);
    dlg.qs("#rsCancel").addEventListener("click", () => dlg.close());
    dlg.qs("#rsGo").addEventListener("click", async () => {
      const local = `${dlg.qs("#rsDate").value}T${dlg.qs("#rsTime").value}`;
      const res = await API.rescheduleScheduled(id, local, "");
      if (!res || !res.ok) {
        const err = dlg.qs("#rsErr");
        err.textContent = (res && res.error === "past_time")
          ? t("batch.scan.past_time", "Scheduled time must be in the future.")
          : t("sched.task.invalid", "Could not reschedule this download.");
        err.hidden = false;
        return;
      }
      settled = true;
      dlg.close();
    });
  },
};
