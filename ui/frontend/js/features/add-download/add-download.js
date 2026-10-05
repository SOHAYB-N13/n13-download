/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Feature: Add Download (dialog)
   ═══════════════════════════════════════════════════════════════════════════

   The New Download dialog: URL entry with debounced validation + probing,
   category selection, folder routing, advanced options (checksum /
   autostart) and the duplicate-policy conflict resolution flow.

   Extracted verbatim from app.js (Phase 6).  `App.openNewDownload` and
   `App._addDownloadResolvingConflict` delegate here.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, API, I18N, Components */

const AddDownload = {
  async open(app, prefillUrl = null, { paste = false } = {}) {
    try {
      const L = (k, f) => I18N.t(k, f);
      const settings = app.state.settings || (await API.getSettings()) || {};
      const baseDir = settings.download_dir || "";
      const categories = ["General", "Compressed", "Videos", "Music", "Documents", "Programs", "Images"];

      // Projects are optional: an empty list just means the selector is hidden
      // and the backend files the download under the Default project.  Never
      // block adding a download because the project list failed to load.
      let projects = Array.isArray(app.state.projects) ? app.state.projects.slice() : [];
      if (!projects.length) {
        try {
          const res = await API.getProjects();
          if (res && res.ok && Array.isArray(res.projects)) projects = res.projects;
        } catch { /* fall through to no selector */ }
      }

      const dlg = Components.showModal(`
      <div class="nd">
        <div class="nd-url-wrap">
          <span class="nd-url-ico">${Utils.icon("link", 17)}</span>
          <input id="ndUrl" class="input nd-url" type="url" placeholder="${L("dlg.url_placeholder", "Paste a download link…  https://")}" autocomplete="off" spellcheck="false" aria-label="${L("dlg.url_placeholder", "Download URL")}">
          <button class="icon-btn nd-paste" id="ndPaste" data-tip="${L("dlg.paste", "Paste from clipboard")}" aria-label="${L("dlg.paste", "Paste from clipboard")}">${Utils.icon("paste", 15)}</button>
        </div>
        <p class="nd-error" id="ndError" hidden></p>

        <div class="nd-detect" id="ndDetect" hidden>
          <div class="nd-detect-ico" id="ndDetectIco">${Utils.icon("file", 20)}</div>
          <div class="nd-detect-info">
            <div class="nd-detect-name" id="ndDetectName"></div>
            <div class="nd-detect-meta" id="ndDetectMeta"></div>
          </div>
          <span class="nd-resume" id="ndResume" hidden>${Utils.icon("bolt", 13)} ${L("dlg.resumable", "Resumable")}</span>
        </div>
        <div class="nd-detect nd-probing" id="ndProbing" hidden>
          <span class="sk sk-box" style="width:40px;height:40px;border-radius:12px"></span>
          <div style="flex:1;display:flex;flex-direction:column;gap:8px">
            <span class="sk sk-line w60"></span><span class="sk sk-line w35"></span>
          </div>
        </div>

        <div class="nd-grid">
          <div class="field">
            <label class="field-label" for="ndName">${L("dlg.file_name", "File name")}</label>
            <input id="ndName" class="input" type="text" placeholder="${L("dlg.file_name_placeholder", "Detected automatically")}" autocomplete="off">
          </div>
          <div class="field">
            <label class="field-label">${L("dlg.category", "Category")}</label>
            <div class="nd-cats" id="ndCats">
              ${categories.map((c, i) => `<button class="cat-chip${i === 0 ? " active" : ""}" data-cat="${c}">${L("category." + c, c)}</button>`).join("")}
            </div>
          </div>
          ${projects.length ? `
          <div class="field">
            <label class="field-label" for="ndProject">${L("dlg.project", "Project")}</label>
            <select id="ndProject" class="input">
              ${projects.map((p) => `<option value="${Utils.escapeHtml(p.id)}">${Utils.escapeHtml(p.name)}</option>`).join("")}
            </select>
          </div>` : ""}
          <div class="field">
            <label class="field-label" for="ndDir">${L("dlg.save_to", "Save to")}</label>
            <div class="input-join">
              <input id="ndDir" class="input" type="text" value="${Utils.escapeHtml(baseDir)}" autocomplete="off">
              <button class="btn btn-ghost" id="ndBrowse">${Utils.icon("folder", 15)} ${L("dlg.browse", "Browse")}</button>
            </div>
          </div>
        </div>

        <button class="nd-adv-toggle" id="ndAdvToggle" aria-expanded="false">
          ${Utils.icon("chevronRight", 14)} ${L("dlg.advanced", "Advanced options")}
        </button>
        <div class="nd-adv" id="ndAdv" hidden>
          <div class="field">
            <label class="field-label" for="ndChecksum">${L("dlg.checksum", "Checksum")} <span class="field-opt">${L("dlg.checksum_hint", "MD5 or SHA-256, optional")}</span></label>
            <input id="ndChecksum" class="input mono" type="text" placeholder="e.g. 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" autocomplete="off" spellcheck="false">
          </div>
          <label class="switch-row">
            <span class="switch"><input type="checkbox" id="ndAutostart" checked><span class="switch-track"></span></span>
            <span class="switch-text">${L("dlg.start_immediately", "Start immediately")}<span class="switch-hint">${L("dlg.start_off", "Off — add to the queue without starting")}</span></span>
          </label>
        </div>
      </div>`,
        { title: L("dlg.add_download", "New download"), subtitle: L("dlg.add_file_by_url", "Add a file by URL"), width: 620 });

      dlg.setFooter(`
      <button class="btn btn-ghost" id="ndCancel">${L("dlg.cancel_btn", "Cancel")}</button>
      <button class="btn btn-primary btn-lg" id="ndGo" disabled>${Utils.icon("download", 16)} ${L("dlg.download_btn", "Download")}</button>`);

      const el = {
        url: dlg.qs("#ndUrl"), name: dlg.qs("#ndName"), dir: dlg.qs("#ndDir"),
        checksum: dlg.qs("#ndChecksum"), autostart: dlg.qs("#ndAutostart"),
        err: dlg.qs("#ndError"), detect: dlg.qs("#ndDetect"), probing: dlg.qs("#ndProbing"),
        detectName: dlg.qs("#ndDetectName"), detectMeta: dlg.qs("#ndDetectMeta"),
        detectIco: dlg.qs("#ndDetectIco"), resume: dlg.qs("#ndResume"),
        go: dlg.qs("#ndGo"), cats: dlg.qs("#ndCats"), project: dlg.qs("#ndProject"),
      };

      // The open group is the natural default: a user who is looking at
      // "TV Series" and presses Add means TV Series, not the Default group.
      // Match on the `is_default` flag rather than position, so the backend's
      // ordering rule can change safely.
      const activeId = app.state.activeProject;
      const initialProject =
        (activeId && activeId !== "all" && projects.find((p) => p.id === activeId))
        || projects.find((p) => p.is_default)
        || projects[0]
        || null;

      const model = {
        valid: false, normalized: "", nameTouched: false, dirTouched: false,
        category: "General", baseDir, probing: 0,
        projectId: initialProject ? initialProject.id : "",
      };

      /** The folder a project routes to, falling back to the global setting. */
      const rootFor = (projectId) => {
        const p = projects.find((x) => x.id === projectId);
        return (p && p.directory) ? p.directory : baseDir;
      };

      const setValid = (ok, msg = "") => {
        model.valid = ok;
        el.go.disabled = !ok;
        el.err.hidden = ok || !msg;
        el.err.textContent = msg;
        el.url.classList.toggle("invalid", !ok && !!msg);
      };

      const applyCategory = (cat) => {
        model.category = cat;
        Utils.$qa(".cat-chip", el.cats).forEach((c) => c.classList.toggle("active", c.dataset.cat === cat));
        if (!model.dirTouched) {
          el.dir.value = cat === "General" ? model.baseDir : model.baseDir.replace(/[\\/]+$/, "") + "\\" + cat;
        }
      };

      const probe = Utils.debounce(async () => {
        const raw = el.url.value.trim();
        el.detect.hidden = true;
        if (!raw) { setValid(false); el.probing.hidden = true; return; }
        const v = await API.validateUrl(raw);
        if (!v || !v.valid) { setValid(false, L("dlg.valid_url_error", "Enter a valid http:// or https:// link")); return; }
        model.normalized = v.normalized;
        setValid(true);

        const ticket = ++model.probing;
        el.probing.hidden = false;
        const res = await API.probeUrl(v.normalized);
        if (ticket !== model.probing) return;   // stale response
        el.probing.hidden = true;
        if (res && res.ok) {
          const fname = res.filename || Utils.fileName({ url: v.normalized });
          el.detect.hidden = false;
          el.detectName.textContent = fname;
          el.detectMeta.textContent =
            (res.size_display ? res.size_display : L("dlg.unknown_size", "Unknown size")) +
            (Utils.hostOf(v.normalized) ? " · " + Utils.hostOf(v.normalized) : "");
          el.detectIco.innerHTML = Utils.fileIcon(fname, 20);
          el.resume.hidden = !res.range;
          if (!model.nameTouched) el.name.value = fname;
          applyCategory(Utils.categoryFor(fname));
        } else {
          el.detect.hidden = false;
          el.detectName.textContent = Utils.fileName({ url: v.normalized });
          el.detectMeta.textContent = (res && res.error) ? res.error : L("dlg.could_not_inspect", "Could not inspect this link");
          el.detectIco.innerHTML = Utils.icon("file", 20);
          el.resume.hidden = true;
          if (!model.nameTouched) el.name.value = Utils.fileName({ url: v.normalized });
        }
      }, 550);

      el.url.addEventListener("input", probe);
      el.url.addEventListener("keydown", (e) => { if (e.key === "Enter" && !el.go.disabled) el.go.click(); });
      el.name.addEventListener("input", () => { model.nameTouched = true; });
      el.dir.addEventListener("input", () => { model.dirTouched = true; model.baseDir = el.dir.value; });
      el.name.addEventListener("keydown", (e) => { if (e.key === "Enter" && !el.go.disabled) el.go.click(); });

      el.cats.addEventListener("click", (e) => {
        const chip = e.target.closest(".cat-chip");
        if (chip) applyCategory(chip.dataset.cat);
      });

      // A project may route to its own folder.  Choosing one only changes the
      // *default* destination — a folder the user typed or browsed to is never
      // overwritten, and the backend applies the same rule.
      if (el.project) {
        el.project.value = model.projectId;
        model.baseDir = rootFor(model.projectId);
        if (!model.dirTouched) applyCategory(model.category);
        el.project.addEventListener("change", () => {
          model.projectId = el.project.value;
          model.baseDir = rootFor(model.projectId);
          if (!model.dirTouched) applyCategory(model.category);
        });
      }

      dlg.qs("#ndPaste").addEventListener("click", async () => {
        try {
          const text = (await navigator.clipboard.readText() || "").trim();
          if (text) { el.url.value = text.split(/\s/)[0]; probe(); }
        } catch {
          Components.toast(L("toast.clipboard_blocked", "Clipboard blocked"), L("toast.clipboard_blocked_msg", "Allow clipboard access or paste manually"), "warning");
        }
      });

      dlg.qs("#ndBrowse").addEventListener("click", async () => {
        const dir = await API.selectDirectory();
        if (dir) {
          model.baseDir = dir;
          model.dirTouched = false;
          applyCategory(model.category);
        }
      });

      dlg.qs("#ndAdvToggle").addEventListener("click", () => {
        const panel = dlg.qs("#ndAdv");
        panel.hidden = !panel.hidden;
        dlg.qs("#ndAdvToggle").setAttribute("aria-expanded", String(!panel.hidden));
        dlg.qs("#ndAdvToggle").classList.toggle("open", !panel.hidden);
      });

      dlg.qs("#ndCancel").addEventListener("click", () => dlg.close());
      el.go.addEventListener("click", async () => {
        const url = (model.normalized || el.url.value).trim();
        const dir = el.dir.value.trim();
        const name = el.name.value.trim();
        el.go.disabled = true;
        el.go.classList.add("busy");
        try {
          const id = await this.resolveConflict(
            app, url, dir, name, el.checksum.value.trim(), el.autostart.checked,
            model.category, model.projectId);
          if (id) {
            app.state.highlightId = id;
            dlg.close();
            Components.toast(I18N.t("toast.download_added", "Download added"), name || Utils.fileName({ url }), "success");
            if (app.state.page !== "downloads") app.navigate("downloads");
          } else {
            setValid(false, L("dlg.add_error", "Could not add this download"));
          }
        } finally {
          el.go.classList.remove("busy");
          el.go.disabled = false;
        }
      });

      // Prefill flows.
      if (prefillUrl) {
        el.url.value = prefillUrl;
        probe();
      } else if (paste) {
        try {
          const text = (await navigator.clipboard.readText() || "").trim();
          if (text && /^https?:\/\//i.test(text)) {
            el.url.value = text.split(/\s/)[0];
            probe();
          }
        } catch {}
      }
      el.url.focus();
    } catch (e) {
      Components.toast(I18N.t("toast.open_dialog_failed", "Could not open dialog"), String(e), "error");
      API.logJs("openNewDownload: " + String(e));
    }
  },

  async resolveConflict(app, url, directory, name, checksum, autostart, category, projectId) {
    const policy = (app.state.settings && app.state.settings.duplicate_policy) || "ask";
    // One place that knows the positional shape of `add_download`, so adding a
    // parameter to the bridge never means editing seven call sites again.
    const add = (allowDuplicate = false, conflict = "") =>
      API.addDownload(url, directory, name, checksum, autostart, category,
        allowDuplicate, conflict, 0, "", projectId || "");
    let conflict = null;
    try { conflict = await API.checkDuplicate(url, directory, name); } catch (e) {}
    const hasConflict = conflict && (conflict.reason || conflict.has_active);
    if (!hasConflict) return add();
    if (policy === "allow") return add(true);
    if (policy === "replace") return add(false, "replace");
    if (policy === "rename") return add();
    // "ask" — show the conflict dialog.
    const choice = await Components.conflictPrompt({
      reason: conflict.reason || (conflict.has_active ? "same_url" : ""),
      filePath: conflict.file_path, name,
    });
    if (choice === "cancel") return "";
    if (choice === "open_task") {
      app.navigate("downloads");
      app.state.highlightId = conflict.active_task_id;
      app.state.listSig = "";
      app._renderDownloads(true);
      return "";
    }
    if (choice === "open") {
      API.openFileAt(conflict.file_path);
      return "";
    }
    if (choice === "replace") return add(false, "replace");
    if (choice === "again") return add(true);
    return add();
  },
};
