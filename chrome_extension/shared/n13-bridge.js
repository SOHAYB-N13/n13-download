/**
 * N13 Bridge — the ONLY layer that talks to N13 Download Manager.
 *
 * Owns base-URL normalization, token authentication, timeouts, connection
 * state, and reconnect/backoff.  No other extension file does fetch()/tokens
 * against N13.
 *
 * Connection states (centralized state machine):
 *
 *   DISCONNECTED     — no server contact yet / server unreachable
 *   CONNECTING       — starting a connection attempt
 *   AUTHENTICATING   — an authenticated probe is in flight
 *   READY            — authenticated AND reachable (verified via a fresh
 *                      authenticated probe).  "AUTHORIZED" is equivalent.
 *   UNAUTHORIZED     — server reachable but the token was rejected (401)
 *   RECONNECTING     — re-establishing after a drop
 *   ERROR            — unexpected/unrecoverable state
 *
 * "READY" means AUTHENTICATED capability, not just reachability.  It is
 * verified with an authenticated probe: POST /download with an empty url
 * returns 400 ("Missing url") = token accepted, 401 = token rejected.
 *
 * Delivery results are truthful: {ok, accepted, rejected, total, partial,
 * reason} derived from the real server response, never from HTTP status alone.
 * An HTTP 200 is NOT proof of success: /download_many returns {accepted,
 * rejected} even on 200, and those counts are authoritative.
 */
