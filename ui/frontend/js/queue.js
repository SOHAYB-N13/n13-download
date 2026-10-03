/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Queue experience
   ═══════════════════════════════════════════════════════════════════════════

   Self-contained module for the Queue page: overview, running list, waiting
   list with drag & drop / multi-select / keyboard reordering, a tabbed details
   panel, and setting recommendations.

   Design rules this file follows
   ------------------------------
   1. Never invent data.  Every number comes from a task snapshot or from
      `API.queuePlan()`.  Where the backend reports "cannot measure", the UI
      says so ("unknown") instead of showing a plausible-looking figure.
   2. Ordering has exactly one source of truth on the backend: the manual queue
      order, with priority breaking ties.  This file therefore offers two
      clearly-labelled views — "Start order" (what will actually happen) and
      "Manual order" (the list you drag) — and never pretends they are the
      same list when priorities differ.
   3. Recommendations are suggestions.  Nothing here changes a setting by
      itself; every change is an explicit click on an explicit button.
   ═══════════════════════════════════════════════════════════════════════════ */

const Queue = {
  /** States the engine treats as "occupied slot". Mirrors ui/common.py. */
  ACTIVE_STATES: ["Downloading", "Analyzing", "Starting", "Merging", "Verifying", "Stopping"],

  state: {
    order: "start",     // "start" = effective start order, "manual" = drag-editable
    plan: null,         // last API.queuePlan() result, or null when unavailable
    planAt: 0,          // timestamp of the last plan fetch
    sel: new Set(),     // selected waiting task ids
    anchor: null,       // shift-click anchor
    focus: null,        // task id shown in the details panel
    tab: "overview",
    drag: null,         // { id } while a row drag is in flight
    dropAt: null,       // { id, after } hover target during a drag
    scroller: 0,        // preserved scrollTop of the waiting list
    timer: null,
    bound: false,
  },

  // ── Lifecycle ────────────────────────────────────────────────────────

  init() {
    if (this.state.bound) return;
    this.state.bound = true;

    const view = Utils.$id("queueView");
    if (!view) return;

    this._bindEvents(view);
    I18N.onChange(() => { if (App.state.page === "queue") this.render(); });

    // A light tick keeps estimates and progress fresh without piggybacking on
    // the download poller.  It does nothing unless the Queue page is visible.
    this.state.timer = setInterval(() => this._tick(), 1200);
  },

  _tick() {
    if (document.hidden) return;
    // The badge is useful from any page, so it updates before the page guard.
    this._updateBadge();
    if (App.state.page !== "queue") return;
    if (this.state.drag) return;             // never re-render mid-drag
    this.render({ refreshPlan: true });
  },

  /** Sidebar count of waiting downloads. */
  _updateBadge() {
    const el = Utils.$id("queueBadge");
    if (!el) return;
    const n = this._all().filter((t) => t.state === "Queued").length;
    el.textContent = String(n);
    el.hidden = n === 0;
  },

  // ── Data helpers ─────────────────────────────────────────────────────

  /** taskId -> plan entry, for the currently known plan. */
  _planIndex() {
    const map = new Map();
    (this.state.plan || []).forEach((p) => map.set(p.id, p));
    return map;
  },

  _active() {
    return this._all()
      .filter((t) => this.ACTIVE_STATES.includes(t.state))
      .sort((a, b) => (b.speed_bps || 0) - (a.speed_bps || 0));
  },

  _all() {
    return Object.values(App.state.downloads || {});
  },

  /**
   * Waiting tasks, ordered for the current view.
   *
   * "start" follows the engine's real selection rule (priority, then manual
   * position) so the list matches what will actually happen.  "manual" follows
   * the raw queue order, which is the list drag & drop edits.
   */
  _waiting() {
    const queued = this._all().filter((t) => t.state === "Queued");
    if (this.state.order === "manual") {
      return queued.sort((a, b) => this._manualIdx(a) - this._manualIdx(b));
    }
    const plan = this._planIndex();
    return queued.sort((a, b) => {
      const pa = plan.get(a.id);
      const pb = plan.get(b.id);
      if (pa && pb) return pa.position - pb.position;
      if (pa) return -1;
      if (pb) return 1;
      return this._manualIdx(a) - this._manualIdx(b);
    });
  },

  _manualIdx(t) {
    const i = t.queue_index;
    // The backend reports -1 for "not in the queue order at all".  A raw -1
    // would sort such a task to the very front, which is the opposite of the
    // truth, so anything negative is pushed to the end instead.
    return typeof i === "number" && i >= 0 ? i : 1e9;
  },

  /**
   * True when priority actually changes the start order.
   *
   * With uniform priorities the two views are identical, so the UI hides the
   * order toggle and all the priority-explanation copy rather than nagging
   * about a distinction that makes no difference.
   */
  _prioritiesDiverge() {
    const waiting = this._all().filter((t) => t.state === "Queued");
    if (waiting.length < 2) return false;
    const first = waiting[0].priority;
    return waiting.some((t) => t.priority !== first);
  },

  /** Total remaining bytes of the waiting queue, or null when unknown. */
  _waitingBytes() {
    const waiting = this._all().filter((t) => t.state === "Queued");
    if (!waiting.length) return 0;
    if (waiting.some((t) => !(t.total > 0))) return null;
    return waiting.reduce((s, t) => s + Math.max(0, t.total - t.completed), 0);
  },

  /**
   * When the queue is expected to finish, or null.
   *
   * The plan already schedules every waiting task behind the ones ahead of it,
   * so the last entry's start time is the earliest the queue can empty; adding
   * that task's own transfer time at the measured per-slot throughput gives
   * the finish.
   *
   * Returns `{ seconds, exact }`.  When a waiting download's size is not known
   * yet the chain cannot be closed, so the answer is reported as a lower bound
   * (`exact: false`) rather than either being withheld or quietly presented as
   * the whole truth.  Returns null only when nothing at all is measurable.
   */
  _drain() {
    const plan = this.state.plan;
    if (!plan || !plan.length) return null;

    const active = this._active();
    const totalSpeed = active.reduce((s, t) => s + (t.speed_bps || 0), 0);
    const perSlot = active.length && totalSpeed > 0 ? totalSpeed / active.length : 0;
    if (perSlot <= 0) return null;

    let finish = 0;
    let exact = true;
    for (const p of plan) {
      if (p.estimated_start_seconds == null || !(p.remaining > 0)) {
        // A task whose own length is unknown can only push the finish later,
        // never earlier, so the running figure stays valid as a lower bound.
        exact = false;
        continue;
      }
      finish = Math.max(finish, p.estimated_start_seconds + p.remaining / perSlot);
    }
    if (finish <= 0) return null;
    return { seconds: finish, exact };
  },

  _slots() {
    const s = App.state.settings || {};
    return Math.max(1, parseInt(s.max_concurrent, 10) || 1);
  },

  async refreshPlan() {
    try {
      const plan = await API.queuePlan();
      this.state.plan = Array.isArray(plan) ? plan : null;
      this.state.planAt = Date.now();
    } catch (e) {
      this.state.plan = null;
    }
    return this.state.plan;
  },

  // ── Rendering ────────────────────────────────────────────────────────

  async render({ refreshPlan = false } = {}) {
    const view = Utils.$id("queueView");
    if (!view) return;

    if (refreshPlan || !this.state.plan || Date.now() - this.state.planAt > 900) {
      await this.refreshPlan();
    }
    // The task list can change while the plan request is in flight.
    if (App.state.page !== "queue") return;

    const waitingEl = Utils.$id("queueWaiting");
    this.state.scroller = waitingEl ? waitingEl.scrollTop : this.state.scroller;

    // The gate banner is only built while it has something to say, so the DOM
    // never carries an empty placeholder node.
    const gate = this._gateBannerEl();
    const children = [this._overviewEl(), this._bodyEl(), this._recoEl()];
    if (gate) children.unshift(gate);
    view.replaceChildren(...children);

    const scroller = Utils.$id("queueWaiting");
    if (scroller) scroller.scrollTop = this.state.scroller;

    // Keep the sidebar count honest from the moment the page paints rather
    // than waiting for the next tick.
    this._updateBadge();
  },

  /**
   * Banner shown while the queue gate is closed.
   *
   * Without it a paused queue looks exactly like a stalled one: nothing starts
   * and nothing explains why.  The state is read from the backend's runtime
   * report — never inferred from the fact that the user pressed Pause once.
   * See docs/QUEUE.md §1.
   *
   * Returns ``null`` when the gate is open, so no empty node is ever created.
   */
  _gateBannerEl() {
    if (!App.state.queuePaused) return null;
    const box = document.createElement("div");
    box.className = "qk-gate";
    box.innerHTML = `
      <span class="qk-gate-ico" aria-hidden="true">${Utils.icon("pause", 16)}</span>
      <div class="qk-gate-text">
        <strong>${Utils.escapeHtml(I18N.t("queue.paused_title", "Queue paused"))}</strong>
        <span>${Utils.escapeHtml(I18N.t("queue.paused_msg", "No new downloads will start until you resume the queue."))}</span>
      </div>
      <button class="btn btn-ghost btn-sm" data-qk="resume-queue">${Utils.escapeHtml(I18N.t("queue.resume_queue", "Resume queue"))}</button>`;
    return box;
  },

  /** Top strip: live counters + queue drain estimate. */
  _overviewEl() {
    const active = this._active();
    const waiting = this._all().filter((t) => t.state === "Queued");
    const totalSpeed = active.reduce((s, t) => s + (t.speed_bps || 0), 0);
    const slots = this._slots();
    const drain = this._drain();

    const card = (label, value, sub, cls = "") => `
      <div class="qk-card ${cls}">
        <span class="qk-val">${Utils.escapeHtml(value)}</span>
        <span class="qk-lbl">${Utils.escapeHtml(label)}</span>
        <span class="qk-sub">${Utils.escapeHtml(sub)}</span>
      </div>`;

    const wrap = document.createElement("div");
    wrap.className = "qk-overview";
    wrap.innerHTML = [
      card(I18N.t("queue.running", "Running"), String(active.length),
        I18N.fmt("queue.of_slots", { n: active.length, m: slots }, "{n} of {m} slots"),
        active.length ? "tint-green" : ""),
      card(I18N.t("queue.waiting", "Waiting"), String(waiting.length),
        waiting.length
          // With the gate closed the waiting work is not "queued behind" the
          // running one — it is held.  Saying the wrong one would be the same
          // class of lie as showing a start time for work that cannot start.
          ? (App.state.queuePaused
            ? I18N.t("queue.held_by_pause", "held — the queue is paused")
            : I18N.t("queue.queued_behind", "queued behind them"))
          : I18N.t("queue.nothing_waiting", "nothing waiting")),
      card(I18N.t("queue.bandwidth", "Bandwidth"), Utils.formatSpeed(totalSpeed),
        I18N.t("queue.measured", "measured right now")),
      card(I18N.t("queue.drain", "Queue finishes in"),
        !drain ? "—" : (drain.exact ? Utils.formatETA(drain.seconds) : "≥ " + Utils.formatETA(drain.seconds)),
        App.state.queuePaused
          // The plan assumes the queue runs; while it is paused the estimate is
          // an "if you resume now" figure, so it must say so.
          ? I18N.t("queue.estimate_held", "estimate — the queue is paused")
          : !drain
            ? I18N.t("queue.unknown_eta", "not enough data yet")
            : drain.exact
              ? I18N.t("queue.estimate", "estimate")
              : I18N.t("queue.at_least", "at least — some sizes are unknown")),
    ].join("");
    return wrap;
  },

  /** Running list + waiting list + details panel. */
  _bodyEl() {
    const wrap = document.createElement("div");
    wrap.className = "qk-body";

    const left = document.createElement("div");
    left.className = "qk-col";
    left.append(this._runningEl(), this._waitingEl());

    wrap.append(left, this._detailsEl());
    return wrap;
  },

  _runningEl() {
    const active = this._active();
    const box = document.createElement("section");
    box.className = "qk-panel";
    box.innerHTML = `
      <header class="qk-panel-head">
        <h2>${Utils.escapeHtml(I18N.t("queue.running_now", "Running now"))}</h2>
        <span class="qk-count">${active.length}</span>
      </header>`;

    const body = document.createElement("div");
    body.className = "qk-list";
    if (!active.length) {
      body.appendChild(this._emptyRow(
        I18N.t("queue.no_active", "Nothing is downloading"),
        I18N.t("queue.no_active_sub", "Start a download or resume a paused one.")));
    } else {
      active.forEach((t) => body.appendChild(this._runningRow(t)));
    }
    box.appendChild(body);
    return box;
  },

  _runningRow(t) {
    const pct = t.total > 0 ? Utils.clamp((t.completed / t.total) * 100, 0, 100) : 0;
    const row = document.createElement("div");
    row.className = "qk-row qk-row-active";
    row.dataset.id = t.id;
    row.tabIndex = 0;
    row.innerHTML = `
      <div class="qk-main">
        <span class="qk-name" title="${Utils.escapeHtml(t.url || "")}">${Utils.escapeHtml(Utils.fileName(t))}</span>
        <span class="qk-meta">${Utils.escapeHtml(Utils.statusLabel(t.state))}${t.connections > 1 ? ` · ${t.connections} ${Utils.escapeHtml(I18N.t("col.connections", "connections").toLowerCase())}` : ""}</span>
      </div>
      <div class="qk-bar" role="progressbar" aria-valuenow="${pct.toFixed(0)}" aria-valuemin="0" aria-valuemax="100">
        <span style="width:${pct}%"></span>
      </div>
      <span class="qk-num">${t.total > 0 ? pct.toFixed(0) + "%" : "—"}</span>
      <span class="qk-num qk-dim">${Utils.formatSpeed(t.speed_bps)}</span>
      <span class="qk-num qk-dim">${t.eta_seconds != null ? Utils.formatETA(t.eta_seconds) : "—"}</span>`;
    row.addEventListener("click", () => this._focus(t.id));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this._focus(t.id); }
    });
    return row;
  },

  _waitingEl() {
    const waiting = this._waiting();
    const plan = this._planIndex();
    const diverges = this._prioritiesDiverge();

    const box = document.createElement("section");
    box.className = "qk-panel qk-panel-grow";
    box.innerHTML = `
      <header class="qk-panel-head">
        <h2>${Utils.escapeHtml(I18N.t("queue.up_next", "Waiting queue"))}</h2>
        <span class="qk-count">${waiting.length}</span>
        ${diverges ? this._orderToggleEl() : ""}
        ${this.state.order === "manual" ? `<button class="btn btn-ghost btn-sm" data-qk="move-top">${Utils.escapeHtml(I18N.t("queue.move_top", "Move to top"))}</button>
        <button class="btn btn-ghost btn-sm" data-qk="move-bottom">${Utils.escapeHtml(I18N.t("queue.move_bottom", "Move to bottom"))}</button>` : ""}
      </header>`;

    if (diverges) {
      const note = document.createElement("p");
      note.className = "qk-note";
      note.textContent = this.state.order === "start"
        ? I18N.t("queue.note_start", "Priority is deciding this order. Switch to Manual order to drag rows.")
        : I18N.t("queue.note_manual", "This is the list you control by dragging. Priority still decides who runs first.");
      box.appendChild(note);
    }

    const body = document.createElement("div");
    body.className = "qk-list qk-waiting";
    body.id = "queueWaiting";

    if (!waiting.length) {
      body.appendChild(this._emptyRow(
        I18N.t("queue.empty", "Nothing is waiting"),
        I18N.t("queue.empty_sub", "Every download is running or finished.")));
    } else {
      waiting.forEach((t, i) => body.appendChild(this._waitingRow(t, i, plan.get(t.id))));
    }
    box.appendChild(body);

    if (this.state.order === "manual" && waiting.length > 1) {
      const hint = document.createElement("p");
      hint.className = "qk-hint";
      hint.textContent = I18N.t("queue.drag_hint", "Drag a row to change the order it starts in.");
      box.appendChild(hint);
    }
    return box;
  },

  _orderToggleEl() {
    const mk = (mode, label) => `
      <button class="qk-seg${this.state.order === mode ? " on" : ""}" data-qk-order="${mode}"
        aria-pressed="${this.state.order === mode}">${Utils.escapeHtml(label)}</button>`;
    return `<div class="qk-seg-group" role="group" aria-label="${Utils.escapeHtml(I18N.t("queue.order_view", "Order view"))}">
      ${mk("start", I18N.t("queue.order_start", "Start order"))}
      ${mk("manual", I18N.t("queue.order_manual", "Manual order"))}
    </div>`;
  },

  _waitingRow(t, index, plan) {
    const row = document.createElement("div");
    row.className = "qk-row qk-row-wait";
    row.dataset.id = t.id;
    row.dataset.pos = String(index);
    row.tabIndex = 0;
    if (this.state.sel.has(t.id)) row.classList.add("sel");
    if (this.state.focus === t.id) row.classList.add("focus");
    if (this.state.drag && this.state.drag.id === t.id) row.classList.add("dragging");
    if (this.state.dropAt && this.state.dropAt.id === t.id) {
      row.classList.add(this.state.dropAt.after ? "drop-after" : "drop-before");
    }

    const draggable = this.state.order === "manual";
    row.draggable = draggable;

    // Effective start position — the badge only earns its space when it
    // disagrees with the row's visual slot (i.e. when priority is at work).
    const effPos = plan ? plan.position : null;
    const showEff = effPos != null && effPos !== index + 1;

    const starts = plan
      ? (plan.starts_immediately
        ? I18N.t("queue.starts_now", "starts now")
        : plan.estimated_start_seconds != null
          ? Utils.formatETA(plan.estimated_start_seconds)
          : I18N.t("queue.unknown", "unknown"))
      : "—";

    const remaining = t.total > 0
      ? Utils.formatSize(Math.max(0, t.total - t.completed))
      : I18N.t("queue.unknown", "unknown");

    row.innerHTML = `
      ${draggable ? '<span class="qk-grip" aria-hidden="true"></span>' : ""}
      <span class="qk-pos">${index + 1}</span>
      <div class="qk-main">
        <span class="qk-name" title="${Utils.escapeHtml(t.url || "")}">${Utils.escapeHtml(Utils.fileName(t))}</span>
        <span class="qk-meta">
          ${Utils.escapeHtml(this._priorityLabel(t.priority))}
          ${showEff ? `· <span class="qk-eff">${Utils.escapeHtml(I18N.fmt("queue.runs_at", { n: effPos }, "runs at #{n}"))}</span>` : ""}
          ${t.speed_limit_bps > 0 ? `· ${Utils.escapeHtml(I18N.t("queue.capped", "capped"))} ${Utils.formatSpeed(t.speed_limit_bps)}` : ""}
        </span>
      </div>
      <span class="qk-num qk-dim">${Utils.escapeHtml(remaining)}</span>
      <span class="qk-num qk-starts" title="${Utils.escapeHtml(I18N.t("queue.starts", "Starts"))}">${Utils.escapeHtml(starts)}</span>
      <div class="qk-row-acts">
        <button class="qk-mini" data-qk-act="priority" data-i18n-tip="queue.change_priority">${Utils.icon("flag", 14)}</button>
        <button class="qk-mini" data-qk-act="details" data-i18n-tip="queue.details">${Utils.icon("info", 14)}</button>
      </div>`;

    return row;
  },

  _priorityLabel(p) {
    if (p <= 3) return I18N.t("queue.pri_high", "High priority");
    if (p >= 8) return I18N.t("queue.pri_low", "Low priority");
    return I18N.t("queue.pri_normal", "Normal priority");
  },

  /**
   * The manual list position, rendered only when it differs from where the
   * task will actually start.
   *
   * With uniform priorities the two numbers are identical, so showing both
   * would be pure noise.  When they *do* differ, hiding the manual one would
   * make a drag & drop look like it did nothing — the exact confusion the two
   * clearly-labelled order views exist to prevent.  See docs/QUEUE.md §2.
   */
  _manualPosRow(t) {
    if (!(t.queue_position > 0) || !(t.queue_index >= 0)) return "";
    if (t.queue_position === t.queue_index + 1) return "";
    return `<div class="qk-kv"><dt>${Utils.escapeHtml(I18N.t("queue.kv_list_pos", "Position in list"))}</dt>`
      + `<dd>${t.queue_index + 1}</dd></div>`;
  },

  _emptyRow(title, sub) {
    const el = document.createElement("div");
    el.className = "qk-empty";
    el.innerHTML = `<strong>${Utils.escapeHtml(title)}</strong><span>${Utils.escapeHtml(sub)}</span>`;
    return el;
  },

  // ── Details panel ────────────────────────────────────────────────────

  _detailsEl() {
    const box = document.createElement("aside");
    box.className = "qk-details";
    const task = this.state.focus ? App.state.downloads[this.state.focus] : null;

    if (!task) {
      box.innerHTML = `<p class="qk-empty">${Utils.escapeHtml(I18N.t("queue.details_hint", "Select a download to see its details."))}</p>`;
      return box;
    }

    const tabs = [
      ["overview", I18N.t("queue.tab_overview", "Overview")],
      ["timeline", I18N.t("queue.tab_timeline", "Timeline")],
      ["advanced", I18N.t("queue.tab_advanced", "Advanced")],
    ];
    box.innerHTML = `
      <header class="qk-details-head">
        <span class="qk-details-name" title="${Utils.escapeHtml(task.url || "")}">${Utils.escapeHtml(Utils.fileName(task))}</span>
        <button class="qk-mini" data-qk-act="close-details" aria-label="${Utils.escapeHtml(I18N.t("dlg.close", "Close dialog"))}">${Utils.icon("x", 14)}</button>
      </header>
      <div class="qk-tabs" role="tablist">
        ${tabs.map(([id, label]) => `<button class="qk-tab${this.state.tab === id ? " on" : ""}" role="tab" data-qk-tab="${id}" aria-selected="${this.state.tab === id}">${Utils.escapeHtml(label)}</button>`).join("")}
      </div>
      <div class="qk-details-body">${this._detailsBody(task)}</div>`;

    box.querySelectorAll("[data-qk-tab]").forEach((btn) => {
      btn.addEventListener("click", () => { this.state.tab = btn.dataset.qkTab; this.render(); });
    });
    box.querySelector("[data-qk-act='close-details']")?.addEventListener("click", () => {
      this.state.focus = null; this.render();
    });
    return box;
  },

  _detailsBody(t) {
    const row = (label, value) => `
      <div class="qk-kv"><dt>${Utils.escapeHtml(label)}</dt><dd>${Utils.escapeHtml(value)}</dd></div>`;
    const plan = this._planIndex().get(t.id);
    const yes = I18N.t("dlg.yes", "Yes");
    const no = I18N.t("dlg.no", "No");
    const dash = "—";

    if (this.state.tab === "timeline") {
      return `<dl class="qk-kvs">
        ${row(I18N.t("queue.kv_created", "Added"), Utils.formatDate(t.created_at))}
        ${row(I18N.t("queue.kv_started", "Started"), t.started_at ? Utils.formatDate(t.started_at) : dash)}
        ${row(I18N.t("queue.kv_finished", "Finished"), t.finished_at ? Utils.formatDate(t.finished_at) : dash)}
        ${row(I18N.t("queue.kv_retries", "Retries"), String(t.retry_count || 0))}
        ${plan ? row(I18N.t("queue.kv_starts_in", "Estimated start"), plan.estimated_start_seconds == null ? I18N.t("queue.unknown", "unknown") : Utils.formatETA(plan.estimated_start_seconds)) : ""}
      </dl>`;
    }

    if (this.state.tab === "advanced") {
      return `<dl class="qk-kvs">
        ${row(I18N.t("queue.kv_priority", "Priority"), `${this._priorityLabel(t.priority)} (${t.priority})`)}
        ${row(I18N.t("queue.kv_cap", "Speed cap"), t.speed_limit_bps > 0 ? Utils.formatSpeed(t.speed_limit_bps) : I18N.t("dlg.unlimited", "Unlimited"))}
        ${row(I18N.t("queue.kv_conn_mode", "Connection mode"), t.connection_mode || dash)}
        ${row(I18N.t("queue.kv_threads", "Threads"), t.num_threads > 0 ? String(t.num_threads) : dash)}
        ${row(I18N.t("queue.kv_range", "Resumable"), t.supports_range ? yes : no)}
        ${row(I18N.t("queue.kv_checksum", "Checksum"), t.checksum || dash)}
        ${row(I18N.t("queue.kv_queue_pos", "Queue position"),
          t.queue_position > 0 ? `#${t.queue_position}` : dash)}
        ${this._manualPosRow(t)}
      </dl>`;
    }

    return `<dl class="qk-kvs">
      ${row(I18N.t("queue.kv_status", "Status"), Utils.statusLabel(t.state))}
      ${row(I18N.t("queue.kv_size", "Size"), t.total > 0 ? Utils.formatSize(t.total) : I18N.t("queue.unknown", "unknown"))}
      ${row(I18N.t("queue.kv_done", "Downloaded"), Utils.formatSize(t.completed))}
      ${row(I18N.t("queue.kv_speed", "Speed"), Utils.formatSpeed(t.speed_bps))}
      ${row(I18N.t("queue.kv_avg", "Average speed"), t.average_speed > 0 ? Utils.formatSpeed(t.average_speed) : dash)}
      ${row(I18N.t("queue.kv_eta", "Time left"), t.eta_seconds != null ? Utils.formatETA(t.eta_seconds) : dash)}
      ${row(I18N.t("queue.kv_category", "Category"), t.category || "General")}
      ${row(I18N.t("queue.kv_host", "Host"), Utils.hostOf(t.url))}
      ${row(I18N.t("queue.kv_server", "Server"), t.server || dash)}
      ${row(I18N.t("queue.kv_dir", "Save to"), t.directory || dash)}
      ${t.error ? row(I18N.t("queue.kv_error", "Error"), t.error) : ""}
    </dl>`;
  },

  // ── Recommendations ──────────────────────────────────────────────────
  //
  // Every entry is derived from something actually observed, and every action
  // is a button the user must press.  Nothing here changes a setting on its
  // own — in particular the queue never silently rewrites `max_concurrent`,
  // which would fight SmartOptimizer and surprise the user.

  _recommendations() {
    const recs = [];
    const settings = App.state.settings || {};
    const waiting = this._all().filter((t) => t.state === "Queued");
    const active = this._active();
    const slots = this._slots();

    // 1. Concurrency: only worth raising when there is real work queued and
    //    the engine is actually serialising it.
    const desired = Math.min(4, active.length + waiting.length);
    if (waiting.length > 0 && slots < desired) {
      recs.push({
        id: "slots",
        text: I18N.fmt("queue.rec_slots",
          { n: waiting.length, m: slots, k: desired },
          "{n} downloads are waiting while only {m} run at once. {k} parallel slots would let them start sooner."),
        action: {
          label: I18N.fmt("queue.rec_slots_apply", { k: desired }, "Use {k} slots"),
          run: () => this._applySlots(desired),
        },
      });
    }

    // 2. A priority spread that does nothing — the single most common way
    //    people get confused by the queue.
    if (waiting.length >= 3 && !this._prioritiesDiverge()) {
      recs.push({
        id: "priority",
        text: I18N.t("queue.rec_priority",
          "Every waiting download has the same priority, so they start in list order. Give one a higher priority to jump it ahead."),
      });
    }

    // 3. A global cap that is not the bottleneck.
    const cap = parseInt(settings.max_speed_bps, 10) || 0;
    const speed = active.reduce((s, t) => s + (t.speed_bps || 0), 0);
    if (cap > 0 && waiting.length > 0 && speed > 0 && speed < cap * 0.6) {
      recs.push({
        id: "cap",
        text: I18N.fmt("queue.rec_cap",
          { cap: Utils.formatSpeed(cap), speed: Utils.formatSpeed(speed) },
          "A {cap} limit is set but only {speed} is being used, so the limit is not what is holding the queue up."),
      });
    }

    return recs;
  },

  _recoEl() {
    const recs = this._recommendations();
    if (!recs.length) return document.createElement("div");

    const box = document.createElement("section");
    box.className = "qk-panel qk-reco";
    box.innerHTML = `
      <header class="qk-panel-head">
        <h2>${Utils.escapeHtml(I18N.t("queue.recommend", "Suggestions"))}</h2>
      </header>
      <div class="qk-reco-list">
        ${recs.map((r) => `
          <div class="qk-reco-item">
            <p>${Utils.escapeHtml(r.text)}</p>
            ${r.action ? `<button class="btn btn-ghost btn-sm" data-qk-reco="${r.id}">${Utils.escapeHtml(r.action.label)}</button>` : ""}
          </div>`).join("")}
      </div>`;

    box.querySelectorAll("[data-qk-reco]").forEach((btn) => {
      const rec = recs.find((r) => r.id === btn.dataset.qkReco);
      if (rec?.action) btn.addEventListener("click", () => rec.action.run());
    });
    return box;
  },

  async _applySlots(n) {
    const ok = await Components.confirm({
      title: I18N.t("queue.rec_slots_confirm", "Change parallel slots"),
      message: I18N.t("queue.rec_slots_confirm_msg",
        "Downloads running at the same time will change. SmartOptimizer still tunes each download's own connections.")
        + ` (${this._slots()} → ${n})`,
      okText: I18N.t("queue.rec_apply", "Apply"),
    });
    if (!ok) return;
    try {
      await API.updateSettings({ max_concurrent: n });
      App.state.settings = { ...(App.state.settings || {}), max_concurrent: n };
      Components.toast(I18N.t("queue.rec_applied", "Setting updated"), `${n}`, "success", 2400);
      this.render({ refreshPlan: true });
    } catch (e) {
      API.logJs("queue slots: " + String(e));
      Components.toast(
        I18N.t("toast.not_saved", "Not saved"),
        I18N.t("toast.not_saved_msg", "A setting could not be applied"),
        "error");
    }
  },

  // ── Interaction ──────────────────────────────────────────────────────

  _bindEvents(view) {
    // Everything is delegated from the stable #queueView container, because
    // the panels and rows inside it are rebuilt on every render.
    view.addEventListener("click", (e) => {
      const seg = e.target.closest("[data-qk-order]");
      if (seg) { this.setOrder(seg.dataset.qkOrder); return; }

      const act = e.target.closest("[data-qk-act]");
      if (act) {
        const row = act.closest(".qk-row");
        const id = row?.dataset.id;
        if (!id) return;
        e.stopPropagation();
        if (act.dataset.qkAct === "priority") this._editPriority(id);
        else if (act.dataset.qkAct === "details") this._focus(id);
        return;
      }

      const bulk = e.target.closest("[data-qk]");
      if (bulk) {
        // The gate banner's button is an action, not a list move.
        if (bulk.dataset.qk === "resume-queue") { App._resumeQueue(); return; }
        this._bulkMove(bulk.dataset.qk);
        return;
      }

      // A plain click anywhere on a waiting row selects it.
      if (e.target.closest(".qk-row-wait")) this._onRowClick(e);
    });

    view.addEventListener("keydown", (e) => this._onKeydown(e));
    this._bindDrag(view);
  },

  setOrder(mode) {
    const next = mode === "manual" ? "manual" : "start";
    if (next === this.state.order) return;
    this.state.order = next;
    this.state.sel.clear();
    this.state.anchor = null;
    this.state.dropAt = null;
    this.render();
  },

  /** Click / Ctrl+Click / Shift+Click selection over the waiting list. */
  _onRowClick(e) {
    const row = e.target.closest(".qk-row-wait");
    if (!row) return;
    const id = row.dataset.id;
    const rows = Array.from(Utils.$qa("#queueWaiting .qk-row-wait")).map((r) => r.dataset.id);
    const idx = rows.indexOf(id);
    const sel = this.state.sel;

    if (e.shiftKey && this.state.anchor != null) {
      const a = rows.indexOf(this.state.anchor);
      if (a !== -1) {
        const [lo, hi] = a <= idx ? [a, idx] : [idx, a];
        const range = rows.slice(lo, hi + 1);
        if (e.ctrlKey || e.metaKey) range.forEach((x) => sel.add(x));
        else { sel.clear(); range.forEach((x) => sel.add(x)); }
      }
    } else if (e.ctrlKey || e.metaKey) {
      if (sel.has(id)) sel.delete(id); else sel.add(id);
      this.state.anchor = id;
    } else {
      sel.clear(); sel.add(id);
      this.state.anchor = id;
    }
    if (!sel.size) this.state.anchor = null;
    this.state.focus = id;
    // The render rebuilds the rows, so focus has to be handed back to the row
    // the user just clicked — otherwise Ctrl+Up / Ctrl+Down and Enter would
    // act on a detached element and never reach the keydown handler.
    this.render().then(() => this._focusRowEl(id));
  },

  _focusRowEl(id) {
    const row = Utils.$qa("#queueWaiting .qk-row-wait").find((r) => r.dataset.id === id);
    if (row) row.focus({ preventScroll: true });
  },

  _focus(id) {
    this.state.focus = id;
    this.render();
  },

  /**
   * Keyboard reordering.
   *
   * Ctrl+Up / Ctrl+Down move the selection; because the move only makes sense
   * against the manual order, doing it from the Start-order view first flips
   * to Manual order and says so, mirroring how the Downloads list already
   * behaves when a sort would hide the reorder.
   */
  async _onKeydown(e) {
    if (App.state.page !== "queue") return;
    const ae = document.activeElement;
    const typing = !!(ae && (/^(input|textarea|select)$/i.test(ae.tagName || "") || ae.isContentEditable));
    if (typing) return;

    if (e.key === "Enter" && !e.ctrlKey) {
      if (this.state.focus) { e.preventDefault(); return; }
      const first = this._waiting()[0];
      if (first) { e.preventDefault(); this._focus(first.id); }
      return;
    }

    if (e.key === "Escape") {
      if (this.state.sel.size || this.state.focus) {
        e.preventDefault();
        this.state.sel.clear();
        this.state.anchor = null;
        this.state.focus = null;
        this.render();
      }
      return;
    }

    if (e.ctrlKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      const ids = this._selectedWaitingIds();
      if (!ids.length) return;
      e.preventDefault();
      await this._move(ids, e.key === "ArrowUp" ? -1 : 1);
      return;
    }

    if (e.ctrlKey && (e.key === "Home" || e.key === "End")) {
      const ids = this._selectedWaitingIds();
      if (!ids.length) return;
      e.preventDefault();
      await this._ensureManual();
      await API.reorderTasks(ids, e.key === "Home" ? 0 : null);
      await this._afterMove();
    }
  },

  _selectedWaitingIds() {
    const queued = new Set(this._waiting().map((t) => t.id));
    const ids = Array.from(this.state.sel).filter((id) => queued.has(id));
    if (ids.length) return ids;
    // No explicit selection: act on the focused row, which is what the user
    // is looking at.
    if (this.state.focus && queued.has(this.state.focus)) return [this.state.focus];
    return [];
  },

  async _ensureManual() {
    if (this.state.order === "manual") return;
    this.state.order = "manual";
    Components.toast(
      I18N.t("queue.switched_manual", "Showing manual order"),
      I18N.t("queue.switched_manual_msg", "Drag rows, or press Ctrl+Up / Ctrl+Down, to change the order downloads start in."),
      "info", 3200);
  },

  /** Move the selection by *delta* places within the manual order. */
  async _move(ids, delta) {
    await this._ensureManual();
    const rows = Array.from(Utils.$qa("#queueWaiting .qk-row-wait")).map((r) => r.dataset.id);
    const set = new Set(ids);
    // Forward for "up", reverse for "down", so a block keeps its own order.
    const seq = (delta < 0 ? rows : rows.slice().reverse()).filter((id) => set.has(id));

    for (const id of seq) {
      const current = rows.indexOf(id);
      const target = current + delta;
      if (target < 0 || target >= rows.length) continue;
      await API.moveTaskTo(id, target);
      // Mirror the move locally so a multi-row block shifts coherently.
      rows.splice(current, 1);
      rows.splice(target, 0, id);
    }
    await this._afterMove();
  },

  /** Move the selection to the very top / very bottom of the queue. */
  async _bulkMove(kind) {
    const ids = this._selectedWaitingIds();
    if (!ids.length) return;
    await this._ensureManual();
    if (kind === "move-top") {
      await API.reorderTasks(ids, 0);
    } else {
      for (const id of ids) await API.moveToBottom(id);
    }
    await this._afterMove();
  },

  async _afterMove() {
    await this.refreshPlan();
    // The snapshots carry `queue_index`, which drives the manual view; ask the
    // app to re-pull them so the list matches the backend immediately.
    try { await App._loadDownloads(); } catch (e) { /* poller will catch up */ }
    this.render();
  },

  async _editPriority(id) {
    const t = App.state.downloads[id];
    if (!t) return;
    const value = await Components.priorityDialog(t.priority ?? 5, Utils.fileName(t));
    if (value === null) return;
    try {
      await API.setPriority(id, value);
      await this._afterMove();
    } catch (e) {
      API.logJs("queue priority: " + String(e));
    }
  },

  // ── Drag & drop ──────────────────────────────────────────────────────

  _bindDrag(view) {
    view.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".qk-row-wait");
      if (!row || this.state.order !== "manual") { e.preventDefault(); return; }
      const id = row.dataset.id;
      this.state.drag = { id };
      row.classList.add("dragging");
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = "move";
        // Required for the drag to start at all in some engines.
        e.dataTransfer.setData("text/plain", id);
      }
    });

    view.addEventListener("dragover", (e) => {
      if (!this.state.drag) return;
      const row = e.target.closest(".qk-row-wait");
      if (!row) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "move";

      const id = row.dataset.id;
      if (id === this.state.drag.id) {
        if (this.state.dropAt) { this.state.dropAt = null; this._paintDrop(); }
        return;
      }
      const rect = row.getBoundingClientRect();
      const after = (e.clientY - rect.top) > rect.height / 2;
      const cur = this.state.dropAt;
      if (!cur || cur.id !== id || cur.after !== after) {
        this.state.dropAt = { id, after };
        this._paintDrop();
      }
    });

    view.addEventListener("drop", (e) => {
      if (!this.state.drag) return;
      e.preventDefault();
      const target = this.state.dropAt;
      // Read the dragged id before `_endDrag` clears the drag state — the drop
      // handler would otherwise dereference null.
      const dragId = this.state.drag.id;
      this._endDrag();
      if (!target) return;
      this._dropOn(target, dragId);
    });

    view.addEventListener("dragend", () => this._endDrag());
  },

  /** Repaint only the drop indicator — a full render would kill the drag. */
  _paintDrop() {
    Utils.$qa("#queueWaiting .qk-row-wait").forEach((row) => {
      row.classList.remove("drop-before", "drop-after");
      const at = this.state.dropAt;
      if (at && at.id === row.dataset.id) row.classList.add(at.after ? "drop-after" : "drop-before");
    });
  },

  _endDrag() {
    this.state.drag = null;
    this.state.dropAt = null;
    Utils.$qa("#queueWaiting .qk-row-wait").forEach((r) =>
      r.classList.remove("dragging", "drop-before", "drop-after"));
  },

  async _dropOn(target, dragId) {
    // A drag of a multi-row selection moves the whole selection; otherwise
    // only the row that was grabbed.
    const selected = this._selectedWaitingIds();
    const movingIds = selected.includes(dragId) ? selected : [dragId];

    const rows = Array.from(Utils.$qa("#queueWaiting .qk-row-wait")).map((r) => r.dataset.id);
    const rest = rows.filter((id) => !movingIds.includes(id));
    const anchorIdx = rest.indexOf(target.id);
    if (anchorIdx === -1) return;
    const position = target.after ? anchorIdx + 1 : anchorIdx;

    await API.reorderTasks(movingIds, position);
    this.state.sel = new Set(movingIds);
    await this._afterMove();
  },
};

// ── Bootstrap ─────────────────────────────────────────────────────────────
// Loaded after app.js, so `App` already exists by the time this runs.  The
// module owns its own startup rather than adding a line to app.js.

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => Queue.init());
} else {
  Queue.init();
}
