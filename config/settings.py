"""Application configuration dataclass.

Centralizes every tunable knob of the download manager: threading, retry
strategy, networking (proxy / cookies / auth), bandwidth shaping, browser
integration, and security defaults.  Values are persisted to JSON via
``config.loader``.
"""

from __future__ import annotations

import os
import re
from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_download_dir() -> str:
    """Pick the most sensible per-user downloads directory.

    Windows exposes this through the registry; on other platforms we fall
    back to ``~/Downloads`` and finally ``$HOME``.
    """
    candidates = [
        Path.home() / "Downloads",
        Path.home(),
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
        except OSError:
            continue
    return str(Path.home())


def _safe_category_dirname(category: str) -> Optional[str]:
    """Return *category* as a single safe directory name, or None.

    Guards against path traversal / separator injection coming from arbitrary
    rule category values, while allowing the built-in category names (and
    simple custom names containing letters, digits, spaces, hyphens,
    underscores or dots).
    """
    name = (category or "").strip()
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    if Path(name).name != name:
        return None
    if re.search(r'[<>:"|?*]', name):
        return None
    return name


@dataclass
class AppConfig:
    """Strongly-typed, serializable application configuration."""

    # --- Destination / threading -------------------------------------------
    download_dir: str = field(default_factory=_default_download_dir)
    num_threads: int = 16
    max_concurrent: int = 3                      # simultaneous downloads
    chunk_size: int = 4 * 1024 * 1024            # read/write chunk for streams
    min_part_size: int = 2 * 1024 * 1024         # smallest range a thread takes
    buffer_size: int = 8 * 1024 * 1024           # merge buffer

    # --- Connection mode ----------------------------------------------------
    # "smart"  — the Smart Download Optimizer picks and adapts the connection
    #            count (size-aware initial selection + safe adaptive scaling).
    # "manual" — the classic fixed `num_threads` behaviour (unchanged).
    connection_mode: str = "smart"
    smart_max_connections: int = 8               # ceiling for Smart mode
    smart_adaptive: bool = True                  # adaptive ramp in Smart mode

    # --- Duplicate downloads -------------------------------------------------
    # How to handle a download whose URL / destination already exists:
    #   "ask"    — show a conflict dialog (interactive) / auto-rename (batch)
    #   "allow"  — always allow duplicates
    #   "rename" — always auto-rename the new file (unique name)
    #   "replace" — delete the existing destination file and re-download
    duplicate_policy: str = "ask"

    # --- Download rules ------------------------------------------------------
    # Automatic rules (core/rules.py) configure new downloads; off = disabled.
    rules_enabled: bool = True

    # --- Desktop notifications ------------------------------------------------
    # Balloon notifications via the system tray (event-driven, never per-progress).
    notifications_enabled: bool = True
    notify_completed: bool = True
    notify_failed: bool = True
    notify_started: bool = False
    notify_batch: bool = True

    # --- Retry strategy ----------------------------------------------------
    max_retries: int = 15
    retry_delay: float = 3.0                     # base delay between attempts
    retry_backoff: float = 2.0                   # exponential multiplier
    retry_jitter: float = 0.25                   # +/- jitter fraction (0..0.5)
    retry_max_delay: float = 120.0               # cap a single backoff sleep

    # --- Startup budget ------------------------------------------------------
    # Bounds for the period BEFORE the first byte of a download is received.
    # A flaky server must never stall the startup for tens of seconds, while a
    # healthy transfer keeps its normal (generous) timeout behaviour.
    probe_connect_timeout: float = 4.0           # probe: connect timeout (s)
    probe_read_timeout: float = 8.0              # probe: read timeout (s)
    startup_connect_timeout: float = 10.0        # transfer pre-first-byte connect (s)
    startup_read_timeout: float = 15.0           # transfer pre-first-byte read (s)
    startup_max_attempts: int = 6                # pre-first-byte attempt cap

    # --- Speed tracking ----------------------------------------------------
    speed_sample_interval: float = 0.2
    speed_window_size: int = 20

    # --- File extensions ---------------------------------------------------
    state_extension: str = ".dlstate"
    temp_extension: str = ".tmp"

    # --- Security / SSL ----------------------------------------------------
    verify_ssl: bool = True
    allow_insecure_ssl: bool = False
    block_private_urls: bool = True              # SSRF protection
    require_protocol_confirm: bool = True

    # --- Scheduling --------------------------------------------------------
    schedule_time: Optional[str] = None

    # --- Startup / session behaviour ---------------------------------------
    # Automatically continue unfinished downloads that were restored from the
    # persistent store when the application starts.
    resume_on_startup: bool = False
    start_minimized: bool = False
    # Minimize / close go to the system tray instead of the taskbar / exit.
    minimize_to_tray: bool = True
    close_to_tray: bool = False

    # --- Scheduler (queue-wide start/stop windows + night speed cap) --------
    scheduler_enabled: bool = False
    schedule_start_time: Optional[str] = None      # "HH:MM" — pause until
    schedule_stop_time: Optional[str] = None       # "HH:MM" — pause from
    night_speed_limit_bps: int = 0                 # cap during the night window
    night_start_time: Optional[str] = "23:00"
    night_end_time: Optional[str] = "07:00"

    # --- Categories ---------------------------------------------------------
    # Per-category destination directory overrides (category -> path).
    category_dirs: Dict[str, str] = field(default_factory=dict)
    # Automatically assign a category from the file extension / content type.
    auto_categorize: bool = True
    # Optional custom category -> extension list (overrides the built-ins).
    # e.g. {"Videos": ["mkv", "mp4", "mov"]}
    category_extensions: Dict[str, List[str]] = field(default_factory=dict)

    # --- Clipboard monitoring ----------------------------------------------
    clipboard_monitor: bool = False
    clipboard_autostart: bool = False              # auto-download detected links

    # --- Language / UI ------------------------------------------------------
    language: str = "en"

    # --- Auto-update ----------------------------------------------------------
    auto_update_check: bool = True
    update_repo: str = "SOHAYB-N13/n13-download"

    # --- Networking identity ----------------------------------------------
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    # --- Proxy -------------------------------------------------------------
    proxy_url: Optional[str] = None              # e.g. http://host:port
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None

    # --- HTTP authentication ----------------------------------------------
    http_username: Optional[str] = None
    http_password: Optional[str] = None
    http_bearer_token: Optional[str] = None

    # --- Cookies -----------------------------------------------------------
    cookies: Optional[str] = None                # raw "k=v; k2=v2" string
    cookie_file: Optional[str] = None            # path to Netscape/Mozilla txt
    browser_cookies: Optional[str] = None        # "chrome" | "firefox" | "edge"

    # --- Bandwidth shaping -------------------------------------------------
    # Max bytes/sec shared across all threads; 0 = unlimited.
    max_speed_bps: int = 0

    # --- Browser integration ----------------------------------------------
    live_server_port: int = 6868
    live_server_token: str = ""
    live_server_host: str = "127.0.0.1"          # bind address
    # Auto-start the loopback relay when the GUI launches.  This keeps browser
    # integration ready and lets a second launch forward URLs to this instance.
    auto_start_server: bool = True

    # --- Checksum / integrity ---------------------------------------------
    # When True, verify that the written file matches Content-Length even when
    # the user did not pass --checksum.
    verify_size: bool = True

    # --- Connection pool sizing -------------------------------------------
    pool_connections: int = 64
    pool_maxsize: int = 64

    # --- TCP socket buffer (SO_RCVBUF / SO_SNDBUF) -----------------------
    # Default OS socket buffers (usually 64–256 KB) are far too small for
    # high-bandwidth / high-latency links.  4 MB receive buffer allows the
    # TCP window to grow sufficiently on typical consumer connections.
    # Set to 0 to leave the OS default unchanged.
    socket_buffer_size: int = 4 * 1024 * 1024  # 4 MB (kernel doubles it)

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Deserialise from a plain dict, clamping numeric fields to safe ranges.

        Unknown keys are silently ignored (forward-compatibility when a new
        version saves fields that an older build doesn't recognise).
        All integer / float fields are validated here so that a corrupt or
        hand-edited config file cannot push the download engine into an
        invalid state (e.g. num_threads=0 or chunk_size=0 causing ZeroDivisionError).
        """
        known = {f.name: (f.default_factory() if f.default_factory is not MISSING else f.default)
                 for f in fields(cls)}
        # Build a clean filtered dict, dropping any key not in the dataclass.
        filtered: Dict[str, Any] = {k: v for k, v in data.items() if k in known}

        # Attempt construction.  If an individual field has a wrong type
        # (e.g. a number stored as a string in a hand-edited file), try to
        # fall back field-by-field so one bad value doesn't wipe the whole cfg.
        try:
            instance = cls(**filtered)
        except (TypeError, ValueError):
            # Construction failed: build with defaults, then apply each valid
            # field individually so as many values as possible are preserved.
            instance = cls()
            for k, v in filtered.items():
                try:
                    setattr(instance, k, v)
                except (TypeError, ValueError, AttributeError):
                    pass  # keep dataclass default for this field

        # --- Clamp numeric fields to safe operating ranges ---------------
        instance.num_threads     = max(1,   min(64,  int(instance.num_threads or 1)))
        instance.max_concurrent  = max(1,   min(10,  int(getattr(instance, "max_concurrent", 3) or 3)))
        instance.chunk_size      = max(65_536, min(64 * 1024 * 1024, int(instance.chunk_size or 65_536)))
        instance.min_part_size   = max(65_536, int(instance.min_part_size or 65_536))
        instance.buffer_size     = max(65_536, min(256 * 1024 * 1024, int(instance.buffer_size or 65_536)))
        instance.max_retries     = max(0,   min(120, int(instance.max_retries or 0)))
        instance.retry_delay     = max(0.0, min(300.0, float(instance.retry_delay or 0)))
        instance.retry_backoff   = max(1.0, min(10.0, float(instance.retry_backoff or 1)))
        instance.retry_jitter    = max(0.0, min(0.5,  float(instance.retry_jitter or 0)))
        instance.retry_max_delay = max(1.0, min(3600.0, float(instance.retry_max_delay or 1)))
        instance.probe_connect_timeout = max(1.0, min(30.0, float(getattr(instance, "probe_connect_timeout", 4) or 4)))
        instance.probe_read_timeout = max(1.0, min(60.0, float(getattr(instance, "probe_read_timeout", 8) or 8)))
        instance.startup_connect_timeout = max(1.0, min(30.0, float(getattr(instance, "startup_connect_timeout", 10) or 10)))
        instance.startup_read_timeout = max(1.0, min(120.0, float(getattr(instance, "startup_read_timeout", 15) or 15)))
        instance.startup_max_attempts = max(1, min(15, int(getattr(instance, "startup_max_attempts", 6) or 6)))
        instance.max_speed_bps   = max(0, int(instance.max_speed_bps or 0))
        instance.live_server_port = max(1024, min(65535, int(instance.live_server_port or 6868)))
        instance.pool_connections = max(1, min(256, int(instance.pool_connections or 64)))
        instance.pool_maxsize     = max(1, min(256, int(instance.pool_maxsize or 64)))
        instance.socket_buffer_size = max(0, min(16 * 1024 * 1024, int(instance.socket_buffer_size or 0)))
        instance.speed_window_size = max(1, min(100, int(instance.speed_window_size or 20)))
        instance.speed_sample_interval = max(0.05, min(5.0, float(instance.speed_sample_interval or 0.2)))
        instance.connection_mode = str(instance.connection_mode or "smart")
        if instance.connection_mode not in ("smart", "manual"):
            instance.connection_mode = "smart"
        instance.smart_max_connections = max(1, min(64, int(instance.smart_max_connections or 8)))
        instance.smart_adaptive = bool(getattr(instance, "smart_adaptive", True))
        instance.duplicate_policy = str(instance.duplicate_policy or "ask")
        if instance.duplicate_policy not in ("ask", "allow", "rename", "replace"):
            instance.duplicate_policy = "ask"
        instance.rules_enabled = bool(getattr(instance, "rules_enabled", True))
        instance.minimize_to_tray = bool(getattr(instance, "minimize_to_tray", True))
        instance.close_to_tray = bool(getattr(instance, "close_to_tray", False))
        instance.notifications_enabled = bool(getattr(instance, "notifications_enabled", True))
        instance.notify_completed = bool(getattr(instance, "notify_completed", True))
        instance.notify_failed = bool(getattr(instance, "notify_failed", True))
        instance.notify_started = bool(getattr(instance, "notify_started", False))
        instance.notify_batch = bool(getattr(instance, "notify_batch", True))

        return instance

    def copy(self) -> "AppConfig":
        return AppConfig.from_dict(self.to_dict())

    # ------------------------------------------------------------------ #
    # Derived accessors
    # ------------------------------------------------------------------ #
    def get_schedule_datetime(self) -> Optional[datetime]:
        if not self.schedule_time:
            return None
        try:
            return datetime.fromisoformat(self.schedule_time)
        except ValueError:
            return None

    def resolve_category_dir(self, category: Optional[str], base_dir: str) -> str:
        """Return the destination directory for *category*.

        Precedence:
        1. An explicit per-category override in ``category_dirs``.
        2. Automatic category routing: ``<base_dir>/<category>`` (e.g.
           ``Downloads/Videos``) for any safe, non-``General`` category.
        3. ``base_dir`` itself (no category / General / unsafe category name).

        The returned directory is *not* created here — the download engine
        creates it on demand.
        """
        cat = (category or "").strip()
        overrides = self.category_dirs or {}
        if cat and cat != "General":
            if overrides.get(cat):
                return overrides[cat]
            name = _safe_category_dirname(cat)
            if name and base_dir:
                return os.path.join(base_dir, name)
        return base_dir or ""

    def set_schedule_datetime(self, value: Optional[datetime]) -> None:
        self.schedule_time = value.isoformat() if value else None

    def get_proxy_dict(self) -> Optional[Dict[str, str]]:
        """Build a ``requests``-style proxy mapping, or ``None`` if unset."""
        if not self.proxy_url:
            return None
        url = self.proxy_url.strip()
        if not url:
            return None
        # Allow bare "host:port" to mean HTTP proxying.
        if "://" not in url:
            url = "http://" + url
        # Inject credentials into the proxy URL when provided separately,
        # which is the only way requests honours proxy auth for HTTPS tunnels.
        if self.proxy_username:
            from urllib.parse import urlparse, urlunparse, quote
            parsed = urlparse(url)
            username = quote(self.proxy_username, safe="")
            password = quote(self.proxy_password or "", safe="")
            netloc = f"{username}:{password}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            url = urlunparse(parsed._replace(netloc=netloc))
        return {"http": url, "https": url}

    def get_proxy_auth(self) -> Optional[str]:
        """Return proxy username for informational use only.

        Credentials are now embedded directly in the proxy URL returned by
        :meth:`get_proxy_dict`, which is the correct way to pass proxy auth
        to ``requests`` for HTTPS tunnels.
        """
        return self.proxy_username or None


DEFAULT_CONFIG = AppConfig()
