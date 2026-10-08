"""Probe honesty: the transfer plan is only as good as the probe's answer.

A wrong range verdict is expensive in both directions — claiming ranges a
server does not have makes every part pull the whole file, and trusting a
``200`` as if it were a ``206`` writes the wrong bytes.  These tests pin the
rules that decide, plus the end-to-end fallback when a server lies anyway.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config.settings import AppConfig
from core.control import TaskControl
from core.download import (
    DownloadController,
    _content_range_start,
    _content_range_total,
)
from core.probe import _metadata_from_headers, _range_info, probe_with_headers
from core.session import SessionManager
from tests.helpers import DATA, NoRangeHandler, TrueNoRangeHandler, TestServer

# 2 MB: above the range-verification threshold (1 MiB).
HIDDEN_DATA = os.urandom(2 * 1024 * 1024 + 4321)


class _Resp:
    """Minimal stand-in for ``requests.Response`` (status + headers only)."""

    def __init__(self, status: int, headers: dict):
        self.status_code = status
        self.headers = headers


class HiddenRangeHandler(BaseHTTPRequestHandler):
    """Supports byte ranges but never advertises ``Accept-Ranges``.

    Common on CDNs that answer a Range request correctly while omitting the
    header, and the case a HEAD-only probe gets wrong.
    """

    range_requests: int = 0

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(HIDDEN_DATA)))
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            HiddenRangeHandler.range_requests += 1
            start = int(rng.split("=")[1].split("-")[0])
            chunk = HIDDEN_DATA[start:]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(HIDDEN_DATA) - 1}/{len(HIDDEN_DATA)}"
            )
        else:
            chunk = HIDDEN_DATA
            self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class _Server:
    def __init__(self, handler_cls):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/file.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


def _cfg(**kw) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.connection_mode = "manual"
    cfg.num_threads = 4
    cfg.verify_size = True
    cfg.max_retries = 3
    cfg.retry_delay = 0.01
    cfg.retry_max_delay = 0.2
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


class RangeInfoTest(unittest.TestCase):
    """``_range_info`` decides whether a ranged response may be believed."""

    def test_honoured_range_yields_the_authoritative_total(self):
        self.assertEqual(
            _range_info(_Resp(206, {"Content-Range": "bytes 0-0/1000"}), 0), (1000, True)
        )

    def test_200_is_never_a_ranged_response(self):
        # A 200 to a ranged request means the Range header was ignored; the
        # body starts at byte 0 whatever the headers claim.
        self.assertEqual(
            _range_info(_Resp(200, {"Content-Range": "bytes 0-0/1000"}), 0), (0, False)
        )

    def test_wrong_start_is_rejected(self):
        # Resuming against a response that starts elsewhere would write the
        # wrong bytes into the part.
        self.assertEqual(
            _range_info(_Resp(206, {"Content-Range": "bytes 5-5/1000"}), 0), (0, False)
        )

    def test_missing_or_unknown_total_is_rejected(self):
        self.assertEqual(_range_info(_Resp(206, {}), 0), (0, False))
        self.assertEqual(
            _range_info(_Resp(206, {"Content-Range": "bytes 0-0/*"}), 0), (0, False)
        )
        self.assertEqual(
            _range_info(_Resp(206, {"Content-Range": "0-0/1000"}), 0), (0, False)
        )

    def test_compressed_body_makes_ranges_meaningless(self):
        self.assertEqual(
            _range_info(
                _Resp(206, {"Content-Range": "bytes 0-0/1000", "Content-Encoding": "gzip"}),
                0,
            ),
            (0, False),
        )

    def test_requested_start_none_skips_the_start_check(self):
        self.assertEqual(
            _range_info(_Resp(206, {"Content-Range": "bytes 400-999/1000"}), None),
            (1000, True),
        )


class HeaderMetadataTest(unittest.TestCase):
    """``_metadata_from_headers`` reads the *claim*, never the proof."""

    def test_content_length_is_the_size(self):
        self.assertEqual(_metadata_from_headers(_Resp(200, {"Content-Length": "500"})), (500, False))

    def test_accept_ranges_advertised(self):
        size, advertises = _metadata_from_headers(
            _Resp(200, {"Content-Length": "500", "Accept-Ranges": "bytes"})
        )
        self.assertEqual((size, advertises), (500, True))

    def test_multi_token_accept_ranges(self):
        _, advertises = _metadata_from_headers(
            _Resp(200, {"Content-Length": "500", "Accept-Ranges": "bytes, bytes"})
        )
        self.assertTrue(advertises)

    def test_accept_ranges_none_is_not_support(self):
        _, advertises = _metadata_from_headers(
            _Resp(200, {"Content-Length": "500", "Accept-Ranges": "none"})
        )
        self.assertFalse(advertises)

    def test_206_content_range_is_authoritative_over_content_length(self):
        # HEAD/GET disagreeing is exactly the "wrong Content-Length" case: the
        # Content-Range total wins.
        size, advertises = _metadata_from_headers(
            _Resp(206, {"Content-Length": "10", "Content-Range": "bytes 0-99/1000"})
        )
        self.assertEqual((size, advertises), (1000, True))

    def test_garbage_content_length_is_ignored(self):
        self.assertEqual(_metadata_from_headers(_Resp(200, {"Content-Length": "abc"})), (0, False))


class ContentRangeParserTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(_content_range_total("bytes 0-99/1000"), 1000)
        self.assertEqual(_content_range_total("bytes 0-99/*"), 0)
        self.assertEqual(_content_range_total(""), 0)
        self.assertEqual(_content_range_total("garbage"), 0)

    def test_start(self):
        self.assertEqual(_content_range_start("bytes 100-199/1000"), 100)
        self.assertEqual(_content_range_start("bytes */1000"), None)
        self.assertEqual(_content_range_start(""), None)
        self.assertEqual(_content_range_start("items 0-9/10"), None)


class ProbeIntegrationTest(unittest.TestCase):
    def setUp(self):
        HiddenRangeHandler.range_requests = 0

    def test_range_support_is_discovered_without_the_header(self):
        with _Server(HiddenRangeHandler) as srv:
            cfg = _cfg()
            ok, size, supports, _name, err, _h, _f = probe_with_headers(
                srv.url, cfg, SessionManager(cfg)
            )
        self.assertTrue(ok, err)
        self.assertEqual(size, len(HIDDEN_DATA))
        self.assertTrue(supports, "ranges work but were not advertised")
        self.assertGreaterEqual(HiddenRangeHandler.range_requests, 1)

    def test_advertised_but_ignored_ranges_are_trusted_at_probe_time(self):
        # Documented trade-off: the probe believes an explicit
        # ``Accept-Ranges: bytes`` rather than spending a request on every
        # large download.  The transfer catches the lie (next test).
        with TestServer(NoRangeHandler) as srv:
            cfg = _cfg()
            ok, size, supports, _name, _err, _h, _f = probe_with_headers(
                srv.url, cfg, SessionManager(cfg)
            )
        self.assertTrue(ok)
        self.assertEqual(size, len(DATA))
        self.assertTrue(supports)

    def test_a_server_without_ranges_is_reported_as_such(self):
        with TestServer(TrueNoRangeHandler) as srv:
            cfg = _cfg()
            ok, size, supports, _name, _err, _h, _f = probe_with_headers(
                srv.url, cfg, SessionManager(cfg)
            )
        self.assertTrue(ok)
        self.assertEqual(size, len(DATA))
        self.assertFalse(supports)


class RangeFallbackTest(unittest.TestCase):
    """A server that lies about ranges must cost one transfer, not N."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_fallback_downloads_correctly_and_reads_the_body_once(self):
        read_bytes = {"n": 0}
        real_iter_content = requests.Response.iter_content

        def counting_iter_content(self, *args, **kwargs):
            for chunk in real_iter_content(self, *args, **kwargs):
                read_bytes["n"] += len(chunk)
                yield chunk

        with TestServer(NoRangeHandler) as srv:
            cfg = _cfg(num_threads=4)
            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            with patch.object(requests.Response, "iter_content", counting_iter_content):
                self.assertTrue(
                    ctrl.download_file(srv.url, self.dir, control=TaskControl()),
                    "the single-stream fallback must still complete the download",
                )

        saved = self.dir / "file.bin"
        self.assertTrue(saved.exists())
        self.assertEqual(
            hashlib.sha256(saved.read_bytes()).hexdigest(),
            hashlib.sha256(DATA).hexdigest(),
        )
        # Two parts were planned; consuming the whole body for each would read
        # ~2× the file.  The fallback must read it once.
        self.assertLess(
            read_bytes["n"],
            len(DATA) * 1.5,
            f"body consumed {read_bytes['n']} bytes for a {len(DATA)} byte file",
        )

    def test_fallback_leaves_no_part_or_state_files_behind(self):
        with TestServer(NoRangeHandler) as srv:
            cfg = _cfg(num_threads=4)
            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            self.assertTrue(ctrl.download_file(srv.url, self.dir, control=TaskControl()))
        leftovers = sorted(p.name for p in self.dir.iterdir() if p.name != "file.bin")
        self.assertEqual(leftovers, [], f"stale artefacts left behind: {leftovers}")


if __name__ == "__main__":
    unittest.main()
