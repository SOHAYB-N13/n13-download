/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Components facade
   ═══════════════════════════════════════════════════════════════════════════

   Public facade over the individual component modules in js/components/.
   Every method keeps its pre-refactor name and signature, so all existing
   callers (App, Queue, core/*, tests) are untouched:

     Existing callers
           ↓
       Components   ← this facade
           ↓
   ToastUI / ModalUI / DialogsUI / ContextMenuUI /
   DownloadRowUI / RuleEditorUI / EmptyStateUI / VisualFX

   The implementation lives in the modules; this file only forwards.
   ═══════════════════════════════════════════════════════════════════════════ */

const Components = {
  // Toast (components/toast.js)
  toast: (...a) => ToastUI.toast(...a),

  // Modal (components/modal.js)
  showModal: (...a) => ModalUI.showModal(...a),
  closeModal: () => ModalUI.closeModal(),

  // Dialogs (components/dialogs.js)
  confirm: (...a) => DialogsUI.confirm(...a),
  linkPrompt: (...a) => DialogsUI.linkPrompt(...a),
  conflictPrompt: (...a) => DialogsUI.conflictPrompt(...a),
  renameDialog: (...a) => DialogsUI.renameDialog(...a),
  speedLimitDialog: (...a) => DialogsUI.speedLimitDialog(...a),
  schedulerDialog: (...a) => DialogsUI.schedulerDialog(...a),
  priorityDialog: (...a) => DialogsUI.priorityDialog(...a),
  propertiesDialog: (...a) => DialogsUI.propertiesDialog(...a),

  // Rule editor (components/rule-editor.js)
  ruleEditor: (...a) => RuleEditorUI.ruleEditor(...a),

  // Context menu (components/context-menu.js)
  showContextMenu: (...a) => ContextMenuUI.showContextMenu(...a),
  hideContextMenu: () => ContextMenuUI.hideContextMenu(),
  rowMenu: (...a) => ContextMenuUI.rowMenu(...a),
  selectionActions: (...a) => ContextMenuUI.selectionActions(...a),

  // Download row (components/download-row.js)
  renderRow: (...a) => DownloadRowUI.renderRow(...a),
  updateRow: (...a) => DownloadRowUI.updateRow(...a),

  // Empty states & skeletons (components/empty-state.js)
  emptyState: (...a) => EmptyStateUI.emptyState(...a),
  skeletonRows: (...a) => EmptyStateUI.skeletonRows(...a),

  // Visual effects (components/empty-state.js)
  countUp: (...a) => VisualFX.countUp(...a),
  sparkline: (...a) => VisualFX.sparkline(...a),
  initRipple: () => VisualFX.initRipple(),
};

const C = Components;
