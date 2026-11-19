"""Render the real frontend with a stubbed pywebview bridge and screenshot pages.

Development aid only — not part of the product or the test suite.  It exists so
UI work is judged from a rendered screenshot instead of from reading CSS.

Usage:
    ./.venv/Scripts/python.exe tools/ui_shot.py [--theme dark|light] [--lang en|fa]
                                                [--out DIR] [--page NAME ...]

Requires Chrome or Edge.  Writes one PNG per page plus any console errors to
``ui-shots/console.txt``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "ui" / "frontend"

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

PAGES = ["downloads", "queue", "history", "batch", "browser", "logs", "dashboard", "settings"]

NOW = time.time()


def _task(**kw):
    """A row shaped exactly like ``ui.common.TaskSnapshot.to_dict()``."""
    base = {
        "id": "t0", "project_id": "default", "url": "https://example.com/file.zip",
        "directory": r"C:\Users\N13\Downloads", "checksum": "", "label": "",
        "state": "Queued", "completed": 0, "total": 0, "speed_bps": 0.0,
        "eta_seconds": None, "error": "", "created_at": NOW, "finished_at": None,
        "priority": 5, "retry_count": 0, "connections": 8, "content_type": "",
        "server": "nginx", "supports_range": True, "category": "General",
        "average_speed": 0.0, "filename": "file.zip", "started_at": None,
        "smart_status": "", "connection_mode": "", "num_threads": 8,
        "speed_limit_bps": 0, "queue_index": -1, "queue_position": -1,
        "control_paused": False,
    }
    base.update(kw)
    return base


DOWNLOADS = [
    _task(id="a1", filename="Ubuntu-24.04.3-desktop-amd64.iso",
          url="https://releases.ubuntu.com/24.04/ubuntu-24.04.3-desktop-amd64.iso",
          state="Downloading", total=6_138_155_008, completed=2_317_864_960,
          speed_bps=18_412_544, average_speed=17_002_311, eta_seconds=207,
          started_at=NOW - 130, connections=8, category="Programs", project_id="linux",
          smart_status="8", queue_index=0, queue_position=1),
    _task(id="a2", filename="Interstellar.2014.2160p.HDR.mkv",
          url="https://cdn.example.org/media/interstellar-2160p.mkv",
          state="Downloading", total=42_949_672_960, completed=6_442_450_944,
          speed_bps=41_943_040, average_speed=39_321_600, eta_seconds=871,
          started_at=NOW - 160, connections=16, category="Videos", project_id="movies",
          queue_index=1, queue_position=2),
    _task(id="a3", filename="Adobe Photoshop 2025 v26.1.0.rar",
          url="https://dl.example.net/ps2025.rar", state="Paused",
          total=3_221_225_472, completed=1_610_612_736,
          category="Programs", connections=8, queue_index=2),
    _task(id="a4", filename="python-3.13.0-amd64.exe",
          url="https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe",
          state="Queued", total=27_512_832, priority=1, category="Programs",
          queue_index=3, queue_position=3),
    _task(id="a5", filename="dataset-2024-q4.csv.gz",
          url="https://data.example.com/2024/q4.csv.gz", state="Queued",
          total=1_073_741_824, priority=9, category="Documents",
          queue_index=4, queue_position=4),
    _task(id="a6", filename="report.pdf", url="https://files.example.com/report.pdf",
          state="Failed", total=4_194_304, completed=1_048_576,
          error="HTTP 403 - Forbidden (server rejected the request)",
          category="Documents", queue_index=5),
    _task(id="a7", filename="photo-pack.zip", url="https://img.example.com/pack.zip",
          state="Starting", total=524_288_000, category="Images", queue_index=6),
]

HISTORY = [
    # Shape mirrors `core/store.list_history` exactly — the table reads `name`,
    # `size`, `category`, `status` and `finished`, not the task snapshot fields.
    {"task_id": "h1", "name": "VLC-media-player-3.0.21-win64.exe",
     "url": "https://get.videolan.org/vlc/3.0.21/win64/vlc-3.0.21-win64.exe",
     "directory": r"C://Users//N13//Downloads", "category": "Programs",
     "size": "40.5 MB", "size_bytes": 42_467_328, "status": "Complete",
     "duration": 14.2, "avg_speed": 2_990_657, "connection_mode": "smart",
     "finished": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 3600))},
    {"task_id": "h2", "name": "family-photos-2024.zip",
     "url": "https://drive.example.com/photos.zip",
     "directory": r"C://Users//N13//Downloads//Images", "category": "Images",
     "size": "1.20 GB", "size_bytes": 1_288_490_188, "status": "Complete",
     "duration": 96.4, "avg_speed": 13_366_082, "connection_mode": "smart",
     "finished": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 86400))},
    {"task_id": "h3", "name": "blender-4.2.3-windows-x64.msi",
     "url": "https://download.blender.org/release/Blender4.2/blender-4.2.3.msi",
     "directory": r"C://Users//N13//Downloads//Programs", "category": "Programs",
     "size": "304 MB", "size_bytes": 318_767_104, "status": "Complete",
     "duration": 41.7, "avg_speed": 7_644_295, "connection_mode": "smart",
     "finished": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 172800))},
    {"task_id": "h4", "name": "lecture-12-recording.mp4",
     "url": "https://uni.example.edu/lectures/12.mp4",
     "directory": r"C://Users//N13//Downloads//Videos", "category": "Videos",
     "size": "1.00 GB", "size_bytes": 1_073_741_824, "status": "Failed",
     "duration": 8.9, "avg_speed": 0, "connection_mode": "manual",
     "finished": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 259200))},
]

SETTINGS = {
    "download_dir": r"C:\Users\N13\Downloads", "max_concurrent": 3, "max_speed_bps": 0,
    "scheduler_enabled": False, "schedule_days": [], "schedule_start_time": "",
    "schedule_stop_time": "", "language": "en", "num_threads": 8,
    "connection_mode": "smart", "verify_size": True, "auto_categorize": True,
    "block_private_urls": True, "theme": "dark", "accent": "#EF4444",
    "clipboard_monitor": True, "notify_on_complete": True,
}

PROJECTS = [
    {"id": "default", "name": "Default", "is_default": True},
    {"id": "movies", "name": "Movies", "is_default": False},
    {"id": "linux", "name": "Linux ISOs", "is_default": False},
]

def _stats_from(tasks):
    """Mirror `Api.get_stats()` exactly — same keys, derived from the tasks.

    The old hand-written fixture invented keys the backend never sends
    (`total_downloads`, `today_bytes`) and omitted the ones it does
    (`running`, `queued`, `total_speed_bps`).  The dashboard therefore read
    `undefined` for half its numbers, and the sidebar footer disagreed with
    the queue strip about the live speed.  Deriving it here means the two
    can no longer drift apart.
    """
    def count(state):
        return sum(1 for t in tasks if t.get("state") == state)

    speed = sum(t.get("speed_bps") or 0 for t in tasks if t.get("state") == "Downloading")
    return {
        "running": count("Downloading"),
        "queued": count("Queued"),
        "paused": count("Paused"),
        "completed": count("Complete"),
        "failed": count("Failed"),
        "total_speed_bps": speed,
        "total_speed_display": f"{speed / 1_048_576:.1f} MB/s",
    }


STATS = _stats_from(DOWNLOADS)

ANALYTICS = {
    "daily": [{"date": "2026-11-12", "bytes": 3_221_225_472, "count": 3},
              {"date": "2026-11-13", "bytes": 6_442_450_944, "count": 5},
              {"date": "2026-11-14", "bytes": 1_073_741_824, "count": 2},
              {"date": "2026-11-15", "bytes": 9_663_676_416, "count": 7},
              {"date": "2026-11-16", "bytes": 4_294_967_296, "count": 4},
              {"date": "2026-11-17", "bytes": 8_589_934_592, "count": 6},
              {"date": "2026-11-18", "bytes": 7_516_192_768, "count": 6}],
    "by_category": [{"category": "Video", "bytes": 42_949_672_960, "count": 18},
                    {"category": "Software", "bytes": 21_474_836_480, "count": 62},
                    {"category": "Images", "bytes": 10_737_418_240, "count": 24},
                    {"category": "Documents", "bytes": 5_368_709_120, "count": 17},
                    {"category": "General", "bytes": 3_682_447_360, "count": 7}],
}

QUEUE_STATUS = {"paused": False, "scheduler_gate": False, "max_concurrent": 3,
                "active": 2, "waiting": 2}

SYSTEM_STATS = {"cpu": 12.4, "memory_used": 5_368_709_120, "memory_total": 17_179_869_184,
                "disk_free": 214_748_364_800, "net_down": 60_355_584, "net_up": 1_048_576}

AUTO_SHUTDOWN = {"state": "Disabled", "enabled": False, "seconds_remaining": 0,
                 "countdown_seconds": 60}

UPDATE_STATE = {"state": "idle", "available": False, "current": "1.4.6",
                "latest": "1.4.6", "progress": 0}

SCHEDULED = [
    {"id": "s1", "status": "scheduled", "due_at": NOW + 7200,
     "local_time": "02:00", "last_error": "",
     "payload": {"url": "https://example.com/nightly-backup.tar.gz",
                 "label": "nightly-backup.tar.gz", "project_id": "default",
                 "directory": r"C:\Users\N13\Downloads"}},
    {"id": "s2", "status": "paused", "due_at": NOW + 86400,
     "local_time": "tomorrow 09:30", "last_error": "",
     "payload": {"url": "https://mirror.example.net/debian-13.1.0-amd64-netinst.iso",
                 "label": "", "project_id": "linux",
                 "directory": r"C:\Users\N13\Downloads"}},
]


def _stub(theme: str, lang: str) -> str:
    settings = dict(SETTINGS, language=lang, theme=theme)
    payload = {
        "downloads": DOWNLOADS, "history": HISTORY, "settings": settings,
        "projects": PROJECTS, "stats": STATS, "analytics": ANALYTICS,
        "queue": QUEUE_STATUS, "sys": SYSTEM_STATS, "as": AUTO_SHUTDOWN,
        "update": UPDATE_STATE, "scheduled": SCHEDULED,
        "theme": {"theme": theme, "accent": "#EF4444", "sidebarCollapsed": False},
    }
    # Raw string: this block is JavaScript.  Without `r`, Python ate the
    # backslashes in `"\\"` and `[\\/]`, which both mangled the path-joining
    # stub and emitted a SyntaxWarning on every run.
    return r"""
