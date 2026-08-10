"""Web API — Python backend exposed to JavaScript via pywebview bridge."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.loader import config_dir, save_config
from config.settings import AppConfig
from core.session import SessionManager
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
        self._session_downloaded: int = 0
        self._win_maximized: bool = False
        self._speed_history: List[float] = []
        self._bw_history: List[float] = []
        self._net_baseline: Optional[tuple[int, int]] = None
        self._net_baseline_time: float = 0.0

        data_dir = Path(__file__).resolve().parent.parent / "saved_links"
        runner = LegacyDownloadRunner(config, session, log=self._log)
        self._manager = TaskManager(runner, data_dir, max_concurrent=getattr(config, "max_concurrent", 3))
        self._manager.subscribe(self._on_task_event)

    def set_window(self, window) -> None:
        self._window = window
        try:
            window.events.maximized += self._on_win_maximized
            window.events.restored += self._on_win_restored
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

    def get_download(self, task_id: str) -> Optional[Dict[str, Any]]:
        snap = self._manager.get(task_id)
        return snap.to_dict() if snap else None

    # ── Download actions ──────────────────────────────────────────

    def add_download(self, url: str, directory: str = "", label: str = "", checksum: str = "", autostart: bool = True) -> str:
        directory = directory or self._config.download_dir
        request = DownloadRequest(url=url, directory=directory, checksum=checksum, label=label)
        return self._manager.add(request, autostart=autostart)

    def add_batch(self, urls: List[str], directory: str) -> int:
        requests = [DownloadRequest(url=u, directory=directory) for u in urls if u.strip()]
        if not requests:
            return 0
        self._manager.add_many(requests, autostart=False)
        self._manager.start_all()
        return len(requests)

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

    def open_path(self, path: str) -> bool:
        """Open an arbitrary directory in Explorer (history page)."""
        try:
            if path and os.path.isdir(path):
                subprocess.Popen(["explorer", path])
                return True
        except Exception:
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
        import os
        from core.utils import filename_from_url
        name = snap.request.label or filename_from_url(snap.request.url)
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

    # ── Settings ──────────────────────────────────────────────────

    def get_settings(self) -> Dict[str, Any]:
        d = asdict(self._config)
        result = {}
        for k, v in d.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                result[k] = v
            elif isinstance(v, (list, tuple)):
                result[k] = list(v)
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
                else:
                    setattr(self._config, k, v)
        save_config(self._config)
        try:
            self._session.configure(self._config)
        except Exception:
            pass
        from core.throttle import sync_limiter_from_config
        sync_limiter_from_config(self._config)
        if "max_concurrent" in settings:
            try:
                self._manager.set_max_concurrent(int(self._config.max_concurrent))
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
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass

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

    def get_theme_config(self) -> Dict[str, Any]:
        prefs_path = config_dir() / "ui_prefs.json"
        try:
            return json.loads(prefs_path.read_text(encoding="utf-8"))
        except Exception:
            return {"theme": "dark", "accent": "#4f8ef7"}

    def save_theme_config(self, prefs: Dict[str, Any]) -> None:
        prefs_path = config_dir() / "ui_prefs.json"
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

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

    def _browser_callback(self, url: str) -> bool:
        url = normalize_url(url)
        if validate_url(url):
            self._event_queue.put_nowait({"type": "browser_url", "url": url})
        return True

    # ── Version ───────────────────────────────────────────────────

    def get_version(self) -> str:
        try:
            from __init__ import __version__
            return __version__
        except ImportError:
            return "2.0.0"

    # ── Shutdown ──────────────────────────────────────────────────

    def shutdown(self) -> None:
        from core.context import DownloadContext
        DownloadContext.request_cancel()
        self._manager.shutdown(cancel=True, wait=True, timeout=2.5)
        self.stop_live_server()
        save_config(self._config)
        try:
            self._session.close()
        except Exception:
            pass
