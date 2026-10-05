"""Headless render test for the auto-shutdown UI.

Loads the real ``index.html`` in headless Chrome with a stubbed
``window.pywebview.api`` and drives the auto-shutdown runtime state through the
*real* backend event pipeline (``poll_events`` → ``Events.handleEvent`` →
``Events.applyAutoShutdown``), then asserts on the rendered DOM.

The single most important assertion here: the queue strip must render the
controller's **runtime state**, not the ``shutdown_when_done`` preference.  The
stub deliberately reports ``settings.shutdown_when_done = false`` while the
runtime state is ``Waiting`` — a UI that reads the flag would render "Off".

Skipped automatically when Chrome/Edge is not installed.

Run:  ./.venv/Scripts/python.exe -m pytest tests/test_auto_shutdown_ui.py -q
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "ui" / "frontend"
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
CHROME = next((p for p in CHROME_CANDIDATES if p.exists()), None)

pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Edge available")

#: Runtime status the backend reports on first paint: waiting, enabled — while
#: the *preference* in settings is off.  The two must not be conflated.
WAITING_STATUS = {
    "enabled": True,
    "state": "Waiting",
    "supported": True,
    "pending": False,
    "countdown_seconds": 0,
    "seconds_remaining": None,
    "deadline": None,
    "reason": "active_task",
    "cancel_reason": "",
    "blocked_reason": "active_task",
    "last_error": "",
    "policy": {"allow_failures": False, "allow_cancelled": False},
    "session": {"session_id": "s1", "state": "Waiting", "countdown_started_at": None,
                "countdown_deadline": None, "queue": {}, "policy": {}},
}

STUB = """
<script>
(function () {
  var STATUS = __STATUS__;
  var SETTINGS = { max_concurrent: 1, max_speed_bps: 0, scheduler_enabled: false, schedule_days: [],
                   schedule_start_time: "00:00", schedule_stop_time: "00:00", language: "en",
                   num_threads: 16, connection_mode: "smart", download_dir: "C:/Downloads",
                   // Deliberately OFF, while the runtime state is Waiting.
                   shutdown_when_done: false };
  var calls = [];
  var events = [];
  window.__calls = calls;
  window.__events = events;
  window.__pushEvent = function (e) { events.push(e); };
  function fn(name, value) {
    return function () {
      calls.push(name);
      return Promise.resolve(typeof value === "function" ? value.apply(null, arguments) : value);
    };
  }
  window.pywebview = { api: {
    poll_events: function () {
      calls.push("poll_events");
      var out = events.slice();
      events.length = 0;
      return Promise.resolve(out);
    },
    get_downloads: fn("get_downloads", []),
    get_download: fn("get_download", null),
    get_history: fn("get_history", []),
    get_settings: fn("get_settings", SETTINGS),
    get_theme_config: fn("get_theme_config", {}),
    get_version: fn("get_version", "1.0.0"),
    get_stats: fn("get_stats", {}),
    get_system_stats: fn("get_system_stats", {}),
    get_analytics: fn("get_analytics", {}),
    get_update_settings: fn("get_update_settings", {}),
    get_update_state: fn("get_update_state", {}),
    scheduler_status: fn("scheduler_status", {}),
    live_server_status: fn("live_server_status", { running: false }),
    clipboard_status: fn("clipboard_status", {}),
    queue_plan: fn("queue_plan", []),
    // The auto-shutdown surface.
    get_auto_shutdown_status: fn("get_auto_shutdown_status", STATUS),
    set_auto_shutdown: fn("set_auto_shutdown", STATUS),
    cancel_auto_shutdown: fn("cancel_auto_shutdown", {
      enabled: false, state: "Disabled", supported: true, pending: false,
      countdown_seconds: 0, seconds_remaining: null, deadline: null,
      reason: "", cancel_reason: "user_cancelled", blocked_reason: "",
      last_error: "", policy: {}, session: {}
    }),
    update_settings: fn("update_settings", true),
    log_js: fn("log_js", null),
    check_for_updates: fn("check_for_updates", null)
  } };
})();
</script>
"""

DRIVER = """
<script>
(function () {
  var out = { errors: [] };
  window.addEventListener("error", function (e) { out.errors.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function (e) {
    out.errors.push("rejection: " + String(e.reason && e.reason.stack || e.reason));
  });

  function calls() { return window.__calls || []; }
  function strip() { return document.getElementById("qsShutdownBtn"); }
  function banner() { return document.getElementById("qsShutdownBanner"); }
  function txt(id) { var e = document.getElementById(id); return e ? e.textContent : null; }
  function toasts() {
    return Array.prototype.slice.call(document.querySelectorAll("#toastStack .toast")).map(function (t) {
      var title = t.querySelector(".toast-title");
      var msg = t.querySelector(".toast-msg");
      return { type: t.className, title: title ? title.textContent : "",
               msg: msg ? msg.textContent : "" };
    });
  }

  function snapshot(label) {
    out[label] = {
      btn: strip() ? strip().textContent.trim() : null,
      btnTip: strip() ? (strip().getAttribute("data-tip") || strip().getAttribute("title") || "") : null,
      bannerHidden: banner() ? banner().hidden : null,
      count: txt("qsShutdownCount"),
      msg: txt("qsShutdownMsg"),
      state: App.state.autoShutdown ? App.state.autoShutdown.state : null,
      enabled: App.state.autoShutdown ? App.state.autoShutdown.enabled : null,
      settingsFlag: (App.state.settings || {}).shutdown_when_done,
      toasts: toasts()
    };
  }

  // Dispatch exactly what the 200ms poll loop dispatches:
  // `Events.startPolling` calls `app._handleEvent(evt)` per event.
  // Driving it directly keeps the test independent of requestAnimationFrame
  // scheduling; `test_poll_loop_is_live` below separately proves the loop
  // itself is running and draining the queue.
  function dispatch(status) {
    App._handleEvent({ type: "auto_shutdown_state", status: status });
  }

  var steps = [];

  steps.push(function () { App.navigate("downloads"); });

  // 1. First paint: runtime state Waiting, preference off.
  steps.push(function () { snapshot("initial"); });

  // 2. The backend reports a live countdown.
  steps.push(function () {
    dispatch({
      enabled: true, state: "Countdown", supported: true, pending: true,
      countdown_seconds: 60, seconds_remaining: 60,
      deadline: Date.now() / 1000 + 60,
      reason: "", cancel_reason: "", blocked_reason: "", last_error: "",
      policy: {}, session: {}
    });
  });
  steps.push(function () { snapshot("countdown"); });

  // 3. A new download invalidates the countdown: the abort must be explained.
  steps.push(function () {
    dispatch({
      enabled: true, state: "Cancelled", supported: true, pending: false,
      countdown_seconds: 0, seconds_remaining: null, deadline: null,
      reason: "new_download", cancel_reason: "new_download", blocked_reason: "",
      last_error: "", policy: {}, session: {}
    });
  });
  steps.push(function () { snapshot("cancelled"); });

  // 4. A fresh countdown, then the user clicks "Cancel shutdown".
  steps.push(function () {
    dispatch({
      enabled: true, state: "Countdown", supported: true, pending: true,
      countdown_seconds: 45, seconds_remaining: 45,
      deadline: Date.now() / 1000 + 45,
      reason: "", cancel_reason: "", blocked_reason: "", last_error: "",
      policy: {}, session: {}
    });
  });
  steps.push(function () {
    out.mark = calls().length;
    var b = document.getElementById("qsShutdownCancel");
    if (b) b.click();
  });
  steps.push(function () {
    out.cancelCalls = calls().slice(out.mark).filter(function (c) {
      return c === "cancel_auto_shutdown";
    }).length;
    snapshot("afterCancel");
  });

  // 5. A blocked state (failed downloads) must be explainable.
  steps.push(function () {
    dispatch({
      enabled: true, state: "Blocked", supported: true, pending: false,
      countdown_seconds: 0, seconds_remaining: null, deadline: null,
      reason: "task_failed", cancel_reason: "", blocked_reason: "task_failed",
      last_error: "", policy: {}, session: {}
    });
  });
  steps.push(function () { snapshot("blocked"); });

  // 6. The Settings page must expose the full policy, not just the toggle.
  steps.push(function () { App.navigate("settings"); });
  steps.push(function () {
    out.settingKeys = Array.prototype.slice
      .call(document.querySelectorAll("[data-key]"))
      .map(function (el) { return el.getAttribute("data-key"); })
      .filter(function (k) { return k && k.indexOf("shutdown_") === 0; });
    var num = document.getElementById("set-shutdown_countdown_seconds");
    out.countdownInput = num
      ? { type: num.type, min: num.getAttribute("min"), max: num.getAttribute("max") }
      : null;
  });

  // 7. The poll loop is live and drains the backend event queue.
  steps.push(function () {
    out.pollsBefore = calls().filter(function (c) { return c === "poll_events"; }).length;
    window.__pushEvent({ type: "auto_shutdown_state", status: {
      enabled: true, state: "Armed", supported: true, pending: false,
      countdown_seconds: 0, seconds_remaining: null, deadline: null,
      reason: "no_workload", cancel_reason: "", blocked_reason: "",
      last_error: "", policy: {}, session: {}
    } });
  });
  steps.push(function () {
    out.pollsAfter = calls().filter(function (c) { return c === "poll_events"; }).length;
    out.eventsLeftQueued = (window.__events || []).length;
  });

  var i = 0;
  function finish() { document.title = "ASRESULT" + JSON.stringify(out) + "ENDAS"; }
  function next() {
    if (i >= steps.length) { finish(); return; }
    var n = i++;
    try { steps[n](); } catch (e) { out.errors.push("step" + n + ": " + e + " | " + e.stack); }
    setTimeout(next, 350);
  }
  setTimeout(next, 900);
})();
</script>
"""


def build_page() -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    stub = STUB.replace("__STATUS__", json.dumps(WAITING_STATUS))
    anchor = '<script src="js/utils.js"></script>'
    assert anchor in html, "index.html no longer loads utils.js where expected"
    html = html.replace(anchor, stub + anchor, 1)
    assert "</body>" in html
    html = html.replace("</body>", DRIVER + "</body>", 1)
    return html


def _write_preview(html: str) -> Path:
    base = FRONTEND.as_uri()
    html = re.sub(
        r'\b(src|href)="(js|css)/',
        lambda m: '%s="%s/%s/' % (m.group(1), base, m.group(2)),
        html,
    )
    out = Path(tempfile.mkdtemp(prefix="n13-as-preview-")) / "preview.html"
    out.write_text(html, encoding="utf-8")
    return out


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def render() -> dict:
    page = _write_preview(build_page())
    profile = Path(tempfile.mkdtemp())
    try:
        proc = subprocess.run(
            [
                str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
                "--allow-file-access-from-files", "--virtual-time-budget=20000",
                "--user-data-dir=" + str(profile), "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        _remove(page)

    m = re.search(r"ASRESULT(.*?)ENDAS", proc.stdout, re.S)
    if not m:
        pytest.fail(
            "the page never reported a result.\n"
            f"--- stdout ---\n{proc.stdout[:4000]}\n--- stderr ---\n{proc.stderr[:2000]}"
        )
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def dom() -> dict:
    return render()


def test_no_script_errors(dom):
    assert dom["errors"] == []


def test_strip_renders_runtime_state_not_the_preference_flag(dom):
    """`shutdown_when_done` is false, yet the state is Waiting — show Waiting."""
    initial = dom["initial"]
    assert initial["settingsFlag"] is False
    assert initial["enabled"] is True
    assert initial["state"] == "Waiting"
    assert initial["btn"] == "Waiting", (
        "the strip must render the runtime state, not the preference flag"
    )
    assert "Downloads are still running" in (initial["btnTip"] or "")


def test_countdown_shows_a_visible_banner_with_the_remaining_time(dom):
    cd = dom["countdown"]
    assert cd["state"] == "Countdown"
    assert cd["bannerHidden"] is False
    assert cd["count"] and cd["count"].endswith("s")
    assert "shut" in (cd["msg"] or "").lower()
    assert cd["btn"] == "Shutting down"
    titles = [t["title"] for t in cd["toasts"]]
    assert any("Shutting down" in t for t in titles), titles


def test_cancel_button_calls_the_backend_and_hides_the_banner(dom):
    assert dom["cancelCalls"] == 1, "the Cancel button must reach cancel_auto_shutdown"
    after = dom["afterCancel"]
    assert after["bannerHidden"] is True
    assert after["state"] == "Disabled"


def test_cancellation_reason_is_surfaced_to_the_user(dom):
    cancelled = dom["cancelled"]
    assert cancelled["bannerHidden"] is True
    msgs = [t["msg"] for t in cancelled["toasts"]]
    assert any("new download" in m.lower() for m in msgs), msgs
    # The strip keeps explaining why it is not counting down.
    assert "new download" in (cancelled["btnTip"] or "").lower()


def test_blocked_state_explains_failed_downloads(dom):
    blocked = dom["blocked"]
    assert blocked["state"] == "Blocked"
    assert blocked["bannerHidden"] is True
    assert "failed" in (blocked["btnTip"] or "").lower()
    titles = [t["title"] for t in blocked["toasts"]]
    assert any("blocked" in t.lower() for t in titles), titles


def test_poll_loop_is_live_and_drains_the_backend_queue(dom):
    """The 200ms loop must actually be polling and consuming events.

    This is the wiring between ``Api._on_auto_shutdown_state`` (which pushes
    into the event queue) and the frontend.  The rendering assertions above
    dispatch through the same entry point the loop uses.
    """
    assert dom["pollsBefore"] > 0
    assert dom["pollsAfter"] > dom["pollsBefore"]
    assert dom["eventsLeftQueued"] == 0


def test_settings_expose_the_whole_policy_not_just_the_toggle(dom):
    keys = dom["settingKeys"]
    for key in ("shutdown_when_done", "shutdown_countdown_seconds",
                "shutdown_allow_failures", "shutdown_allow_cancelled"):
        assert key in keys, f"{key} is missing from the Settings page ({keys})"
    assert dom["countdownInput"] == {"type": "number", "min": "5", "max": "3600"}
