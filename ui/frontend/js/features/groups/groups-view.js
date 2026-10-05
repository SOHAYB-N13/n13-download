/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Download groups: view layer
   ═══════════════════════════════════════════════════════════════════════════

   Markup only, for the two pieces of chrome the group strip owns:

     * `strip()`  — the `[ + ] [ All ] [ TV Series ] [ GTA V ] …` tab row that
                    sits at the top of the Downloads page;
     * `header()` — the active group's name, state, progress and settings.

   Every decision about *what* to show comes from `GroupsModel`; every piece of
   data comes from the backend payload or the live task list.  This file never
   calls the API and never keeps state, so `groups.js` stays the only place that
   talks to the bridge.

   Design rules it follows:
     * one flat payload shape (see ui/projects_api.py) — no guessing;
     * unknown values render as "unknown", never as a plausible number;
     * all text goes through I18N, all user data through Utils.escapeHtml.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, GroupsModel */

const GroupsView = {
  esc(value) {
    return Utils.escapeHtml(value === null || value === undefined ? "" : String(value));
  },

  /** A short state chip.  Text comes from i18n, tone from the model. */
  chip(project, t) {
    const tone = GroupsModel.stateTone(project.state);
    const label = t(GroupsModel.stateKey(project.state));
    return `<span class="grp-chip grp-chip-${tone}">${this.esc(label)}</span>`;
  },

  /** The "why" line next to a chip — the backend's machine-readable reason. */
  reason(project, t) {
    if (!project.state_reason) return "";
    const label = t("groups.reason." + project.state_reason, "");
    return label ? `<span class="grp-reason">${this.esc(label)}</span>` : "";
  },

  progressBar(pct, t) {
    if (pct === null) {
      // Deliberately no fill: the total size is not known yet.
      return `<div class="grp-bar grp-bar-unknown" title="${this.esc(t("groups.progress_unknown"))}"></div>`;
    }
    return `<div class="grp-bar"><span style="width:${Math.round(pct)}%"></span></div>`;
  },

  limitLabel(project, t) {
    const limit = Number(project.max_concurrent) || 0;
    return limit > 0 ? t("groups.limit_n", { n: limit }) : t("groups.limit_inherit");
  },

  // ── Tab strip ──────────────────────────────────────────────────────

  /**
   * One tab.
   *
   * The tone dot is the only per-tab status signal on purpose: numbers on every
   * tab would compete with the names, and the header already carries the detail
   * for whichever tab is open.
   */
  tab(tab, t, count) {
    const label = tab.id === GroupsModel.ALL_ID
      ? t("groups.all")
      : GroupsModel.displayName(tab.name, tab.isDefault, t);
    const classes = ["grp-tab"];
    if (tab.active) classes.push("active");
    if (tab.paused) classes.push("paused");
    const n = count > 0 ? `<span class="grp-tab-n">${count}</span>` : "";
    return `<button class="${classes.join(" ")}" type="button" role="tab"
                    data-group="${this.esc(tab.id)}"
                    aria-selected="${tab.active}"
                    title="${this.esc(label)}">
              <span class="grp-tab-dot grp-dot-${tab.tone}" aria-hidden="true"></span>
              <span class="grp-tab-name">${this.esc(label)}</span>
              ${n}
            </button>`;
  },

  strip(tabs, t, countFor) {
    return tabs.map((tab) => this.tab(tab, t, countFor(tab.id))).join("");
  },

  // ── Active group header ────────────────────────────────────────────

  fact(label, value) {
    if (!value) return "";
    return `<span class="grp-fact"><span class="grp-fact-k">${this.esc(label)}</span>`
      + `<span class="grp-fact-v">${this.esc(value)}</span></span>`;
  },

  /**
   * The header for the open group.
   *
   * `counts` comes from the live task list, so the numbers move as downloads
   * progress instead of freezing until the next project mutation.
   */
  header(project, counts, t) {
    const paused = project.status === "paused";
    const pct = GroupsModel.progress(counts);
    const schedule = GroupsModel.scheduleSummary(project.schedule, t);
    const completion = project.completion_action === "shutdown"
      ? t("groups.completion.shutdown")
      : t("groups.completion.none");

    const hints = [];
    if (paused) hints.push(t("groups.paused_hint"));
    if (!GroupsModel.windowOpen(project)) hints.push(t("groups.schedule.closed_hint"));

    return `
      <div class="grp-head-main">
        <span class="grp-head-ico">${Utils.icon("folder", 17)}</span>
        <div class="grp-head-text">
          <h2 class="grp-head-name">${this.esc(GroupsModel.displayName(project.name, project.is_default, t))}</h2>
          <p class="grp-head-meta">${this.esc(GroupsModel.countsSummary(counts, t))}</p>
        </div>
        <div class="grp-head-state">
          ${this.chip(project, t)}
          ${pct === null ? "" : `<span class="grp-head-pct">${Math.round(pct)}%</span>`}
        </div>
        <div class="grp-head-actions">
          <button class="icon-btn grp-act" id="grpPause" type="button"
                  data-i18n-tip="${paused ? "groups.resume" : "groups.pause"}"
                  aria-label="${this.esc(paused ? t("groups.resume") : t("groups.pause"))}">
            ${Utils.icon(paused ? "play" : "pause", 15)}
          </button>
          <button class="icon-btn grp-act" id="grpEdit" type="button"
                  data-i18n-tip="groups.edit" aria-label="${this.esc(t("groups.edit"))}">
            ${Utils.icon("edit", 15)}
          </button>
          <button class="icon-btn grp-act grp-act-danger" id="grpDelete" type="button"
                  data-i18n-tip="groups.delete" aria-label="${this.esc(t("groups.delete"))}"
                  ${project.can_delete ? "" : "disabled"}>
            ${Utils.icon("trash", 15)}
          </button>
        </div>
      </div>

      ${this.progressBar(pct, t)}

      <div class="grp-head-foot">
        ${this.fact(t("groups.field.directory"), project.directory || t("groups.field.directory_default"))}
        ${this.fact(t("groups.field.schedule"), schedule || t("groups.field.schedule_none", null, "Always"))}
        ${this.fact(t("groups.field.concurrency"), this.limitLabel(project, t))}
        ${this.fact(t("groups.field.completion"), completion)}
        ${this.reason(project, t)}
        ${hints.map((h) => `<span class="grp-hint">${this.esc(h)}</span>`).join("")}
      </div>`;
  },

  // ── Empty state ────────────────────────────────────────────────────
  //
  // Both helpers take the same `(key, vars, fallback)` translator the rest of
  // this file uses, so a caller can pass one function and never have to know
  // which of `I18N.t` and `I18N.fmt` a particular string needs.

  /** `Components.emptyState` options for a group that has no downloads yet. */
  emptyGroupOptions(t, onAdd) {
    return {
      icon: "download",
      title: t("groups.empty.title", null, "No downloads in this group yet"),
      desc: t("groups.empty.desc", null,
        "Add a link and it lands in this group's folder."),
      actions: [
        { label: t("groups.empty.add", null, "Add download"), icon: "plus", primary: true, onClick: onAdd },
      ],
    };
  },

  /**
   * `Components.emptyState` options for a Downloads page with nothing in it.
   *
   * `handlers` is `{ add, newGroup, setupExtension, importBatch }` — the four
   * things a first-run user actually wants, in the order they are most likely
   * to want them.  Keeping them as one options object is what stops this from
   * becoming four positional arguments nobody can read at the call site.
   */
  emptyAllOptions(t, handlers) {
    const h = handlers || {};
    const actions = [
      { label: t("title.new_download", null, "New download"), icon: "plus", primary: true, onClick: h.add },
      { label: t("groups.new", null, "New group"), icon: "folder", onClick: h.newGroup },
    ];
    if (h.setupExtension) {
      actions.push({
        label: t("empty.install_extension", null, "Install Browser Extension"),
        icon: "browser",
        onClick: h.setupExtension,
      });
    }
    if (h.importBatch) {
      actions.push({
        label: t("empty.import", null, "Import Downloads"),
        icon: "batch",
        onClick: h.importBatch,
      });
    }
    return {
      icon: "download",
      title: t("empty.no_downloads", null, "No downloads yet"),
      desc: t("empty.no_downloads_desc", null, "Paste a link or drop it anywhere to start your first download."),
      actions,
    };
  },
};
