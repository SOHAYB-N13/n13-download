"""Download-engine benchmark harness.

What this measures, and what it does not
========================================
Localhost has no bandwidth limit, so timing a loopback transfer says almost
nothing about real internet throughput.  What it *can* measure honestly:

* **Engine overhead** — how much wall time the engine adds on top of moving
  bytes (segment setup, merging, verification, progress accounting).
* **Parallelism under latency** — with an artificial per-chunk delay standing in
  for RTT + server think-time, the wall time shows whether N ranges really
  overlap or whether they are serialised by the scheduler.
* **Request count** — redundant probes and duplicate fetches are visible here
  even when the clock hides them.
* **Peak RSS** — memory per part is a real constraint on a 4 GB machine.

Every number printed is measured in this run.  Nothing is extrapolated to
"real-world speed", and no comparison with another download manager is claimed.

Usage
-----
    python tools/engine_bench.py --scenario all
    python tools/engine_bench.py --scenario latency --threads 1,2,4,8
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import statistics
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.download import DownloadController
from core.session import SessionManager
from tests.helpers import test_config

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

DEFAULT_SIZE = 64 * 1024 * 1024          # 64 MB
CHUNK = 256 * 1024


def _payload(size: int) -> bytes:
    """Deterministic pseudo-random payload (incompressible, reproducible)."""
    seed = hashlib.sha256(b"n13-bench").digest()
    out = bytearray()
    block = seed
    while len(out) < size:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:size])


# ---------------------------------------------------------------------------
# Instrumented server
# ---------------------------------------------------------------------------


class BenchHandler(BaseHTTPRequestHandler):
    payload: bytes = b""
    chunk_delay: float = 0.0        # seconds to sleep per CHUNK written
    requests_served: int = 0
    bytes_served: int = 0
    _lock = threading.Lock()

    def log_message(self, *args):
        pass

    def send_response(self, code, message=None):
        self.send_response_only(code, message)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("ETag", '"bench"')
        self.end_headers()

    def do_GET(self):
        # Serve through a memoryview: slicing `bytes` per request would copy
        # the whole range into the server's heap and swamp the client-side
        # memory measurement with the benchmark's own fixture.
        view = memoryview(self.payload)
        rng = self.headers.get("Range")
        if rng:
            spec = rng.split("=", 1)[1]
            first, _, last = spec.partition("-")
            start = int(first)
            end = int(last) if last else len(view) - 1
            end = min(end, len(view) - 1)
            body = view[start:end + 1]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{end}/{len(view)}"
            )
        else:
            body = view
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"bench"')
        self.end_headers()
        with self._lock:
            type(self).requests_served += 1
            type(self).bytes_served += len(body)
        try:
            for i in range(0, len(body), CHUNK):
                self.wfile.write(body[i:i + CHUNK])
                if self.chunk_delay:
                    self.wfile.flush()
                    time.sleep(self.chunk_delay)
        except (BrokenPipeError, ConnectionResetError):
            pass


class BenchServer:
    def __init__(self, payload: bytes, chunk_delay: float = 0.0):
        BenchHandler.payload = payload
        BenchHandler.chunk_delay = chunk_delay
        BenchHandler.requests_served = 0
        BenchHandler.bytes_served = 0
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), BenchHandler)
        self._srv.daemon_threads = True
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/bench.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False

    @property
    def requests(self) -> int:
        return BenchHandler.requests_served


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _rss_now_mb() -> float:
    """Current working set of this process, or -1 when unavailable."""
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.wintypes.HANDLE(handle),
            ctypes.byref(counters),
            ctypes.c_ulong(counters.cb),
        )
        return counters.WorkingSetSize / (1024 * 1024) if ok else -1.0
    except Exception:
        return -1.0


def peak_rss_mb() -> float:
    """Peak working set of this process (Windows), or -1 when unavailable.

    Includes the benchmark's own fixture bytes, so treat it as an upper bound
    and compare *between runs* rather than against an absolute target.
    """
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.wintypes.HANDLE(handle),
            ctypes.byref(counters),
            ctypes.c_ulong(counters.cb),
        )
        return counters.PeakWorkingSetSize / (1024 * 1024) if ok else -1.0
    except Exception:
        return -1.0


class _RssSampler:
    """Samples working set while a download runs, reporting the growth.

    The fixture (payload + server) is already resident before the transfer
    starts, so the *delta* isolates what the engine itself allocates — write
    buffers, read slices and thread stacks — instead of the benchmark's own
    data.
    """

    def __init__(self, interval: float = 0.02):
        self.interval = interval
        self.baseline = _rss_now_mb()
        self.peak = self.baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            current = _rss_now_mb()
            if current > self.peak:
                self.peak = current
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        return False

    @property
    def growth_mb(self) -> float:
        return max(0.0, self.peak - self.baseline)


def run_once(size: int, threads: int, chunk_delay: float, payload: bytes) -> dict:
    directory = Path(tempfile.mkdtemp(prefix="n13-bench-"))
    cfg = test_config(num_threads=threads, verify_size=True)
    cfg.min_part_size = 1024 * 1024          # let the thread count actually apply
    cfg.buffer_size = 1024 * 1024
    first_progress = [None]
    started = time.monotonic()

    def on_progress(done: int, total: int) -> None:
        if first_progress[0] is None and done > 0:
            first_progress[0] = time.monotonic() - started

    controller = DownloadController(
        cfg, SessionManager(cfg), show_progress=False, console_print=lambda *a, **k: None
    )
    with BenchServer(payload, chunk_delay) as srv:
        with _RssSampler() as sampler:
            ok = controller.download_file(
                srv.url, directory, progress_callback=on_progress
            )
        requests = srv.requests
        rss_growth = sampler.growth_mb
    elapsed = time.monotonic() - started

    saved = directory / "bench.bin"
    digest_ok = saved.exists() and hashlib.sha256(saved.read_bytes()).hexdigest() == \
        hashlib.sha256(payload).hexdigest()
    for leftover in directory.iterdir():
        leftover.unlink(missing_ok=True)
    directory.rmdir()

    return {
        "ok": ok and digest_ok,
        "seconds": elapsed,
        "mb_per_s": size / (1024 * 1024) / elapsed if elapsed > 0 else 0.0,
        "ttfb": first_progress[0] if first_progress[0] is not None else float("nan"),
        "requests": requests,
        "rss_mb": rss_growth,
    }


def summarise(label: str, runs: list) -> None:
    if not runs:
        return
    secs = [r["seconds"] for r in runs]
    print(
        f"{label:<34} "
        f"{statistics.mean(secs):7.2f}s  "
        f"{statistics.mean([r['mb_per_s'] for r in runs]):7.1f} MB/s  "
        f"ttfb {statistics.mean([r['ttfb'] for r in runs]):5.2f}s  "
        f"req {statistics.mean([r['requests'] for r in runs]):5.1f}  "
        f"rss {max(r['rss_mb'] for r in runs):6.1f} MB  "
        f"ok {sum(1 for r in runs if r['ok'])}/{len(runs)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all",
                    choices=["all", "overhead", "latency"])
    ap.add_argument("--size-mb", type=int, default=64)
    ap.add_argument("--threads", default="1,2,4,8")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.02,
                    help="per-256KB artificial delay for the latency scenario")
    ap.add_argument("--write-buffer-mb", type=float, default=0.0,
                    help="override the per-part write buffer (A/B measurement)")
    args = ap.parse_args()

    if args.write_buffer_mb > 0:
        import core.download as _dl
        _dl._PART_WRITE_BUFFER = int(args.write_buffer_mb * 1024 * 1024)
        print(f"write buffer overridden to {args.write_buffer_mb:g} MB/part")

    size = args.size_mb * 1024 * 1024
    payload = _payload(size)
    thread_counts = [int(t) for t in args.threads.split(",") if t.strip()]

    print(f"payload {args.size_mb} MB · repeats {args.repeat} · "
          f"host {os.cpu_count()} cores")
    print("-" * 118)

    if args.scenario in ("all", "overhead"):
        print("scenario: engine overhead (no artificial delay)")
        for n in thread_counts:
            runs = [run_once(size, n, 0.0, payload) for _ in range(args.repeat)]
            summarise(f"  {n} part(s)", runs)

    if args.scenario in ("all", "latency"):
        print(f"scenario: parallelism under {args.delay * 1000:.0f} ms/256KB latency")
        for n in thread_counts:
            runs = [run_once(size, n, args.delay, payload) for _ in range(args.repeat)]
            summarise(f"  {n} part(s)", runs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
