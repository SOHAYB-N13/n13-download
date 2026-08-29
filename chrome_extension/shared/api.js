/** Communication layer with the local N13 application. */
(function (global) {
  const DEFAULT_SERVER = "http://127.0.0.1:6868";
  const PROTOCOL = "dldm";
  const MAX_BODY_BYTES = 64 * 1024;

  class N13API {
    constructor() {
      this._config = null;
    }

    async loadConfig() {
      try {
        const res = await fetch(chrome.runtime.getURL("token.json"), { cache: "no-store" });
        if (res.ok) this._config = await res.json();
      } catch (_) {}
      if (!this._config) this._config = { live_server_url: DEFAULT_SERVER, token: "" };
      if (this._config.live_server_url && !this._config.live_server_url.startsWith("http")) {
        this._config.live_server_url = DEFAULT_SERVER;
      }
      return this._config;
    }

    _baseUrl() {
      const cfg = this._config || {};
      // token.json historically stores live_server_url WITH the /download path.
      // Strip it so endpoints built here (/health, /download, /download_many)
      // resolve against the server root.
      let base = (cfg.live_server_url || DEFAULT_SERVER).replace(/\/+$/, "");
      if (base.endsWith("/download")) {
        base = base.slice(0, -"/download".length).replace(/\/+$/, "");
      }
      return base;
    }

    _token() {
      return (this._config && this._config.token) || "";
    }

    _headers() {
      const token = this._token();
      return {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
        "X-TDM-Token": token,
      };
    }

    /** Quick health check; returns true if N13 Live Server is reachable. */
    async isConnected(timeoutMs = 1200) {
      if (!this._config) await this.loadConfig();
      const token = this._token();
      if (!token) return false;
      try {
        const ctrl = new AbortController();
        const id = setTimeout(() => ctrl.abort(), timeoutMs);
        const res = await fetch(`${this._baseUrl()}/health`, {
          signal: ctrl.signal,
          headers: { "Authorization": `Bearer ${token}`, "X-TDM-Token": token },
        });
        clearTimeout(id);
        return res.ok;
      } catch (_) {
        return false;
      }
    }

    /** Send one or more URLs to N13. */
    async send(urls, autostart = true) {
      if (!this._config) await this.loadConfig();
      const token = this._token();
      if (!token) return { ok: false, reason: "missing_token" };

      const list = (Array.isArray(urls) ? urls : [urls])
        .map((u) => (u || "").trim())
        .filter((u) => /^https?:\/\//i.test(u));
      if (!list.length) return { ok: false, reason: "no_urls" };

      const isBatch = list.length > 1;
      const endpoint = isBatch ? `${this._baseUrl()}/download_many` : `${this._baseUrl()}/download`;
      const body = isBatch ? JSON.stringify({ urls: list, autostart }) : JSON.stringify({ url: list[0], autostart });
      if (body.length > MAX_BODY_BYTES) return { ok: false, reason: "too_large" };

      try {
        const res = await fetch(endpoint, {
          method: "POST",
          headers: this._headers(),
          body,
        });
        if (res.ok) return { ok: true, count: list.length };
        return { ok: false, reason: "http_error", status: res.status };
      } catch (_) {
        return { ok: false, reason: "network" };
      }
    }

    /**
     * Legacy fallback: dispatch a single URL via the dldm:// protocol.
     * Silent by design — a hidden iframe first, the CURRENT tab as fallback
     * (never chrome.tabs.create).  The current architecture delivers via the
     * authenticated HTTP API; this exists only for historical compatibility.
     */
    async sendViaProtocol(url) {
      url = (url || "").trim();
      if (!/^https?:\/\//i.test(url)) return false;
      const protocolUrl = `${PROTOCOL}://${encodeURIComponent(url)}`;

      // 1) hidden iframe via the active page's content script
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab && tab.id != null) {
          const resp = await new Promise((resolve) => {
            chrome.tabs.sendMessage(tab.id, { action: "protocol_navigate", url: protocolUrl }, (r) => {
              resolve(chrome.runtime.lastError ? null : r);
            });
          });
          if (resp && resp.ok) return true;
        }
      } catch (err) { /* fall through to the tab fallback */ }

      // 2) fallback: reuse the current tab, restore the original page after
      //    a few seconds (the restore also cancels any prompt).
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || tab.id == null) return false;
        const previousUrl = tab.url || "";
        await chrome.tabs.update(tab.id, { url: protocolUrl });
        setTimeout(() => {
          if (previousUrl && /^https?:\/\//i.test(previousUrl)) {
            chrome.tabs.update(tab.id, { url: previousUrl }).catch(() => {});
          }
        }, 3000);
        return true;
      } catch (err) {
        console.warn("Protocol dispatch failed", err);
        return false;
      }
    }
  }

  global.N13_API = N13API;
})(typeof globalThis !== "undefined" ? globalThis : this);
