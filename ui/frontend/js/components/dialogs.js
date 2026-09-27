/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Components: Dialogs
   ═══════════════════════════════════════════════════════════════════════════

   General-purpose dialogs built on the shared modal shell:
   confirm, link prompt, duplicate-conflict prompt, rename, per-download
   speed limit, scheduler, priority picker, properties.

   Extracted verbatim from components.js (Phase 3).  Every dialog opens via
   `ModalUI.showModal` and keeps its original public name on the
   `Components` facade.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, ModalUI, Components */

const DialogsUI = {
  async confirm({ title, message, okText = I18N.t("confirm.ok", "Confirm"), cancelText = I18N.t("confirm.cancel", "Cancel"), danger = false, icon = null }) {
    return new Promise((resolve) => {
      const dlg = ModalUI.showModal(`
        <div class="confirm-body">
          <span class="confirm-ico ${danger ? "danger" : ""}">${Utils.icon(icon || (danger ? "alert" : "info"), 22)}</span>
          <p class="confirm-msg">${Utils.escapeHtml(message)}</p>
        </div>`, { title, width: 420, onClose: () => resolve(false) });

      dlg.setFooter(`
        <button class="btn btn-ghost" id="cfCancel">${Utils.escapeHtml(cancelText)}</button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" id="cfOk">${Utils.escapeHtml(okText)}</button>`);
      dlg.qs("#cfCancel").addEventListener("click", () => { resolve(false); dlg.close(); });
      dlg.qs("#cfOk").addEventListener("click", () => { resolve(true); dlg.close(); });
      dlg.qs("#cfOk").focus();
    });
  },

  async linkPrompt(url) {
    return new Promise((resolve) => {
      const dlg = ModalUI.showModal(`
        <div class="confirm-body">
          <span class="confirm-ico accent">${Utils.icon("download", 22)}</span>
          <p class="confirm-msg">${I18N.t("dlg.link_detected", "Download link detected")}</p>
          <p class="confirm-sub mono" style="word-break:break-all">${Utils.escapeHtml(url)}</p>
        </div>`, { title: I18N.t("dlg.clipboard", "Clipboard"), width: 460, onClose: () => resolve(false) });

      dlg.setFooter(`
        <button class="btn btn-ghost" id="cpIgnore">${Utils.icon("x", 14)} ${I18N.t("act.ignore", "Ignore")}</button>
        <button class="btn btn-primary" id="cpDownload">${Utils.icon("download", 14)} ${I18N.t("dlg.download_btn", "Download")}</button>`);
      dlg.qs("#cpIgnore").addEventListener("click", () => { resolve(false); dlg.close(); });
      dlg.qs("#cpDownload").addEventListener("click", () => { resolve(true); dlg.close(); });
      dlg.qs("#cpDownload").focus();
    });
  },

  async conflictPrompt({ reason, filePath, name }) {
    const isActive = reason === "same_url";
    const lines = [];
    if (isActive) lines.push(I18N.t("conflict.already_in_queue", "This URL is already in your download queue."));
    else {
      if (reason === "in_history" || reason === "file_exists")
        lines.push(I18N.t("conflict.already_exists", "This download already exists on your system."));
    }
    if (filePath) lines.push(filePath);
    return new Promise((resolve) => {
      const body = `
        <div class="confirm-body">
          <span class="confirm-ico warning">${Utils.icon("alert", 22)}</span>
          <p class="confirm-msg">${I18N.t("conflict.download_exists", "Download already exists")}</p>
          ${lines.length ? `<p class="confirm-sub mono" style="word-break:break-all">${Utils.escapeHtml(lines.join("\n"))}</p>` : ""}
        </div>`;
      const dlg = ModalUI.showModal(body, { title: isActive ? I18N.t("conflict.already_downloading", "Already downloading") : I18N.t("conflict.duplicate_detected", "Duplicate detected"), width: 480, onClose: () => resolve("cancel") });

      const btns = [];
      if (isActive) {
        btns.push(`<button class="btn btn-ghost" id="cfOpenTask">${Utils.icon("folder", 14)} ${I18N.t("conflict.open_existing_task", "Open existing task")}</button>`);
        btns.push(`<button class="btn btn-primary" id="cfAnyway">${Utils.icon("download", 14)} ${I18N.t("conflict.download_anyway", "Download anyway")}</button>`);
        btns.push(`<button class="btn btn-ghost" id="cfCancel2">${I18N.t("confirm.cancel", "Cancel")}</button>`);
      } else {
        if (filePath) btns.push(`<button class="btn btn-ghost" id="cfOpen">${Utils.icon("external", 14)} ${I18N.t("conflict.open_existing", "Open existing")}</button>`);
        btns.push(`<button class="btn btn-ghost" id="cfRename">${Utils.icon("retry", 14)} ${I18N.t("conflict.rename_auto", "Rename automatically")}</button>`);
        btns.push(`<button class="btn btn-ghost" id="cfReplace">${Utils.icon("trash", 14)} ${I18N.t("conflict.replace_existing", "Replace existing")}</button>`);
        btns.push(`<button class="btn btn-primary" id="cfAgain">${Utils.icon("download", 14)} ${I18N.t("conflict.download_again", "Download again")}</button>`);
        btns.push(`<button class="btn btn-ghost" id="cfCancel3">${I18N.t("confirm.cancel", "Cancel")}</button>`);
      }
      dlg.setFooter(btns.join(""));
      const wire = (id, val) => dlg.qs(id)?.addEventListener("click", () => { resolve(val); dlg.close(); });
      if (isActive) {
        wire("#cfOpenTask", "open_task");
        wire("#cfAnyway", "again");
        wire("#cfCancel2", "cancel");
      } else {
        wire("#cfOpen", "open");
        wire("#cfRename", "rename");
        wire("#cfReplace", "replace");
        wire("#cfAgain", "again");
        wire("#cfCancel3", "cancel");
      }
    });
  },

  async renameDialog(currentName, { onDuplicate = null } = {}) {
    const L = (k, f) => I18N.t(k, f);
    const dot = String(currentName || "").lastIndexOf(".");
    const base = dot > 0 ? currentName.slice(0, dot) : (currentName || "");
    const ext = dot > 0 ? currentName.slice(dot) : "";

    return new Promise((resolve) => {
      const dlg = ModalUI.showModal(`
        <div class="field">
          <label class="field-label" for="rnName">${L("dlg.new_name", "New name")}</label>
          <div class="rename-row">
            <input class="input" id="rnName" value="${Utils.escapeHtml(base)}" spellcheck="false" autocomplete="off">
            ${ext ? `<span class="rename-ext mono">${Utils.escapeHtml(ext)}</span>` : ""}
          </div>
          <p class="field-hint">${L("dlg.rename_hint", "The extension is kept so the file still opens correctly.")}</p>
        </div>`,
        { title: L("act.rename", "Rename"), width: 470, onClose: () => resolve(null) });

      dlg.setFooter(`
        <button class="btn btn-ghost" id="rnCancel">${L("confirm.cancel", "Cancel")}</button>
        <button class="btn btn-primary" id="rnOk">${Utils.icon("check", 15)} ${L("dlg.rename_btn", "Rename")}</button>`);

      const input = dlg.qs("#rnName");
      // Select the stem so typing immediately replaces it.
      setTimeout(() => { input.focus(); input.select(); }, 60);

      const submit = () => {
        const v = input.value.trim();
        if (!v) { input.focus(); return; }
        resolve(v + ext);
        dlg.close();
      };
      dlg.qs("#rnOk").addEventListener("click", submit);
      dlg.qs("#rnCancel").addEventListener("click", () => { resolve(null); dlg.close(); });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); submit(); }
      });
    });
  },

  async speedLimitDialog(currentBps, fileName = "") {
    const L = (k, f) => I18N.t(k, f);
    const presets = [0, 262144, 524288, 1048576, 2097152, 5242880, 10485760];
    const toMbps = (bps) => (bps > 0 ? +(bps / 1048576).toFixed(2) : 0);
    const current = currentBps || 0;

    return new Promise((resolve) => {
      const dlg = ModalUI.showModal(`
        ${fileName ? `<p class="confirm-sub mono" style="margin-top:0">${Utils.escapeHtml(fileName)}</p>` : ""}
        <div class="field">
          <label class="field-label" for="slValue">${L("dlg.speed_limit", "Per-download speed limit")}</label>
          <div class="input-join">
            <input class="input input-num" id="slValue" type="number" min="0" step="0.1" value="${toMbps(current)}">
            <span class="unit-tag">MB/s</span>
          </div>
          <p class="field-hint">${L("dlg.speed_limit_hint", "0 means unlimited. Applies on top of the global limit.")}</p>
        </div>
        <div class="preset-row" id="slPresets">
          ${presets.map((p) => `<button class="chip${p === current ? " active" : ""}" data-bps="${p}">${p === 0 ? L("dlg.unlimited", "Unlimited") : toMbps(p) + " MB/s"}</button>`).join("")}
        </div>`,
        { title: L("act.speed_limit", "Speed limit"), width: 480, onClose: () => resolve(null) });

      dlg.setFooter(`
        <button class="btn btn-ghost" id="slCancel">${L("confirm.cancel", "Cancel")}</button>
        <button class="btn btn-primary" id="slOk">${Utils.icon("check", 15)} ${L("confirm.apply", "Apply")}</button>`);

      const input = dlg.qs("#slValue");
      Utils.$qa(".chip", dlg.el).forEach((chip) => {
        chip.addEventListener("click", () => {
          Utils.$qa(".chip", dlg.el).forEach((c) => c.classList.remove("active"));
          chip.classList.add("active");
          input.value = toMbps(+chip.dataset.bps) || 0;
        });
      });

      const submit = () => {
        const mb = Math.max(0, Number(input.value) || 0);
        resolve(Math.round(mb * 1048576));
        dlg.close();
      };
      dlg.qs("#slOk").addEventListener("click", submit);
      dlg.qs("#slCancel").addEventListener("click", () => { resolve(null); dlg.close(); });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
    });
  },

  /**
   * Quick scheduler editor driven from the queue strip.
   *
   * Deliberately mirrors `speedLimitDialog` — same modal shell, width, close
   * button and Cancel/Apply footer — so the two queue-strip controls behave
   * identically.  Resolves to `{enabled, start, stop, days}`, or `null` when
   * the user cancels / closes.
   */
  async schedulerDialog(current = {}) {
    const L = (k, f) => I18N.t(k, f);
    const days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
    const selected = Array.isArray(current.schedule_days) ? current.schedule_days : [];

    return new Promise((resolve) => {
      const dlg = ModalUI.showModal(`
        <div class="sched-body">
          <div class="sched-row">
            <div class="field-info">
              <label class="field-label" for="scEnabled">${L("sched.enable", "Enable scheduler")}</label>
              <p class="field-hint">${L("sched.enable_hint", "Only start downloads inside the window below.")}</p>
            </div>
            <span class="switch">
              <input type="checkbox" id="scEnabled" ${current.scheduler_enabled ? "checked" : ""}>
              <span class="switch-track"></span>
            </span>
          </div>
          <div class="field">
            <label class="field-label">${L("sched.window", "Active window")}</label>
            <div class="sched-window">
              <input class="input mono" type="time" id="scStart" value="${Utils.escapeHtml(current.schedule_start_time || "01:00")}">
              <span class="sched-dash">–</span>
              <input class="input mono" type="time" id="scStop" value="${Utils.escapeHtml(current.schedule_stop_time || "")}">
            </div>
            <p class="field-hint">${L("sched.window_hint", "Downloads run between these times. Leave the end empty for no end time.")}</p>
          </div>
          <div class="field">
            <label class="field-label">${L("sched.days", "Days")}</label>
            <div class="sched-days" id="scDays">
              ${days.map((d) => `<button type="button" class="chip${selected.includes(d) ? " active" : ""}" data-day="${d}">${L("day." + d, d)}</button>`).join("")}
            </div>
            <p class="field-hint">${L("sched.days_hint", "No selection means every day.")}</p>
          </div>
        </div>`,
        { title: L("sched.title", "Scheduler"), width: 480, onClose: () => resolve(null) });

      dlg.setFooter(`
        <button class="btn btn-ghost" id="scCancel">${L("confirm.cancel", "Cancel")}</button>
        <button class="btn btn-primary" id="scOk">${Utils.icon("check", 15)} ${L("confirm.apply", "Apply")}</button>`);

      const enabledInp = dlg.qs("#scEnabled");
      const startInp = dlg.qs("#scStart");
      const stopInp = dlg.qs("#scStop");
      const chips = Utils.$qa(".chip", dlg.qs("#scDays"));

      chips.forEach((chip) => chip.addEventListener("click", () => chip.classList.toggle("active")));

      // Grey out the window while the scheduler is off, so it is obvious that
      // those values are not in effect yet.
      const syncEnabled = () => {
        const off = !enabledInp.checked;
        [startInp, stopInp].forEach((i) => { i.disabled = off; });
        chips.forEach((c) => { c.disabled = off; });
        dlg.qs("#scDays").classList.toggle("off", off);
      };
      enabledInp.addEventListener("change", syncEnabled);
      syncEnabled();

      const submit = () => {
        resolve({
          enabled: enabledInp.checked,
          start: startInp.value || "",
          stop: stopInp.value || "",
          days: chips.filter((c) => c.classList.contains("active")).map((c) => c.dataset.day),
        });
        dlg.close();
      };
      dlg.qs("#scOk").addEventListener("click", submit);
      dlg.qs("#scCancel").addEventListener("click", () => { resolve(null); dlg.close(); });
      [startInp, stopInp].forEach((i) =>
        i.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }));
    });
  },

  /**
   * Priority picker.
   *
   * The manager stores ``0`` = highest … ``10`` = lowest, which is the exact
   * opposite of what most people read into the word "priority".  The dialog
   * therefore speaks in High / Normal / Low and only converts on the way out,
   * with an exact 0–10 field for anyone who wants the raw value.
   */
  async priorityDialog(currentPriority, fileName = "") {
    const L = (k, f) => I18N.t(k, f);
    const raw = Number(currentPriority);
    const cur = Math.max(0, Math.min(10, Number.isFinite(raw) ? raw : 5));
    const PRESETS = [
      { v: 1, key: "dlg.pri_high", fb: "High" },
      { v: 5, key: "dlg.pri_normal", fb: "Normal" },
      { v: 9, key: "dlg.pri_low", fb: "Low" },
    ];

    return new Promise((resolve) => {
      const dlg = ModalUI.showModal(`
        ${fileName ? `<p class="confirm-sub mono" style="margin-top:0">${Utils.escapeHtml(fileName)}</p>` : ""}
        <div class="pri-body">
          <div class="field">
            <span class="field-label">${L("dlg.priority", "Download priority")}</span>
            <div class="preset-row" id="prPresets">
              ${PRESETS.map((p) => `<button class="chip${p.v === cur ? " active" : ""}" data-pri="${p.v}">${L(p.key, p.fb)}</button>`).join("")}
            </div>
            <p class="field-hint">${L("dlg.priority_hint", "When a queue slot frees up, higher priority downloads start first.")}</p>
          </div>
          <div class="field">
            <label class="field-label" for="prValue">${L("dlg.priority_exact", "Exact level")}</label>
            <div class="input-join">
              <input class="input input-num" id="prValue" type="number" min="0" max="10" step="1" value="${cur}">
              <span class="unit-tag">0–10</span>
            </div>
          </div>
        </div>`,
        { title: L("act.priority", "Priority"), width: 460, onClose: () => resolve(null) });

      dlg.setFooter(`
        <button class="btn btn-ghost" id="prCancel">${L("confirm.cancel", "Cancel")}</button>
        <button class="btn btn-primary" id="prOk">${Utils.icon("check", 15)} ${L("confirm.apply", "Apply")}</button>`);

      const input = dlg.qs("#prValue");
      const syncChips = () => {
        const v = +input.value;
        Utils.$qa(".chip", dlg.el).forEach((c) => c.classList.toggle("active", +c.dataset.pri === v));
      };
      Utils.$qa(".chip", dlg.el).forEach((chip) => {
        chip.addEventListener("click", () => { input.value = chip.dataset.pri; syncChips(); });
      });
      input.addEventListener("input", syncChips);

      const submit = () => {
        const v = Math.max(0, Math.min(10, Math.round(Number(input.value) || 0)));
        resolve(v);
        dlg.close();
      };
      dlg.qs("#prOk").addEventListener("click", submit);
      dlg.qs("#prCancel").addEventListener("click", () => { resolve(null); dlg.close(); });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
    });
  },

  propertiesDialog(tasks) {
    const L = (k, f) => I18N.t(k, f);
    if (!tasks || !tasks.length) return;

    if (tasks.length > 1) {
      const states = {};
      let total = 0, done = 0;
      tasks.forEach((t) => {
        states[t.state] = (states[t.state] || 0) + 1;
        total += t.total || 0;
        done += t.completed || 0;
      });
      const body = `
        <div class="prop-sec">${L("props.selection", "Selection")}</div>
        <div class="prop-list">
          ${this._propRow(L("props.count", "Downloads"), tasks.length)}
          ${this._propRow(L("props.total_size", "Total size"), Utils.formatSize(total))}
          ${this._propRow(L("props.downloaded", "Downloaded"), `${Utils.formatSize(done)} (${total > 0 ? ((done / total) * 100).toFixed(1) : "0"}%)`)}
        </div>
        <div class="prop-sec">${L("props.by_status", "By status")}</div>
        <div class="prop-list">
          ${Object.entries(states).map(([s, n]) => this._propRow(Utils.statusLabel(s), n)).join("")}
        </div>`;
      const dlg = ModalUI.showModal(body, { title: L("act.properties", "Properties"), width: 520 });
      dlg.setFooter(`<button class="btn btn-primary" id="ppClose">${L("dlg.close", "Close")}</button>`);
      dlg.qs("#ppClose").addEventListener("click", () => dlg.close());
      return;
    }

    const t = tasks[0];
    const name = Utils.fileName(t);
    const path = t.directory ? `${t.directory}\\${t.filename || name}` : "—";
    const etaTxt = (t.state === "Downloading" && t.eta_seconds != null && isFinite(t.eta_seconds))
      ? Utils.formatDuration(t.eta_seconds) : "—";
    const body = `
      <div class="prop-sec">${L("props.file", "File")}</div>
      <div class="prop-list">
        ${this._propRow(L("props.name", "Name"), Utils.escapeHtml(name), true)}
        ${this._propRow(L("props.category", "Category"), Utils.escapeHtml(t.category || "General"), true)}
        ${this._propRow(L("props.path", "Location"), Utils.escapeHtml(path), true, "mono")}
        ${this._propRow(L("props.url", "Source URL"), `<span class="mono">${Utils.escapeHtml(t.url || "")}</span>`, true)}
        ${this._propRow(L("props.checksum", "Checksum"), t.checksum ? Utils.escapeHtml(t.checksum) : "—", true)}
      </div>
      <div class="prop-sec">${L("props.transfer", "Transfer")}</div>
      <div class="prop-list">
        ${this._propRow(L("props.status", "Status"), DownloadRowUI._badge(t), true)}
        ${this._propRow(L("props.size", "Size"), `${Utils.formatSize(t.completed || 0)} / ${t.total > 0 ? Utils.formatSize(t.total) : "—"}`)}
        ${this._propRow(L("col.speed", "Speed"), t.state === "Downloading" ? Utils.formatSpeed(t.speed_bps) : "—")}
        ${this._propRow(L("props.avg_speed", "Average speed"), t.average_speed > 0 ? Utils.formatSpeed(t.average_speed) : "—")}
        ${this._propRow(L("col.eta", "ETA"), etaTxt)}
        ${this._propRow(L("col.connections", "Connections"), `${t.connections || 1}${t.smart_status ? ` · ${I18N.fmt("fmt.smart_connections", { status: t.smart_status })}` : ""}`)}
        ${this._propRow(L("props.priority", "Priority"), `${t.priority ?? 5}`)}
        ${this._propRow(L("dlg.speed_limit", "Speed limit"), t.speed_limit_bps > 0 ? Utils.formatSpeed(t.speed_limit_bps) : L("dlg.unlimited", "Unlimited"))}
        ${this._propRow(L("props.retries", "Retries"), `${t.retry_count || 0}`)}
      </div>
      <div class="prop-sec">${L("props.server", "Server")}</div>
      <div class="prop-list">
        ${this._propRow(L("props.server_name", "Server"), Utils.escapeHtml(t.server || "—"))}
        ${this._propRow(L("props.content_type", "Content type"), Utils.escapeHtml(t.content_type || "—"))}
        ${this._propRow(L("props.resumable", "Resumable"), t.supports_range ? L("props.yes", "Yes") : L("props.no", "No"))}
      </div>
      ${t.error ? `<div class="prop-sec">${L("props.error", "Last error")}</div>
        <div class="prop-list">${this._propRow(L("props.error", "Error"), Utils.escapeHtml(t.error))}</div>` : ""}`;

    const dlg = ModalUI.showModal(body, { title: L("act.properties", "Properties"), width: 620 });
    dlg.setFooter(`
      <button class="btn btn-ghost" id="ppCopyPath">${Utils.icon("copy", 14)} ${L("props.copy_path", "Copy path")}</button>
      <button class="btn btn-primary" id="ppClose">${L("dlg.close", "Close")}</button>`);
    dlg.qs("#ppClose").addEventListener("click", () => dlg.close());
    dlg.qs("#ppCopyPath").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(path);
        Components.toast(L("toast.copied", "Copied"), L("toast.copied_path", "File path copied to clipboard"), "info", 2000);
      } catch { /* clipboard unavailable */ }
    });
  },

  _propRow(key, valueHtml, isHtml = false, extraCls = "") {
    return `<div class="prop-row"><span class="prop-key">${Utils.escapeHtml(key)}</span>
      <span class="prop-val ${extraCls}">${isHtml ? valueHtml : Utils.escapeHtml(String(valueHtml))}</span></div>`;
  },
};
