/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Components: Empty states & visual effects
   ═══════════════════════════════════════════════════════════════════════════

   Shared presentation helpers that do not belong to any one feature:
   empty states, skeleton rows, the count-up animation, the dashboard
   sparkline and the global ripple effect.

   Extracted verbatim from components.js (Phase 3).
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils */

const EmptyStateUI = {
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
};

const VisualFX = {
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
