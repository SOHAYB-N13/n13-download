/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Component: Toast
   ═══════════════════════════════════════════════════════════════════════════

   Transient notifications.  Owns the toast stack (#toastStack), the
   max-4 cap, per-toast lifetime timing, close/action buttons.

   Extracted verbatim from components.js (Phase 3).  Reached through the
   `Components` facade — call sites never import this object directly.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N */

const ToastUI = {
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
};
