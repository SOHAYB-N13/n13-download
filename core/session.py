"""HTTP session management.

Builds a single shared :class:`requests.Session` configured from the
application config: realistic browser-like defaults, a sized connection
pool, optional proxy, HTTP authentication (basic + bearer), and cookies.

Two transports exist:

* :attr:`SessionManager.session` — the download transport.  urllib3 retries
  connection-level failures with a small backoff and tolerates read stalls.
* :attr:`SessionManager.probe_session` — the metadata/probe transport.  It
  retries almost nothing (connect=1, read=0) so a probe can never be
  multiplied into a multi-minute gate before a download starts.

A short-TTL DNS cache (60 s) avoids re-resolving the same host for every new
connection while never pinning changing CDN addresses for long.
"""

from __future__ import annotations

import platform
import socket
import threading
import time
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

from config.settings import AppConfig
from core.cookies import cookie_header_from_config, resolve_cookie_jar

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# DNS short-TTL cache — lightweight, thread-safe, never pins addresses long.
# ---------------------------------------------------------------------------

_DNS_TTL = 60.0
_DNS_CACHE: dict = {}
_DNS_LOCK = threading.Lock()
_real_getaddrinfo = socket.getaddrinfo


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """getaddrinfo with a 60-second per-host cache (successes only)."""
    key = (host, port, family, type, proto, flags)
    now = time.monotonic()
    with _DNS_LOCK:
        hit = _DNS_CACHE.get(key)
        if hit is not None and now - hit[0] < _DNS_TTL:
            return hit[1]
    result = _real_getaddrinfo(host, port, family, type, proto, flags)
    with _DNS_LOCK:
        if len(_DNS_CACHE) > 1024:
            _DNS_CACHE.clear()
        _DNS_CACHE[key] = (now, result)
    return result


if not getattr(socket, "_n13_dns_cache_installed", False):
    socket.getaddrinfo = _cached_getaddrinfo
    socket._n13_dns_cache_installed = True  # type: ignore[attr-defined]

# Socket buffer sizes — critical for high-BDP connections.
# Default SO_RCVBUF on most platforms is ~64-256 KB, far too small
# for high-latency/high-bandwidth links.  We set a 4 MB receive buffer
# and 512 KB send buffer (kernel doubles the set value on most systems).
# Applied via socket_options which are set *before* connect().
# These defaults can be overridden via AppConfig.socket_buffer_size.
_SO_RCVBUF_DEFAULT = 4 * 1024 * 1024  # 4 MB
_SO_SNDBUF = 512 * 1024               # 512 KB (send is less critical)
_IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Custom adapter — TCP optimisations at the adapter level
# ---------------------------------------------------------------------------

