/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Component: Modal
   ═══════════════════════════════════════════════════════════════════════════

   Owns the shared modal shell (#modalOverlay / #modal): open/close
   lifecycle, backdrop click, Escape handling, the lightweight Tab focus
   trap and first-input autofocus.

   Extracted verbatim from components.js (Phase 3).  All dialogs resolve
   through `ModalUI.showModal`.
   ═══════════════════════════════════════════════════════════════════════════ */

/* global Utils, I18N */

const ModalUI = {
  showModal(contentHtml, options = {}) {
    const overlay = Utils.$id("modalOverlay");
    const modal = Utils.$id("modal");
    const { title = "", subtitle = "", width = 520, onClose = null } = options;

    modal.style.maxWidth = width + "px";
    modal.innerHTML = `
      <header class="modal-head">
        <div class="modal-headings">
          <h2 class="modal-title">${Utils.escapeHtml(title)}</h2>
          ${subtitle ? `<p class="modal-sub">${Utils.escapeHtml(subtitle)}</p>` : ""}
        </div>
        <button class="icon-btn modal-x" id="modalClose" aria-label="${I18N.t("dlg.close", "Close dialog")}">${Utils.icon("x", 16)}</button>
      </header>
      <div class="modal-body">${contentHtml}</div>
      <footer class="modal-foot" id="modalFoot"></footer>`;

    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");

    const api = {
      el: modal,
      setFooter(html) { Utils.$id("modalFoot").innerHTML = html; },
      qs(sel) { return modal.querySelector(sel); },
      close(result) {
        overlay.classList.remove("open");
        overlay.setAttribute("aria-hidden", "true");
        document.removeEventListener("keydown", onKey, true);
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        if (onClose) onClose(result);
      },
    };

    const prevFocus = document.activeElement;
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); api.close(); }
      if (e.key === "Tab") {
        // Lightweight focus trap.
        const focusables = Utils.$qa('button, input, select, textarea, [tabindex]:not([tabindex="-1"])', modal)
          .filter((n) => !n.disabled && n.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey, true);

    Utils.$id("modalClose").addEventListener("click", () => api.close());
    overlay.onmousedown = (e) => { if (e.target === overlay) api.close(); };

    const firstInput = modal.querySelector("input:not([type=hidden]), textarea, select");
    if (firstInput) setTimeout(() => firstInput.focus(), 60);

    return api;
  },

  closeModal() {
    const overlay = Utils.$id("modalOverlay");
    if (overlay) overlay.classList.remove("open");
  },
};
