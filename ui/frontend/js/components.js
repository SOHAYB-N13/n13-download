/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — UI Components
   ═══════════════════════════════════════════════════════════════════════════ */

const Components = {

  // ── Toast ──────────────────────────────────────────────────────────

  toast(title, message = "", type = "info", duration = 4200, action = null) {
    const stack = Utils.$id("toastStack");
    if (!stack) return;
    // Cap the stack — drop the oldest when flooded.
    while (stack.children.length >= 4) stack.firstElementChild.remove();

    const icons = { success: "check", error: "xCircle", warning: "alert", info: "info" };
    const el = document.createElement("div");
    el.className = `toast toast-${type}`;
    el.setAttribute("role", "status");
    el.innerHTML = `
      <span class="toast-bar"></span>
      <span class="toast-ico">${Utils.icon(icons[type] || "info", 18)}</span>
      <div class="toast-body">
        <div class="toast-title">${Utils.escapeHtml(title)}</div>
        ${message ? `<div class="toast-msg">${Utils.escapeHtml(message)}</div>` : ""}
        ${action && action.label ? `<button class="toast-action">${Utils.escapeHtml(action.label)}</button>` : ""}
      </div>
      <button class="toast-close icon-btn" aria-label="${I18N.t("toast.dismiss", "Dismiss notification")}">${Utils.icon("x", 14)}</button>
      <span class="toast-life" style="animation-duration:${duration}ms"></span>`;

    const kill = () => {
      if (el.classList.contains("out")) return;
      el.classList.add("out");
      setTimeout(() => el.remove(), 260);
    };
    el.querySelector(".toast-close").addEventListener("click", kill);
    const actionBtn = el.querySelector(".toast-action");
    if (actionBtn && action && typeof action.onClick === "function") {
      actionBtn.addEventListener("click", () => { kill(); action.onClick(); });
    }
    el.addEventListener("click", (e) => { if (!e.target.closest("button")) kill(); });
    stack.appendChild(el);
    setTimeout(kill, duration);
  },

  // ── Modal ─────────────────────────────────────────────────────────

  _modalStack: [],

  showModal(contentHtml, options = {}) {
    const overlay = Utils.$id("modalOverlay");
    const modal = Utils.$id("modal");
    const { title = "", subtitle = "", width = 520, onClose = null } = options;

    modal.style.maxWidth = width + "px";
    modal.innerHTML = `
      <header class="modal-head">
        <div class="modal-headings">
          <h2 class="modal-title">${Utils.escapeHtml(title)}</h2>
          ${subtitle ? `<p class="modal-sub">${Utils.escapeHtml(subtitle)}</p>` : ""}
        </div>
        <button class="icon-btn modal-x" id="modalClose" aria-label="${I18N.t("dlg.close", "Close dialog")}">${Utils.icon("x", 16)}</button>
      </header>
      <div class="modal-body">${contentHtml}</div>
      <footer class="modal-foot" id="modalFoot"></footer>`;

    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");

    const api = {
      el: modal,
      setFooter(html) { Utils.$id("modalFoot").innerHTML = html; },
      qs(sel) { return modal.querySelector(sel); },
      close(result) {
        overlay.classList.remove("open");
        overlay.setAttribute("aria-hidden", "true");
        document.removeEventListener("keydown", onKey, true);
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        if (onClose) onClose(result);
      },
    };

    const prevFocus = document.activeElement;
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); api.close(); }
      if (e.key === "Tab") {
        // Lightweight focus trap.
        const focusables = Utils.$qa('button, input, select, textarea, [tabindex]:not([tabindex="-1"])', modal)
          .filter((n) => !n.disabled && n.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey, true);

    Utils.$id("modalClose").addEventListener("click", () => api.close());
    overlay.onmousedown = (e) => { if (e.target === overlay) api.close(); };

    const firstInput = modal.querySelector("input:not([type=hidden]), textarea, select");
    if (firstInput) setTimeout(() => firstInput.focus(), 60);

    return api;
  },

  closeModal() {
    const overlay = Utils.$id("modalOverlay");
    if (overlay) overlay.classList.remove("open");
  },

  async confirm({ title, message, okText = I18N.t("confirm.ok", "Confirm"), cancelText = I18N.t("confirm.cancel", "Cancel"), danger = false, icon = null }) {
    return new Promise((resolve) => {
      const dlg = this.showModal(`
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

  // ── Clipboard link prompt ─────────────────────────────────────────

  async linkPrompt(url) {
    return new Promise((resolve) => {
      const dlg = this.showModal(`
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

  // ── Duplicate download conflict prompt ────────────────────────────

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
      const dlg = this.showModal(body, { title: isActive ? I18N.t("conflict.already_downloading", "Already downloading") : I18N.t("conflict.duplicate_detected", "Duplicate detected"), width: 480, onClose: () => resolve("cancel") });

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

  // ── Rename ────────────────────────────────────────────────────────

  async renameDialog(currentName, { onDuplicate = null } = {}) {
    const L = (k, f) => I18N.t(k, f);
    const dot = String(currentName || "").lastIndexOf(".");
    const base = dot > 0 ? currentName.slice(0, dot) : (currentName || "");
    const ext = dot > 0 ? currentName.slice(dot) : "";

    return new Promise((resolve) => {
      const dlg = this.showModal(`
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

  // ── Per-download speed limit ──────────────────────────────────────

  async speedLimitDialog(currentBps, fileName = "") {
    const L = (k, f) => I18N.t(k, f);
    const presets = [0, 262144, 524288, 1048576, 2097152, 5242880, 10485760];
    const toMbps = (bps) => (bps > 0 ? +(bps / 1048576).toFixed(2) : 0);
    const current = currentBps || 0;

    return new Promise((resolve) => {
      const dlg = this.showModal(`
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
      const dlg = this.showModal(`
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

  // ── Properties / details ──────────────────────────────────────────
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
      const dlg = this.showModal(body, { title: L("act.properties", "Properties"), width: 520 });
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
        ${this._propRow(L("props.category", "Category"), Utils.escapeHtml(t.category || "General"))}
        ${this._propRow(L("props.path", "Location"), Utils.escapeHtml(path), true, "mono")}
        ${this._propRow(L("props.url", "Source URL"), `<span class="mono">${Utils.escapeHtml(t.url || "")}</span>`, true)}
        ${this._propRow(L("props.checksum", "Checksum"), t.checksum ? Utils.escapeHtml(t.checksum) : "—")}
      </div>
      <div class="prop-sec">${L("props.transfer", "Transfer")}</div>
      <div class="prop-list">
        ${this._propRow(L("props.status", "Status"), this._badge(t), true)}
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

    const dlg = this.showModal(body, { title: L("act.properties", "Properties"), width: 620 });
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

  // ── Download rule editor ──────────────────────────────────────────

  async ruleEditor(rule) {
    const fields = ["extension", "domain", "url_contains", "filename_contains", "mime", "min_size", "max_size"];
    const conds = (rule.conditions || []).length ? rule.conditions : [{ field: "extension", value: "" }];
    const L = (k, f) => I18N.t(k, f);
    const dlg = this.showModal(`
      <div class="rule-edit">
        <div class="field">
          <label class="field-label">${L("rules.name", "Rule name")}</label>
          <input class="input" id="reName" value="${Utils.escapeHtml(rule.name || "")}" spellcheck="false">
        </div>
        <div class="re-row">
          <label class="switch"><input type="checkbox" id="reEnabled" ${rule.enabled === false ? "" : "checked"}><span class="switch-track"></span></label>
          <span class="switch-text">${L("rules.enabled", "Enabled")}</span>
        </div>
        <div class="field">
          <label class="field-label">${L("rules.priority", "Priority")} <span class="field-opt">${L("rules.priority_hint", "higher wins")}</span></label>
          <input class="input input-num" type="number" id="rePriority" value="${rule.priority || 0}" min="0">
        </div>
        <div class="re-conds">
          <div class="re-conds-head">${L("rules.conditions", "Conditions")} <span class="field-opt">${L("rules.conditions_hint", "all must match (AND)")}</span></div>
          <div id="reConds">${conds.map((c) => this._condRow(c, fields)).join("")}</div>
          <button class="btn btn-ghost btn-sm" id="reAddCond">${Utils.icon("plus", 13)} ${L("rules.add_condition", "Add condition")}</button>
        </div>
        <div class="re-actions">
          <div class="re-actions-head">${L("rules.actions", "Actions")}</div>
          <div class="re-grid">
            <div class="field">
              <label class="field-label">${L("rules.category", "Category")}</label>
              <input class="input" id="reCategory" value="${Utils.escapeHtml(rule.category || "")}" placeholder="${L("category.Videos", "Videos")}" spellcheck="false">
            </div>
            <div class="field">
              <label class="field-label">${L("rules.folder", "Download folder")}</label>
              <input class="input mono" id="reFolder" value="${Utils.escapeHtml(rule.folder || "")}" placeholder="D:\\Downloads\\Videos" spellcheck="false">
            </div>
            <div class="field">
              <label class="field-label">${L("rules.task_priority", "Task priority")}</label>
              <input class="input input-num" type="number" id="reTaskPriority" value="${rule.priority_value ?? 5}" min="0" max="10">
            </div>
            <div class="field">
              <label class="field-label">${L("rules.connection_mode", "Connection mode")}</label>
              <select class="input" id="reConn">
                <option value="" ${!rule.connection_mode ? "selected" : ""}>${L("setopt.inherit", "Inherit")}</option>
                <option value="smart" ${rule.connection_mode === "smart" ? "selected" : ""}>${L("setopt.smart", "Smart")}</option>
                <option value="manual" ${rule.connection_mode === "manual" ? "selected" : ""}>${L("setopt.manual", "Manual")}</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">${L("rules.manual_connections", "Manual connections")}</label>
              <input class="input input-num" type="number" id="reThreads" value="${rule.manual_connections || 4}" min="1" max="64">
            </div>
          </div>
        </div>
      </div>`, { title: rule.id ? L("rules.edit_rule", "Edit rule") : L("rules.new_rule", "New rule"), width: 620 });

    dlg.setFooter(`
      <button class="btn btn-ghost" id="reCancel">${L("rules.cancel", "Cancel")}</button>
      <button class="btn btn-primary" id="reSave">${Utils.icon("check", 15)} ${L("rules.save_rule", "Save rule")}</button>`);

    const qs = (id) => dlg.qs("#" + id);
    qs("reAddCond").addEventListener("click", () => {
      qs("reConds").insertAdjacentHTML("beforeend", this._condRow({ field: "extension", value: "" }, fields));
      this._wireCondRows(dlg.el);
    });
    this._wireCondRows(dlg.el);

    return new Promise((resolve) => {
      qs("reCancel").addEventListener("click", () => { resolve(null); dlg.close(); });
      qs("reSave").addEventListener("click", () => {
        const conditions = Utils.$qa(".re-cond-row", dlg.el).map((row) => ({
          field: row.querySelector("select").value,
          value: row.querySelector("input").value.trim(),
        })).filter((c) => c.value);
        resolve({
          name: qs("reName").value.trim() || I18N.t("rules.untitled", "Untitled rule"),
          enabled: qs("reEnabled").checked,
          priority: +qs("rePriority").value || 0,
          conditions,
          category: qs("reCategory").value.trim(),
          folder: qs("reFolder").value.trim(),
          priority_value: Math.max(0, Math.min(10, +qs("reTaskPriority").value || 5)),
          connection_mode: qs("reConn").value,
          manual_connections: Math.max(1, Math.min(64, +qs("reThreads").value || 4)),
        });
        dlg.close();
      });
    });
  },

  _condRow(cond, fields) {
    return `
      <div class="re-cond-row">
        <select class="input">${fields.map((f) => `<option value="${f}" ${cond.field === f ? "selected" : ""}>${I18N.t("rules.condition_fields." + f, f.replace(/_/g, " "))}</option>`).join("")}</select>
        <input class="input mono" value="${Utils.escapeHtml(cond.value || "")}" placeholder="${I18N.t("rules.value", "value")}" spellcheck="false">
        <button class="icon-btn btn-xs re-cond-del" data-tip="${I18N.t("rules.remove_condition", "Remove condition")}" aria-label="${I18N.t("rules.remove_condition", "Remove condition")}">${Utils.icon("x", 13)}</button>
      </div>`;
  },

  _wireCondRows(root) {
    Utils.$qa(".re-cond-row .re-cond-del", root).forEach((b) => {
      b.addEventListener("click", () => b.closest(".re-cond-row").remove());
    });
  },

  // ── Context menu ──────────────────────────────────────────────────

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
    // Position after render so dimensions are known.
    const rect = { w: 224, h: items.length * 34 + 12 };
    const px = Math.min(x, window.innerWidth - rect.w - 8);
    const py = Math.min(y, window.innerHeight - rect.h - 8);
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

  // ── Download row ──────────────────────────────────────────────────

  rowMenu(task, cb) {
    const s = task.state;
    const name = Utils.fileName(task);
    const L = (k, f) => (typeof I18N !== "undefined" ? I18N.t(k, f) : f);
    // Action targets follow the File Explorer rule: if the right-clicked task
    // is part of the current selection, act on the whole selection; otherwise
    // act on just the right-clicked task.
    const targets = (cb && typeof cb.targetsFor === "function") ? cb.targetsFor(task.id) : [task.id];
    const multi = targets.length > 1;
    const cnt = multi ? ` (${targets.length})` : "";
    const items = [];
    const active = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];
    if (s === "Downloading") items.push({ label: L("act.pause", "Pause") + cnt, icon: "pause", action: () => cb.onPause(targets) });
    if (s === "Paused") items.push({ label: L("act.resume", "Resume") + cnt, icon: "play", action: () => cb.onResume(targets) });
    if (s === "Queued") items.push({ label: L("act.start_now", "Start now") + cnt, icon: "play", action: () => cb.onStart(targets) });
    if (active.includes(s) || s === "Paused" || s === "Queued")
      items.push({ label: L("act.cancel", "Cancel") + cnt, icon: "xCircle", action: () => cb.onCancel(targets) });
    if (s === "Failed" || s === "Cancelled" || s === "Stopped")
      items.push({ label: L("act.retry", "Retry") + cnt, icon: "retry", action: () => cb.onRetry(targets) });
    if (items.length)     items.push({ separator: true });
    items.push({ label: L("act.copy_url", "Copy URL") + cnt, icon: "copy", action: () => cb.onCopyUrl(targets) });
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
    if (s === "Queued") {
      items.push({ separator: true });
      items.push({ label: L("act.move_up", "Move up") + cnt, icon: "arrowUp", action: () => cb.onReorder(targets, -1) });
      items.push({ label: L("act.move_down", "Move down") + cnt, icon: "arrowDown", action: () => cb.onReorder(targets, 1) });
    }
    items.push({ separator: true });
    items.push({ label: L("act.remove", "Remove from list") + cnt, icon: "x", danger: true, action: () => cb.onRemove(targets) });
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
    const states = tasks.map((t) => t.state);
    const any = (list) => states.some((s) => list.includes(s));
    const ids = tasks.map((t) => t.id);
    const single = tasks.length === 1 ? tasks[0] : null;
    const firstComplete = tasks.find((t) => t.state === "Complete") || null;
    const n = tasks.length;
    const out = [];

    const ACTIVE = ["Downloading", "Analyzing", "Starting", "Merging", "Verifying"];

    if (any(["Queued"]))
      out.push({ id: "start", label: L("act.start_now", "Start"), icon: "play", kind: "primary", run: () => cb.onStart(ids) });
    if (any(["Downloading"]))
      out.push({ id: "pause", label: L("act.pause", "Pause"), icon: "pause", kind: "", run: () => cb.onPause(ids) });
    if (any(["Paused"]))
      out.push({ id: "resume", label: L("act.resume", "Resume"), icon: "play", kind: "primary", run: () => cb.onResume(ids) });
    if (any(["Failed", "Cancelled", "Stopped"]))
      out.push({ id: "retry", label: L("act.retry", "Retry"), icon: "retry", kind: "primary", run: () => cb.onRetry(ids) });
    if (any(ACTIVE) || any(["Paused", "Queued"]))
      out.push({ id: "cancel", label: L("act.cancel", "Cancel"), icon: "xCircle", kind: "", run: () => cb.onCancel(ids) });

    if (out.length) out.push({ separator: true });

    if (firstComplete)
      out.push({ id: "openfile", label: L("act.open_file", "Open file"), icon: "external", kind: "", run: () => cb.onOpenFile(firstComplete.id) });
    out.push({ id: "folder", label: L("act.open_folder", "Open folder"), icon: "folderOpen", kind: "", run: () => cb.onOpenFolder(ids[0]) });
    out.push({ id: "copy", label: L("act.copy_url", "Copy link"), icon: "copy", kind: "", run: () => cb.onCopyUrl(ids) });
    if (single)
      out.push({ id: "rename", label: L("act.rename", "Rename"), icon: "edit", kind: "", run: () => cb.onRename(single.id) });
    if (single)
      out.push({ id: "speed", label: L("act.speed_limit", "Speed limit"), icon: "gauge", kind: "", run: () => cb.onSpeedLimit(single.id) });

    // Queue controls.  Reordering is only meaningful for tasks that are still
    // waiting, so Move up/down is offered only when *every* selected task is
    // queued — otherwise the buttons would appear to do nothing.
    if (states.length && states.every((s) => s === "Queued")) {
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
        this.showContextMenu(this.rowMenu(task, cb), r.right - 190, r.bottom + 6);
      }
    });
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      this.showContextMenu(this.rowMenu(task, cb), e.clientX, e.clientY);
    });
    row.addEventListener("keydown", (e) => {
      if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
        e.preventDefault();
        const r = row.getBoundingClientRect();
        this.showContextMenu(this.rowMenu(task, cb), r.left + 60, r.bottom);
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

  // ── Empty states & skeletons ──────────────────────────────────────

  emptyState({ icon = "download", title, desc = "", actionLabel = "", onAction = null, actions = null }) {
    const wrap = document.createElement("div");
    wrap.className = "empty";

    // `actions` (preferred) renders several one-click entry points so a brand
    // new user is never staring at a dead end.  `actionLabel`/`onAction` stay
    // supported for the simpler single-button callers.
    const list = actions && actions.length
      ? actions
      : (actionLabel ? [{ label: actionLabel, icon: "plus", primary: true, onClick: onAction }] : []);

    wrap.innerHTML = `
      <span class="empty-ico">${Utils.icon(icon, 30)}</span>
      <h3 class="empty-title">${Utils.escapeHtml(title)}</h3>
      ${desc ? `<p class="empty-desc">${Utils.escapeHtml(desc)}</p>` : ""}
      ${list.length ? `<div class="empty-actions">${list.map((a, i) => `
        <button class="btn ${a.primary ? "btn-primary" : "btn-ghost"} empty-btn" data-ei="${i}">
          ${Utils.icon(a.icon || "plus", 15)}${Utils.escapeHtml(a.label)}
        </button>`).join("")}</div>` : ""}`;

    Utils.$qa(".empty-btn", wrap).forEach((btn) => {
      const a = list[+btn.dataset.ei];
      if (a && typeof a.onClick === "function") btn.addEventListener("click", a.onClick);
    });
    return wrap;
  },

  skeletonRows(n = 4) {
    let html = "";
    for (let i = 0; i < n; i++) {
      html += `<div class="dl-row sk-row" aria-hidden="true">
        <div class="dl-ico"><span class="sk sk-box"></span></div>
        <div class="dl-main"><span class="sk sk-line w60"></span><span class="sk sk-line w35"></span></div>
        <div class="dl-progress"><span class="sk sk-bar"></span></div>
        <div class="dl-cell"><span class="sk sk-line w70"></span></div>
        <div class="dl-cell"><span class="sk sk-line w50"></span></div>
        <div class="dl-status"><span class="sk sk-pill"></span></div>
        <div class="dl-actions"><span class="sk sk-dot3"></span></div>
      </div>`;
    }
    return html;
  },

  // ── Count-up animation ────────────────────────────────────────────

  countUp(el, to, { duration = 700, format = (v) => Math.round(v).toString() } = {}) {
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.textContent = format(to);
      return;
    }
    const from = 0;
    const start = performance.now();
    const tick = (now) => {
      const t = Utils.clamp((now - start) / duration, 0, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(from + (to - from) * eased);
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  },

  // ── Sparkline ─────────────────────────────────────────────────────

  sparkline(canvas, { points = 48, stroke = "var(--accent)", fill = true } = {}) {
    const data = new Array(points).fill(0);
    let peak = 1;
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const step = w / (points - 1);
      const maxV = Math.max(peak, 1);
      const xy = data.map((v, i) => [i * step, h - 3 - (v / maxV) * (h - 8)]);

      const style = getComputedStyle(canvas);
      const lineColor = stroke.startsWith("var") ? style.getPropertyValue(stroke.slice(4, -1).trim()) || "#3B82F6" : stroke;

      ctx.beginPath();
      ctx.moveTo(xy[0][0], xy[0][1]);
      for (let i = 1; i < xy.length - 1; i++) {
        const xc = (xy[i][0] + xy[i + 1][0]) / 2;
        const yc = (xy[i][1] + xy[i + 1][1]) / 2;
        ctx.quadraticCurveTo(xy[i][0], xy[i][1], xc, yc);
      }
      ctx.lineTo(xy[xy.length - 1][0], xy[xy.length - 1][1]);

      if (fill) {
        const grad = ctx.createLinearGradient(0, 0, 0, h);
        grad.addColorStop(0, lineColor + "33");
        grad.addColorStop(1, lineColor + "00");
        ctx.save();
        ctx.lineTo(w, h);
        ctx.lineTo(0, h);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.restore();
        // Redraw the stroke on top of the fill.
        ctx.beginPath();
        ctx.moveTo(xy[0][0], xy[0][1]);
        for (let i = 1; i < xy.length - 1; i++) {
          const xc = (xy[i][0] + xy[i + 1][0]) / 2;
          const yc = (xy[i][1] + xy[i + 1][1]) / 2;
          ctx.quadraticCurveTo(xy[i][0], xy[i][1], xc, yc);
        }
        ctx.lineTo(xy[xy.length - 1][0], xy[xy.length - 1][1]);
      }
      ctx.strokeStyle = lineColor;
      ctx.lineWidth = 1.8;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();
    };
    return {
      push(v) {
        data.push(v);
        data.shift();
        peak = Math.max(...data) * 1.15;
        draw();
      },
      redraw: draw,
    };
  },

  // ── Ripple ────────────────────────────────────────────────────────

  initRipple() {
    document.addEventListener("pointerdown", (e) => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      const host = e.target.closest(".btn, .chip, .nav-item, .icon-btn, .tab-btn, .seg-btn, .ctx-item");
      if (!host || host.disabled) return;
      const rect = host.getBoundingClientRect();
      const ripple = document.createElement("span");
      const size = Math.max(rect.width, rect.height) * 2.1;
      ripple.className = "ripple";
      ripple.style.width = ripple.style.height = size + "px";
      ripple.style.left = (e.clientX - rect.left - size / 2) + "px";
      ripple.style.top = (e.clientY - rect.top - size / 2) + "px";
      host.appendChild(ripple);
      setTimeout(() => ripple.remove(), 650);
    }, { passive: true });
  },
};

const C = Components;
