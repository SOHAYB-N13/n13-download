/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Download groups: dialogs
   ═══════════════════════════════════════════════════════════════════════════

   The create/edit form and the delete policy picker.

   Split out of `groups.js` because they are a different job: the controller
   owns data and navigation, these own form construction, inline validation and
   the destructive-action confirmation.  Keeping them apart means the "which
   delete mode destroys files?" question lives in exactly one place.

   The creation form shows **two** fields — name and destination — and nothing
   else.  Everything else (description, concurrency, completion action, the
   daily window) lives behind one "More options" toggle, because a user who
   wants a folder called "TV Series" should not have to read a settings page to
   get it.

   Only one modal may exist at a time (`Components.showModal` drives the global
   overlay), so nothing here nests a dialog inside a dialog.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, API, Components, GroupsModel */

const GroupDialogs = {
  t(key, vars, fallback) {
    return I18N.fmt(key, vars, fallback);
  },

  /** i18n text for a backend/validation error code. */
  errorText(code) {
    if (!code) return "";
    return this.t("groups.error." + code, null, code);
  },

  dayNames() {
    return [0, 1, 2, 3, 4, 5, 6].map((d) => this.t("groups.day." + d, null, String(d)));
  },

  // ── Create / edit ──────────────────────────────────────────────────

  /**
   * Open the create (``project === null``) or edit form.
   *
   * Resolves the affected group's **id** so the caller can activate it — or
   * ``null`` when the user cancelled, which is not an error and must not
   * produce a toast.
   */
  openEditor(app, project, projects) {
    return new Promise((resolve) => {
      const editing = !!project;
      const schedule = (project && project.schedule) || { enabled: false, start: "23:00", stop: "07:00", days: [] };
      const t = (k, v, f) => this.t(k, v, f);
      const days = schedule.days || [];

      // The advanced block starts open when the group already uses any of it,
      // so editing never hides a setting that is actually in effect.
      const advancedOpen = !!(
        (project && (project.max_concurrent || project.completion_action === "shutdown" || project.description))
        || schedule.enabled
      );

      const body = `
        <div class="grp-form">
          <label class="grp-field">
            <span>${Utils.escapeHtml(t("groups.field.name", null, "Name"))}</span>
            <input id="grpFName" class="input" type="text" maxlength="80" autocomplete="off"
                   placeholder="${Utils.escapeHtml(t("groups.field.name_placeholder", null, "e.g. TV Series"))}"
                   value="${Utils.escapeHtml(project ? project.name : "")}">
          </label>
          <p class="grp-field-err" id="grpFNameErr" hidden></p>

          <label class="grp-field">
            <span>${Utils.escapeHtml(t("groups.field.directory", null, "Destination folder"))}</span>
            <span class="grp-row">
              <input id="grpFDir" class="input" type="text" autocomplete="off"
                     placeholder="${Utils.escapeHtml(t("groups.field.directory_placeholder", null, "Use the default download folder"))}"
                     value="${Utils.escapeHtml(project ? project.directory : "")}">
              <button class="btn btn-ghost" id="grpFBrowse" type="button">
                ${Utils.icon("folder", 14)}${Utils.escapeHtml(t("groups.browse", null, "Browse"))}
              </button>
            </span>
            <small class="grp-hint">${Utils.escapeHtml(t("groups.field.directory_hint", null, "Downloads in this group are saved here."))}</small>
          </label>

          <details class="grp-adv" ${advancedOpen ? "open" : ""}>
            <summary>${Utils.escapeHtml(t("groups.advanced", null, "More options"))}</summary>

            <label class="grp-field">
              <span>${Utils.escapeHtml(t("groups.field.description", null, "Description"))}</span>
              <textarea id="grpFDesc" class="input" rows="2" maxlength="500">${Utils.escapeHtml(project ? project.description : "")}</textarea>
            </label>

            <label class="grp-field">
              <span>${Utils.escapeHtml(t("groups.field.concurrency", null, "Simultaneous downloads"))}</span>
              <input id="grpFMax" class="input" type="number" min="0" max="50" step="1"
                     value="${project ? Number(project.max_concurrent) || 0 : 0}">
              <small class="grp-hint">${Utils.escapeHtml(t("groups.field.concurrency_hint", null, "0 uses the global limit. A group never exceeds it either way."))}</small>
            </label>

            <label class="grp-field">
              <span>${Utils.escapeHtml(t("groups.field.completion", null, "When this group finishes"))}</span>
              <select id="grpFDone" class="input">
                <option value="none">${Utils.escapeHtml(t("groups.completion.none", null, "Do nothing"))}</option>
                <option value="shutdown" ${project && project.completion_action === "shutdown" ? "selected" : ""}>
                  ${Utils.escapeHtml(t("groups.completion.shutdown", null, "Shut down the computer"))}
                </option>
              </select>
              <small class="grp-hint">${Utils.escapeHtml(t("groups.completion_hint", null, "The computer is only shut down once every group has finished."))}</small>
            </label>

            <label class="grp-check">
              <input id="grpFSchedOn" type="checkbox" ${schedule.enabled ? "checked" : ""}>
              <span>${Utils.escapeHtml(t("groups.schedule.enable", null, "Only download during a time window"))}</span>
            </label>
            <div class="grp-row grp-times" id="grpFTimes">
              <input id="grpFStart" class="input" type="time" value="${Utils.escapeHtml(schedule.start || "23:00")}">
              <span class="grp-arrow">→</span>
              <input id="grpFStop" class="input" type="time" value="${Utils.escapeHtml(schedule.stop || "07:00")}">
            </div>
            <small class="grp-hint">${Utils.escapeHtml(t("groups.schedule.overnight_hint", null, "A window that ends earlier than it starts runs across midnight."))}</small>
            <div class="grp-days" id="grpFDays">
              ${this.dayNames().map((label, i) => `
                <label class="grp-day">
                  <input type="checkbox" data-day="${i}" ${days.indexOf(i) !== -1 ? "checked" : ""}>
                  <span>${Utils.escapeHtml(label)}</span>
                </label>`).join("")}
            </div>
            <small class="grp-hint">${Utils.escapeHtml(t("groups.schedule.days_hint", null, "No day selected means every day."))}</small>
          </details>

          <p class="grp-field-err" id="grpFErr" hidden></p>
        </div>`;

      const modal = Components.showModal(body, {
        title: editing
          ? t("groups.edit_title", null, "Edit group")
          : t("groups.new_title", null, "New download group"),
        subtitle: t("groups.form_sub", null, "Group downloads that share a folder, a limit or a time window."),
        width: 560,
        onClose: () => resolve(null),
      });

      modal.setFooter(`
        <button class="btn btn-ghost" id="grpFCancel">${Utils.escapeHtml(t("groups.cancel", null, "Cancel"))}</button>
        <button class="btn btn-primary" id="grpFSave">${Utils.escapeHtml(editing ? t("groups.save", null, "Save") : t("groups.create", null, "Create group"))}</button>`);

      const q = (sel) => modal.qs(sel);
      const err = q("#grpFErr");
      const nameErr = q("#grpFNameErr");

      // The time inputs and the day grid are only meaningful when the window is
      // on, so they are disabled rather than hidden — the user can still see the
      // values they configured earlier.
      const syncSchedule = () => {
        const on = q("#grpFSchedOn").checked;
        q("#grpFTimes").classList.toggle("grp-disabled", !on);
        q("#grpFStart").disabled = !on;
        q("#grpFStop").disabled = !on;
        q("#grpFDays").classList.toggle("grp-disabled", !on);
        Utils.$qa("input[data-day]", q("#grpFDays")).forEach((c) => { c.disabled = !on; });
      };
      q("#grpFSchedOn").addEventListener("change", syncSchedule);
      syncSchedule();

      q("#grpFBrowse").addEventListener("click", async () => {
        const picked = await API.selectDirectory();
        if (picked) q("#grpFDir").value = picked;
      });
      q("#grpFCancel").addEventListener("click", () => modal.close());
      q("#grpFName").addEventListener("input", () => { nameErr.hidden = true; });

      const showError = (field, code) => {
        const text = this.errorText(code);
        if (field === "name") {
          nameErr.textContent = text;
          nameErr.hidden = false;
          q("#grpFName").focus();
          return;
        }
        err.textContent = text;
        err.hidden = false;
      };

      const collect = () => ({
        name: q("#grpFName").value,
        description: q("#grpFDesc").value,
        directory: q("#grpFDir").value,
        max_concurrent: q("#grpFMax").value,
        completion_action: q("#grpFDone").value,
        schedule: {
          enabled: q("#grpFSchedOn").checked,
          start: q("#grpFStart").value,
          stop: q("#grpFStop").value,
          days: Utils.$qa("input[data-day]", q("#grpFDays"))
            .filter((c) => c.checked)
            .map((c) => Number(c.dataset.day)),
        },
      });

      const submit = async () => {
        const draft = collect();
        err.hidden = true;
        nameErr.hidden = true;

        // Validate locally first so the user is not made to wait for a round
        // trip to learn their name is empty — using the same rules the backend
        // applies, so the two can never disagree.
        const check = GroupsModel.validateDraft(draft, projects || [], editing ? project.id : "");
        if (!check.ok) {
          showError(check.field, check.error);
          return;
        }

        const payload = {
          description: draft.description.trim(),
          directory: draft.directory.trim(),
          max_concurrent: Number(draft.max_concurrent) || 0,
          completion_action: draft.completion_action,
          schedule: draft.schedule,
        };
        const result = editing
          ? await API.updateProject(project.id, Object.assign({ name: draft.name.trim() }, payload))
          : await API.createProject(
              draft.name.trim(), payload.description, payload.directory,
              payload.max_concurrent, payload.schedule, payload.completion_action
            );

        if (!result || result.ok === false) {
          showError((result && result.field) || "", (result && result.error) || "unknown");
          return;
        }
        const id = (result.project && result.project.id) || (editing ? project.id : "");
        modal.close();
        Components.toast(
          editing
            ? t("groups.toast.updated", null, "Group updated")
            : t("groups.toast.created", null, "Group created"),
          draft.name.trim(),
          "success"
        );
        resolve(id || null);
      };

      q("#grpFSave").addEventListener("click", submit);
      q("#grpFName").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
      q("#grpFName").focus();
    });
  },

  // ── Delete ─────────────────────────────────────────────────────────

  /**
   * Ask which delete policy to apply.
   *
   * The three options are ordered by how much they destroy, and the one that
   * touches files on disk needs a second, separate confirmation — a user must
   * never be able to lose downloads with one click.
   */
  openDelete(app, project) {
    return new Promise((resolve) => {
      const t = (k, v, f) => this.t(k, v, f);
      const options = [
        { mode: "keep_tasks", label: t("groups.delete.keep", null, "Keep the downloads"), hint: t("groups.delete.keep_hint", null, "Move them to the Default group. Nothing is deleted.") },
        { mode: "delete_records", label: t("groups.delete.records", null, "Remove the download records"), hint: t("groups.delete.records_hint", null, "The files stay on your disk.") },
        { mode: "delete_files", label: t("groups.delete.files", null, "Delete the records and the files"), hint: t("groups.delete.files_hint", null, "This permanently deletes the downloaded files inside the group folder.") },
      ];

      const body = `
        <div class="grp-form">
          <p class="grp-del-lead">${Utils.escapeHtml(t("groups.delete.lead", { name: project.name }))}</p>
          ${options.map((o, i) => `
            <label class="grp-radio ${o.mode === "delete_files" ? "grp-radio-danger" : ""}">
              <input type="radio" name="grpDelMode" value="${o.mode}" ${i === 0 ? "checked" : ""}>
              <span class="grp-radio-body">
                <strong>${Utils.escapeHtml(o.label)}</strong>
                <small>${Utils.escapeHtml(o.hint)}</small>
              </span>
            </label>`).join("")}
          <label class="grp-check grp-del-confirm" id="grpDelConfirmWrap" hidden>
            <input id="grpDelConfirm" type="checkbox">
            <span>${Utils.escapeHtml(t("groups.delete.confirm_files", null, "I understand the downloaded files will be deleted."))}</span>
          </label>
          <p class="grp-field-err" id="grpDelErr" hidden></p>
        </div>`;

      const modal = Components.showModal(body, {
        title: t("groups.delete.title", null, "Delete group"),
        subtitle: t("groups.delete.sub", null, "Choose what happens to the downloads in this group."),
        width: 560,
        onClose: () => resolve(false),
      });

      modal.setFooter(`
        <button class="btn btn-ghost" id="grpDelCancel">${Utils.escapeHtml(t("groups.cancel", null, "Cancel"))}</button>
        <button class="btn btn-danger" id="grpDelGo">${Utils.escapeHtml(t("groups.delete.go", null, "Delete group"))}</button>`);

      const q = (sel) => modal.qs(sel);
      const confirmWrap = q("#grpDelConfirmWrap");
      const err = q("#grpDelErr");

      const selectedMode = () => {
        const picked = q("input[name=grpDelMode]:checked");
        return picked ? picked.value : "keep_tasks";
      };
      const syncConfirm = () => {
        confirmWrap.hidden = !GroupsModel.destroysFiles(selectedMode());
      };
      Utils.$qa("input[name=grpDelMode]", modal.el).forEach((r) => {
        r.addEventListener("change", syncConfirm);
      });
      syncConfirm();

      q("#grpDelCancel").addEventListener("click", () => modal.close());
      q("#grpDelGo").addEventListener("click", async () => {
        const mode = selectedMode();
        err.hidden = true;
        if (GroupsModel.destroysFiles(mode) && !q("#grpDelConfirm").checked) {
          err.textContent = this.errorText("delete_files_not_confirmed");
          err.hidden = false;
          return;
        }
        const result = await API.deleteProject(project.id, mode, q("#grpDelConfirm").checked);
        if (!result || result.ok === false) {
          err.textContent = this.errorText((result && result.error) || "unknown");
          err.hidden = false;
          return;
        }
        modal.close();
        Components.toast(
          t("groups.toast.deleted", null, "Group deleted"),
          project.name,
          "success"
        );
        resolve(true);
      });
    });
  },
};
