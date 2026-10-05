"""Headless render + interaction test for the Queue page (js/queue.js).

Builds a temporary preview page from the real `index.html` with a stubbed
`window.pywebview.api`, loads it in headless Chrome, drives it through the
Queue page, and asserts on the rendered DOM.  The stub is a plain object rather
than a Proxy, so a call to a method the page never declares fails loudly
instead of silently returning undefined.

This is the only test that exercises the real DOM/CSS wiring — the render
pipeline, drag & drop, the keyboard handler and RTL layout.  `queue-logic.test.mjs`
covers the same module's pure decision logic without a browser.

Skipped automatically when Chrome is not installed.

Run:  ./.venv/Scripts/python.exe -m pytest tests/test_queue_ui.py -q
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

# ── Fixture: three running downloads and four waiting ones ──────────────────
# Priorities deliberately differ from the manual order so the two order views
# disagree — that is the case the page has to get right.

RUNNING = [
    {"id": "run1", "state": "Downloading", "filename": "Movie.mkv", "url": "https://cdn.example.com/Movie.mkv",
     "total": 800_000_000, "completed": 360_000_000, "speed_bps": 4_200_000, "eta_seconds": 105,
     "connections": 8, "queue_index": 0, "queue_position": -1, "priority": 5, "category": "Videos", "created_at": 1_760_000_000,
     "started_at": 1_760_000_050, "finished_at": None, "average_speed": 3_900_000, "supports_range": True,
     "server": "nginx", "content_type": "video/x-matroska", "directory": r"C:\Downloads", "retry_count": 0,
     "connection_mode": "smart", "num_threads": 16, "checksum": "", "speed_limit_bps": 0, "error": "", "smart_status": ""},
    {"id": "run2", "state": "Analyzing", "filename": "Archive.zip", "url": "https://cdn.example.com/Archive.zip",
     "total": 0, "completed": 0, "speed_bps": 0, "eta_seconds": None,
     "connections": 1, "queue_index": 1, "queue_position": -1, "priority": 5, "category": "Compressed", "created_at": 1_760_000_100,
     "started_at": 1_760_000_120, "finished_at": None, "average_speed": 0, "supports_range": False,
     "server": "", "content_type": "", "directory": r"C:\Downloads", "retry_count": 0,
     "connection_mode": "smart", "num_threads": 16, "checksum": "", "speed_limit_bps": 0, "error": "", "smart_status": ""},
]

# `queue_position` is the effective start order and deliberately disagrees with
# `queue_index` — the same disagreement the real backend reports once priorities
# differ, and the thing the Downloads list used to get wrong.
WAITING = [
    {"id": "w1", "filename": "Linux.iso", "priority": 9, "queue_index": 2, "queue_position": 4, "total": 2_600_000_000, "completed": 0},
    {"id": "w2", "filename": "Image.png", "priority": 0, "queue_index": 3, "queue_position": 1, "total": 4_000_000, "completed": 0},
    {"id": "w3", "filename": "Song.mp3", "priority": 5, "queue_index": 4, "queue_position": 2, "total": 12_000_000, "completed": 0},
    {"id": "w4", "filename": "NoSize.bin", "priority": 5, "queue_index": 5, "queue_position": 3, "total": 0, "completed": 0},
]

# Effective start order: priority first (Image.png, then the two Normal ones in
# manual order, then the Low one), so it disagrees with the manual order.
PLAN = [
    {"id": "w2", "name": "Image.png", "state": "Queued", "priority": 0, "position": 1, "manual_index": 3,
     "total": 4_000_000, "completed": 0, "remaining": 4_000_000, "category": "Images", "speed_limit_bps": 0,
     "estimated_start_seconds": 105.0, "expected_wait_seconds": 105.0, "starts_immediately": False},
    {"id": "w3", "name": "Song.mp3", "state": "Queued", "priority": 5, "position": 2, "manual_index": 4,
     "total": 12_000_000, "completed": 0, "remaining": 12_000_000, "category": "Music", "speed_limit_bps": 0,
     "estimated_start_seconds": 107.0, "expected_wait_seconds": 107.0, "starts_immediately": False},
    {"id": "w4", "name": "NoSize.bin", "state": "Queued", "priority": 5, "position": 3, "manual_index": 5,
     "total": 0, "completed": 0, "remaining": 0, "category": "General", "speed_limit_bps": 0,
     "estimated_start_seconds": 110.0, "expected_wait_seconds": 110.0, "starts_immediately": False},
    {"id": "w1", "name": "Linux.iso", "state": "Queued", "priority": 9, "position": 4, "manual_index": 2,
     "total": 2_600_000_000, "completed": 0, "remaining": 2_600_000_000, "category": "General", "speed_limit_bps": 0,
     "estimated_start_seconds": 110.0, "expected_wait_seconds": 110.0, "starts_immediately": False},
]


def task(row: dict, waiting: bool) -> dict:
    base = {
        "state": "Queued", "completed": 0, "speed_bps": 0.0, "eta_seconds": None, "error": "",
        "created_at": 1_760_000_200, "finished_at": None, "priority": 5, "retry_count": 0, "connections": 1,
        "content_type": "application/octet-stream", "server": "", "supports_range": True, "category": "General",
        "average_speed": 0.0, "started_at": None, "smart_status": "", "connection_mode": "smart",
        "num_threads": 16, "speed_limit_bps": 0, "queue_index": -1, "queue_position": -1,
        "checksum": "", "label": "",
        "directory": r"C:\Downloads",
    }
    base.update(row)
    base["url"] = row.get("url", f"https://cdn.example.com/{row['filename']}")
    return base


DOWNLOADS = [task(r, False) for r in RUNNING] + [task(r, True) for r in WAITING]

STUB = """
<script>
(function () {
  var DOWNLOADS = __DOWNLOADS__;
  var PLAN = __PLAN__;
  var SETTINGS = { max_concurrent: 1, max_speed_bps: 0, scheduler_enabled: false, schedule_days: [],
                   schedule_start_time: "00:00", schedule_stop_time: "00:00", language: "en",
                   num_threads: 16, connection_mode: "smart", download_dir: "C:/Downloads" };
  // The queue gate is runtime state the page must render, never infer.
  var QUEUE_OPEN = { paused: false, scheduler_gate: false, max_concurrent: 1 };
  var QUEUE_PAUSED = { paused: true, scheduler_gate: false, max_concurrent: 1 };
  var calls = [];
  window.__calls = calls;
  function fn(name, value) { return function () { calls.push(name); return Promise.resolve(typeof value === "function" ? value.apply(null, arguments) : value); }; }
  window.pywebview = { api: {
    poll_events: fn("poll_events", []),
    get_downloads: fn("get_downloads", DOWNLOADS),
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
    queue_plan: fn("queue_plan", PLAN),
    reorder_tasks: fn("reorder_tasks", null),
    move_task_to: fn("move_task_to", null),
    move_to_top: fn("move_to_top", null),
    move_to_bottom: fn("move_to_bottom", null),
    move_task: fn("move_task", null),
    set_priority: fn("set_priority", null),
    update_settings: fn("update_settings", true),
    log_js: fn("log_js", null),
    get_queue_status: fn("get_queue_status", QUEUE_OPEN),
    pause_queue: fn("pause_queue", QUEUE_PAUSED),
    resume_queue: fn("resume_queue", QUEUE_OPEN),
    pause_all: fn("pause_all", QUEUE_PAUSED),
    resume_all: fn("resume_all", QUEUE_OPEN),
    check_for_updates: fn("check_for_updates", null)
  } };
})();
</script>
"""

DRIVER = """
<script>
(function () {
  function q(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  var out = { errors: [] };
  var calls = function () { return window.__calls || []; };
  var callsSince = function (n) { return calls().slice(n); };
  window.addEventListener("error", function (e) { out.errors.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function (e) {
    out.errors.push("rejection: " + String(e.reason && e.reason.stack || e.reason));
  });

  function rows() { return q("#queueWaiting .qk-row-wait"); }
  function names() { return rows().map(function (r) { return r.querySelector(".qk-name").textContent; }); }
  function dragEvent(type, dt, extra) {
    var o = { dataTransfer: dt, bubbles: true, cancelable: true };
    if (extra) Object.keys(extra).forEach(function (k) { o[k] = extra[k]; });
    return new DragEvent(type, o);
  }

  // ── Steps run one per tick so async renders settle between them ──────────
  var steps = [];

  steps.push(function () {
    App.navigate("queue");
  });

  steps.push(function () {
    out.viewChildren = document.getElementById("queueView").children.length;
    out.activeRows = q("#queueView .qk-row-active").map(function (r) {
      return r.querySelector(".qk-name").textContent;
    });
    out.waitRows = rows().map(function (r) {
      return { name: r.querySelector(".qk-name").textContent,
               pos: r.querySelector(".qk-pos").textContent,
               starts: r.querySelector(".qk-starts").textContent,
               eff: r.querySelector(".qk-eff") ? r.querySelector(".qk-eff").textContent : null };
    });
    out.overview = q("#queueView .qk-card .qk-val").map(function (e) { return e.textContent; });
    out.drainSub = q("#queueView .qk-card .qk-sub")[3] ? q("#queueView .qk-card .qk-sub")[3].textContent : null;
    out.drainLabel = q("#queueView .qk-card .qk-lbl")[3] ? q("#queueView .qk-card .qk-lbl")[3].textContent : null;
    out.hasToggle = !!document.querySelector("[data-qk-order]");
    out.toggleLabels = q("[data-qk-order]").map(function (b) { return b.textContent; });
    out.note = document.querySelector(".qk-note") ? document.querySelector(".qk-note").textContent : null;
    out.suggestions = q(".qk-reco-item p").map(function (e) { return e.textContent; });
    out.pageActive = document.getElementById("page-queue").classList.contains("active");
    out.title = document.getElementById("pageTitle").textContent;
    out.queueBadge = (function () { var b = document.getElementById("queueBadge");
      return b ? { n: b.textContent, hidden: b.hidden } : null; })();
    out.updateCalls = calls().filter(function (c) { return c === "update_settings"; }).length;
  });

  // Switch to manual order.
  steps.push(function () {
    var manual = document.querySelector('[data-qk-order="manual"]');
    if (manual) manual.click();
  });

  steps.push(function () {
    out.manualRows = names();
    out.manualEff = rows().map(function (r) {
      var e = r.querySelector(".qk-eff");
      return e ? e.textContent : null;
    });
    out.manualDraggable = rows().map(function (r) { return r.draggable; });
    out.grips = q("#queueWaiting .qk-grip").length;
    out.hint = document.querySelector(".qk-hint") ? document.querySelector(".qk-hint").textContent : null;
  });

  // Select the first row and open its details.
  steps.push(function () {
    rows()[0].click();
  });

  steps.push(function () {
    out.selCount = q("#queueWaiting .qk-row-wait.sel").length;
    out.detailsName = document.querySelector(".qk-details-name")
      ? document.querySelector(".qk-details-name").textContent : null;
    out.tabs = q(".qk-tab").map(function (b) { return b.textContent; });
    out.overviewKv = q(".qk-kvs .qk-kv").length;
    var tl = q(".qk-tab")[1]; if (tl) tl.click();
  });

  steps.push(function () {
    out.timelineKv = q(".qk-kvs .qk-kv").length;
    var adv = q(".qk-tab")[2]; if (adv) adv.click();
  });

  steps.push(function () {
    out.advancedKv = q(".qk-kvs .qk-kv").length;
    out.advancedText = q(".qk-kvs .qk-kv dd").map(function (e) { return e.textContent; });
  });

  // ── Keyboard reorder: Ctrl+ArrowDown on the selected row ────────────────
  steps.push(function () {
    out.orderBeforeKeys = names();
    out.mark = calls().length;
    rows()[0].dispatchEvent(new KeyboardEvent("keydown",
      { key: "ArrowDown", ctrlKey: true, bubbles: true, cancelable: true }));
  });

  steps.push(function () {
    out.keyCalls = callsSince(out.mark).filter(function (c) {
      return c === "move_task_to" || c === "reorder_tasks"; });
    out.keyArgs = (window.__calls__args || []).slice();
  });

  // ── Drag & drop: drag row 0 onto the second half of row 2 ───────────────
  steps.push(function () {
    var r = rows();
    var dt = new DataTransfer();
    r[0].dispatchEvent(dragEvent("dragstart", dt));
    out.dragStarted = r[0].classList.contains("dragging");
    var target = r[2];
    var rect = target.getBoundingClientRect();
    out.dragRect = { top: Math.round(rect.top), h: Math.round(rect.height) };
    target.dispatchEvent(dragEvent("dragover", dt, { clientY: rect.top + rect.height - 1, clientX: rect.left + 4 }));
    out.dropAt = Queue.state.dropAt ? JSON.parse(JSON.stringify(Queue.state.dropAt)) : null;
    out.dropIndicator = target.classList.contains("drop-after");
    out.mark2 = calls().length;
    r[0].dispatchEvent(dragEvent("drop", dt));
  });

  steps.push(function () {
    out.dropCalls = callsSince(out.mark2).filter(function (c) {
      return c === "reorder_tasks" || c === "move_task_to"; });
    out.dragCleared = Queue.state.drag === null;
    out.staleIndicators = q("#queueWaiting .qk-row-wait").filter(function (r) {
      return r.classList.contains("dragging") || r.classList.contains("drop-before")
          || r.classList.contains("drop-after"); }).length;
  });

  // ── Queue gate: a paused queue must be visible AND recoverable ──────────
  // The gate is runtime state, so it is driven through the event the backend
  // actually sends — never by calling a render helper directly.
  steps.push(function () {
    out.gateBannerBefore = !!document.querySelector(".qk-gate");
    out.stripPausedBefore = document.getElementById("queueStrip").classList.contains("paused");
    App._handleEvent({ type: "queue_gate",
                       status: { paused: true, scheduler_gate: false, max_concurrent: 1 } });
  });

  steps.push(function () {
    var banner = document.querySelector(".qk-gate");
    out.gateBannerShown = !!banner;
    out.gateText = banner ? banner.textContent.replace(/\\s+/g, " ").trim() : null;
    out.gateResumeBtn = !!document.querySelector('[data-qk="resume-queue"]');
    out.stripPaused = document.getElementById("queueStrip").classList.contains("paused");
    out.pausedOverviewSubs = q("#queueView .qk-card .qk-sub").map(function (e) { return e.textContent; });
    out.dlPausedBanner = (function () {
      var b = document.getElementById("qsQueuePausedBanner");
      return b ? !b.hidden : null; })();
    out.mark3 = calls().length;
    var btn = document.querySelector('[data-qk="resume-queue"]');
    if (btn) btn.click();
  });

  steps.push(function () {
    out.resumeCalls = callsSince(out.mark3).filter(function (c) { return c === "resume_queue"; });
    out.gateBannerAfter = !!document.querySelector(".qk-gate");
    out.stripPausedAfter = document.getElementById("queueStrip").classList.contains("paused");
    out.dlPausedBannerAfter = (function () {
      var b = document.getElementById("qsQueuePausedBanner");
      return b ? !b.hidden : null; })();
  });

  // ── Downloads list: the queue sort must show the EFFECTIVE order ────────
  // `queue_index` is the list the user drags; `queue_position` is what will
  // actually start.  Sorting by the former was the lie this covers.
  steps.push(function () {
    App.navigate("downloads");
    App.state.sortKey = "queue";
    App.state.sortDir = 1;
    var sel = document.getElementById("sortSelect");
    if (sel) sel.value = "queue:1";
    App._renderDownloads(true);
  });

  steps.push(function () {
    out.dlQueueOrder = q("#downloadList .dl-row .dl-name").map(function (e) { return e.textContent; });
    out.dlRows = q("#downloadList .dl-row").length;
  });

  // ── RTL: switch to Persian and confirm the page still renders ───────────
  // Back to the Queue page first — the downloads check above moved away.
  steps.push(function () {
    App.navigate("queue");
  });

  steps.push(function () {
    I18N.setLang("fa");
  });

  steps.push(function () {
    out.dir = document.documentElement.dir;
    out.rtlBody = document.body.classList.contains("rtl");
    out.rtlRows = names().length;
    out.rtlTitle = document.getElementById("pageTitle").textContent;
    out.rtlToggle = q("[data-qk-order]").map(function (b) { return b.textContent; });
    out.rtlLabels = q(".qk-panel-head h2").map(function (h) { return h.textContent; });
    out.rtlNav = (function () {
      var n = document.querySelector('.nav-item[data-page="queue"] .nav-label');
      return n ? n.textContent : null; })();
    out.rtlAnyUntranslated = q("#queueView *").filter(function (el) {
      return el.children.length === 0 && /^(Queue|Waiting queue|Running now|Start order|Manual order|Suggestions)$/
        .test((el.textContent || "").trim());
    }).length;
  });

  var i = 0;
  function finish() {
    document.title = "QKRESULT" + JSON.stringify(out) + "ENDQK";
  }
  function next() {
    if (i >= steps.length) { finish(); return; }
    var n = i++;
    try { steps[n](); } catch (e) { out.errors.push("step" + n + ": " + e + " | " + e.stack); }
    setTimeout(next, 150);
  }
  setTimeout(next, 900);
})();
</script>
"""

# Record the arguments of the queue-mutating bridge calls so the interaction
# checks can assert on the exact position requested.  Runs right after STUB.
ARGS_HOOK = """
<script>
(function () {
  window.__calls__args = [];
  var api = window.pywebview.api;
  ["reorder_tasks", "move_task_to", "move_to_top", "move_to_bottom"].forEach(function (name) {
    var orig = api[name];
    api[name] = function () {
      window.__calls__args.push({ method: name, args: Array.prototype.slice.call(arguments) });
      return orig.apply(this, arguments);
    };
  });
})();
</script>
"""


def build_page() -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    stub = STUB.replace("__DOWNLOADS__", json.dumps(DOWNLOADS)).replace("__PLAN__", json.dumps(PLAN))
    anchor = '<script src="js/utils.js"></script>'
    assert anchor in html, "index.html no longer loads utils.js where expected"
    html = html.replace(anchor, stub + ARGS_HOOK + anchor, 1)
    assert "</body>" in html
    html = html.replace("</body>", DRIVER + "</body>", 1)
    return html


def _write_preview(html: str) -> Path:
    """Write *html* to a throwaway page whose asset URLs still resolve.

    The page is written *outside* the repository on purpose: the project is
    under version control, so a preview file that survives cleanup would show
    up as an untracked change.  `index.html` references its assets with
    relative paths (`js/...`, `css/...`), so those are rewritten to absolute
    `file://` URIs and the page then loads correctly from anywhere.
    """
    base = FRONTEND.as_uri()
    html = re.sub(
        r'\b(src|href)="(js|css)/',
        lambda m: '%s="%s/%s/' % (m.group(1), base, m.group(2)),
        html,
    )
    out = Path(tempfile.mkdtemp(prefix="n13-preview-")) / "preview.html"
    out.write_text(html, encoding="utf-8")
    return out


def _remove(path: Path) -> None:
    """Best-effort delete — a refused delete must never fail the run.

    An OS-level delete guard can legitimately veto the unlink, and on Windows
    the file may still be held open by the browser that was just launched.
    The leftover lives in a temp directory either way, so the only thing that
    matters is that cleanup itself cannot raise.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def render_queue() -> dict:
    """Drive the Queue page in headless Chrome and return the measurements."""
    page = _write_preview(build_page())
    profile = Path(tempfile.mkdtemp())
    try:
        proc = subprocess.run(
            [
                str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
                "--allow-file-access-from-files", "--virtual-time-budget=6000",
                "--user-data-dir=" + str(profile), "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        _remove(page)

    m = re.search(r"QKRESULT(.*?)ENDQK", proc.stdout, re.S)
    if not m:
        pytest.fail(
            "the page never reported a result.\n"
            f"--- stdout ---\n{proc.stdout[:4000]}\n--- stderr ---\n{proc.stderr[:2000]}"
        )
    return json.loads(m.group(1))


SHOT_DRIVER = """
<script>
(function () {
  setTimeout(function () {
    App.navigate("queue");
    setTimeout(function () {
      var r = document.querySelector("#queueWaiting .qk-row-wait");
      if (r) r.click();
      setTimeout(function () {
        var view = document.getElementById("queueView");
        var reco = document.querySelector(".qk-reco");
        document.title = "SHOT" + JSON.stringify({
          children: view.children.length,
          recoPresent: !!reco,
          recoItems: document.querySelectorAll(".qk-reco-item").length,
          recoHeight: reco ? Math.round(reco.getBoundingClientRect().height) : 0,
          viewHeight: Math.round(view.getBoundingClientRect().height),
          theme: document.documentElement.dataset.theme
        });
      }, 200);
    }, 500);
  }, 900);
})();
</script>
"""

# Seed the theme before App.init() reads localStorage (the pre-paint script in
# <head> runs earlier still, so this covers the app's own preference load).
THEME_SEED = '<script>try{localStorage.setItem("n13-prefs",\'{"theme":"__THEME__"}\')}catch(e){}</script>'


def screenshot(outdir: Path, theme: str) -> tuple[Path, str]:
    """Render the queue page in *theme* and save a PNG."""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    stub = STUB.replace("__DOWNLOADS__", json.dumps(DOWNLOADS)).replace("__PLAN__", json.dumps(PLAN))
    seed = THEME_SEED.replace("__THEME__", theme)
    html = html.replace(
        '<script src="js/utils.js"></script>',
        seed + stub + ARGS_HOOK + '<script src="js/utils.js"></script>', 1)
    html = html.replace("</body>", SHOT_DRIVER + "</body>", 1)

    page = _write_preview(html)
    out = outdir / f"queue-{theme}.png"
    profile = Path(tempfile.mkdtemp())
    try:
        proc = subprocess.run(
            [
                str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
                "--allow-file-access-from-files", "--virtual-time-budget=4000",
                "--hide-scrollbars", "--window-size=1500,1400",
                "--user-data-dir=" + str(profile),
                "--screenshot=" + str(out), page.as_uri(),
            ],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        _remove(page)
    m = re.search(r"<title>SHOT(.*?)</title>", proc.stdout if proc.stdout else "", re.S)
    return out, (m.group(1) if m else "(no measurement)")


@pytest.fixture(scope="module")
def page() -> dict:
    """One browser run, shared by the assertions below (rendering is slow)."""
    return render_queue()


# ── Rendering ───────────────────────────────────────────────────────────────


def test_page_renders_without_errors(page):
    assert page["errors"] == [], "the page reported uncaught errors"
    assert page["pageActive"] is True
    assert page["title"] == "Queue"
    # Overview + body + suggestions.
    assert page["viewChildren"] == 3


def test_overview_reports_only_measured_numbers(page):
    assert page["overview"][0] == "2", "running count"
    assert page["overview"][1] == "4", "waiting count"
    assert page["drainLabel"] == "Queue finishes in"
    # NoSize.bin has no known size, so the figure must be a labelled lower
    # bound rather than a bare number that looks authoritative.
    assert page["overview"][3].startswith("≥ "), page["overview"][3]
    assert page["drainSub"] == "at least — some sizes are unknown"


def test_running_list_shows_active_downloads(page):
    assert page["activeRows"] == ["Movie.mkv", "Archive.zip"]


def test_waiting_list_follows_the_effective_start_order(page):
    names = [w["name"] for w in page["waitRows"]]
    # Priority 0 first, priority 9 last — not the manual order.
    assert names == ["Image.png", "Song.mp3", "NoSize.bin", "Linux.iso"]
    assert [w["pos"] for w in page["waitRows"]] == ["1", "2", "3", "4"]
    assert all(w["starts"] not in ("", "—") for w in page["waitRows"])
    # In this view the visual position IS the effective position, so the
    # "runs at #n" badge would be redundant and must not be rendered.
    assert [w["eff"] for w in page["waitRows"]] == [None, None, None, None]


def test_order_toggle_appears_only_when_priorities_diverge(page):
    assert page["hasToggle"] is True
    assert page["toggleLabels"] == ["Start order", "Manual order"]
    assert page["note"]


def test_sidebar_badge_counts_the_waiting_queue(page):
    assert page["queueBadge"]["n"] == "4"
    assert page["queueBadge"]["hidden"] is False


# ── Order views ─────────────────────────────────────────────────────────────


def test_manual_view_uses_the_raw_queue_order_and_enables_dragging(page):
    assert page["manualRows"] == ["Linux.iso", "Image.png", "Song.mp3", "NoSize.bin"]
    assert all(page["manualDraggable"])
    assert page["grips"] == 4
    assert page["hint"]


def test_manual_view_flags_rows_that_priority_reordered(page):
    # Manual order is Linux.iso, Image.png, Song.mp3, NoSize.bin while the
    # effective order is Image.png, Song.mp3, NoSize.bin, Linux.iso — every row
    # sits somewhere other than where it will run, and each must say where.
    assert page["manualEff"] == ["runs at #4", "runs at #1", "runs at #2", "runs at #3"]


# ── Selection + details ─────────────────────────────────────────────────────


def test_clicking_a_row_selects_it_and_opens_the_details_panel(page):
    assert page["selCount"] == 1
    assert page["detailsName"] == "Linux.iso"


def test_details_panel_tabs_all_have_content(page):
    assert page["tabs"] == ["Overview", "Timeline", "Advanced"]
    assert page["overviewKv"] >= 8
    assert page["timelineKv"] >= 4
    assert page["advancedKv"] >= 6
    # The Advanced tab must report the stored priority, not a placeholder.
    assert any("Low priority" in v for v in page["advancedText"])


# ── Keyboard reordering ─────────────────────────────────────────────────────


def test_ctrl_down_moves_the_selected_row_down_one_place(page):
    assert page["keyCalls"] == ["move_task_to"]
    moved = [a for a in page["keyArgs"] if a["method"] == "move_task_to"]
    # Linux.iso sat at manual index 0; Ctrl+Down asks for index 1.
    assert moved and moved[0]["args"] == ["w1", 1]


# ── Drag & drop ─────────────────────────────────────────────────────────────


def test_drag_and_drop_asks_the_backend_to_reorder(page):
    assert page["dragStarted"] is True
    assert page["dropAt"] == {"id": "w3", "after": True}
    assert page["dropIndicator"] is True
    assert page["dropCalls"] == ["reorder_tasks"]


def test_drop_leaves_no_drag_state_behind(page):
    assert page["dragCleared"] is True
    assert page["staleIndicators"] == 0


# ── RTL (Persian) ───────────────────────────────────────────────────────────


def test_rtl_layout_renders_and_is_translated(page):
    assert page["dir"] == "rtl"
    assert page["rtlBody"] is True
    assert page["rtlRows"] == 4, "the queue must still render under RTL"
    assert page["rtlTitle"] == "صف"
    assert page["rtlNav"] == "صف"
    assert page["rtlToggle"] == ["ترتیب اجرا", "ترتیب دستی"]
    assert page["rtlLabels"] == ["در حال اجرا", "صف انتظار", "پیشنهادها"]
    assert page["rtlAnyUntranslated"] == 0, "untranslated queue strings found"


# ── Queue gate ──────────────────────────────────────────────────────────────


def test_an_open_gate_shows_no_banner(page):
    assert page["gateBannerBefore"] is False
    assert page["stripPausedBefore"] is False


def test_a_paused_queue_is_visible_on_both_pages(page):
    """A paused queue must never look like a stalled application."""
    assert page["gateBannerShown"] is True, "the Queue page showed no gate banner"
    assert page["gateResumeBtn"] is True
    assert "Queue paused" in page["gateText"]
    assert page["stripPaused"] is True, "the queue strip did not mark the gate"
    assert page["dlPausedBanner"] is True, "the Downloads page showed no gate banner"


def test_a_paused_queue_stops_claiming_the_work_will_run(page):
    """No invented numbers: while the gate is closed, say so."""
    subs = page["pausedOverviewSubs"]
    assert "held — the queue is paused" in subs[1], subs
    assert "estimate — the queue is paused" in subs[3], subs


def test_resume_queue_is_reachable_from_the_banner(page):
    """The banner's own button must clear the gate — no hidden state left."""
    assert page["resumeCalls"] == ["resume_queue"]
    assert page["gateBannerAfter"] is False
    assert page["stripPausedAfter"] is False
    assert page["dlPausedBannerAfter"] is False


# ── Downloads list ordering ─────────────────────────────────────────────────


def test_the_downloads_queue_sort_shows_the_effective_order(page):
    """The list must agree with the engine, not with the manual index."""
    assert page["dlRows"] == 6, "expected every download in the unfiltered list"
    # Image.png starts first despite sitting 4th in the manual order; the two
    # running downloads hold no effective position and fall in behind.
    assert page["dlQueueOrder"] == [
        "Image.png", "Song.mp3", "NoSize.bin", "Linux.iso", "Movie.mkv", "Archive.zip",
    ]


# ── Suggestions ─────────────────────────────────────────────────────────────


def test_suggestions_are_offered_but_never_applied_automatically(page):
    assert len(page["suggestions"]) >= 1
    # Rendering the page, selecting a row and reordering must not touch a
    # single setting — the user applies suggestions explicitly.
    assert page["updateCalls"] == 0
