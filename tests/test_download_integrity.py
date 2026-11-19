"""Download-integrity regressions.

Every test here describes a way a download can finish with the *right number of
bytes* and the *wrong content* — the failure mode behind the reported
"movie plays but stops early" incident.  A size check cannot catch any of them,
so each case compares SHA-256 against the source bytes.

The rule these tests enforce is simple and absolute:

    A download that cannot be proven correct must not report success.

Failing cleanly (``download_file() -> False``) is always acceptable.  Returning
``True`` with bytes that do not match the source is never acceptable.
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.download import DownloadController
from core.session import SessionManager
from tests.helpers import DATA, TestServer, test_config as make_config

SOURCE_SHA = hashlib.sha256(DATA).hexdigest()


def _range_of(header: str) -> tuple[int, int]:
    """Parse ``bytes=a-b`` into (a, b).  Raises on anything unexpected."""
    spec = header.split("=", 1)[1]
    first, last = spec.split("-", 1)
    return int(first), int(last)


class _Hostile(BaseHTTPRequestHandler):
    """Base for the misbehaving servers below."""

    def log_message(self, *args):
        pass

    def send_response(self, code, message=None):
        self.send_response_only(code, message)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()


class WrongStartHandler(_Hostile):
    """Claims 206 but always answers from byte 0 — the classic "proxy that
    ignores Range while still saying Partial Content"."""

    def do_GET(self):
        self.send_response(206)
        self.send_header("Content-Range", f"bytes 0-{len(DATA) - 1}/{len(DATA)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        try:
            self.wfile.write(DATA)
        except (BrokenPipeError, ConnectionResetError):
            pass


class NoContentRangeHandler(_Hostile):
    """A 206 with no ``Content-Range`` at all: nothing to validate against."""

    def do_GET(self):
        rng = self.headers.get("Range")
        start = _range_of(rng)[0] if rng else 0
        body = DATA[start:]
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LyingBodyHandler(_Hostile):
    """Correct ``Content-Range`` for the request, but the body always starts at
    byte 0.  Only a length or hash check can catch this."""

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end = _range_of(rng) if rng else (0, len(DATA) - 1)
        length = end - start + 1
        body = DATA[:length]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ShortBodyHandler(_Hostile):
    """Announces the full range length, then closes 1000 bytes early."""

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end = _range_of(rng) if rng else (0, len(DATA) - 1)
        length = end - start + 1
        body = DATA[start:start + max(0, length - 1000)]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class GzipRangeHandler(_Hostile):
    """Compresses a ranged response.  The decoded bytes no longer correspond to
    the requested offsets, so they must never be written to a part file."""

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end = _range_of(rng) if rng else (0, len(DATA) - 1)
        raw = DATA[start:end + 1]
        body = gzip.compress(raw)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class StrippedRangeHandler(_Hostile):
    """Advertises byte ranges in HEAD, then answers every GET with a full
    ``200 OK`` — what a CDN or corporate proxy that strips the Range header
    does.  Appending that body to a part file would write the *head* of the
    file at every offset, so the result has the right length and the wrong
    bytes: exactly the reported movie failure.

    ``served`` counts bytes written and ``gets`` counts requests, so a test
    can prove the engine did not fall back to pulling the whole entity once
    per part.  (Bytes are the weaker signal: the session sets a 4 MB
    SO_RCVBUF, so the kernel absorbs most of a doomed body and ``served``
    overstates the real cost.  Request count is not confounded that way.)
    """

    served = 0
    gets = 0

    def do_GET(self):
        StrippedRangeHandler.gets += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        try:
            self.wfile.write(DATA)
            StrippedRangeHandler.served += len(DATA)
        except (BrokenPipeError, ConnectionResetError):
            pass


class MutableHandler(_Hostile):
    """A file that can be replaced between sessions, like a re-uploaded movie.

    ``payload`` / ``etag`` are class attributes so a test can swap the remote
    content between two downloads of the same URL.  ``cut`` withholds that many
    bytes from every response, which leaves a resumable partial download.
    """

    payload = DATA
    etag = '"v1"'
    cut = 0
    served = 0

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("ETag", self.etag)
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        start, end = _range_of(rng) if rng else (0, len(self.payload) - 1)
        end = min(end, len(self.payload) - 1)
        body = self.payload[start:end + 1]
        if self.cut:
            body = body[: max(0, len(body) - self.cut)]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header(
                "Content-Range", f"bytes {start}-{end}/{len(self.payload)}"
            )
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("ETag", self.etag)
        self.end_headers()
        MutableHandler.served += len(body)
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class IntegrityHarness(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, handler, **cfg_kw):
        cfg = make_config(**cfg_kw)
        ctl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
        with TestServer(handler) as srv:
            ok = ctl.download_file(srv.url, self.dir)
        saved = self.dir / "file.bin"
        actual = hashlib.sha256(saved.read_bytes()).hexdigest() if saved.exists() else ""
        return ok, actual, ctl

    def assert_honest(self, ok, actual, ctl, label):
        """Success must mean the bytes are exactly the source bytes."""
        if ok:
            self.assertEqual(
                actual, SOURCE_SHA,
                f"{label}: reported success but the file content is wrong — "
                f"this is silent corruption (size {len(DATA)} bytes)",
            )
        else:
            self.assertIn(
                "file.bin", str(list(self.dir.iterdir())),
                f"{label}: failed without keeping anything (fine)",
            )

    # ── A. HTTP response validation ─────────────────────────────────────

    def test_wrong_content_range_start_is_rejected(self):
        ok, actual, ctl = self._run(WrongStartHandler, num_threads=4)
        self.assert_honest(ok, actual, ctl, "206 from byte 0")

    def test_missing_content_range_is_rejected(self):
        ok, actual, ctl = self._run(NoContentRangeHandler, num_threads=4)
        self.assert_honest(ok, actual, ctl, "206 without Content-Range")

    def test_body_that_ignores_the_requested_range_is_rejected(self):
        ok, actual, ctl = self._run(LyingBodyHandler, num_threads=4)
        self.assert_honest(ok, actual, ctl, "206 body from byte 0")

    def test_short_body_never_completes(self):
        ok, actual, ctl = self._run(ShortBodyHandler, num_threads=4, max_retries=2)
        self.assert_honest(ok, actual, ctl, "short body")

    def test_compressed_range_response_is_not_written_raw(self):
        ok, actual, ctl = self._run(GzipRangeHandler, num_threads=4)
        self.assert_honest(ok, actual, ctl, "gzip range")

    def test_full_200_response_to_a_range_request_is_never_appended(self):
        """A 200 answering a Range request is a whole entity, not a slice.

        It must never be written into a part file, and it must not be fetched
        once per part either — the safe answer is to notice the server cannot
        do ranges and restart as a single stream.
        """
        StrippedRangeHandler.served = 0
        StrippedRangeHandler.gets = 0
        ok, actual, ctl = self._run(StrippedRangeHandler, num_threads=16)
        self.assert_honest(ok, actual, ctl, "200 instead of 206")
        self.assertTrue(ok, "a server without range support must still succeed")
        # A correct engine fetches the entity about once, not once per part.
        # Two parts are in flight before the first 200 is seen, so 3 requests
        # is the expected shape; 17 would mean one fetch per planned part.
        self.assertLessEqual(
            StrippedRangeHandler.gets, 4,
            f"{StrippedRangeHandler.gets} requests for one file — the 200 "
            "fallback is refetching once per part",
        )

    # ── B. Resume must not splice two versions of a file ────────────────
    #
    # Both phases share ONE server instance: resume state is keyed by URL, and
    # a fresh TestServer binds a fresh port, which would look like a different
    # download entirely.

    def _controller(self, messages, **cfg_kw):
        cfg = make_config(**cfg_kw)
        return DownloadController(
            cfg, SessionManager(cfg), show_progress=False,
            console_print=lambda *a, **k: messages.append(str(a[0]) if a else ""),
        )

    def _partial_then(self, messages, then):
        """Download against a truncating server, then run *then* on the same URL."""
        MutableHandler.payload, MutableHandler.etag, MutableHandler.cut = DATA, '"v1"', 1000
        ctl = self._controller(messages, num_threads=4, max_retries=1)
        with TestServer(MutableHandler) as srv:
            self.assertFalse(
                ctl.download_file(srv.url, self.dir),
                "the truncated server should not complete",
            )
            self.assertTrue(
                list(self.dir.glob("*.dlstate")),
                "a failed transfer must leave resume state",
            )
            messages.clear()
            ctl.config = make_config(num_threads=4)
            ctl.session.configure(ctl.config)
            return then(srv, ctl)

    def test_resume_of_an_unchanged_file_reuses_the_partial_data(self):
        messages = []

        def finish(srv, ctl):
            MutableHandler.cut = 0
            MutableHandler.served = 0
            return ctl.download_file(srv.url, self.dir)

        ok = self._partial_then(messages, finish)
        self.assertTrue(ok, "resume should finish once the server is healthy")
        saved = (self.dir / "file.bin").read_bytes()
        self.assertEqual(hashlib.sha256(saved).hexdigest(), SOURCE_SHA)
        self.assertTrue(
            any("Resuming" in m or "remapping" in m for m in messages),
            f"expected the resume path, got: {messages}",
        )
        # The point of resume: the second pass must not re-fetch the whole file.
        self.assertLess(
            MutableHandler.served, len(DATA) // 2,
            "the resume re-downloaded the entire file instead of continuing it",
        )

    def test_resume_of_a_replaced_file_never_mixes_two_versions(self):
        # Same URL, same length, different bytes — the case a size check cannot
        # see.  The engine must notice the ETag change and start over.
        other = bytes((b ^ 0xFF) for b in DATA[:4096]) + DATA[4096:]
        self.assertEqual(len(other), len(DATA), "test fixture must be same-length")
        messages = []

        def finish(srv, ctl):
            MutableHandler.payload = other
            MutableHandler.etag = '"v2"'
            MutableHandler.cut = 0
            return ctl.download_file(srv.url, self.dir)

        try:
            ok = self._partial_then(messages, finish)
            self.assertTrue(ok, "the replacement file should download cleanly")
            saved = (self.dir / "file.bin").read_bytes()
            self.assertEqual(
                hashlib.sha256(saved).hexdigest(),
                hashlib.sha256(other).hexdigest(),
                "resume spliced the old partial data into the new version",
            )
            self.assertTrue(
                any("changed" in m.lower() for m in messages),
                f"the change was not reported: {messages}",
            )
        finally:
            MutableHandler.payload, MutableHandler.etag, MutableHandler.cut = DATA, '"v1"', 0


if __name__ == "__main__":
    unittest.main()
