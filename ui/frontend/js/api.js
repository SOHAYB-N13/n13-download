/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Python Bridge API
   ═══════════════════════════════════════════════════════════════════════════ */

const API = {
  _ready: false,

  get available() {
    return !!(window.pywebview && window.pywebview.api);
  },

  async _call(method, ...args) {
    try {
      if (this.available) {
        return await window.pywebview.api[method](...args);
      }
      return null;
    } catch (err) {
      if (!this._ready) return null;
      console.error(`API call ${method} failed:`, err);
      return null;
    }
  },

  async ready() {
    this._ready = true;
  },

  // ── Event polling ────────────────────────────────────────────────

  async pollEvents() { return this._call("poll_events"); },
  async getDownloads() { return this._call("get_downloads"); },
  async getDownload(id) { return this._call("get_download", id); },
  async getHistory() { return this._call("get_history"); },
  async clearHistory() { return this._call("clear_history"); },
  async removeHistoryEntry(taskId) { return this._call("remove_history_entry", taskId); },
  async clearFinished() { return this._call("clear_finished"); },

  // ── Download actions ──────────────────────────────────────────────

  async addDownload(url, directory, label, checksum, autostart, category) {
    return this._call("add_download", url, directory || "", label || "", checksum || "", autostart !== false, category || "");
  },
  async addBatch(urls, directory) { return this._call("add_batch", urls, directory); },
  async pauseDownload(id) { return this._call("pause_download", id); },
  async resumeDownload(id) { return this._call("resume_download", id); },
  async cancelDownload(id) { return this._call("cancel_download", id); },
  async retryDownload(id) { return this._call("retry_download", id); },
  async removeDownload(id) { return this._call("remove_download", id); },
  async pauseAll() { return this._call("pause_all"); },
  async resumeAll() { return this._call("resume_all"); },
  async startTask(id) { return this._call("start_task", id); },
  async openFolder(id) { return this._call("open_folder", id); },
  async openFile(id) { return this._call("open_file", id); },
  async openFileFromHistory(entry) { return this._call("open_file_from_history", entry); },
  async redownload(id) { return this._call("redownload", id); },
  async openPath(path) { return this._call("open_path", path); },
  async deleteFile(taskId) { return this._call("delete_file", taskId); },
  async moveTask(id, delta) { return this._call("move_task", id, delta); },
  async setPriority(id, priority) { return this._call("set_priority", id, priority); },
  async retryFailed() { return this._call("retry_failed"); },
  async clearFailed() { return this._call("clear_failed"); },
  async clearCompleted() { return this._call("clear_completed"); },

  // ── URL validation & probing ──────────────────────────────────────

  async validateUrl(url) { return this._call("validate_url", url); },
  async probeUrl(url) { return this._call("probe_url", url); },
  async analyzeUrl(url) { return this._call("analyze_url", url); },

  // ── Settings ──────────────────────────────────────────────────────

  async getSettings() { return this._call("get_settings"); },
  async updateSettings(settings) { return this._call("update_settings", settings); },
  async selectDirectory() { return this._call("select_directory"); },
  async selectFile() { return this._call("select_file"); },
  async readTextFile(path) { return this._call("read_text_file", path); },

  // ── Theme / UI preferences ────────────────────────────────────────

  async getThemeConfig() { return this._call("get_theme_config"); },
  async saveThemeConfig(prefs) { return this._call("save_theme_config", prefs); },

  // ── Dashboard ─────────────────────────────────────────────────────

  async getStats() { return this._call("get_stats"); },
  async getSystemStats() { return this._call("get_system_stats"); },

  // ── Browser integration ───────────────────────────────────────────

  async startLiveServer() { return this._call("start_live_server"); },
  async stopLiveServer() { return this._call("stop_live_server"); },
  async liveServerStatus() { return this._call("live_server_status"); },
  async schedulerStatus() { return this._call("scheduler_status"); },
  async clipboardStatus() { return this._call("clipboard_status"); },
  async createExtension() { return this._call("create_extension"); },
  async registerProtocol() { return this._call("register_protocol"); },
  async unregisterProtocol() { return this._call("unregister_protocol"); },

  // ── Pattern scan ──────────────────────────────────────────────────

  async scanPattern(pattern, directory, start, padding) {
    return this._call("scan_pattern", pattern, directory || "", start ?? 1, padding ?? 2);
  },

  // ── Window controls (frameless) ───────────────────────────────────

  async winMinimize() { return this._call("window_minimize"); },
  async winToggleMaximize() { return this._call("window_toggle_maximize"); },
  async winClose() { return this._call("window_close"); },
  async winSetBounds(x, y, w, h) { return this._call("window_set_bounds", x, y, w, h); },

  // ── System ────────────────────────────────────────────────────────

  async getVersion() { return this._call("get_version"); },
  async shutdown() { return this._call("shutdown"); },
  async logJs(msg) { return this._call("log_js", String(msg)); },
};