(function (global) {
  "use strict";

  var DEFAULT_SERVER = "http://127.0.0.1:6868";
  var PROTOCOL = "dldm";
  var MAX_BODY_BYTES = 64 * 1024;
  var PROBE_TIMEOUT = 5000;     // connection/authentication probe
  var SEND_TIMEOUT = 15000;     // download delivery (single + batch)
  var LAUNCH_TAB_LINGER_MS = 3000;

  var STATES = {
    DISCONNECTED: "disconnected",
    CONNECTING: "connecting",
    AUTHENTICATING: "authenticating",
    READY: "ready",
    UNAUTHORIZED: "unauthorized",
    RECONNECTING: "reconnecting",
    ERROR: "error",
  };

  // Terminal "authenticated + reachable" states (AUTHORIZED ≡ READY).
  var GOOD_STATES = { ready: true, authorized: true, connected: true };

  function N13Bridge() {
    this._config = null;
    this._state = STATES.DISCONNECTED;
    this._listeners = [];
    this._connecting = false; // guards duplicate simultaneous connects
  }

  N13Bridge.STATES = STATES;

  N13Bridge.prototype.onStatusChange = function (cb) {
    if (typeof cb === "function") this._listeners.push(cb);
  };

  N13Bridge.prototype._notify = function () {
    var state = this._state;
    var self = this;
    this._listeners.forEach(function (cb) {
      try { cb(state, self); } catch (e) { /* ignore */ }
    });
  };

  N13Bridge.prototype._setState = function (state) {
    if (this._state === state) return;
    this._state = state;
    this._notify();
  };

  N13Bridge.prototype.getState = function () {
    return this._state;
  };

  N13Bridge.prototype.getStatus = function () {
    return { state: this._state, server: this._baseUrl() };
  };

  // ------------------------------------------------------------------ config

  N13Bridge.prototype.loadConfig = function () {
    var self = this;
    return new Promise(function (resolve) {
      try {
        fetch(chrome.runtime.getURL("token.json"), { cache: "no-store" })
          .then(function (res) { return res.ok ? res.json() : null; })
          .then(function (cfg) { resolve(cfg); })
          .catch(function () { resolve(null); });
      } catch (e) { resolve(null); }
    }).then(function (cfg) {
      if (!cfg || typeof cfg !== "object") cfg = { live_server_url: DEFAULT_SERVER, token: "" };
      if (cfg.live_server_url && !/^http/i.test(String(cfg.live_server_url))) {
        cfg.live_server_url = DEFAULT_SERVER;
      }
      self._config = cfg;
      return cfg;
    });
  };

  N13Bridge.prototype._baseUrl = function () {
    var cfg = this._config || {};
    // Normalize: token.json may carry the server root or ".../download".  Strip
    // a trailing /download so /health, /download, /download_many are always
    // built against the root — /download/download can never occur.
    var base = String(cfg.live_server_url || DEFAULT_SERVER).replace(/\/+$/, "");
    if (base.slice(-"/download".length) === "/download") {
      base = base.slice(0, -"/download".length).replace(/\/+$/, "");
    }
    return base;
  };

  N13Bridge.prototype._token = function () {
    return (this._config && this._config.token) || "";
  };

  N13Bridge.prototype._headers = function () {
    var token = this._token();
    return {
      "Content-Type": "application/json",
      "Authorization": "Bearer " + token,
      "X-TDM-Token": token,
    };
  };

  N13Bridge.prototype._request = function (endpoint, body, timeoutMs) {
    var self = this;
    return new Promise(function (resolve) {
      var ctrl = new AbortController();
      var id = setTimeout(function () { ctrl.abort(); }, timeoutMs || PROBE_TIMEOUT);
      try {
        fetch(endpoint, {
          method: "POST",
          headers: self._headers(),
          body: body,
          signal: ctrl.signal,
        })
          .then(function (res) {
            clearTimeout(id);
            res.text().then(function (text) {
              var payload = null;
              try { payload = JSON.parse(text); } catch (e) { payload = null; }
              resolve({ status: res.status, payload: payload, ok: res.ok, reason: "" });
            });
          })
          .catch(function (err) {
            clearTimeout(id);
            resolve({
              status: 0,
              payload: null,
              ok: false,
              reason: err && err.name === "AbortError" ? "timeout" : "network",
            });
          });
      } catch (e) {
        clearTimeout(id);
        resolve({ status: 0, payload: null, ok: false, reason: "network" });
      }
    });
  };

  // ------------------------------------------------------------------ auth

  /**
   * Authenticated probe: POST /download with an empty url.
   *   - 400  => token accepted (server reachable + authorized)   -> READY
   *   - 200  => accepted (defensive)                            -> READY
   *   - 401  => token rejected                                   -> UNAUTHORIZED
   *   - 0    => network/timeout (server down)                    -> DISCONNECTED
   *   - other => unexpected response                             -> ERROR
   */
  N13Bridge.prototype._probe = function () {
    var self = this;
    return this._request(this._baseUrl() + "/download", JSON.stringify({ url: "", autostart: false }))
      .then(function (res) {
        if (res.status === 400 || res.status === 200) return STATES.READY;
        if (res.status === 401) return STATES.UNAUTHORIZED;
        if (res.status === 0) return STATES.DISCONNECTED;
        return STATES.ERROR;
      });
  };

  /** Establish/refresh the connection and authentication state. */
  N13Bridge.prototype.connect = function () {
    var self = this;
    if (this._connecting) return Promise.resolve(this.getStatus());
    this._connecting = true;
    this._setState(STATES.CONNECTING);
    // Always re-read token.json so a freshly-synced token (written by N13 on
    // startup) is picked up even if this service worker survived a token
    // change.  This is the auth recovery path for a stale token.
    return this.loadConfig().then(function () {
      if (!self._token()) { self._setState(STATES.UNAUTHORIZED); return self.getStatus(); }
      self._setState(STATES.AUTHENTICATING);
      return self._probe().then(function (state) {
        self._setState(state);
        return self.getStatus();
      });
    }).catch(function () {
      self._setState(STATES.ERROR);
      return self.getStatus();
    }).finally(function () {
      self._connecting = false;
    });
  };

  /** Re-read token.json (auth recovery) without changing the current state. */
  N13Bridge.prototype.refreshConfig = function () {
    this._config = null;
    return this.loadConfig();
  };

  /** Synonym kept for the architectural contract: authenticate() === connect(). */
  N13Bridge.prototype.authenticate = function () {
    return this.connect();
  };

  /** True only when the connection is authenticated (with a fresh probe). */
  N13Bridge.prototype.isReady = function () {
    var self = this;
    return this._probe().then(function (state) {
      self._setState(state);
      return GOOD_STATES[state] === true;
    });
  };

  /** Alias for isReady (authenticated reachability). */
  N13Bridge.prototype.isConnected = function () {
    return this.isReady();
  };

  N13Bridge.prototype.healthCheck = function () {
    var self = this;
    return this._probe().then(function (state) {
      self._setState(state);
      return GOOD_STATES[state] === true;
    });
  };

  N13Bridge.prototype.reconnect = function () {
    this._setState(STATES.RECONNECTING);
    return this.connect();
  };

  N13Bridge.prototype.disconnect = function () {
    this._setState(STATES.DISCONNECTED);
  };

  /**
   * Poll the authenticated probe until the server is READY, the token is
   * rejected, or the timeout elapses.  Used after launching N13 so delivery
   * waits for the real API instead of guessing.  Returns the final status.
   */
  N13Bridge.prototype.waitUntilReady = function (timeoutMs, intervalMs) {
    var self = this;
    timeoutMs = timeoutMs || 30000;
    intervalMs = intervalMs || 500;
    var deadline = Date.now() + timeoutMs;
    var step = function () {
      return self._probe().then(function (state) {
        self._setState(state);
        if (state === STATES.READY) return self.getStatus();
        if (state === STATES.UNAUTHORIZED || state === STATES.ERROR) return self.getStatus();
        if (Date.now() >= deadline) {
          self._setState(STATES.ERROR);
          return self.getStatus();
        }
        return new Promise(function (resolve) {
          setTimeout(resolve, intervalMs);
        }).then(step);
      });
    };
    this._setState(STATES.RECONNECTING);
    return step();
  };

  // ----------------------------------------------------------------- send

  N13Bridge.prototype.sendDownload = function (url) {
    var self = this;
    var cfg = this._config ? Promise.resolve() : this.loadConfig();
    return cfg.then(function () {
      if (!self._token()) {
        return { ok: false, accepted: 0, rejected: 1, total: 1, partial: false, reason: "unauthorized" };
      }
      var body = JSON.stringify({ url: String(url || "").trim(), autostart: true });
      return self._request(self._baseUrl() + "/download", body, SEND_TIMEOUT).then(function (res) {
        return self._mapResult(res, 1, false);
      });
    });
  };

  N13Bridge.prototype.sendBatch = function (urls) {
    var self = this;
    var list = (Array.isArray(urls) ? urls : [urls])
      .map(function (u) { return String(u || "").trim(); })
      .filter(function (u) { return /^https?:\/\//i.test(u); });
    list = Array.from(new Set(list));
    var cfg = this._config ? Promise.resolve() : this.loadConfig();
    return cfg.then(function () {
      if (!list.length) {
        return { ok: false, accepted: 0, rejected: 0, total: 0, partial: false, reason: "no_urls" };
      }
      if (!self._token()) {
        return { ok: false, accepted: 0, rejected: list.length, total: list.length, partial: false, reason: "unauthorized" };
      }
      var body = JSON.stringify({ urls: list, autostart: true });
      if (body.length > MAX_BODY_BYTES) {
        return { ok: false, accepted: 0, rejected: list.length, total: list.length, partial: false, reason: "too_large" };
      }
      return self._request(self._baseUrl() + "/download_many", body, SEND_TIMEOUT).then(function (res) {
        return self._mapResult(res, list.length, true);
      });
    });
  };

  /**
   * Map a raw HTTP result to a truthful delivery result.  HTTP 200 is never
   * treated as complete success by itself; the server's accepted/rejected
   * counts (or the single-endpoint semantics) are authoritative.
   */
  N13Bridge.prototype._mapResult = function (res, total, isBatch) {
    if (res.status === 401) {
      return { ok: false, accepted: 0, rejected: total, total: total, partial: false, reason: "unauthorized" };
    }
    if (res.status === 0) {
      return { ok: false, accepted: 0, rejected: total, total: total, partial: false, reason: res.reason || "network" };
    }
    var p = res.payload || {};
    if (!res.ok) {
      // HTTP error other than 401/network.  400 = N13 rejected the URL.
      var reason = res.status === 400 ? "rejected" : "http_error";
      return { ok: false, accepted: 0, rejected: total, total: total, partial: false, reason: reason };
    }

    var accepted = 0;
    var rejected = 0;
    if (typeof p.accepted === "number") {
      accepted = p.accepted;
    } else if (!isBatch) {
      accepted = 1; // /download 200 == queued
    }
    if (typeof p.rejected === "number") {
      rejected = p.rejected;
    } else {
      rejected = Math.max(0, total - accepted);
    }
    return {
      ok: accepted > 0,
      accepted: accepted,
      rejected: rejected,
      total: total,
      partial: accepted > 0 && accepted < total,
      reason: accepted > 0 ? (accepted < total ? "partial" : "") : "rejected",
    };
  };

  /**
   * Controlled protocol launch used ONLY to START N13 when it is genuinely
   * unavailable.  It is never used to deliver a download URL.  The launch tab
   * is closed shortly after to avoid leaking a blank tab.
   */
  N13Bridge.prototype.launchApp = function () {
    return new Promise(function (resolve) {
      try {
        chrome.tabs.create({ url: PROTOCOL + "://launch", active: false }, function (tab) {
          if (chrome.runtime.lastError) {
            void chrome.runtime.lastError;
            resolve(false);
            return;
          }
          if (!tab || !tab.id) {
            resolve(false);
            return;
          }
          // The protocol launch may dismiss/close the tab itself; the linger
          // cleanup must tolerate the tab already being gone (never surface an
          // "Unchecked runtime.lastError: No tab with id").
          setTimeout(function () {
            chrome.tabs.remove(tab.id, function () {
              void chrome.runtime.lastError; // tab may already be gone
            });
          }, LAUNCH_TAB_LINGER_MS);
          resolve(true);
        });
      } catch (e) {
        resolve(false);
      }
    });
  };

  /** Alias kept for the architectural contract: launchN13IfNeeded(). */
  N13Bridge.prototype.launchN13IfNeeded = function () {
    return this.launchApp();
  };

  global.N13Bridge = N13Bridge;
})(typeof globalThis !== "undefined" ? globalThis : this);
