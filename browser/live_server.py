"""Authenticated live server for Chrome extension integration.

The Live Server is a tiny authenticated queue running on localhost.  The
browser extension POSTs a URL; the server validates the bearer token using a
constant-time comparison, enforces SSRF protection, and feeds the URL to the
download engine through a worker thread.

Security notes
--------------
* The token is compared with :func:`secrets.compare_digest` to avoid timing
  side channels.
* The server binds to loopback only and CORS is restricted to local origins
  so that arbitrary websites cannot reach it from a remote machine.
* A request size cap guards against memory abuse.
"""

from __future__ import annotations

import json
import queue
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from rich.console import Console

from config.settings import AppConfig
from core.download import DownloadController
from core.security import validate_download_url
from core.session import SessionManager

console = Console()

# Origins allowed to issue cross-origin requests to the server.  The extension
# runs from a chrome-extension://<id> origin, but we also accept localhost so
# the popup / dev tools can call the API directly.
_ALLOWED_CORS_ORIGINS = (
    "http://localhost",
    "http://127.0.0.1",
)

# Reject request bodies larger than 64 KiB; a download URL is always tiny.
_MAX_BODY_BYTES = 64 * 1024


class LiveServer:
    """Threaded, authenticated queue server bound to loopback."""

    def __init__(
        self,
        config: AppConfig,
        session: SessionManager,
        download_callback: Optional[Callable[[str, bool], bool]] = None,
    ):
        self.config = config
        self.session = session
        self.download_callback = download_callback
        self.download_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._stats = {"queued": 0, "completed": 0, "failed": 0}
        self._stats_lock = threading.Lock()

    def _inc_stat(self, key: str, amount: int = 1) -> None:
        """Thread-safe stat counter increment."""
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + amount

    def _get_stats(self) -> dict:
        """Return a snapshot of stats under the lock."""
        with self._stats_lock:
            return dict(self._stats)

    # ------------------------------------------------------------------ #
    # Authentication / helpers
    # ------------------------------------------------------------------ #
    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        """Constant-time bearer-token check."""
        token = self.config.live_server_token
        if not token:
            return False
        auth = handler.headers.get("Authorization", "") or ""
        header_token = handler.headers.get("X-TDM-Token", "") or ""
        candidate = ""
        if auth.startswith("Bearer "):
            candidate = auth[len("Bearer "):].strip()
        elif header_token:
            candidate = header_token.strip()
        if not candidate:
            return False
        # compare_digest raises ValueError when lengths differ.
        if len(candidate) != len(token):
            return False
        return secrets.compare_digest(candidate, token)

    @staticmethod
    def _cors_origin_for(handler: BaseHTTPRequestHandler) -> Optional[str]:
        """Echo the request Origin only if it is a trusted local origin."""
        origin = handler.headers.get("Origin", "") or ""
        if not origin:
            return None
        # chrome-extension://<id> origins are allowed (the extension itself).
        if origin.startswith("chrome-extension://"):
            return origin
        host = urlparse(origin).hostname or ""
        if host in ("localhost", "127.0.0.1"):
            return origin
        return None

    def _send_json(
        self, handler: BaseHTTPRequestHandler, code: int, payload: dict
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        origin = self._cors_origin_for(handler)
        if origin:
            handler.send_header("Access-Control-Allow-Origin", origin)
            handler.send_header("Vary", "Origin")
        handler.send_header("Access-Control-Allow-Private-Network", "true")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            # Avoid DNS reverse lookups on every connection.
            def address_string(self) -> str:
                return self.client_address[0]

            def log_message(self, format, *args):  # noqa: A002 - signature fixed by stdlib
                pass

            def _cors_headers(self) -> None:
                origin = server._cors_origin_for(self)
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Authorization, Content-Type, X-TDM-Token, X-Requested-With",
                )
                self.send_header("Access-Control-Allow-Private-Network", "true")

            def do_OPTIONS(self):
                self.send_response(HTTPStatus.NO_CONTENT)
                self._cors_headers()
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    server._send_json(
                        self,
                        HTTPStatus.OK,
                        {
                            "status": "ok",
                            "queued": server.download_queue.qsize(),
                            "stats": server._get_stats(),
                        },
                    )
                    return
                self.send_response(HTTPStatus.NOT_FOUND)
                self._cors_headers()
                self.end_headers()

            def do_POST(self):
                if not server._authorized(self):
                    server._send_json(
                        self, HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"}
                    )
                    return

                parsed = urlparse(self.path)
                if parsed.path not in ("/download", "/queue", "/download_many"):
                    self.send_response(HTTPStatus.NOT_FOUND)
                    self._cors_headers()
                    self.end_headers()
                    return

                try:
                    length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    length = 0
                if length <= 0 or length > _MAX_BODY_BYTES:
                    server._send_json(
                        self, HTTPStatus.BAD_REQUEST, {"error": "Invalid request size"}
                    )
                    return

                try:
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        server._send_json(
                            self,
                            HTTPStatus.BAD_REQUEST,
                            {"error": "Incomplete request body"},
                        )
                        return
                    data = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    server._send_json(
                        self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"}
                    )
                    return

                if parsed.path == "/download_many":
                    # Batch endpoint: {"urls": ["https://...", ...]}
                    urls = data.get("urls") if isinstance(data.get("urls"), list) else []
                    autostart = bool(data.get("autostart", False))
                    accepted, rejected = server._validate_and_queue(urls, autostart=autostart)
                    server._send_json(
                        self,
                        HTTPStatus.OK,
                        {"status": "queued", "accepted": accepted, "rejected": rejected},
                    )
                    return

                url = (data.get("url") or "").strip()
                if not url:
                    server._send_json(
                        self, HTTPStatus.BAD_REQUEST, {"error": "Missing url"}
                    )
                    return
                autostart = bool(data.get("autostart", False))
                accepted, _rejected = server._validate_and_queue([url], autostart=autostart)
                if accepted != 1:
                    server._send_json(
                        self, HTTPStatus.BAD_REQUEST, {"error": "Invalid URL"}
                    )
                    return
                server._send_json(
                    self,
                    HTTPStatus.OK,
                    {
                        "status": "queued",
                        "position": server.download_queue.qsize(),
                    },
                )

        return Handler

    def _validate_and_queue(self, urls, autostart: bool = False) -> tuple:
        """Validate + queue a list of URLs.

        Returns ``(accepted_count, rejected_count)``.  Invalid URLs are counted
        as rejected and skipped; SSRF validation runs on every URL.
        """
        accepted = 0
        rejected = 0
        seen = set()
        for raw in urls:
            url = str(raw or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            ok, err = validate_download_url(
                url, block_private=self.config.block_private_urls
            )
            if not ok:
                rejected += 1
                continue
            self.download_queue.put((url, autostart))
            self._inc_stat("queued")
            accepted += 1
        return accepted, rejected

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #
    def _process_queue(self) -> None:
        """Worker thread: drain the download queue sequentially.

        Callback contract
        -----------------
        ``download_callback(url)`` may return:
        * ``True``  — the URL was delegated to an external handler (the GUI
          queue).  No internal download happens; a ``delegated`` stat is kept.
        * anything else / raises — fall back to downloading here as before.

        Each download gets its own ``DownloadController`` constructed with the
        shared ``SessionManager``.  Since this worker processes one URL at a
        time sequentially, sharing the session is safe — but we re-create the
        controller per iteration so any per-download state (retries, progress
        counters) is cleanly reset between jobs.
        """
        download_dir = Path(self.config.download_dir)

        while not self.stop_event.is_set():
            try:
                item = self.download_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            # Queue items are (url, autostart) tuples.
            url, autostart = item if isinstance(item, tuple) else (item, False)

            console.print(f"\n[bold yellow]Received: {url}[/bold yellow]")
            if self.download_callback:
                try:
                    delegated = bool(self.download_callback(url, autostart=autostart))
                except Exception as exc:  # callback must not break the worker
                    console.print(f"[red]Download callback error: {exc}[/red]")
                    delegated = False
                if delegated:
                    self._inc_stat("delegated")
                    self.download_queue.task_done()
                    console.print("[cyan]Handed to the desktop queue.[/cyan]")
                    continue

            # Fresh controller per download: no state bleed between jobs.
            controller = DownloadController(self.config, self.session, console.print)
            try:
                ok = controller.download_file(url, download_dir)
            except Exception as exc:
                console.print(f"[red]Download failed: {exc}[/red]")
                ok = False
            if ok:
                self._inc_stat("completed")
            else:
                self._inc_stat("failed")

            self.download_queue.task_done()
            console.print("\n[cyan]Waiting for more links...[/cyan]")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        host = self.config.live_server_host or "127.0.0.1"
        port = self.config.live_server_port
        # Never bind to a public interface: enforce loopback.
        if host not in ("127.0.0.1", "::1", "localhost"):
            console.print(
                f"[yellow]Live server host '{host}' is not loopback; "
                "forcing 127.0.0.1 for security.[/yellow]"
            )
            host = "127.0.0.1"
        try:
            self._server = ThreadingHTTPServer((host, port), self.make_handler())
        except OSError as exc:
            console.print(
                f"[bold red]Could not start server on {host}:{port}: {exc}[/bold red]"
            )
            return False

        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

        worker = threading.Thread(target=self._process_queue, daemon=True)
        worker.start()
        return True

    def stop(self) -> None:
        self.stop_event.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def run_live_server(config: AppConfig, session: SessionManager) -> None:
    """Run the live server until the user presses ENTER."""
    if not config.live_server_token:
        console.print("[red]Live server token missing. Regenerate config.[/red]")
        return

    server = LiveServer(config, session)
    if not server.start():
        return

    # Keep the installable extension's token.json in sync with the real token
    # this server now validates (idempotent; never rotates the credential).
    try:
        from browser.protocol import sync_extension_token
        sync_extension_token(config)
    except Exception:
        pass

    port = config.live_server_port
    host = config.live_server_host or "127.0.0.1"
    console.print(
        f"\n[bold green]Live server running on http://{host}:{port}[/bold green]"
    )
    console.print("[cyan]Authenticated queue API: POST /download or /queue[/cyan]")
    console.print("[cyan]Health check: GET /health[/cyan]")
    console.print(f"[dim]Token (for extension): {config.live_server_token[:8]}...[/dim]")
    console.print("[dim](Press ENTER to stop)[/dim]")

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass

    server.stop()
    console.print("[yellow]Server stopped.[/yellow]")
