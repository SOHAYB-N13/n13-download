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

    /** Fallback: send a single URL via the dldm:// custom protocol. */
    async sendViaProtocol(url) {
      url = (url || "").trim();
      if (!/^https?:\/\//i.test(url)) return false;
      const encoded = encodeURIComponent(url);
      const protocolUrl = `${PROTOCOL}://${encoded}`;

      try {
        const tab = await chrome.tabs.create({ url: "about:blank", active: false });
        if (!tab || !tab.id) throw new Error("No tab");
        await chrome.tabs.update(tab.id, { url: protocolUrl });
        // Navigating a tab to a custom protocol can make Chrome close that tab.
        // chrome.tabs.remove() returns a Promise whose rejection is NOT caught
        // by try/catch; handle it so a gone tab never becomes an uncaught
        // "No tab with id" promise rejection.
        setTimeout(() => {
          chrome.tabs.remove(tab.id).catch(() => {});
        }, 1500);
        return true;
      } catch (err) {
        console.warn("Protocol dispatch failed", err);
        return false;
      }
    }
  }

  global.N13_API = N13API;
})(typeof globalThis !== "undefined" ? globalThis : this);
