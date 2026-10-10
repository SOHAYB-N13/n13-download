"""The Downloads list must keep up with the backend without a navigation.

Reported symptom: after the browser extension launched N13 from cold, the row
sat on "Analyzing" for the whole download; switching to another page and back
showed the real progress.

The cause was a second source of truth for the open page.  ``index.html``
marked ``#page-downloads`` active, while ``App.state.page`` started as
``"dashboard"``.  The row updater is page-gated, so every state and progress
event for the visible list was dropped: the row kept whatever label it had when
the list was last rebuilt (``Analyzing``), and only a navigation — which forces
a full rebuild — repainted it.

These tests load the real ``index.html`` in headless Chrome with a stubbed
``window.pywebview.api`` and drive the same ``App._handleEvent`` path the 200 ms
poll loop uses, then assert on the rendered DOM.

Skipped automatically when Chrome/Edge is not installed.

Run:  ./.venv/Scripts/python.exe -m pytest tests/test_download_state_sync_ui.py -q
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

SETTINGS = {
    "max_concurrent": 3, "max_speed_bps": 0, "scheduler_enabled": False,
    "schedule_days": [], "schedule_start_time": "00:00", "schedule_stop_time": "00:00",
    "language": "en", "num_threads": 4, "connection_mode": "manual",
    "download_dir": "C:/Downloads", "shutdown_when_done": False,
}

STUB = """
<script>
(function () {
  var SETTINGS = __SETTINGS__;
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
    get_version: fn("get_version", "1.4.4"),
    get_stats: fn("get_stats", {}),
    get_system_stats: fn("get_system_stats", {}),
    get_analytics: fn("get_analytics", {}),
    get_update_settings: fn("get_update_settings", {}),
    get_update_state: fn("get_update_state", {}),
    get_auto_shutdown_status: fn("get_auto_shutdown_status", {
      enabled: false, state: "Disabled", supported: true, pending: false,
      countdown_seconds: 0, seconds_remaining: null, deadline: null,
      reason: "", cancel_reason: "", blocked_reason: "", last_error: "",
      policy: {}, session: {}
    }),
    get_queue_status: fn("get_queue_status", { paused: false, scheduler_gate: false, max_concurrent: 3 }),
    scheduler_status: fn("scheduler_status", {}),
    live_server_status: fn("live_server_status", { running: false }),
    clipboard_status: fn("clipboard_status", {}),
    queue_plan: fn("queue_plan", []),
    resolve_destination: fn("resolve_destination", { directory: "C:/Downloads", category: "General" }),
    update_settings: fn("update_settings", true),
    log_js: fn("log_js", null),
    check_for_updates: fn("check_for_updates", null)
  } };
})();
</script>
"""

# A task exactly as `ui/api.py::_on_task_event` sends it.
def _task(state, completed=0, total=0, error=""):
    return {
        "id": "t1", "url": "https://cdn.example.com/big.rar", "filename": "big.rar",
        "name": "big.rar", "state": state, "completed": completed, "total": total,
        "speed_bps": 0.0, "eta_seconds": None, "error": error, "created_at": 1_760_000_000,
        "finished_at": None, "priority": 5, "retry_count": 0, "connections": 1,
        "content_type": "application/x-rar-compressed", "server": "", "supports_range": True,
        "category": "Archives", "average_speed": 0.0, "started_at": None, "smart_status": "",
        "connection_mode": "manual", "num_threads": 4, "speed_limit_bps": 0,
        "queue_index": -1, "queue_position": -1, "checksum": "", "label": "",
        "directory": r"C:\Downloads\Archives",
    }


DRIVER = """
<script>
(function () {
  var out = { errors: [] };
  window.addEventListener("error", function (e) { out.errors.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function (e) {
    out.errors.push("rejection: " + String(e.reason && e.reason.stack || e.reason));
  });

  function row() { return document.querySelector("#downloadList .dl-row"); }
  function status() { var s = row() && row().querySelector(".dl-status"); return s ? s.textContent.trim() : null; }
  function pct() { var p = row() && row().querySelector(".dl-pct"); return p ? p.textContent.trim() : null; }
  function snapshot(label) {
    out[label] = {
      page: App.state.page,
      activeSection: (document.querySelector(".page.active") || {}).id || null,
      rowState: row() ? row().dataset.state : null,
      status: status(),
      pct: pct(),
      backend: Object.keys(App.state.downloads).map(function (k) {
        return App.state.downloads[k].state;
      })
    };
  }

  // Exactly what the 200ms poll loop does with each buffered event.
  function deliver(evt) { App._handleEvent(evt); }

  var steps = [];
  var seen = [];

  // 1. Boot state, before any event: the open page and the document agree.
  steps.push(function () { snapshot("boot"); });

  // 2. A cold launch delivers the task while the app is still starting: the
  //    whole history is replayed, exactly as the buffered queue delivers it.
  steps.push(function () {
    deliver({ type: "task", event: "added", task: __TASK_QUEUED__ });
  });
  steps.push(function () {
    deliver({ type: "task", event: "started", task: __TASK_ANALYZING__ });
  });
  steps.push(function () { snapshot("analyzing"); });

  // 3. The probe finished: the download is running.  No navigation happens.
  steps.push(function () {
    deliver({ type: "task", event: "updated", task: __TASK_DOWNLOADING__ });
  });
  steps.push(function () { snapshot("downloading"); });

  // 4. Progress keeps arriving; the row must follow it, monotonically.
  [10, 25, 50, 75, 90].forEach(function (p) {
    steps.push(function () {
      deliver({ type: "task", event: "progress",
                task: __TASK_PROGRESS__(p) });
    });
    steps.push(function () {
      var snap = {};
      snapshot("p" + p);
      seen.push(parseInt(out["p" + p].pct, 10));
    });
  });

  // 5. Terminal states render their own label.
  steps.push(function () {
    deliver({ type: "task", event: "finished", task: __TASK_COMPLETED__ });
  });
  steps.push(function () { snapshot("completed"); });

  steps.push(function () {
    deliver({ type: "task", event: "finished", task: __TASK_FAILED__ });
  });
  steps.push(function () { snapshot("failed"); });

  // 6. A pause must be shown as paused, not as an activity it is not doing.
  steps.push(function () {
    deliver({ type: "task", event: "updated", task: __TASK_PAUSED__ });
  });
  steps.push(function () { snapshot("paused"); });

  steps.push(function () {
    out.pcts = seen;
    out.monotonic = seen.every(function (v, i) { return i === 0 || v >= seen[i - 1]; });
  });

  var i = 0;
  function finish() { document.title = "SYNCRESULT" + JSON.stringify(out) + "ENDSYNC"; }
  function next() {
    if (i >= steps.length) { finish(); return; }
    var n = i++;
    try { steps[n](); } catch (e) { out.errors.push("step" + n + ": " + e + " | " + e.stack); }
    setTimeout(next, 5);
  }
  setTimeout(next, 1200);
})();
</script>
"""


def _task_js(task: dict) -> str:
    return json.dumps(task)


def _progress_js(pct: int) -> str:
    """A progress event builder: the row must follow the reported bytes."""
    return (
        "(function () { var t = %s; t.completed = Math.round(t.total * %d / 100);"
        " t.speed_bps = 1024 * 1024; return t; })()" % (_task_js(_task("Downloading", total=1000)), pct)
    )


def build_page(total: int = 1000) -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    stub = STUB.replace("__SETTINGS__", json.dumps(SETTINGS))
    anchor = '<script src="js/utils.js"></script>'
    assert anchor in html, "index.html no longer loads utils.js where expected"
    html = html.replace(anchor, stub + anchor, 1)

    driver = DRIVER
    driver = driver.replace("__TASK_QUEUED__", _task_js(_task("Queued")))
    driver = driver.replace("__TASK_ANALYZING__", _task_js(_task("Analyzing")))
    driver = driver.replace("__TASK_DOWNLOADING__", _task_js(_task("Downloading", 0, total)))
    driver = driver.replace("__TASK_PROGRESS__", _PROGRESS_BUILDER)
    driver = driver.replace("__TASK_COMPLETED__", _task_js(_task("Complete", total, total)))
    driver = driver.replace("__TASK_FAILED__", _task_js(_task("Failed", 0, total, "Destination folder is not available")))
    driver = driver.replace("__TASK_PAUSED__", _task_js(_task("Paused", total // 2, total)))
    assert "</body>" in html
    return html.replace("</body>", driver + "</body>", 1)


#: Progress events are built per percentage inside the driver, so the same row
#: keeps being updated with a growing byte count.
_PROGRESS_BUILDER = (
    "(function (p) { var t = %s; t.completed = Math.round(t.total * p / 100);"
    " t.speed_bps = 1048576; return t; })" % _task_js(_task("Downloading", 0, 1000))
)


def _write_preview(html: str) -> Path:
    base = FRONTEND.as_uri()
    html = re.sub(
        r'\b(src|href)="(js|css)/',
        lambda m: '%s="%s/%s/' % (m.group(1), base, m.group(2)),
        html,
    )
    out = Path(tempfile.mkdtemp(prefix="n13-sync-preview-")) / "preview.html"
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
            capture_output=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
    finally:
        _remove(page)

    m = re.search(r"SYNCRESULT(.*?)ENDSYNC", proc.stdout, re.S)
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


def test_open_page_and_document_agree_at_boot(dom):
    """One source of truth: the state matches the section the document shows."""
    boot = dom["boot"]
    assert boot["activeSection"] == "page-downloads", boot
    assert boot["page"] == "downloads", (
        "App.state.page disagrees with the active section — every page-gated "
        "render (the row updater included) would target the wrong page"
    )


def test_row_leaves_analyzing_without_a_navigation(dom):
    """The reported symptom: stuck on Analyzing while the transfer runs."""
    assert dom["analyzing"]["status"] == "Analyzing"
    assert dom["analyzing"]["rowState"] == "Analyzing"
    for label in ("downloading", "p50", "completed"):
        snap = dom[label]
        assert snap["page"] == "downloads"
        assert snap["rowState"] != "Analyzing", (
            f"the row stayed on Analyzing at {label}: {snap}"
        )
    assert dom["downloading"]["status"] == "Downloading"
    assert dom["completed"]["rowState"] == "Complete"


def test_dom_matches_the_backend_state_at_every_step(dom):
    for label in ("analyzing", "downloading", "p25", "p50", "p90",
                  "completed", "failed", "paused"):
        snap = dom[label]
        assert snap["backend"] == [snap["rowState"]], f"{label}: {snap}"


def test_progress_is_monotonic_and_reaches_full(dom):
    assert dom["monotonic"] is True, dom["pcts"]
    assert dom["pcts"] == sorted(dom["pcts"])
    assert dom["completed"]["pct"] == "100%"


def test_terminal_and_paused_states_are_labelled(dom):
    assert dom["completed"]["pct"] == "100%"
    assert dom["failed"]["rowState"] == "Failed"
    assert dom["failed"]["status"] == "Failed"
    assert dom["paused"]["rowState"] == "Paused"
    assert dom["paused"]["status"] == "Paused"
