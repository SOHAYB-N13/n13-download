/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Component: Download rule editor
   ═══════════════════════════════════════════════════════════════════════════

   The Settings → Rules editor dialog.  Built on the shared modal shell.

   Extracted verbatim from components.js (Phase 3).
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, ModalUI */

const RuleEditorUI = {
  async ruleEditor(rule) {
    const fields = ["extension", "domain", "url_contains", "filename_contains", "mime", "min_size", "max_size"];
    const conds = (rule.conditions || []).length ? rule.conditions : [{ field: "extension", value: "" }];
    const L = (k, f) => I18N.t(k, f);
    const dlg = ModalUI.showModal(`
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
};
