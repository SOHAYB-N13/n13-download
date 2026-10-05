"""Shared test helpers (in-memory HTTP server with Range support)."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config.settings import AppConfig

# ~5 MB of pseudo-random bytes.
DATA = os.urandom(5 * 1024 * 1024 + 12345)

# ~9 MB — exercises multi-chunk segments.
LARGE_DATA = os.urandom(9 * 1024 * 1024 + 777)


class RangeHandler(BaseHTTPRequestHandler):
    """Serves ``DATA`` with Range support, optional failure injection."""

    # Failure injection knobs (per-instance via class attrs set by tests).
    fail_requests: int = 0          # fail this many GETs with 500
    fail_mode: str = "once"         # "once" | "always" | "range-only"
    server_override: str = "testserver"

    def log_message(self, *args):  # silence
        pass

    def send_response(self, code, message=None):
        self.log_request(code)
        self.send_response_only(code, message)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("ETag", '"abc123"')
        self.send_header("Server", self.server_override)
        self.end_headers()

    def do_GET(self):
        if RangeHandler.fail_requests > 0:
            RangeHandler.fail_requests -= 1
            self.send_response(500)
            self.end_headers()
            return

        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            chunk = DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("ETag", '"abc123"')
        self.send_header("Server", self.server_override)
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class NoRangeHandler(RangeHandler):
    """Server that ignores Range requests (always 200, full body)."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        try:
            self.wfile.write(DATA)
        except (BrokenPipeError, ConnectionResetError):
            pass


class TrueNoRangeHandler(NoRangeHandler):
    """Server that genuinely does NOT support ranges (no Accept-Ranges on HEAD)."""

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()


class RedirectHandler(BaseHTTPRequestHandler):
    """Server that 302-redirects every request to the real file on /file.bin."""

    def log_message(self, *args):
        pass

    def send_response(self, code, message=None):
        self.log_request(code)
        self.send_response_only(code, message)

    def do_HEAD(self):
        if self.path != "/file.bin":
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        if self.path != "/file.bin":
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
            return
        # Serve DATA with proper range support.
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            chunk = DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class FlakyHandler(RangeHandler):
    """Resets the connection once, then serves normally (tests retry/resume)."""

    resets_left: int = 1

    def do_GET(self):
        if FlakyHandler.resets_left > 0:
            FlakyHandler.resets_left -= 1
            # Abort the response mid-write: no Content-Length, then close.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                self.wfile.write(DATA[: max(1, len(DATA) // 4)])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.connection.close()
            return
        super().do_GET()


class SlowHandler(RangeHandler):
    """Range server that sleeps briefly per chunk (simulates a slow server)."""

    def do_GET(self):
        import time as _time
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            chunk = DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            for i in range(0, len(chunk), 256 * 1024):
                self.wfile.write(chunk[i:i + 256 * 1024])
                self.wfile.flush()
                _time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            pass


class BigRangeHandler(RangeHandler):
    """Serves LARGE_DATA (~9 MB) with correct Range support."""

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(LARGE_DATA)))
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = LARGE_DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{len(LARGE_DATA) - 1}/{len(LARGE_DATA)}")
        else:
            chunk = LARGE_DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class NotFoundHandler(RangeHandler):
    def do_HEAD(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        self.send_response(404)
        self.end_headers()


class TestServer:
    """Context manager wrapping a ThreadingHTTPServer."""

    def __init__(self, handler_cls=RangeHandler):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        RangeHandler.fail_requests = 0
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/file.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


def test_config(**overrides) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False  # tests use loopback
    cfg.num_threads = 4
    cfg.verify_size = True
    cfg.max_retries = 4
    cfg.retry_delay = 0.01
    cfg.retry_max_delay = 0.2
    # Engine tests exercise the fixed Manual connection behaviour; Smart mode
    # is covered separately by tests/test_optimizer.py.
    cfg.connection_mode = "manual"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg
