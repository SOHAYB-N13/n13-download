/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Pattern Scan results (discovery + explicit submit)

   Scanning and submission are deliberately separate. This module only ever
   renders probe results and collects a user decision; it never creates a
   download task until `onSubmit` is called from the confirmation dialog.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, Components, API */

const ScanResultsUI = {
  /**
   * @param app  the App instance (for state.projects / settings)
   * @param report  ScanOutcome.to_dict() from the backend
   * @param source  { pattern, start, padding, directory }
   * @param onSubmit  async ({ urls, directory, projectId, behavior, dueAt }) -> result
   */
  show(app, report, source, onSubmit) {
    const root = Utils.$id("patternResults");
    if (!root) return;
    const t = (key, fallback) => I18N.t(key, fallback);
    const items = Array.isArray(report.items) ? report.items : [];
    const selected = new Set(items.map((_, i) => i).filter((i) => items[i].available));
    const view = { status: "all", text: "", submitting: false };
    let renderedResult = null;

    const stopText = () => {
      const map = {
        completed: "Scan complete",
        consecutive_404: "Stopped after repeated 404s",
        attempt_limit: "Attempt limit reached",
        server_errors: "Stopped after repeated server errors",
        cancelled: "Scan cancelled",
        invalid_pattern: "Invalid pattern",
        failed: "Scan failed",
      };
      return t("batch.scan.stop." + report.stop_reason, map[report.stop_reason] || "Scan finished");
    };

    const sizeText = (item) => item.size > 0
      ? Utils.formatSize(item.size)
      : t("batch.scan.unknown_size", "unknown");

    const statusText = (item) => {
      const map = {
        available: "Available", not_found: "Not found", timeout: "Timeout",
        server_error: "Server error", error: "Failed",
      };
      return t("batch.scan.status." + item.status, map[item.status] || item.status);
    };

    const visibleIndexes = () => items.map((_, i) => i).filter((i) => {
      const item = items[i];
      if (view.status === "available" && !item.available) return false;
      if (view.status === "unavailable" && item.available) return false;
      if (view.status === "failed" && !["timeout", "server_error", "error"].includes(item.status)) return false;
      if (!view.text) return true;
      const hay = `${item.filename} ${item.url} ${item.error}`.toLowerCase();
      return hay.includes(view.text.toLowerCase());
    });

    const shell = () => {
      root.innerHTML = `
        <div class="scan-result" data-scan-root>
          <div class="scan-summary">
            <strong>${Utils.escapeHtml(stopText())}</strong>
            <span>${Utils.escapeHtml(String(report.discovered || 0))} ${Utils.escapeHtml(t("batch.scan.discovered", "discovered"))}</span>
            <span class="ok">${Utils.escapeHtml(String(report.available || 0))} ${Utils.escapeHtml(t("batch.scan.available", "available"))}</span>
            <span>${Utils.escapeHtml(String(report.unavailable || 0))} ${Utils.escapeHtml(t("batch.scan.unavailable", "unavailable"))}</span>
            <span>${Utils.escapeHtml(String(report.failed_probes || 0))} ${Utils.escapeHtml(t("batch.scan.failed_probes", "failed probes"))}</span>
            <span class="scan-selected-n" data-scan-selected></span>
          </div>
          <div class="scan-toolbar">
            <select class="input input-sm" data-scan-filter aria-label="${Utils.escapeHtml(t("batch.scan.filter", "Filter"))}">
              <option value="all">${Utils.escapeHtml(t("batch.scan.filter_all", "All results"))}</option>
              <option value="available">${Utils.escapeHtml(t("batch.scan.filter_available", "Available only"))}</option>
              <option value="unavailable">${Utils.escapeHtml(t("batch.scan.filter_unavailable", "Unavailable only"))}</option>
              <option value="failed">${Utils.escapeHtml(t("batch.scan.filter_failed", "Failed probes"))}</option>
            </select>
            <input class="input input-sm" type="search" data-scan-search placeholder="${Utils.escapeHtml(t("batch.scan.search", "Filter by name or URL"))}">
            <button class="btn btn-ghost btn-sm" data-scan-all>${Utils.escapeHtml(t("batch.scan.select_all", "Select all"))}</button>
            <button class="btn btn-ghost btn-sm" data-scan-none>${Utils.escapeHtml(t("batch.scan.select_none", "Select none"))}</button>
            <button class="btn btn-ghost btn-sm" data-scan-invert>${Utils.escapeHtml(t("batch.scan.invert", "Invert"))}</button>
            <button class="btn btn-ghost btn-sm" data-scan-cancel>${Utils.escapeHtml(t("batch.scan.cancel_scan", "Stop scan"))}</button>
          </div>
          <div class="scan-list" data-scan-list></div>
          <div class="scan-config">
            <h4>${Utils.escapeHtml(t("batch.scan.config_title", "Download configuration"))}</h4>
            <div class="scan-config-grid">
              <label class="field"><span>${Utils.escapeHtml(t("batch.scan.destination", "Destination"))}</span>
                <input class="input mono" data-scan-dir value="${Utils.escapeHtml(source.directory || "")}"></label>
              <label class="field"><span>${Utils.escapeHtml(t("batch.scan.group", "Group"))}</span>
                <select class="input" data-project-select data-scan-group></select></label>
              <label class="field"><span>${Utils.escapeHtml(t("batch.scan.filename_behavior", "Filename"))}</span>
                <select class="input" data-scan-names>
                  <option value="server">${Utils.escapeHtml(t("batch.scan.name_server", "Server filename"))}</option>
                  <option value="url">${Utils.escapeHtml(t("batch.scan.name_url", "Name from URL"))}</option>
                </select></label>
            </div>
            <div class="scan-behavior">
              <label><input type="radio" name="scanBehavior" value="queue" checked> ${Utils.escapeHtml(t("batch.scan.behavior_queue", "Add to queue"))}</label>
              <label><input type="radio" name="scanBehavior" value="now"> ${Utils.escapeHtml(t("batch.scan.behavior_now", "Download now"))}</label>
              <label><input type="radio" name="scanBehavior" value="schedule"> ${Utils.escapeHtml(t("batch.scan.behavior_schedule", "Schedule"))}</label>
            </div>
            <div class="scan-when" data-scan-when hidden>
              <input class="input" type="date" data-scan-date>
              <input class="input" type="time" data-scan-time>
              <small class="dim-note">${Utils.escapeHtml(t("batch.scan.local_time", "Your local time"))}</small>
            </div>
            <p class="scan-error" data-scan-error hidden></p>
            <div class="scan-actions">
              <button class="btn btn-primary" data-scan-confirm>${Utils.escapeHtml(t("batch.scan.review", "Review & confirm"))}</button>
            </div>
          </div>
        </div>`;

      const cancelBtn = root.querySelector("[data-scan-cancel]");
      if (cancelBtn) cancelBtn.disabled = report.stop_reason === "cancelled" || report.stop_reason === "completed";
    };

    const renderList = () => {
      const list = root.querySelector("[data-scan-list]");
      if (!list) return;
      const indexes = visibleIndexes();
      if (!indexes.length) {
        list.innerHTML = `<p class="dim-note">${Utils.escapeHtml(t("batch.scan.empty", "No results match this filter."))}</p>`;
      } else {
        list.innerHTML = indexes.map((i) => {
          const item = items[i];
          return `<div class="scan-row${item.available ? "" : " dim-row"}">
            <input type="checkbox" data-scan-pick="${i}" ${selected.has(i) ? "checked" : ""}
              ${item.available ? "" : "disabled"} aria-label="${Utils.escapeHtml(item.filename || item.url)}">
            <div class="scan-row-main">
              <span class="scan-name" title="${Utils.escapeHtml(item.filename || "")}">${Utils.escapeHtml(item.filename || t("batch.scan.unknown_name", "unknown file"))}</span>
              <span class="scan-url mono" title="${Utils.escapeHtml(item.url)}">${Utils.escapeHtml(item.url)}</span>
              ${item.error ? `<span class="scan-err">${Utils.escapeHtml(item.error)}</span>` : ""}
            </div>
            <span class="scan-meta">${Utils.escapeHtml(sizeText(item))}</span>
            <span class="scan-meta">${Utils.escapeHtml(item.content_type || item.server || "")}</span>
            <span class="scan-state ${item.available ? "ok" : "bad"}">${Utils.escapeHtml(statusText(item))}</span>
            <button class="icon-btn btn-xs" data-scan-remove="${i}" title="${Utils.escapeHtml(t("act.remove", "Remove"))}">${Utils.icon("x", 13)}</button>
          </div>`;
        }).join("");
      }
      const countEl = root.querySelector("[data-scan-selected]");
      if (countEl) countEl.textContent = `${selected.size} ${t("batch.scan.selected", "selected")}`;
      const confirmBtn = root.querySelector("[data-scan-confirm]");
      if (confirmBtn) confirmBtn.disabled = selected.size === 0 || view.submitting;
    };

    const wire = () => {
      root.querySelector("[data-scan-filter]").addEventListener("change", (e) => {
        view.status = e.target.value; renderList();
      });
      root.querySelector("[data-scan-search]").addEventListener("input", (e) => {
        view.text = e.target.value.trim(); renderList();
      });
      root.querySelector("[data-scan-all]").addEventListener("click", () => {
        visibleIndexes().forEach((i) => { if (items[i].available) selected.add(i); }); renderList();
      });
      root.querySelector("[data-scan-none]").addEventListener("click", () => {
        visibleIndexes().forEach((i) => selected.delete(i)); renderList();
      });
      root.querySelector("[data-scan-invert]").addEventListener("click", () => {
        visibleIndexes().forEach((i) => {
          if (!items[i].available) return;
          if (selected.has(i)) selected.delete(i); else selected.add(i);
        });
        renderList();
      });
      root.querySelector("[data-scan-cancel]").addEventListener("click", async () => {
        await API.cancelPatternScan();
      });
      root.querySelectorAll("[data-scan-pick]").forEach((box) => {
        box.addEventListener("change", () => {
          const i = +box.dataset.scanPick;
          if (box.checked) selected.add(i); else selected.delete(i);
          renderList();
        });
      });
      root.querySelectorAll("[data-scan-remove]").forEach((btn) => {
        btn.addEventListener("click", () => { selected.delete(+btn.dataset.scanRemove); renderList(); });
      });
      root.querySelectorAll('input[name="scanBehavior"]').forEach((radio) => {
        radio.addEventListener("change", () => {
          const when = root.querySelector("[data-scan-when]");
          if (when) when.hidden = root.querySelector('input[name="scanBehavior"]:checked').value !== "schedule";
        });
      });
      root.querySelector("[data-scan-confirm]").addEventListener("click", () =>
        ScanResultsUI.confirm(view, items, selected, root, onSubmit, t, source));
    };

    shell();
    // Fill the group selector from the app's real project model, never a
    // second disconnected list.
    if (app && typeof app._syncProjectSelects === "function") app._syncProjectSelects();
    const groupSelect = root.querySelector("[data-scan-group]");
    if (groupSelect && app && app.state.activeProject && app.state.activeProject !== "all"
        && Array.from(groupSelect.options).some((o) => o.value === app.state.activeProject)) {
      groupSelect.value = app.state.activeProject;
    }
    renderList();
    wire();

    return {
      /** Update rows while a scan is still streaming is not supported; the
          result set is final when shown. Kept for future incremental scans. */
      refresh: renderList,
    };
  },

  /** Show the explicit confirmation summary; nothing is submitted on Cancel.
   *  `source` is passed in explicitly: it belongs to show()'s scope, not this
   *  method's, and must reach the submit callback as part of the payload. */
  async confirm(view, items, selected, root, onSubmit, t, source = null) {
    const behavior = (root.querySelector('input[name="scanBehavior"]:checked') || {}).value || "queue";
    const directory = (root.querySelector("[data-scan-dir]").value || "").trim();
    const group = root.querySelector("[data-scan-group]");
    const projectId = group ? group.value : "";
    const groupName = group && group.selectedIndex >= 0 ? group.options[group.selectedIndex].text : "";
    const urls = Array.from(selected).map((i) => items[i].url).filter(Boolean);
    let dueAt = 0;
    let localTime = "";
    const tzName = (Intl.DateTimeFormat().resolvedOptions().timeZone) || "";
    if (behavior === "schedule") {
      const date = root.querySelector("[data-scan-date]").value;
      const time = root.querySelector("[data-scan-time]").value;
      if (!date || !time) {
        ScanResultsUI.showError(root, t("batch.scan.need_time", "Choose a date and time first."));
        return;
      }
      dueAt = new Date(`${date}T${time}`).getTime() / 1000;
      if (!Number.isFinite(dueAt) || dueAt <= Date.now() / 1000) {
        ScanResultsUI.showError(root, t("batch.scan.past_time", "Scheduled time must be in the future."));
        return;
      }
      localTime = `${date}T${time}`;
    }
    if (!urls.length) {
      ScanResultsUI.showError(root, t("batch.scan.no_selection", "Select at least one available file."));
      return;
    }

    const behaviorLabel = t("batch.scan.behavior_" + behavior,
      behavior === "now" ? "Download now" : behavior === "schedule" ? "Schedule" : "Add to queue");
    const whenLine = behavior === "schedule"
      ? `<div><strong>${Utils.escapeHtml(t("batch.scan.scheduled_for", "Scheduled for"))}</strong> ${Utils.escapeHtml(new Date(dueAt * 1000).toLocaleString())}</div>` : "";
    const actionKey = "batch.scan.confirm_" + behavior;
    const actionLabel = t(actionKey,
      behavior === "now" ? "Download now" : behavior === "schedule" ? "Schedule downloads" : "Add to queue");

    let settled = false;
    const dlg = Components.showModal(`
      <div class="scan-confirm">
        <p>${Utils.escapeHtml(t("batch.scan.confirm_intro", "Review before submitting. Nothing is added until you confirm."))}</p>
        <div class="scan-confirm-grid">
          <div><strong>${Utils.escapeHtml(t("batch.scan.selected", "Selected"))}</strong> ${urls.length}</div>
          <div><strong>${Utils.escapeHtml(t("batch.scan.destination", "Destination"))}</strong> ${Utils.escapeHtml(directory || t("batch.scan.default_dir", "default folder"))}</div>
          <div><strong>${Utils.escapeHtml(t("batch.scan.group", "Group"))}</strong> ${Utils.escapeHtml(groupName || t("batch.scan.default_group", "Default group"))}</div>
          <div><strong>${Utils.escapeHtml(t("batch.scan.behavior", "Behavior"))}</strong> ${Utils.escapeHtml(behaviorLabel)}</div>
          ${whenLine}
        </div>
        <details><summary>${Utils.escapeHtml(t("batch.scan.review_urls", "Review URLs"))} (${urls.length})</summary>
          <div class="scan-confirm-urls">${urls.map((u) => `<div class="mono">${Utils.escapeHtml(u)}</div>`).join("")}</div>
        </details>
      </div>`, {
      title: t("batch.scan.confirm_title", "Confirm downloads"),
      width: 560,
      onClose: () => { if (!settled) { settled = true; view.submitting = false; ScanResultsUI.refreshButtons(root); } },
    });
    const cancel = () => dlg.close();
    dlg.setFooter(`
      <button class="btn btn-ghost" data-cf-cancel>${Utils.escapeHtml(t("batch.cancel", "Cancel"))}</button>
      <button class="btn btn-primary" data-cf-go>${Utils.escapeHtml(actionLabel)}</button>`);
    dlg.qs("[data-cf-cancel]").addEventListener("click", cancel);
    dlg.qs("[data-cf-go]").addEventListener("click", async () => {
      const go = dlg.qs("[data-cf-go]");
      if (go.disabled) return;
      go.disabled = true;
      view.submitting = true;
      try {
        const result = await onSubmit({
          urls, directory, projectId, behavior, dueAt, localTime, tzName, source,
        });
        settled = true;
        dlg.close();
        ScanResultsUI.showResult(root, result, t);
      } catch (err) {
        go.disabled = false;
        ScanResultsUI.showError(root, String(err && err.message ? err.message : err));
      } finally {
        view.submitting = false;
        ScanResultsUI.refreshButtons(root);
      }
    });
  },

  refreshButtons(root) {
    const btn = root.querySelector("[data-scan-confirm]");
    if (btn) {
      const n = root.querySelectorAll("[data-scan-pick]:checked").length;
      btn.disabled = n === 0;
    }
  },

  showError(root, message) {
    const el = root.querySelector("[data-scan-error]");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
  },

  showResult(root, result, t) {
    const added = Number(result && result.added) || 0;
    const duplicates = Number(result && result.duplicates) || 0;
    const failed = Number(result && result.failed) || 0;
    const scheduled = Number(result && result.scheduled) || 0;
    const parts = [
      `${added} ${t("batch.scan.result_added", "added")}`,
      `${scheduled} ${t("batch.scan.result_scheduled", "scheduled")}`,
      `${duplicates} ${t("batch.scan.result_duplicates", "duplicate")}`,
      `${failed} ${t("batch.scan.result_failed", "failed")}`,
    ];
    // A successful submission clears the selection so a second click cannot
    // re-submit the same files.
    root.querySelectorAll("[data-scan-pick]").forEach((box) => {
      box.checked = false; box.disabled = true;
    });
    const note = document.createElement("p");
    note.className = "scan-submit-result";
    note.textContent = `${t("batch.scan.result_title", "Submission result")}: ${parts.join(" · ")}`;
    const actions = root.querySelector(".scan-actions");
    if (actions) actions.replaceChildren(note);
  },
};
