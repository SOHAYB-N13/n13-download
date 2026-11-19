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

  /**
   * Compact "how long until it fires" text.
   *
   * The naive `Math.floor(s / 60)` produced "1439m 59s" for anything booked
   * for tomorrow, which is unreadable — a user has to divide by 60 to learn
   * it is a day away.  Hours and days are broken out instead, and the
   * smallest unit is dropped once the span gets long enough that it is noise.
   */
  _duration(secs) {
    const s = Math.max(0, Math.round(secs));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (d > 0) return h > 0 ? `${d}d ${h}h` : `${d}d`;
    if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
    return `${m}m ${String(r).padStart(2, "0")}s`;
  },

  /**
   * Local "HH:MM" (plus a day word when it is not today) for a scheduled row.
   *
   * The backend also sends a pre-formatted `local_time`, but it is built from
   * English words — a Persian interface showed "tomorrow 09:30" verbatim.
   * `due_at` is an absolute epoch, so formatting it here is both localized and
   * still exactly "your local timezone" as the panel's note promises.
   */
  _whenText(row, now, t) {
    const due = Number(row.due_at);
    if (!isFinite(due) || due <= 0) return row.local_time || "";
    const d = new Date(due * 1000);
    if (isNaN(d.getTime())) return row.local_time || "";
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const time = `${hh}:${mm}`;
    const today = new Date(now * 1000);
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
      && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    if (sameDay(d, today)) return time;
    const tomorrow = new Date(today.getTime());
    tomorrow.setDate(today.getDate() + 1);
    if (sameDay(d, tomorrow)) return `${t("sched.task.tomorrow", "tomorrow")} ${time}`;
    // Persian month names, but Latin digits like the rest of the interface.
    const locale = (typeof I18N !== "undefined" && I18N.lang === "fa") ? "fa-IR-u-nu-latn" : undefined;
    const day = d.toLocaleDateString(locale, { month: "short", day: "numeric" });
    return `${day} ${time}`;
  },

  _row(row, now, t) {
    const payload = row.payload || {};
    const name = payload.label || (payload.url || "").split("/").pop() || row.id;
    const statusLabel = t("sched.task.status." + row.status, row.status);
    const when = this._whenText(row, now, t);
    // The countdown is kept as a separate isolated run.  Concatenated into one
    // string, the RTL paragraph reordered "13:57 · 119m 59s" into
    // "119m 59s · 13:57" — the time and the remaining duration swapped places
    // depending on the digits involved.
    let countdown = "";
    const due = Number(row.due_at);
    if ((row.status === "scheduled" || row.status === "paused") && isFinite(due) && due > 0) {
      const remaining = Math.ceil(due - now);
      if (remaining > 0) {
        countdown = this._duration(remaining);
      } else {
        countdown = t("sched.task.due", "due");
      }
    }
    const canStart = ["scheduled", "paused", "missed", "failed"].includes(row.status);
    const canCancel = ["scheduled", "paused", "missed", "failed"].includes(row.status);
    return `<div class="sched-row">
      <div class="sched-main">
        <span class="sched-name" title="${Utils.escapeHtml(payload.url || "")}">${Utils.escapeHtml(name)}</span>
        <span class="sched-when"><bdi>${Utils.escapeHtml(when)}</bdi>${countdown ? ` <bdi dir="ltr">· ${Utils.escapeHtml(countdown)}</bdi>` : ""}</span>
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
