/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Download groups: controller
   ═══════════════════════════════════════════════════════════════════════════

   Owns the group strip that lives at the top of the **Downloads** page.  There
   is no separate "Projects" destination: a group is just a scope for the
   workspace you are already in, so `[ + ] [ All ] [ TV Series ] [ GTA V ]`
   appears above the download toolbar and switching a tab re-filters the very
   same list.

   Naming: the backend persists these as **projects** (`projects/` package,
   `ui/projects_api.py`, `API.getProjects()`).  The UI calls them *groups*
   because that is what they are to the user.  `load()` is the one place the two
   vocabularies meet.

   Rendering lives in `groups-view.js`, the forms in `group-dialogs.js`, the
   rules in `groups-model.js` — so this file is the only one that talks to the
   API.  Event handling is delegated from the stable containers (`#grpTabs`,
   `#grpHead`), never from a tab, because `paint()` replaces those on every
   refresh.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N, API, Components, GroupsModel, GroupsView, GroupDialogs */

const Groups = {
  _app: null,
  _bound: false,
  _sig: "",

  t(key, vars, fallback) {
    return I18N.fmt(key, vars, fallback);
  },

  init(app) {
    this._app = app;
    if (this._bound) return;
    const tabs = Utils.$id("grpTabs");
    if (!tabs) return;

    // Delegated: `paint()` rebuilds the tabs, so binding to a tab would bind to
    // an element that no longer exists one refresh later.
    tabs.addEventListener("click", (e) => {
      const tab = e.target.closest("[data-group]");
      if (tab) this.setActive(tab.dataset.group);
    });
    tabs.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const list = Utils.$qa("[data-group]", tabs);
      const i = list.indexOf(document.activeElement);
      if (i === -1) return;
      e.preventDefault();
      const next = list[(i + (e.key === "ArrowRight" ? 1 : list.length - 1)) % list.length];
      next.focus();
    });

    Utils.$id("grpNew")?.addEventListener("click", () => this.create());
    Utils.$id("grpHead")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      const project = this.current();
      if (!btn || !project) return;
      if (btn.id === "grpPause") return this.togglePause(project);
      if (btn.id === "grpEdit") return this.edit(project);
      if (btn.id === "grpDelete") return this.remove(project);
    });

    this._bound = true;
  },

  // ── Data ───────────────────────────────────────────────────────────

  /** Re-read the group list from the bridge and repaint the strip. */
  async load() {
    const app = this._app;
    if (!app) return;
    try {
      const result = await API.getProjects();
      app.state.projects = (result && result.ok && Array.isArray(result.projects))
        ? result.projects
        : [];
    } catch (e) {
      API.logJs("groups load: " + String(e));
      app.state.projects = [];
    }

    // A group deleted on another screen (or by a failed delete) must not leave
    // the workspace scoped to something that no longer exists.
    if (!GroupsModel.isAll(app.state.activeProject)
        && !GroupsModel.find(app.state.projects, app.state.activeProject)) {
      app.state.activeProject = GroupsModel.ALL_ID;
      app.state.listSig = "";
    }
    this.paint();
  },

  /**
   * Cheap periodic re-read, driven from the existing stats poll.
   *
   * A group's derived state (a window opening, a slot freeing) changes without
   * any project mutation, so waiting for a `projects_changed` event would leave
   * the header lying.  Repaints only when something actually moved.
   */
  async refresh() {
    const app = this._app;
    if (!app) return;
    let result;
    try { result = await API.getProjects(); } catch { return; }
    const list = (result && result.ok && Array.isArray(result.projects)) ? result.projects : [];
    const sig = this._signature(list, app.state.activeProject);
    app.state.projects = list;
    if (sig === this._sig) return;
    this.paint();
  },

  _signature(projects, activeId) {
    return (projects || [])
      .map((p) => [p.id, p.name, p.state, p.state_reason, p.status, p.schedule_open, p.can_delete].join(":"))
      .join("|") + "::" + activeId;
  },

  // ── Active group ───────────────────────────────────────────────────

  /** The open group, or `null` while the "All" tab is showing. */
  current() {
    const app = this._app;
    if (!app) return null;
    return GroupsModel.find(app.state.projects, app.state.activeProject);
  },

  /** Live counts for one group, derived from the task list. */
  countsFor(projectId) {
    const app = this._app;
    const tasks = app ? Object.values(app.state.downloads) : [];
    return GroupsModel.counts(GroupsModel.tasksFor(tasks, projectId, this._defaultId()));
  },

  /** The number shown on a tab.  The "All" tab counts everything. */
  countFor(projectId) {
    const app = this._app;
    const tasks = app ? Object.values(app.state.downloads) : [];
    return GroupsModel.tasksFor(tasks, projectId, this._defaultId()).length;
  },

  _defaultId() {
    const app = this._app;
    const def = ((app && app.state.projects) || []).find((p) => p.is_default);
    return def ? def.id : "default";
  },

  /**
   * Switch the workspace to a group.
   *
   * Only `activeProject` and the list signature change — the task list, the
   * toolbar, the queue strip and every row component are reused untouched, so
   * switching is a re-filter rather than a page change.
   */
  setActive(projectId) {
    const app = this._app;
    if (!app) return;
    const id = projectId || GroupsModel.ALL_ID;
    if (id === app.state.activeProject) return;
    app.state.activeProject = id;
    app.state.listSig = "";
    app._clearSelection();
    this.paint();
    // The batch page's selector follows the open group too, so "queue these
    // into what I am looking at" is the default there as well.
    app._syncProjectSelects();
    if (app.state.page === "downloads") app._renderDownloads(true);
    else app.navigate("downloads");
  },

  // ── Painting ───────────────────────────────────────────────────────

  paint() {
    const app = this._app;
    if (!app) return;
    const strip = Utils.$id("grpStrip");
    const tabs = Utils.$id("grpTabs");
    const head = Utils.$id("grpHead");
    if (!strip || !tabs || !head) return;

    const projects = app.state.projects || [];
    const activeId = app.state.activeProject;

    // The strip only earns its space once there is a group to switch to.  The
    // "+" stays reachable through the empty state until then.
    strip.hidden = !projects.length;

    const t = (k, v, f) => this.t(k, v, f);
    if (projects.length) {
      tabs.innerHTML = GroupsView.strip(
        GroupsModel.tabs(projects, activeId), t, (id) => this.countFor(id)
      );
    } else {
      tabs.innerHTML = "";
    }

    const project = this.current();
    if (project) {
      head.hidden = false;
      head.innerHTML = GroupsView.header(project, this.countsFor(project.id), t);
    } else {
      head.hidden = true;
      head.innerHTML = "";
    }

    this._sig = this._signature(projects, activeId);
  },

  // ── Actions ────────────────────────────────────────────────────────

  /**
   * The "+" flow: configure the group, then open it.
   *
   * The new group becomes active immediately, so the user lands on the empty
   * state for the thing they just made rather than on a list that looks
   * unchanged.
   */
  async create() {
    const app = this._app;
    const id = await GroupDialogs.openEditor(app, null, app.state.projects || []);
    if (!id) return;
    await this.load();
    if (app.state.page !== "downloads") app.navigate("downloads");
    this.setActive(id);
  },

  async edit(project) {
    const app = this._app;
    const id = await GroupDialogs.openEditor(app, project, app.state.projects || []);
    if (!id) return;
    await this.load();
    this.setActive(id);
  },

  async togglePause(project) {
    const paused = project.status === "paused";
    const result = paused
      ? await API.resumeProject(project.id)
      : await API.pauseProject(project.id);
    if (!result || result.ok === false) {
      Components.toast(
        this.t("groups.toast.failed", null, "That did not work"),
        GroupDialogs.errorText(result && result.error),
        "error"
      );
      return;
    }
    Components.toast(
      paused ? this.t("groups.toast.resumed", null, "Group resumed")
             : this.t("groups.toast.paused", null, "Group paused"),
      project.name,
      "info"
    );
    await this.load();
  },

  async remove(project) {
    if (!project.can_delete) {
      Components.toast(
        this.t("groups.toast.failed", null, "That did not work"),
        GroupDialogs.errorText("project_not_deletable"),
        "error"
      );
      return;
    }
    const deleted = await GroupDialogs.openDelete(this._app, project);
    if (!deleted) return;
    const app = this._app;
    if (app.state.activeProject === project.id) app.state.activeProject = GroupsModel.ALL_ID;
    app.state.listSig = "";
    await this.load();
    app._renderDownloads(true);
  },
};