class _OptimisedAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that tunes TCP stack on every connection.

    Sets socket options at the pool-manager level so they apply to every
    connection created by the pool.  This is the correct place: using an
    adapter subclass avoids the previous approach of mutating
    ``HTTPConnectionPool.default_socket_options`` at the *class* level, which
    was a permanent global mutation that affected every requests.Session in the
    process and accumulated duplicate entries on each ``_build_session()`` call.
    """

    def __init__(self, so_rcvbuf: int = 0, **kwargs):
        self._so_rcvbuf = so_rcvbuf
        super().__init__(**kwargs)

    def send(self, *args, **kwargs):  # type: ignore[override]
        return super().send(*args, **kwargs)

    def _build_socket_options(self) -> list:
        import socket as _socket
        rcvbuf = self._so_rcvbuf if self._so_rcvbuf > 0 else _SO_RCVBUF_DEFAULT
        opts = [
            (_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1),
            (_socket.SOL_SOCKET, _socket.SO_RCVBUF, rcvbuf),
            (_socket.SOL_SOCKET, _socket.SO_SNDBUF, _SO_SNDBUF),
        ]
        if not _IS_WINDOWS:
            opts.append((_socket.IPPROTO_TCP, getattr(_socket, "TCP_QUICKACK", 14), 1))
        return opts

    def init_poolmanager(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("socket_options", self._build_socket_options())
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):  # type: ignore[override]
        proxy_kwargs.setdefault("socket_options", self._build_socket_options())
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class SessionManager:
    """Lazy, reconfigurable wrapper around :class:`requests.Session`."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self._config = config
        self._session: Optional[requests.Session] = None
        self._probe_session: Optional[requests.Session] = None

    def configure(self, config: AppConfig) -> None:
        """Bind (or rebind) a configuration; the next access rebuilds the pool."""
        self._config = config
        self.close()

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = self._build_session()
        return self._session

    @property
    def probe_session(self) -> requests.Session:
        """Metadata/probe transport: retries almost nothing so a probe can
        never be multiplied into a multi-minute startup gate."""
        if self._probe_session is None:
            from urllib3.util.retry import Retry

            probe_retry = Retry(
                total=1,
                connect=1,
                read=0,               # never multiply a probe read timeout
                status=0,
                raise_on_status=False,
                backoff_factor=0.1,
                allowed_methods={"GET", "HEAD"},
            )
            self._probe_session = self._build_session(retry=probe_retry)
        return self._probe_session

    def _build_session(self, retry: Optional[object] = None) -> requests.Session:
        config = self._config
        session = requests.Session()

        # Realistic browser-like defaults.
        # NOTE: Accept-Encoding is intentionally NOT set here so urllib3
        # can negotiate the optimal encoding with the server.  Setting it
        # manually (especially to "identity") disables urllib3's content
        # negotiation and can cause some CDNs to behave sub-optimally.
        # urllib3 transparently decompresses gzip/deflate/brotli, which
        # adds minimal CPU overhead (~1-2% per 100 MB/s) vs the benefit
        # of proper server-side content negotiation.
        session.headers.update(
            {
                "User-Agent": config.user_agent if config else DEFAULT_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )

        pool_conn = config.pool_connections if config else 64
        pool_size = config.pool_maxsize if config else 64

        # Use urllib3's Retry for transient connection-level failures only
        # (not HTTP errors — those are handled by DownloadController).
        from urllib3.util.retry import Retry

        if retry is None:
            retry = Retry(
                total=3,
                connect=3,
                read=2,
                status=0,               # HTTP status retries handled by controller
                raise_on_status=False,
                backoff_factor=0.5,
                allowed_methods={"GET", "HEAD"},
            )

        so_rcvbuf = config.socket_buffer_size if config else 0
        adapter = _OptimisedAdapter(
            so_rcvbuf=so_rcvbuf,
            pool_connections=pool_conn,
            pool_maxsize=pool_size,
            max_retries=retry,
            pool_block=False,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        if config is not None:
            self._apply_networking(session, config)

        return session

    @staticmethod
    def _apply_networking(session: requests.Session, config: AppConfig) -> None:
        # --- Proxy ---------------------------------------------------------
        proxies = config.get_proxy_dict()
        if proxies:
            session.proxies.update(proxies)
            session.trust_env = False  # ignore HTTP_PROXY env surprises

        # --- HTTP authentication ------------------------------------------
        # Bearer token takes precedence; basic auth otherwise.
        if config.http_bearer_token:
            token = config.http_bearer_token.strip()
            session.headers["Authorization"] = f"Bearer {token}"
        elif config.http_username:
            session.auth = HTTPBasicAuth(
                config.http_username, config.http_password or ""
            )

        # --- Cookies -------------------------------------------------------
        # A CookieJar (Netscape file / browser import) is the richest source.
        jar = resolve_cookie_jar(config)
        if jar is not None:
            session.cookies.update(jar)
        # A raw header string is applied as a default header and also merged
        # into the session cookie store so redirects keep the cookies.
        header = cookie_header_from_config(config)
        if header:
            session.headers["Cookie"] = header

    def close(self) -> None:
        for attr in ("_session", "_probe_session"):
            s = getattr(self, attr, None)
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
                setattr(self, attr, None)
