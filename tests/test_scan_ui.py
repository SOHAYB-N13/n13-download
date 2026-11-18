"""Headless-browser regression for the Pattern Scan SUBMIT flow.

Loads the real index.html with a stubbed pywebview bridge, drives the exact
user steps (scan -> select -> Review & confirm -> Add to Queue), and asserts
on the browser console and bridge calls. This is the test that reproduces the
`source is not defined` ReferenceError at browser level.

Skipped automatically when Chrome/Edge is unavailable.
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
PACKAGED_FRONTEND = ROOT / "dist" / "N13" / "_internal" / "ui" / "frontend"
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
CHROME = next((p for p in CHROME_CANDIDATES if p.exists()), None)
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Edge available")

REPORT = {
    "items": [
        {"url": "https://x.test/e-01.mkv", "requested_url": "https://x.test/e-01.mkv",
         "number": 1, "available": True, "status": "available", "filename": "e-01.mkv",
         "size": 1024, "content_type": "video/x-matroska", "server": "nginx", "error": ""},
        {"url": "https://x.test/e-02.mkv", "requested_url": "https://x.test/e-02.mkv",
         "number": 2, "available": False, "status": "not_found", "filename": "",
         "size": 0, "content_type": "", "server": "", "error": "HTTP 404 - Not Found"},
    ],
    "urls": ["https://x.test/e-01.mkv"],
    "discovered": 2, "available": 1, "unavailable": 1, "failed_probes": 0,
    "scanned": 2, "stop_reason": "consecutive_404", "cancelled": False,
}

STUB = """
<script>
(function () {
  var REPORT = __REPORT__;
  var calls = [];
  window.__calls = calls;
  window.__consoleErrors = [];
  var origErr = console.error;
  console.error = function () { window.__consoleErrors.push(Array.from(arguments).join(" ")); origErr.apply(console, arguments); };
  window.onerror = function (msg, src, line, col) {
    window.__consoleErrors.push("onerror: " + msg + " @" + line + ":" + col + " " + src);
  };
  function fn(name, value) { return function () { calls.push(name); return Promise.resolve(typeof value === "function" ? value.apply(null, arguments) : value); }; }
  var SETTINGS = { max_concurrent: 1, max_speed_bps: 0, scheduler_enabled: false,
                   schedule_days: [], schedule_start_time: "", schedule_stop_time: "",
                   language: "en", download_dir: "C:/Downloads" };
  window.pywebview = { api: {
    poll_events: fn("poll_events", []),
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
    get_queue_status: fn("get_queue_status", { paused: false, scheduler_gate: false, max_concurrent: 1 }),
    get_projects: fn("get_projects", [{ id: "default", name: "Default", is_default: true }]),
    get_project_gates: fn("get_project_gates", {}),
    parse_batch_urls: fn("parse_batch_urls", { accepted: 0, duplicates: 0, skipped: 0, urls: [] }),
    scan_pattern_preview: fn("scan_pattern_preview", REPORT),
    add_batch_report: fn("add_batch_report", { added: 1, duplicates: 0, skipped: 0, failed: 0, total: 1, ids: ["t1"] }),
    list_scheduled: fn("list_scheduled", []),
    log_js: fn("log_js", null)
  } };
  var api = window.pywebview.api;
  window.__addBatchArgs = [];
  var orig = api.add_batch_report;
  api.add_batch_report = function () {
    window.__addBatchArgs.push(Array.prototype.slice.call(arguments));
    return orig.apply(this, arguments);
  };
})();
</script>
"""

DRIVER = """
<script>
(function () {
  var out = { errors: [], calls: [], addBatchArgs: [], consoleErrors: [], resultText: "" };
  function q(s) { return document.querySelector(s); }
  function qa(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function step(name, fn) {
    try { fn(); } catch (e) { out.errors.push(name + ": " + e + " | " + (e.stack || "")); }
  }
  function finish() {
    out.calls = window.__calls || [];
    out.addBatchArgs = window.__addBatchArgs || [];
    out.consoleErrors = window.__consoleErrors || [];
    out.resultText = (q(".scan-submit-result") || {}).textContent || "";
    out.errorText = (q("[data-scan-error]") || {}).textContent || "";
    document.title = "SRESULT" + JSON.stringify(out) + "ENDSR";
  }
  var steps = [
    function () { App.navigate("batch"); },
    function () {
      var patternTab = q('[data-tab="pattern"]');
      patternTab.click();
      q("#patternUrl").value = "https://x.test/e-*.mkv";
      q("#patternStart").value = "1";
      q("#patternPadding").value = "2";
      q("#patternDir").value = "D:\\\\N13\\\\Shows";
    },
    function () { q("#btnPatternScan").click(); },
    function () { /* results render */ },
    function () {
      out.rows = qa("[data-scan-pick]").length;
      var box = q("[data-scan-pick]");
      if (box) { box.checked = true; box.dispatchEvent(new Event("change", { bubbles: true })); }
      var group = q("[data-scan-group]");
      if (group) group.value = "default";
      q("[data-scan-confirm]").click();
    },
    function () { /* modal opens */ },
    function () {
      out.modal = !!q("[data-cf-go]");
      if (q("[data-cf-go]")) q("[data-cf-go]").click();
    },
    function () { /* submit resolves */ },
    finish
  ];
  var i = 0;
  function next() {
    if (i >= steps.length) return;
    step("step" + i, steps[i++]);
    setTimeout(next, 180);
  }
  setTimeout(next, 700);
})();
</script>
"""


def _build_page(frontend: Path = FRONTEND) -> str:
    html = (frontend / "index.html").read_text(encoding="utf-8")
    stub = STUB.replace("__REPORT__", json.dumps(REPORT))
    anchor = '<script src="js/utils.js"></script>'
    assert anchor in html, "index.html no longer loads utils.js where expected"
    html = html.replace(anchor, stub + anchor, 1)
    assert "</body>" in html
    html = html.replace("</body>", DRIVER + "</body>", 1)
    base = frontend.as_uri()
    html = re.sub(
        r'\b(src|href)="(js|css)/',
        lambda m: '%s="%s/%s/' % (m.group(1), base, m.group(2)),
        html,
    )
    return html


def _drive(frontend: Path = FRONTEND) -> dict:
    html = _build_page(frontend)
    page = Path(tempfile.mkdtemp(prefix="n13-scan-preview-")) / "preview.html"
    page.write_text(html, encoding="utf-8")
    profile = Path(tempfile.mkdtemp())
    try:
        proc = subprocess.run(
            [str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--allow-file-access-from-files", "--virtual-time-budget=7000",
             "--user-data-dir=" + str(profile), "--dump-dom", page.as_uri()],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        try:
            page.unlink(missing_ok=True)
        except OSError:
            pass
    m = re.search(r"SRESULT(.*?)ENDSR", proc.stdout, re.S)
    if not m:
        pytest.fail(
            "the page never reported a result.\n"
            f"--- stdout ---\n{proc.stdout[:4000]}\n--- stderr ---\n{proc.stderr[:2000]}"
        )
    return json.loads(m.group(1))


def test_scan_to_add_to_queue_in_real_browser():
    result = _drive()
    assert result["errors"] == [], result["errors"]
    assert result["consoleErrors"] == [], result["consoleErrors"]
    assert result["rows"] == 2
    assert result["modal"] is True
    assert result["errorText"] == "", "no submission error may be shown"
    assert "add_batch_report" in result["calls"]
    assert len(result["addBatchArgs"]) == 1, "exactly one submission"
    args = result["addBatchArgs"][0]
    assert args[0] == ["https://x.test/e-01.mkv"]
    assert args[1] == r"D:\N13\Shows"
    # An empty group means the backend's Default behaviour; a populated
    # selector carries the chosen id. Both are valid submission payloads.
    assert args[2] in ("", "default")
    assert args[3] is False, "queue behaviour must not autostart"
    assert "added" in result["resultText"]


def test_packaged_frontend_scan_submission_in_real_browser():
    """The shipped bundle must contain the fixed JS and submit correctly."""
    if not (PACKAGED_FRONTEND / "js/features/batch/scan-results.js").exists():
        pytest.skip("packaged application has not been built")
    shipped = (PACKAGED_FRONTEND / "js/features/batch/scan-results.js").read_text(encoding="utf-8")
    source_js = (FRONTEND / "js/features/batch/scan-results.js").read_text(encoding="utf-8")
    assert shipped == source_js, "packaged scan-results.js is stale"
    result = _drive(PACKAGED_FRONTEND)
    assert result["errors"] == [], result["errors"]
    assert result["consoleErrors"] == [], result["consoleErrors"]
    assert result["addBatchArgs"], result
    assert result["addBatchArgs"][0][0] == ["https://x.test/e-01.mkv"]
