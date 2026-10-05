/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Download groups: pure model logic
   ═══════════════════════════════════════════════════════════════════════════

   A *download group* is what the user sees.  It is the same thing the backend
   calls a **project** (`projects/` package, `ui/projects_api.py`) — the word
   "group" is used here because that is what it is to the person using the app,
   and this file is the UI's vocabulary.  The two names meet in exactly one
   place: `Groups.load()`, which reads the `projects` payload.

   Everything here is a pure function of its arguments: no DOM, no API calls,
   no module state.  That is what lets `tests/frontend/groups-logic.test.mjs`
   exercise the tab strip, progress and validation directly.

   The backend is the single source of truth for a group's *state*.  It sends
   `state` and `state_reason`; this module only maps them to a label key and a
   colour tone.  Nothing here re-derives a state from counts, because two
   implementations of the same rule eventually disagree — and the copy in the
   UI would be the one that is wrong.
   ═══════════════════════════════════════════════════════════════════════════ */

const GroupsModel = {
  /** The synthetic "All" tab.  Never a real id, so it can never collide. */
  ALL_ID: "all",

  /** Every state the backend may send.  A test asserts the backend agrees. */
  STATES: [
    "empty",
    "queued",
    "running",
    "scheduled",
    "waiting_for_schedule",
    "paused_by_user",
    "paused_by_schedule",
    "completed",
    "failed",
  ],

  MAX_NAME_LENGTH: 80,
  MAX_CONCURRENT_CEILING: 50,

  /** True for the "All" tab (and for "nothing selected", which means All). */
  isAll(id) {
    return !id || id === this.ALL_ID;
  },

  /** i18n key for a state chip. */
  stateKey(state) {
    return "groups.state." + (this.STATES.includes(state) ? state : "empty");
  },

  /** Colour tone for a state chip.  Drives a CSS class, never the text. */
  stateTone(state) {
    switch (state) {
      case "running":
      case "completed":
        return "ok";
      case "failed":
        return "bad";
      case "paused_by_user":
      case "paused_by_schedule":
        return "warn";
      case "scheduled":
      case "waiting_for_schedule":
        return "info";
      default:
        return "muted";
    }
  },

  /**
   * Overall progress in percent, or `null` when it is not measurable.
   *
   * `null`, never `0`.  A group whose total size is still unknown would
   * otherwise render a confident "0%" bar — a number nobody has.  The queue's
   * `queue_plan` follows the same rule for the same reason.
   */
  progress(counts) {
    const c = counts || {};
    const total = Number(c.total_bytes) || 0;
    const done = Number(c.downloaded_bytes) || 0;
    if (total <= 0) return null;
    return Math.min(100, Math.max(0, (done / total) * 100));
  },

  /** Whether the backend currently reports this group's window as open. */
  windowOpen(project) {
    if (!project) return true;
    return project.schedule_open !== false;
  },

  hasSchedule(project) {
    return !!(project && project.schedule && project.schedule.enabled);
  },

  // ── Tab strip ──────────────────────────────────────────────────────

  /**
   * The name to render for a group.
   *
   * The Default group is seeded by the backend with the literal name
   * "Default".  That is not user data — it is a label the app chose — so it is
   * translated like any other label.  Every other name is whatever the user
   * typed and is shown verbatim.
   *
   * `isDefault` is passed in rather than read off an object on purpose: the tab
   * strip hands over a camelCase view model (`isDefault`) while the header
   * hands over the raw snake_case project (`is_default`).  Taking the flag
   * explicitly keeps this helper honest against both shapes.
   */
  displayName(name, isDefault, t) {
    if (isDefault) return t("groups.default_name");
    return name || t("groups.untitled");
  },

  /**
   * The tab strip, in **creation order**.
   *
   * Deliberately not sorted by state or recency: a tab that moves while the
   * user is looking at it is a tab they will click by mistake.  The backend
   * orders by creation for the same reason; sorting again here would be a
   * second, silent opinion about the same thing.
   */
  tabs(projects, activeId) {
    const list = (projects || []).map((p) => ({
      id: p.id,
      name: p.name || "",
      tone: this.stateTone(p.state),
      active: p.id === activeId,
      isDefault: !!p.is_default,
      paused: p.status === "paused",
    }));
    return [{
      id: this.ALL_ID,
      name: "",
      tone: "muted",
      active: this.isAll(activeId),
      isDefault: false,
      paused: false,
    }, ...list];
  },

  /** The group with this id, or `null` (including for the "All" tab). */
  find(projects, id) {
    if (this.isAll(id)) return null;
    return (projects || []).find((p) => p.id === id) || null;
  },

  /**
   * The tasks belonging to one group.
   *
   * A task with no `project_id` is treated as belonging to the Default group,
   * which is exactly what the backend does — the two must agree or a task
   * would be invisible in every tab.
   */
  tasksFor(tasks, projectId, defaultId) {
    const list = tasks || [];
    if (this.isAll(projectId)) return list.slice();
    const fallback = defaultId || "default";
    return list.filter((t) => (t.project_id || fallback) === projectId);
  },

  // ── Summaries ──────────────────────────────────────────────────────

  /** A one-line description of a schedule, or "" when there is none. */
  scheduleSummary(schedule, t) {
    if (!schedule || !schedule.enabled) return "";
    const start = schedule.start || "";
    const stop = schedule.stop || "";
    const days = (schedule.days || []).slice().sort((a, b) => a - b);
    const range = t("groups.schedule.range", { start, stop });
    if (!days.length) return t("groups.schedule.every_day", { range });
    const names = days.map((d) => t("groups.day." + d, String(d))).join(", ");
    return t("groups.schedule.days", { days: names, range });
  },

  /** Counts summary line: "12 downloads · 3 active · 1 failed". */
  countsSummary(counts, t) {
    const c = counts || {};
    const parts = [t("groups.counts.total", { n: c.total || 0 })];
    if (c.active) parts.push(t("groups.counts.active", { n: c.active }));
    if (c.queued) parts.push(t("groups.counts.queued", { n: c.queued }));
    if (c.paused) parts.push(t("groups.counts.paused", { n: c.paused }));
    if (c.failed) parts.push(t("groups.counts.failed", { n: c.failed }));
    return parts.join(" · ");
  },

  /**
   * Counts for one group, computed from the live task list.
   *
   * The strip and the header must never show a stale number, and the task list
   * already streams into the UI — so the count is derived from it rather than
   * from the backend payload, which only refreshes when a group itself changes.
   */
  counts(tasks) {
    const list = tasks || [];
    const inStates = (states) => list.filter((t) => states.includes(t.state)).length;
    return {
      total: list.length,
      active: inStates(["Downloading", "Analyzing", "Starting", "Merging", "Verifying"]),
      queued: inStates(["Queued"]),
      paused: inStates(["Paused"]),
      failed: inStates(["Failed", "Cancelled", "Stopped"]),
      completed: inStates(["Complete"]),
      downloaded_bytes: list.reduce((s, t) => s + (Number(t.completed) || 0), 0),
      total_bytes: list.reduce((s, t) => s + (Number(t.total) || 0), 0),
    };
  },

  // ── Validation ─────────────────────────────────────────────────────

  /**
   * Validate the create/edit form.
   *
   * Mirrors `projects/models.validate_name` exactly, so a user sees a problem
   * before the round trip — and, more importantly, the two layers cannot
   * disagree about what a valid name is.
   */
  validateDraft(draft, projects, editingId) {
    const raw = String((draft && draft.name) || "");
    const name = raw.trim().replace(/\s+/g, " ");
    if (!name) return { ok: false, field: "name", error: "name_required" };
    if (name.length > this.MAX_NAME_LENGTH) {
      return { ok: false, field: "name", error: "name_too_long" };
    }
    const lower = name.toLowerCase();
    const clash = (projects || []).some((p) => {
      if (p.id === editingId) return false;
      const other = String(p.name || "").trim().replace(/\s+/g, " ").toLowerCase();
      return other === lower;
    });
    if (clash) return { ok: false, field: "name", error: "name_duplicate" };

    const rawMax = draft && draft.max_concurrent;
    if (rawMax !== "" && rawMax !== null && rawMax !== undefined) {
      const max = Number(rawMax);
      if (!Number.isFinite(max) || max < 0 || max > this.MAX_CONCURRENT_CEILING) {
        return { ok: false, field: "max_concurrent", error: "max_concurrent_range" };
      }
    }

    const schedule = (draft && draft.schedule) || {};
    if (schedule.enabled) {
      const time = /^([01]?\d|2[0-3]):[0-5]\d$/;
      if (!time.test(String(schedule.start || ""))) {
        return { ok: false, field: "schedule_start", error: "schedule_time_invalid" };
      }
      if (!time.test(String(schedule.stop || ""))) {
        return { ok: false, field: "schedule_stop", error: "schedule_time_invalid" };
      }
    }
    return { ok: true, field: "", error: "" };
  },

  /** The three delete policies, in escalating order of destruction. */
  DELETE_MODES: ["keep_tasks", "delete_records", "delete_files"],

  /** True when a delete mode is the one that touches files on disk. */
  destroysFiles(mode) {
    return mode === "delete_files";
  },
};