<script>
(function () {
  var D = __DATA__;
  var calls = [];
  window.__consoleErrors = [];
  var origErr = console.error;
  console.error = function () {
    window.__consoleErrors.push(Array.prototype.slice.call(arguments).join(" "));
    origErr.apply(console, arguments);
  };
  window.onerror = function (m, s, l, c) {
    window.__consoleErrors.push("onerror: " + m + " @" + l + ":" + c);
  };
  window.onunhandledrejection = function (e) {
    window.__consoleErrors.push("unhandled: " + (e.reason && e.reason.message || e.reason));
  };
  function fn(name, value) {
    return function () {
      calls.push(name);
      var v = typeof value === "function" ? value.apply(null, arguments) : value;
      return Promise.resolve(v);
    };
  }
  var api = {
    poll_events: fn("poll_events", []),
    get_downloads: fn("get_downloads", D.downloads),
    get_download: fn("get_download", D.downloads[0]),
    get_history: fn("get_history", D.history),
    get_settings: fn("get_settings", D.settings),
    get_theme_config: fn("get_theme_config", D.theme),
    get_version: fn("get_version", "1.4.6"),
    get_stats: fn("get_stats", D.stats),
    get_system_stats: fn("get_system_stats", D.sys),
    get_analytics: fn("get_analytics", D.analytics),
    get_update_settings: fn("get_update_settings", {auto_check: true}),
    get_update_state: fn("get_update_state", D.update),
    scheduler_status: fn("scheduler_status", {enabled: false, next_window: ""}),
    live_server_status: fn("live_server_status", {running: false, port: 6868}),
    clipboard_status: fn("clipboard_status", {enabled: true, last_url: ""}),
    get_queue_status: fn("get_queue_status", D.queue),
    get_projects: fn("get_projects", D.projects),
    get_project_gates: fn("get_project_gates", {}),
    get_auto_shutdown_status: fn("get_auto_shutdown_status", D.as),
    list_scheduled: fn("list_scheduled", D.scheduled),
    parse_batch_urls: fn("parse_batch_urls",
      {accepted: 0, duplicates: 0, skipped: 0, urls: []}),
    // Without these the New-download dialog treated every URL as invalid and
    // painted a red "Enter a valid http:// or https:// link" under a perfectly
    // good link — a harness artefact that made the dialog screenshots lie.
    validate_url: fn("validate_url", function (url) {
      var raw = String(url || "").trim();
      var ok = /^https?:\/\/[^\s]+$/i.test(raw);
      return { valid: ok, normalized: raw };
    }),
    probe_url: fn("probe_url", function (url) {
      var raw = String(url || "");
      var name = (raw.split("?")[0].split("/").pop() || "download.bin");
      return { ok: true, filename: name, size_display: "6.1 GB", range: true };
    }),
    resolve_destination: fn("resolve_destination", function (dir, cat) {
      var base = String(dir || D.settings.download_dir).replace(/[\\/]+$/, "");
      return { directory: (cat && cat !== "General") ? base + "\\" + cat : base };
    }),
    check_duplicate: fn("check_duplicate", {}),
    get_logs: fn("get_logs", [
      {time: "23:04:11", level: "INFO", message: "Queue worker started (max_concurrent=3)"},
      {time: "23:04:12", level: "INFO", message: "Resuming 2 interrupted downloads"},
      {time: "23:04:14", level: "INFO", message: "Probe ubuntu-24.04.3-desktop-amd64.iso: 200, ranges=yes, size=6.1 GB"},
      {time: "23:04:15", level: "INFO", message: "Downloading with 8 connections"},
      {time: "23:04:31", level: "WARN", message: "Part 3 retry 1/15 after ReadTimeout"},
      {time: "23:05:02", level: "INFO", message: "Probe interstellar-2160p.mkv: 200, ranges=yes, size=40.0 GB"},
      {time: "23:05:44", level: "ERROR", message: "report.pdf failed: HTTP 403 - Forbidden"},
      {time: "23:06:10", level: "INFO", message: "Smart mode: scaled 8 -> 16 connections"},
    ]),
    get_extension_status: fn("get_extension_status", {installed: true, browsers: ["Chrome"]}),
    log_js: fn("log_js", null),
  };
  window.pywebview = { api: api };
})();
</script>
""".replace("__DATA__", json.dumps(payload))


def build_page(theme: str, lang: str) -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    stub = _stub(theme, lang)
    anchor = '<script src="js/utils.js"></script>'
    html = html.replace(anchor, stub + anchor, 1)
    prefs = json.dumps({"theme": theme, "lang": lang})
    boot = (
        "<script>try{localStorage.setItem('n13-prefs', %s);}catch(e){}</script>"
        % json.dumps(prefs)
    )
    # Headless capture runs on a virtual clock, so CSS entry animations never
    # advance and rows would be photographed at opacity 0.  Kill motion so the
    # screenshot shows the settled layout.
    freeze = (
        "<style>*,*::before,*::after{animation:none!important;"
        "transition:none!important}"
        ".dl-row,.toast,.sched-row{opacity:1!important}</style>"
    )
    html = html.replace("<head>", "<head>" + boot + freeze, 1)
    base = FRONTEND.as_uri()
    html = re.sub(
        r'\b(src|href)="(js|css|brand|favicon)',
        lambda m: '%s="%s/%s' % (m.group(1), base, m.group(2)),
        html,
    )
    return html


def drive(page_file: Path, page: str, out_png: Path, width: int, height: int,
          extra_js: str = "") -> str:
    driver = """
