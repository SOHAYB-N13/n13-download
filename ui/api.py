"""Web API — Python backend exposed to JavaScript via pywebview bridge."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.loader import config_dir, save_config
from config.settings import AppConfig
from core.session import SessionManager
from core.updater import UpdateController
from core.utils import normalize_url, validate_url
from ui.common import (
    DownloadRequest,
    TaskManager,
    TaskSnapshot,
    TaskState,
    human_size,
)
from ui.legacy import LegacyDownloadRunner

log = logging.getLogger("n13")


class Api:
    """Python API exposed to JavaScript via pywebview bridge.

    Every public method (except _-prefixed) is callable from JS as:
        await window.pywebview.api.methodName(args...)
    """

    def __init__(self, config: AppConfig, session: SessionManager) -> None:
        self._config = config
        self._session = session
        self._event_queue: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._window = None
        self._live_server = None
        self._updater = UpdateController(config)
        self._updater.subscribe(self._on_update_state)
        self._session_downloaded: int = 0
        self._win_maximized: bool = False
        self._speed_history: List[float] = []
        self._bw_history: List[float] = []
        self._net_baseline: Optional[tuple[int, int]] = None
        self._net_baseline_time: float = 0.0
        self._shutdown_done: bool = False
        self._tray = None

        from core.paths import data_dir, migrate_legacy_saved_links

        # Migrate any legacy project-relative saved_links/ into the per-user
        # data directory (idempotent, originals preserved).
        project_root = Path(__file__).resolve().parent.parent
        migrate_legacy_saved_links(project_root)

        runner = LegacyDownloadRunner(config, session, log=self._log)
        self._manager = TaskManager(
            runner,
            data_dir(),
            max_concurrent=getattr(config, "max_concurrent", 3),
            config=config,
        )
        self._manager.subscribe(self._on_task_event)

        # Scheduler: queue window gating + night speed override.
        from core.scheduler import Scheduler
        self._scheduler = Scheduler(
            config,
            on_gate=self._manager.set_scheduler_gate,
            on_speed=self._apply_scheduled_speed,
            logger=log,
        )
        self._scheduler.start()

        # Optional clipboard monitor (off by default).
        self._clipboard = None
        self._sync_clipboard_monitor()

        # Download rules (auto-configuration) stored under the user config dir.
        from core.rules import RuleEngine
        self._rules = RuleEngine(config_dir() / "rules.json")

        # Auto-start the loopback relay so browser integration is ready and a
        # second launch can forward URLs to this instance.  Best-effort: a
        # busy port simply means forwarding falls back to a friendly message.
        if getattr(config, "auto_start_server", True):
            try:
                self.start_live_server()
            except Exception:
                pass

    def _sync_clipboard_monitor(self) -> None:
        """Start/stop the clipboard monitor to match the config."""
        try:
            enabled = bool(getattr(self._config, "clipboard_monitor", False))
            if enabled and self._clipboard is None:
                from core.clipboard import ClipboardMonitor
                self._clipboard = ClipboardMonitor(
                    self._on_clipboard_url, logger=log
                )
                self._clipboard.start()
            elif not enabled and self._clipboard is not None:
                self._clipboard.stop()
                self._clipboard = None
        except Exception:
            self._clipboard = None

    def _on_clipboard_url(self, url: str) -> None:
        """A downloadable URL was copied while monitoring is on."""
        url = normalize_url(url)
        if not validate_url(url):
            return
        if getattr(self._config, "clipboard_autostart", False):
            try:
                allow, resolve = self._duplicate_policy_args(url, "", "")
                self.add_download(url, allow_duplicate=allow, resolve_conflict=resolve)
                self._event_queue.put_nowait({"type": "toast",
                                              "title": self._tray_labels().get("notification.download_started", "Download started"),
                                              "message": url[:80]})
            except Exception:
                pass
        else:
            self._event_queue.put_nowait({"type": "clipboard_url", "url": url})

    def _apply_scheduled_speed(self, bps: int) -> None:
        """Apply a scheduler-chosen bandwidth cap without mutating config.

        When the scheduler is off or outside the night window it reports the
        configured ``max_speed_bps``; in that case the override is *cleared* so
        future settings changes take effect, instead of the scheduler's value
        permanently pinning the limiter.
        """
        try:
            from core.throttle import set_schedule_override, sync_limiter_from_config
            configured = int(getattr(self._config, "max_speed_bps", 0) or 0)
            if int(bps or 0) == configured:
                set_schedule_override(None)
            else:
                set_schedule_override(bps)
            sync_limiter_from_config(self._config)
        except Exception:
            pass

    def set_window(self, window) -> None:
        self._window = window
        try:
            window.events.maximized += self._on_win_maximized
            window.events.restored += self._on_win_restored
            window.events.minimized += self._on_win_minimized
            # Fires before the window closes (title-bar X, Alt+F4, OS close).
            # Cleanup happens BEFORE the window is destroyed.
            window.events.closing += self._on_closing
        except Exception:
            pass
        self._start_tray()

    def _on_win_minimized(self) -> None:
        if getattr(self._config, "minimize_to_tray", True) and self._window:
            try:
                self._window.hide()
            except Exception:
                pass
        self._event_queue.put_nowait({"type": "window", "minimized": True})

    def _start_tray(self) -> None:
        """Create the optional system tray (best-effort; off without pywin32)."""
        try:
            from core.tray import SystemTray
            self._tray = SystemTray(
                on_show=self._tray_show,
                on_pause_all=lambda: self._manager.pause_all(),
                on_resume_all=lambda: self._manager.resume_all(),
                on_open_folder=self._tray_open_folder,
                on_settings=lambda: self._event_queue.put_nowait(
                    {"type": "navigate", "page": "settings"}),
                on_exit=self.shutdown,
            )
            self._tray.start()
            self._tray.set_labels(self._tray_labels())
            self._tray.set_tooltip(self._tray_tooltip())
        except Exception:
            self._tray = None

    def _tray_labels(self) -> dict[str, str]:
        """Return tray menu labels in the current UI language."""
        lang = getattr(self._config, "language", "en")
        labels: dict[str, dict[str, str]] = {
            "en": {
                "tray.show": "Show N13",
                "tray.pause_all": "Pause all",
                "tray.resume_all": "Resume all",
                "tray.open_folder": "Open downloads folder",
                "tray.settings": "Settings",
                "tray.exit": "Exit",
                "tray.active": "active",
                "notification.download_started": "Download started",
                "notification.download_complete": "Download complete",
                "notification.download_failed": "Download failed",
                "toast.download_added": "Download added",
            },
            "fa": {
                "tray.show": "نمایش N13",
                "tray.pause_all": "توقف همه",
                "tray.resume_all": "ادامهٔ همه",
                "tray.open_folder": "باز کردن پوشهٔ دانلودها",
                "tray.settings": "تنظیمات",
                "tray.exit": "خروج",
                "tray.active": "فعال",
                "notification.download_started": "دانلود شروع شد",
                "notification.download_complete": "دانلود کامل شد",
                "notification.download_failed": "دانلود ناموفق بود",
                "toast.download_added": "دانلود اضافه شد",
            },
        }
        return labels.get(lang, labels["en"])

    def _tray_show(self) -> None:
        w = self._window
        if w:
            try:
                w.show()
                w.restore()
                w.focus()
            except Exception:
                pass

    def _tray_open_folder(self) -> None:
        try:
            d = self._config.download_dir
            if d and os.path.isdir(d):
                subprocess.Popen(["explorer", d])
        except Exception:
            pass

    def _tray_tooltip(self) -> str:
        stats = self.get_stats()
        active = self._tray_labels().get("tray.active", "active")
        return "N13\n{} {} · {}/s".format(stats["running"], active, stats["total_speed_display"])

    def _tray_tick(self) -> None:
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.set_tooltip(self._tray_tooltip())
            except Exception:
                pass

    def _on_win_maximized(self) -> None:
        self._win_maximized = True
        self._event_queue.put_nowait({"type": "window", "maximized": True})

    def _on_win_restored(self) -> None:
        self._win_maximized = False
        self._event_queue.put_nowait({"type": "window", "maximized": False})

    def _log(self, msg: str, *args, **kwargs) -> None:
        self._event_queue.put_nowait({"type": "log", "message": str(msg)})

    def _on_task_event(self, event: str, snapshot: TaskSnapshot) -> None:
        if event == "finished" and snapshot.state == TaskState.COMPLETED:
            self._session_downloaded += max(snapshot.completed, snapshot.total, 0)
        self._event_queue.put_nowait({
            "type": "task",
            "event": event,
            "task": snapshot.to_dict(),
        })
        self._maybe_notify(event, snapshot)
        self._tray_tick()

    def _on_update_state(self, state: Dict[str, Any]) -> None:
        """Forward updater state changes to the frontend event stream."""
        self._event_queue.put_nowait({"type": "update_state", "state": state})

    def _maybe_notify(self, event: str, snapshot: TaskSnapshot) -> None:
        """Event-driven desktop notifications (never per-progress updates)."""
        cfg = self._config
        if not getattr(cfg, "notifications_enabled", True):
            return
        tray = getattr(self, "_tray", None)
        if tray is None:
            return
        name = snapshot.name
        labels = self._tray_labels()
        try:
            if event == "started" and getattr(cfg, "notify_started", False):
                tray.notify(labels.get("notification.download_started", "Download started"), name)
            elif event == "finished":
                if snapshot.state == TaskState.COMPLETED and getattr(cfg, "notify_completed", True):
                    tray.notify(labels.get("notification.download_complete", "Download complete"), name)
                elif snapshot.state == TaskState.FAILED and getattr(cfg, "notify_failed", True):
                    tray.notify(labels.get("notification.download_failed", "Download failed"),
                                f"{name} — {snapshot.error or 'unknown error'}")
        except Exception:
            pass

    # ── Event polling ─────────────────────────────────────────────

    def poll_events(self) -> List[Dict[str, Any]]:
        events = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def get_downloads(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._manager.snapshots()]

    def get_history(self) -> List[Dict[str, Any]]:
        return self._manager.history[:200]

    def clear_history(self) -> None:
        self._manager.clear_history()

    def remove_history_entry(self, task_id: str) -> None:
        self._manager.remove_history(task_id)

    def get_download(self, task_id: str) -> Optional[Dict[str, Any]]:
        snap = self._manager.get(task_id)
        return snap.to_dict() if snap else None

    # ── Download actions ──────────────────────────────────────────

    def _category_for_hint(self, hint: str, content_type: str = "") -> str:
        """Auto-detect a category from a filename/URL hint when enabled."""
        from core.analyzer import detect_category
        if not getattr(self._config, "auto_categorize", True):
            return "General"
        ext_map = getattr(self._config, "category_extensions", None) or {}
        return detect_category(hint, content_type, ext_map=ext_map)

    def _resolve_directory(self, directory: str, category: str, hint: str) -> str:
        """Pick the destination: explicit dir, else per-category override."""
        if directory:
            return directory
        base = self._config.download_dir
        cat = category or self._category_for_hint(hint)
        return self._config.resolve_category_dir(cat, base) or base

    def check_duplicate(self, url: str, directory: str = "", filename: str = "") -> Dict[str, Any]:
        """Duplicate-detection check for a download about to be added."""
        return self._manager.check_duplicate(url or "", directory or "", filename or "")

    def _rule_overrides(self, url: str, filename: str, size: int = 0, content_type: str = "") -> Optional[Dict[str, Any]]:
        """Best matching download rule, or None (only when rules are enabled)."""
        if not getattr(self._config, "rules_enabled", True):
            return None
        rule = self._rules.evaluate(url or "", filename or "", int(size or 0), content_type or "")
        if rule is None:
            return None
        return {
            "category": rule.category or "",
            "folder": rule.folder or "",
            "priority": rule.priority_value,
            "connection_mode": rule.connection_mode or "",
            "num_threads": rule.manual_connections if rule.connection_mode == "manual" else 0,
        }

    def add_download(self, url: str, directory: str = "", label: str = "",
                     checksum: str = "", autostart: bool = True,
                     category: str = "", allow_duplicate: bool = False,
                     resolve_conflict: str = "", size: int = 0,
                     content_type: str = "") -> str:
        hint = label or url
        rule = self._rule_overrides(url, hint, size, content_type)
        if rule:
            # Rules fill in only what the user has NOT explicitly chosen.
            if not category and rule["category"]:
                category = rule["category"]
            if not directory and rule["folder"]:
                directory = rule["folder"]
        cat = category or self._category_for_hint(hint)
        resolved = self._resolve_directory(directory, cat, hint)
        if resolve_conflict == "replace":
            self._delete_destination_file(resolved, label or "")
        priority = int(rule["priority"]) if rule else 5
        conn = rule["connection_mode"] if rule else ""
        nthreads = int(rule["num_threads"]) if rule else 0
        request = DownloadRequest(
            url=url, directory=resolved, checksum=checksum, label=label, category=cat,
            priority=priority, connection_mode=conn, num_threads=nthreads,
        )
        return self._manager.add(request, autostart=autostart, allow_duplicate=allow_duplicate)

    @staticmethod
    def _delete_destination_file(directory: str, filename: str) -> None:
        """Delete an existing destination file (Replace policy).

        Only a bare filename is ever removed (never a path / traversal), and
        only if it is a regular file.
        """
        if not filename:
            return
        name = Path(filename).name
        if not name or name in (".", ".."):
            return
        try:
            p = Path(directory) / name
            if p.is_file():
                p.unlink()
        except OSError:
            pass

    def _duplicate_policy_args(self, url: str, directory: str, filename: str) -> tuple:
        """(allow_duplicate, resolve_conflict) for non-interactive add flows."""
        policy = getattr(self._config, "duplicate_policy", "ask")
        allow = policy == "allow"
        resolve = "replace" if policy == "replace" else ""
        return allow, resolve

    def add_batch(self, urls: List[str], directory: str) -> int:
        from ui.common import name_from_url
        policy = getattr(self._config, "duplicate_policy", "ask")
        # "ask" in a batch context means auto-rename (never overwrite silently);
        # "replace" deletes existing destinations; "allow" permits duplicates.
        allow_duplicate = policy == "allow"
        requests = []
        for u in urls:
            if not u.strip():
                continue
            rule = self._rule_overrides(u, name_from_url(u))
            cat = self._category_for_hint(u)
            if rule and rule["category"]:
                cat = rule["category"]
            resolved = self._resolve_directory(directory, cat, u)
            if rule and rule["folder"] and not directory:
                resolved = rule["folder"]
            if policy == "replace":
                self._delete_destination_file(resolved, name_from_url(u))
            requests.append(DownloadRequest(
                url=u, directory=resolved, category=cat,
                priority=int(rule["priority"]) if rule else 5,
                connection_mode=rule["connection_mode"] if rule else "",
                num_threads=int(rule["num_threads"]) if rule else 0,
            ))
        if not requests:
            return 0
        self._manager.add_many(requests, autostart=False, allow_duplicate=allow_duplicate)
        self._manager.start_all()
        return len(requests)

    # ── Download rules CRUD ──────────────────────────────────────────

    def get_rules(self) -> List[Dict[str, Any]]:
        return self._rules.all()

    def add_rule(self, rule: Dict[str, Any]) -> str:
        from core.rules import DownloadRule
        r = DownloadRule.from_dict(rule)
        return self._rules.add(r)

    def update_rule(self, rule_id: str, fields: Dict[str, Any]) -> bool:
        return self._rules.update(rule_id, fields)

    def delete_rule(self, rule_id: str) -> bool:
        return self._rules.delete(rule_id)

    def duplicate_rule(self, rule_id: str) -> Optional[str]:
        return self._rules.duplicate(rule_id)

    def reorder_rules(self, rule_ids: List[str]) -> None:
        self._rules.reorder(rule_ids)

    def test_rule(self, url: str) -> Dict[str, Any]:
        """Rule preview: evaluate *url* and report the matched rule + actions."""
        from ui.common import name_from_url
        rule = self._rules.evaluate(url or "", name_from_url(url or ""))
        if rule is None:
            return {"matched": False, "rule": None}
        return {
            "matched": True,
            "rule": rule.to_dict(),
            "actions": {
                "category": rule.category or "",
                "folder": rule.folder or "",
                "priority": rule.priority_value,
                "connection_mode": rule.connection_mode or "",
                "num_threads": rule.manual_connections if rule.connection_mode == "manual" else 0,
            },
        }

    def pause_download(self, task_id: str) -> None:
        self._manager.pause_task(task_id)

    def resume_download(self, task_id: str) -> None:
        self._manager.resume_task(task_id)

    def cancel_download(self, task_id: str) -> None:
        self._manager.cancel_task(task_id)

    def retry_download(self, task_id: str) -> None:
        self._manager.retry_task(task_id)

    def remove_download(self, task_id: str) -> None:
        self._manager.remove_task(task_id)

    def clear_finished(self) -> None:
        self._manager.clear_finished()

    def clear_failed(self) -> int:
        return self._manager.clear_failed()

    def clear_completed(self) -> int:
        return self._manager.clear_completed()

    def retry_failed(self) -> int:
        return self._manager.retry_failed()

    def move_task(self, task_id: str, delta: int) -> None:
        self._manager.move_task(task_id, int(delta))

    def set_priority(self, task_id: str, priority: int) -> None:
        self._manager.set_priority(task_id, int(priority))

    def pause_all(self) -> None:
        self._manager.pause_all()

    def resume_all(self) -> None:
        self._manager.resume_all()

    def start_task(self, task_id: str) -> None:
        self._manager.start_task(task_id)

    def open_folder(self, task_id: str) -> None:
        snap = self._manager.get(task_id)
        if snap and os.path.isdir(snap.request.directory):
            subprocess.Popen(["explorer", snap.request.directory])

    def open_file(self, task_id: str) -> bool:
        """Open the downloaded file with its default application."""
        snap = self._manager.get(task_id)
        if not snap:
            return False
        name = snap.filename or snap.name
        path = os.path.join(snap.request.directory, name)
        try:
            if os.path.isfile(path):
                os.startfile(path)  # type: ignore[attr-defined]
                return True
            return False
        except OSError:
            return False

    def redownload(self, task_id: str) -> None:
        """Re-queue a task (works for failed/cancelled/completed)."""
        self._manager.retry_task(task_id)

    def open_path(self, path: str) -> bool:
        """Open an arbitrary directory in Explorer (history page)."""
        try:
            if path and os.path.isdir(path):
                subprocess.Popen(["explorer", path])
                return True
        except Exception:
            pass
        return False

    def open_file_from_history(self, entry: Dict[str, Any]) -> bool:
        """Open a history entry's file with its default application."""
        try:
            name = (entry or {}).get("name") or ""
            directory = (entry or {}).get("directory") or ""
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                os.startfile(path)  # type: ignore[attr-defined]
                return True
        except OSError:
            pass
        return False

    def open_file_at(self, path: str) -> bool:
        """Open a file by its absolute path (duplicate-conflict 'Open')."""
        try:
            if path and os.path.isfile(path):
                os.startfile(path)  # type: ignore[attr-defined]
                return True
        except OSError:
            pass
        return False

    # ── Pattern Scan ─────────────────────────────────────────────

    def scan_pattern(self, pattern: str, directory: str = "", start: int = 1, padding: int = 2) -> Dict[str, Any]:
        from batch.pattern import scan_pattern_urls
        try:
            urls = scan_pattern_urls(
                pattern, self._config, self._session,
                start_num=start, padding=padding,
                quiet=True,
            )
            return {"urls": urls, "count": len(urls)}
        except Exception as e:
            return {"urls": [], "count": 0, "error": str(e)}

    # ── Delete File ──────────────────────────────────────────────

    def delete_file(self, task_id: str) -> bool:
        snap = self._manager.get(task_id)
        if not snap:
            return False
        name = snap.filename or snap.name
        if not name:
            return False
        file_path = os.path.join(snap.request.directory, name)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                return True
            return False
        except OSError:
            return False

    # ── URL validation ────────────────────────────────────────────

    def validate_url(self, url: str) -> Dict[str, Any]:
        url = normalize_url(url)
        valid = validate_url(url)
        return {"valid": valid, "normalized": url if valid else ""}

    def probe_url(self, url: str) -> Dict[str, Any]:
        """Lightweight HEAD/range probe used by the New Download dialog to
        auto-detect filename and size before the download starts."""
        from core.probe import probe_url as _probe
        url = normalize_url(url or "")
        if not validate_url(url):
            return {"ok": False, "error": "Enter a valid http(s) URL", "normalized": ""}
        try:
            ok, size, supports_range, filename, err = _probe(
                url, self._config, self._session, timeout=12
            )
        except Exception as exc:  # never leak a traceback into the UI
            return {"ok": False, "error": str(exc), "normalized": url}
        return {
            "ok": ok,
            "size": int(size or 0),
            "size_display": human_size(size) if size else "",
            "range": bool(supports_range),
            "filename": filename or "",
            "error": err or "",
            "normalized": url,
        }

    def analyze_url(self, url: str) -> Dict[str, Any]:
        """Full pre-download analysis (filename, size, server, media detection)."""
        from core.analyzer import analyze_url as _analyze
        url = normalize_url(url or "")
        if not validate_url(url):
            return {"ok": False, "error": "Enter a valid http(s) URL"}
        try:
            a = _analyze(url, self._config, self._session, timeout=12)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        result = a.to_dict()
        result["normalized"] = url
        result["size_display"] = human_size(a.total_size) if a.total_size else ""
        return result

    # ── Settings ──────────────────────────────────────────────────

    def get_settings(self) -> Dict[str, Any]:
        d = asdict(self._config)
        result = {}
        for k, v in d.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                result[k] = v
            elif isinstance(v, (list, tuple, dict)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def update_settings(self, settings: Dict[str, Any]) -> bool:
        for k, v in settings.items():
            if hasattr(self._config, k):
                current = getattr(self._config, k)
                if isinstance(current, bool):
                    setattr(self._config, k, bool(v))
                elif isinstance(current, int):
                    setattr(self._config, k, int(v))
                elif isinstance(current, float):
                    setattr(self._config, k, float(v))
                elif isinstance(current, (dict, list)):
                    setattr(self._config, k, v)
                else:
                    setattr(self._config, k, v)
        save_config(self._config)
        try:
            self._session.configure(self._config)
        except Exception:
            pass
        from core.throttle import sync_limiter_from_config
        sync_limiter_from_config(self._config)
        self._sync_clipboard_monitor()
        if "max_concurrent" in settings:
            try:
                self._manager.set_max_concurrent(int(self._config.max_concurrent))
            except Exception:
                pass
        if "language" in settings and self._tray is not None:
            try:
                self._tray.set_labels(self._tray_labels())
            except Exception:
                pass
        return True

    def select_directory(self) -> str:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askdirectory(initialdir=self._config.download_dir)
        root.destroy()
        return path or ""

    def select_file(self) -> str:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename()
        root.destroy()
        return path or ""

    def read_text_file(self, path: str) -> Dict[str, Any]:
        """Read a small text file (used to import batch URL lists)."""
        try:
            p = Path(path or "")
            if not p.is_file():
                return {"ok": False, "text": "", "error": "File not found"}
            if p.stat().st_size > 2_000_000:
                return {"ok": False, "text": "", "error": "File is too large (max 2 MB)"}
            return {"ok": True, "text": p.read_text(encoding="utf-8", errors="ignore")}
        except OSError as exc:
            return {"ok": False, "text": "", "error": str(exc)}

    # ── Window controls (frameless window) ──────────────────────────

    def window_minimize(self) -> None:
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass

    def window_toggle_maximize(self) -> None:
        if not self._window:
            return
        try:
            if self._win_maximized:
                self._window.restore()
            else:
                self._window.maximize()
        except Exception:
            pass

    def window_close(self) -> None:
        """Frameless X button."""
        if getattr(self._config, "close_to_tray", False):
            if self._window:
                try:
                    self._window.hide()
                except Exception:
                    pass
            return
        # Normal close: run the full safe shutdown, then destroy.
        self.shutdown()

    def window_set_bounds(self, x: int, y: int, width: int, height: int) -> None:
        """Move + resize from the JS frameless border-drag handler."""
        if not self._window:
            return
        try:
            width = max(1120, int(width))
            height = max(680, int(height))
            self._window.move(int(x), int(y))
            self._window.resize(width, height)
        except Exception:
            pass

    def log_js(self, message: str) -> None:
        """Frontend error reporting — surfaces JS exceptions in stderr."""
        log.warning("JS: %s", message)

    # ── Theme ─────────────────────────────────────────────────────

    def _load_ui_prefs(self) -> Dict[str, Any]:
        prefs_path = config_dir() / "ui_prefs.json"
        try:
            return json.loads(prefs_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_ui_prefs(self, prefs: Dict[str, Any]) -> None:
        prefs_path = config_dir() / "ui_prefs.json"
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

    def get_theme_config(self) -> Dict[str, Any]:
        prefs = self._load_ui_prefs()
        return {
            "theme": prefs.get("theme", "dark"),
            "accent": prefs.get("accent", "#4f8ef7"),
        }

    def save_theme_config(self, prefs: Dict[str, Any]) -> None:
        existing = self._load_ui_prefs()
        existing.update({k: prefs[k] for k in ("theme", "accent") if k in prefs})
        self._save_ui_prefs(existing)

    # ── Updates ───────────────────────────────────────────────────

    def get_version(self) -> str:
        from core.version import get_version
        return get_version()

    def get_update_settings(self) -> Dict[str, Any]:
        prefs = self._load_ui_prefs()
        return {
            "current_version": self.get_version(),
            "auto_update_check": bool(getattr(self._config, "auto_update_check", True)),
            "skipped_version": prefs.get("skipped_update_version", ""),
            "last_checked": prefs.get("update_last_checked", ""),
        }

    def set_auto_update_check(self, enabled: bool) -> bool:
        self._config.auto_update_check = bool(enabled)
        save_config(self._config)
        return True

    def check_for_updates(self, manual: bool = False) -> Dict[str, Any]:
        """Trigger a background update check.

        Manual checks always fetch; automatic checks update the last-checked
        timestamp and are used by the startup-once logic.
        """
        if not manual:
            prefs = self._load_ui_prefs()
            prefs["update_last_checked"] = datetime.now().isoformat()
            self._save_ui_prefs(prefs)
        self._updater.check()
        return self._updater.get_state()

    def get_update_state(self) -> Dict[str, Any]:
        return self._updater.get_state()

    def download_update(self) -> Dict[str, Any]:
        self._updater.download()
        return self._updater.get_state()

    def install_update(self, restart: bool = True) -> Dict[str, Any]:
        """Launch the verified installer and schedule safe shutdown.

        The installer is started in a detached process so it survives this
        process exiting. A short PowerShell wrapper waits for the installer
        and optionally restarts N13.
        """
        import sys
        import threading

        state = self._updater.get_state()
        if state.get("state") != "ready":
            return {"status": "not_ready"}

        app_dir = Path(sys.executable).resolve().parent

        def _install_then_exit() -> None:
            try:
                # Give the JS response a moment to return before tearing down.
                import time
                time.sleep(0.5)
                self._updater.install(app_dir=app_dir, restart=restart)
                self.shutdown()
            except Exception as exc:
                log.error("Update install failed: %s", exc)

        threading.Thread(target=_install_then_exit, daemon=False).start()
        return {"status": "installing"}

    def skip_update_version(self, version: str) -> bool:
        prefs = self._load_ui_prefs()
        prefs["skipped_update_version"] = version
        self._save_ui_prefs(prefs)
        return True

    def clear_skipped_update(self) -> bool:
        prefs = self._load_ui_prefs()
        prefs.pop("skipped_update_version", None)
        self._save_ui_prefs(prefs)
        return True

    # ── Dashboard stats ───────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        snaps = self._manager.snapshots()
        total_speed = sum(s.speed_bps for s in snaps if s.state == TaskState.DOWNLOADING)
        return {
            "running": sum(1 for s in snaps if s.state == TaskState.DOWNLOADING),
            "queued": sum(1 for s in snaps if s.state == TaskState.QUEUED),
            "paused": sum(1 for s in snaps if s.state == TaskState.PAUSED),
            "completed": sum(1 for s in snaps if s.state == TaskState.COMPLETED),
            "failed": sum(1 for s in snaps if s.state == TaskState.FAILED),
            "total_speed_bps": total_speed,
            "total_speed_display": human_size(total_speed) + "/s",
        }

    def get_analytics(self) -> Dict[str, Any]:
        """Local-only download analytics computed from history + live tasks."""
        from datetime import datetime
        from pathlib import Path

        hist = self._manager.history
        completed = [h for h in hist if h.get("status") == "Complete"]
        failed = [h for h in hist if h.get("status") == "Failed"]
        cancelled = [h for h in hist if h.get("status") == "Cancelled"]
        total_bytes = sum(int(h.get("size_bytes") or 0) for h in hist)
        speeds = [float(h.get("avg_speed") or 0) for h in completed if h.get("avg_speed")]
        durations = [float(h.get("duration") or 0) for h in completed]
        by_day: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        by_mode: Dict[str, int] = {"smart": 0, "manual": 0, "inherit": 0}

        for h in hist:
            cat = h.get("category") or "General"
            by_category[cat] = by_category.get(cat, 0) + 1
            ext = Path(h.get("name") or "").suffix.lstrip(".").lower() or "none"
            by_type[ext] = by_type.get(ext, 0) + 1
            mode = h.get("connection_mode") or ""
            by_mode[mode if mode in by_mode else "inherit"] += 1
            try:
                day = datetime.strptime(str(h.get("finished", ""))[:10], "%Y-%m-%d")
                key = day.strftime("%Y-%m-%d")
                by_day[key] = by_day.get(key, 0) + 1
            except (ValueError, TypeError):
                pass

        # Live tasks also count toward mode usage.
        for s in self._manager.snapshots():
            mode = s.connection_mode or ""
            by_mode[mode if mode in by_mode else "inherit"] += 1

        top_categories = sorted(by_category.items(), key=lambda kv: -kv[1])[:8]
        top_types = sorted(by_type.items(), key=lambda kv: -kv[1])[:10]

        return {
            "total_downloads": len(hist),
            "completed": len(completed),
            "failed": len(failed),
            "cancelled": len(cancelled),
            "total_bytes": total_bytes,
            "total_bytes_display": human_size(total_bytes),
            "avg_speed": (sum(speeds) / len(speeds)) if speeds else 0.0,
            "peak_speed": max(speeds) if speeds else 0.0,
            "avg_duration": (sum(durations) / len(durations)) if durations else 0.0,
            "total_duration": sum(durations),
            "downloads_per_day": dict(sorted(by_day.items())),
            "by_category": dict(top_categories),
            "by_type": dict(top_types),
            "by_mode": by_mode,
        }

    def get_system_stats(self) -> Dict[str, Any]:
        cpu = ram = 0.0
        disk_free = disk_total = disk_used = 0
        disk_percent = 0.0
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            du = psutil.disk_usage(self._config.download_dir)
            disk_free, disk_total, disk_used = du.free, du.total, du.used
            disk_percent = round((disk_used / disk_total) * 100, 1) if disk_total else 0.0
        except ImportError:
            pass
        return {
            "cpu_percent": round(cpu, 1),
            "ram_percent": round(ram, 1),
            "disk_free": disk_free,
            "disk_free_display": human_size(disk_free),
            "disk_total": disk_total,
            "disk_total_display": human_size(disk_total),
            "disk_used": disk_used,
            "disk_used_display": human_size(disk_used),
            "disk_percent": disk_percent,
            "session_downloaded": self._session_downloaded,
            "session_downloaded_display": human_size(self._session_downloaded),
        }

    # ── Browser integration ───────────────────────────────────────

    def start_live_server(self) -> bool:
        from browser.live_server import LiveServer
        server = LiveServer(
            self._config, self._session,
            download_callback=self._browser_callback,
        )
        if server.start():
            self._live_server = server
            return True
        return False

    def stop_live_server(self) -> None:
        if self._live_server:
            try:
                self._live_server.stop()
            except Exception:
                pass
            self._live_server = None

    def live_server_status(self) -> Dict[str, Any]:
        return {
            "running": self._live_server is not None,
            "host": self._config.live_server_host,
            "port": self._config.live_server_port,
            "token": self._config.live_server_token[:12] + "...",
        }

    def scheduler_status(self) -> Dict[str, Any]:
        """Scheduler state (enabled, current gate, active speed cap)."""
        try:
            from core.throttle import get_global_limiter
            limiter = get_global_limiter()
            speed = limiter.max_rate if limiter else 0
        except Exception:
            speed = 0
        return {
            "enabled": bool(getattr(self._config, "scheduler_enabled", False)),
            "start_time": self._config.schedule_start_time,
            "stop_time": self._config.schedule_stop_time,
            "night_cap_bps": int(getattr(self._config, "night_speed_limit_bps", 0) or 0),
            "night_start": self._config.night_start_time,
            "night_end": self._config.night_end_time,
            "current_speed_bps": speed,
        }

    def clipboard_status(self) -> Dict[str, Any]:
        return {
            "monitoring": bool(getattr(self._config, "clipboard_monitor", False)),
            "autostart": bool(getattr(self._config, "clipboard_autostart", False)),
        }

    def create_extension(self) -> str:
        from browser.protocol import create_chrome_extension
        ext_dir = create_chrome_extension()
        token_path = ext_dir / "token.json"
        data = json.dumps({
            "live_server_url": f"http://127.0.0.1:{self._config.live_server_port}/download",
            "token": self._config.live_server_token,
        })
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(data, encoding="utf-8")
        return str(ext_dir)

    def register_protocol(self) -> bool:
        from browser.protocol import register_protocol
        return register_protocol()

    def unregister_protocol(self) -> bool:
        from browser.protocol import unregister_protocol
        return unregister_protocol()

    def _browser_callback(self, url: str, autostart: bool = False) -> bool:
        url = normalize_url(url)
        if validate_url(url):
            if autostart:
                # Single-instance forwarding: add directly to the queue.
                allow, resolve = self._duplicate_policy_args(url, "", "")
                self.add_download(url, allow_duplicate=allow, resolve_conflict=resolve)
                self._event_queue.put_nowait({"type": "toast",
                                              "title": self._tray_labels().get("toast.download_added", "Download added"),
                                              "message": url[:80]})
            else:
                self._event_queue.put_nowait({"type": "browser_url", "url": url})
        return True

    # ── Shutdown ──────────────────────────────────────────────────
    # One authoritative shutdown path.  It is idempotent and thread-safe, and
    # is invoked by BOTH the frameless X button (window_close) and the
    # pywebview ``closing`` event (covers the OS title bar close / Alt+F4).

    def shutdown(self) -> None:
        """Full safe shutdown: stop transfers, persist, cleanup, destroy window."""
        self._cleanup()
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass

    def _on_closing(self) -> None:
        """pywebview ``closing`` event.

        With ``close_to_tray`` the window hides to the tray and the close is
        cancelled; otherwise the graceful shutdown runs and the window closes.
        """
        if getattr(self._config, "close_to_tray", False) and not getattr(self, "_shutdown_done", False):
            if self._window:
                try:
                    self._window.hide()
                except Exception:
                    pass
            return False   # cancel the close
        self._cleanup()
        return None

    def _cleanup(self) -> None:
        """Idempotent, thread-safe teardown shared by both close paths."""
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        try:
            if getattr(self, "_scheduler", None) is not None:
                self._scheduler.stop()
        except Exception:
            pass
        # Graceful exit: pause transfers, persist their resumable state, then
        # cancel the paused workers so the process can terminate promptly.
        try:
            self._manager.prepare_for_exit()
        except Exception:
            pass
        self.stop_live_server()
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
            self._tray = None
        if getattr(self, "_clipboard", None) is not None:
            try:
                self._clipboard.stop()
            except Exception:
                pass
            self._clipboard = None
        try:
            save_config(self._config)
        except Exception:
            pass
        try:
            self._manager.close()
        except Exception:
            pass
        try:
            self._session.close()
        except Exception:
            pass
