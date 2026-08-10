"""Application configuration dataclass.

Centralizes every tunable knob of the download manager: threading, retry
strategy, networking (proxy / cookies / auth), bandwidth shaping, browser
integration, and security defaults.  Values are persisted to JSON via
``config.loader``.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


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

    # --- Retry strategy ----------------------------------------------------
    max_retries: int = 15
    retry_delay: float = 3.0                     # base delay between attempts
    retry_backoff: float = 2.0                   # exponential multiplier
    retry_jitter: float = 0.25                   # +/- jitter fraction (0..0.5)
    retry_max_delay: float = 120.0               # cap a single backoff sleep

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
        instance.max_speed_bps   = max(0, int(instance.max_speed_bps or 0))
        instance.live_server_port = max(1024, min(65535, int(instance.live_server_port or 6868)))
        instance.pool_connections = max(1, min(256, int(instance.pool_connections or 64)))
        instance.pool_maxsize     = max(1, min(256, int(instance.pool_maxsize or 64)))
        instance.socket_buffer_size = max(0, min(16 * 1024 * 1024, int(instance.socket_buffer_size or 0)))
        instance.speed_window_size = max(1, min(100, int(instance.speed_window_size or 20)))
        instance.speed_sample_interval = max(0.05, min(5.0, float(instance.speed_sample_interval or 0.2)))

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
