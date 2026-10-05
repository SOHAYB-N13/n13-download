"""Release hardening — real-world server behavior test matrix.

Focus: validate the multi-part `skip_bytes` logic and resume correctness across
server behaviors — Range, no-Range, range-advertising-but-ignoring, redirects,
large files, slow servers, and mid-stream connection resets.  Every case must
end with the correct final size AND checksum (no skipped/duplicated/gapped
bytes).
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.control import TaskControl
from core.download import DownloadController
from core.session import SessionManager
from tests.helpers import (
    DATA,
    LARGE_DATA,
    BigRangeHandler,
    FlakyHandler,
    NoRangeHandler,
    RangeHandler,
    RedirectHandler,
    SlowHandler,
    TestServer,
    TrueNoRangeHandler,
)


def _cfg(num_threads: int, **kw) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.connection_mode = 'manual'
    cfg.num_threads = num_threads
    cfg.verify_size = True
    cfg.max_retries = 5
    cfg.retry_delay = 0.01
    cfg.retry_max_delay = 0.2
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify(path: Path, expected: bytes) -> bool:
    return path.exists() and path.stat().st_size == len(expected) and \
        _checksum(path) == hashlib.sha256(expected).hexdigest()


class NetworkMatrixTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _download(self, handler_cls, num_threads=4, expected=DATA, **cfg_kw):
        with TestServer(handler_cls) as srv:
            cfg = _cfg(num_threads=num_threads, **cfg_kw)
            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            ok = ctrl.download_file(srv.url, self.dir, control=TaskControl())
            self.assertTrue(ok, f"download failed for {handler_cls.__name__}")
            saved = self.dir / "file.bin"
            self.assertTrue(_verify(saved, expected),
                            f"{handler_cls.__name__}: size/checksum mismatch")

    def test_range_server(self):
        self._download(RangeHandler, num_threads=4)

    def test_true_no_range_single_thread(self):
        self._download(TrueNoRangeHandler, num_threads=1)

    def test_range_advertising_but_ignoring(self):
        # Advertises Accept-Ranges on HEAD but returns 200 full body on GET.
        # This is the path the skip_bytes fix targets.
        self._download(NoRangeHandler, num_threads=4)
        self._download(NoRangeHandler, num_threads=1)

    def test_redirects(self):
        self._download(RedirectHandler, num_threads=4)

    def test_large_file_multipart(self):
        self._download(BigRangeHandler, num_threads=4, expected=LARGE_DATA)
        self._download(BigRangeHandler, num_threads=8, expected=LARGE_DATA)

    def test_slow_server(self):
        self._download(SlowHandler, num_threads=4)

    def test_connection_reset_then_retry(self):
        FlakyHandler.resets_left = 1
        try:
            self._download(FlakyHandler, num_threads=4)
        finally:
            FlakyHandler.resets_left = 0

    def test_small_single_chunk(self):
        # A file smaller than one chunk must still be correct.
        small = b"hello world" * 3
        from http.server import BaseHTTPRequestHandler

        class SmallHandler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send_response(self, code, message=None):
                self.log_request(code)
                self.send_response_only(code, message)

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(small)))
                self.end_headers()

            def do_GET(self):
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                    chunk = small[start:]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(small)-1}/{len(small)}")
                else:
                    chunk = small
                    self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        with TestServer(SmallHandler) as srv:
            cfg = _cfg(num_threads=4)
            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            self.assertTrue(ctrl.download_file(srv.url, self.dir, control=TaskControl()))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.read_bytes(), small)


if __name__ == "__main__":
    unittest.main()