<script>
(function () {
  var page = "__PAGE__";
  function go() {
    if (page === "none") return;
    try { App.navigate(page); } catch (e) { console.error("navigate: " + e); }
  }
  setTimeout(go, 500);
  setTimeout(go, 1400);
  setTimeout(function () {
    try { __EXTRA__ } catch (e) { console.error("extra: " + e); }
  }, 1900);
})();
</script>
""".replace("__PAGE__", page).replace("__EXTRA__", extra_js or "")
    html = page_file.read_text(encoding="utf-8").replace("</body>", driver + "</body>", 1)
    tmp = page_file.with_name("shot.html")
    tmp.write_text(html, encoding="utf-8")
    profile = Path(tempfile.mkdtemp(prefix="n13-shot-profile-"))
    cmd = [
        str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        "--allow-file-access-from-files", "--virtual-time-budget=4000",
        "--window-size=%d,%d" % (width, height),
        "--user-data-dir=" + str(profile),
        "--screenshot=" + str(out_png),
        tmp.as_uri(),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    tmp.unlink(missing_ok=True)
    return proc.stderr or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="dark", choices=["dark", "light"])
    ap.add_argument("--lang", default="en", choices=["en", "fa"])
    ap.add_argument("--out", default="ui-shots")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--page", action="append", default=None)
    ap.add_argument("--name", default=None,
                    help="output basename override (default: the page name)")
    ap.add_argument("--js", default=None,
                    help="JS run ~1.9s after load, for opening dialogs/states")
    args = ap.parse_args()

    global CHROME
    CHROME = next((p for p in CHROME_CANDIDATES if p.exists()), None)
    if CHROME is None:
        print("no Chrome/Edge found")
        return 2

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    page_file = Path(tempfile.mkdtemp(prefix="n13-shot-")) / "page.html"
    page_file.write_text(build_page(args.theme, args.lang), encoding="utf-8")

    pages = args.page or PAGES
    suffix = "-%s-%s" % (args.theme, args.lang)
    for page in pages:
        name = args.name or page
        png = out_dir / (name + suffix + ".png")
        drive(page_file, page, png, args.width, args.height, args.js or "")
        print("wrote", png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
