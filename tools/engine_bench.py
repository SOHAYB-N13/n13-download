"""Download-engine benchmark harness.

What this measures, and what it does not
========================================
Localhost has no bandwidth limit, so timing a plain loopback transfer says almost
nothing about real internet throughput.  What it *can* measure honestly:

* **Engine overhead** — how much wall time the engine adds on top of moving
  bytes (segment setup, merging, verification, progress accounting).
* **Parallelism under a per-connection cap** — the ``wan`` scenario gives each
  TCP connection its own token bucket, which is what a congested link or a
  per-connection-throttling CDN actually does.  Aggregate throughput then shows
  whether the engine exploits the parallelism the link offers or serialises it.
* **Parallelism under latency** — an artificial per-chunk delay stands in for
  RTT + server think-time.
* **Telemetry accuracy** — the ``telemetry`` scenario drives the *shipped*
  ``DownloadTask`` speed path from real progress callbacks and compares what the
  UI would display against the bytes that actually moved, including during a
  deliberate mid-transfer stall.
* **Startup breakdown** — the engine's own ``DownloadReport.timeline`` splits
  probe / connect / first-byte, so engine delay is separable from server delay.
* **Request count** — redundant probes and duplicate fetches are visible here
  even when the clock hides them.
* **Peak RSS** — memory per part is a real constraint on a 4 GB machine.

Every number printed is measured in this run.  Nothing is extrapolated to
"real-world speed", and no comparison with another download manager is claimed.

Usage
-----
    python tools/engine_bench.py --scenario all
    python tools/engine_bench.py --scenario wan --per-conn-mb 4 --size-mb 96
    python tools/engine_bench.py --scenario telemetry
    python tools/engine_bench.py --scenario concurrent --downloads 4
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
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
from core.task import DownloadTask, TaskStatus
from tests.helpers import test_config

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

DEFAULT_SIZE = 64 * 1024 * 1024          # 64 MB
CHUNK = 256 * 1024
WAN_SLICE = 64 * 1024

#: Below this true rate a percentage error is noise, not a measurement.
_MIN_TRUTH = 1 * 1024 * 1024

#: Where downloads are written.  Storage is a controlled variable, not a
#: constant: on the development host the system temp volume sustains ~75 MB/s
#: and the project volume ~98 MB/s, so a merge of a 512 MB file costs seconds
#: and end-to-end timings are storage-bound.  Set ``--work-dir`` to the volume
#: under test and report it alongside the numbers.
WORK_DIR: str | None = None


def _work_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=WORK_DIR))


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
    per_conn_bps: int = 0           # per-connection byte cap (0 = unlimited)
    freeze_after: float = 0.0       # freeze all connections this long after start
    freeze_seconds: float = 0.0
    head_delay: float = 0.0         # seconds to stall before answering HEAD
    body_delay: float = 0.0         # seconds to stall before the first GET body byte
    requests_served: int = 0
    bytes_served: int = 0
    concurrent_peak: int = 0
    _active = 0
    _lock = threading.Lock()
    _started_at = 0.0
    _frozen = False

    def log_message(self, *args):
        pass

    def send_response(self, code, message=None):
        self.send_response_only(code, message)

    def do_HEAD(self):
        if type(self).head_delay:
            time.sleep(type(self).head_delay)
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("ETag", '"bench"')
        self.end_headers()

    def _pace(self, sent: int, started: float) -> None:
        """Hold this connection to ``per_conn_bps`` using a cumulative schedule.

        Correcting after every write keeps the server from running ahead of the
        cap, so the *send* rate is accurate (a single stream measures 8.02 s
        against an 8.00 s theoretical for 32 MB at 4 MB/s).  Coarser
        corrections are not an improvement: they let the connection burst a
        whole block ahead, and the client's receive buffer then makes a short
        transfer look faster than the configured cap.
        """
        cap = type(self).per_conn_bps
        if cap <= 0:
            return
        allowed = cap * (time.monotonic() - started)
        if sent > allowed:
            time.sleep((sent - allowed) / cap)

    def _wait_if_frozen(self) -> float:
        """Simulate a network freeze: every connection goes silent at once.

        A freeze is what a real stall looks like — not one slow socket, but
        bytes simply stopping on all of them.  Timing it from the first request
        keeps it deterministic regardless of how many parts the engine opens.

        Returns the seconds actually slept so the caller can shift its pacing
        clock.  Without that shift the token bucket banks the whole freeze as
        credit and bursts afterwards, which makes a stall cost nothing in
        throughput — and then the telemetry test measures a burst, not a stall.
        """
        cls = type(self)
        if not cls.freeze_seconds:
            return 0.0
        elapsed = time.monotonic() - cls._started_at
        if cls.freeze_after <= elapsed < cls.freeze_after + cls.freeze_seconds:
            cls._frozen = True
            slept = cls.freeze_after + cls.freeze_seconds - elapsed
            time.sleep(slept)
            return slept
        return 0.0

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

        cls = type(self)
        if cls.body_delay:
            time.sleep(cls.body_delay)
        with self._lock:
            if cls._started_at == 0.0:
                cls._started_at = time.monotonic()
            cls.requests_served += 1
            cls._active += 1
            cls.concurrent_peak = max(cls.concurrent_peak, cls._active)
        started = time.monotonic()
        sent = 0
        try:
            for i in range(0, len(body), WAN_SLICE):
                piece = body[i:i + WAN_SLICE]
                self.wfile.write(piece)
                sent += len(piece)
                if cls.per_conn_bps:
                    self._pace(sent, started)
                slept = self._wait_if_frozen()
                if slept:
                    started += slept
                if cls.chunk_delay and (i // WAN_SLICE) % 4 == 0:
                    self.wfile.flush()
                    time.sleep(cls.chunk_delay)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self._lock:
                cls._active -= 1
                cls.bytes_served += sent


class BenchServer:
    def __init__(self, payload: bytes, chunk_delay: float = 0.0,
                 per_conn_bps: int = 0, freeze_after: float = 0.0,
                 freeze_seconds: float = 0.0, head_delay: float = 0.0,
                 body_delay: float = 0.0):
        BenchHandler.payload = payload
        BenchHandler.chunk_delay = chunk_delay
        BenchHandler.per_conn_bps = per_conn_bps
        BenchHandler.freeze_after = freeze_after
        BenchHandler.freeze_seconds = freeze_seconds
        BenchHandler.head_delay = head_delay
        BenchHandler.body_delay = body_delay
        BenchHandler.requests_served = 0
        BenchHandler.bytes_served = 0
        BenchHandler.concurrent_peak = 0
        BenchHandler._active = 0
        BenchHandler._started_at = 0.0
        BenchHandler._frozen = False
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

    @property
    def peak_connections(self) -> int:
        return BenchHandler.concurrent_peak


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


def _make_controller(cfg):
    return DownloadController(
        cfg, SessionManager(cfg), show_progress=False,
        console_print=lambda *a, **k: None,
    )


def _cleanup(directory: Path) -> None:
    for leftover in directory.iterdir():
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


def run_once(size: int, threads: int, chunk_delay: float, payload: bytes,
             per_conn_bps: int = 0, connection_mode: str = "manual",
             smart_max: int = 8, head_delay: float = 0.0,
             body_delay: float = 0.0) -> dict:
    directory = _work_dir("n13-bench-")
    cfg = test_config(num_threads=threads, verify_size=True,
                      connection_mode=connection_mode)
    cfg.smart_max_connections = smart_max
    cfg.min_part_size = 1024 * 1024          # let the thread count actually apply
    cfg.buffer_size = 1024 * 1024
    first_progress = [None]
    last_progress = [None]
    started = time.monotonic()

    def on_progress(done: int, total: int) -> None:
        now = time.monotonic() - started
        if first_progress[0] is None and done > 0:
            first_progress[0] = now
        if total and done >= total:
            last_progress[0] = now

    controller = _make_controller(cfg)
    with BenchServer(payload, chunk_delay, per_conn_bps,
                     head_delay=head_delay, body_delay=body_delay) as srv:
        with _RssSampler() as sampler:
            ok = controller.download_file(
                srv.url, directory, progress_callback=on_progress
            )
        requests = srv.requests
        peak_conns = srv.peak_connections
        rss_growth = sampler.growth_mb
    elapsed = time.monotonic() - started
    # Transfer time ends at the last byte the engine reported, not when
    # download_file() returns: the return also covers merge, final validation
    # and the digest read, which are real costs but not transfer throughput.
    transfer = last_progress[0] if last_progress[0] is not None else elapsed

    saved = directory / "bench.bin"
    digest_ok = saved.exists() and hashlib.sha256(saved.read_bytes()).hexdigest() == \
        hashlib.sha256(payload).hexdigest()
    timeline = dict(controller.last_report.timeline) if controller.last_report else {}
    _cleanup(directory)

    return {
        "ok": ok and digest_ok,
        "seconds": elapsed,
        "transfer_seconds": transfer,
        "post_seconds": max(0.0, elapsed - transfer),
        "mb_per_s": size / (1024 * 1024) / transfer if transfer > 0 else 0.0,
        "total_mb_per_s": size / (1024 * 1024) / elapsed if elapsed > 0 else 0.0,
        "ttfb": first_progress[0] if first_progress[0] is not None else float("nan"),
        "requests": requests,
        "rss_mb": rss_growth,
        # Honest names: the loopback server counts accepted handlers (which can
        # linger after the client is done); the engine's meter counts real
        # client-side simultaneous requests.
        "server_peak_conns": peak_conns,
        "client_peak_conns": (
            controller.last_report.peak_connections
            if controller.last_report is not None else 0
        ),
        "timeline": timeline,
    }


# ---------------------------------------------------------------------------
# Telemetry scenario — drives the shipped DownloadTask speed path
# ---------------------------------------------------------------------------


class TelemetryProbe:
    """Mirror of ``TaskManager.update_progress`` for one task.

    The UI's speed number comes from ``DownloadTask.update_speed``, which is
    fed by progress callbacks and then *sampled* by the renderer at its own
    cadence.  Reproducing both halves here means the numbers below are what a
    user would see, not a re-implementation of it.
    """

    def __init__(self, sample_interval: float = 0.1):
        self.task = DownloadTask(url="bench://telemetry", filename="bench.bin")
        self.task.transition(TaskStatus.ANALYZING)
        self.task.mark_starting()
        self.task.mark_downloading()
        self.sample_interval = sample_interval
        self.samples: list[tuple[float, int, float, float | None]] = []
        self._truth: list[tuple[float, int]] = []
        # Out-of-order progress deliveries: a part thread reads the shared
        # counter, then another thread reports a higher value first.  Counting
        # them matters because the shipped task treats a lower value as a
        # restart, which resets the displayed speed.
        self.regressions = 0
        self._highest = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self.started = time.monotonic()

    def on_progress(self, done: int, total: int) -> None:
        if done < self._highest:
            self.regressions += 1
        else:
            self._highest = done
        self.task.total_size = max(0, int(total))
        self.task.update_speed(done)
        self._truth.append((time.monotonic() - self.started, int(done)))

    def _sample(self) -> None:
        # ``refresh_speed`` is what makes an idle transfer observable, and it
        # only exists in the current engine.  The baseline tree has no such
        # method, so fall back to reading the attributes directly — otherwise
        # every baseline sample would be ``nan`` and the comparison would be
        # vacuous rather than unfavourable.
        refresh = getattr(self.task, "refresh_speed", None)
        while not self._stop.is_set():
            if refresh is not None:
                refresh()
            self.samples.append((
                time.monotonic() - self.started,
                self.task.downloaded_size,
                float(self.task.current_speed),
                self.task.eta_seconds,
            ))
            self._stop.wait(self.sample_interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        return False

    def truth_rate(self, at: float, window: float = 1.0) -> float:
        """Actual bytes/second over ``[at-window, at]`` from the byte counter."""
        hi = [p for p in self._truth if p[0] <= at]
        lo = [p for p in self._truth if p[0] <= at - window]
        if not hi or not lo:
            return 0.0
        dt = hi[-1][0] - lo[-1][0]
        return (hi[-1][1] - lo[-1][1]) / dt if dt > 0 else 0.0

    def report(self, freeze_at: float | None, end_at: float,
               actual_rate: float, freeze_seconds: float = 0.0) -> dict:
        """Summarise what the UI would have shown.

        Accuracy is judged sample by sample against the **actual bytes that
        arrived in the preceding second**, not against the whole-run average.
        The average is a single number for a transfer whose true rate changes —
        and it is worst exactly where accuracy matters most, around a stall: a
        short freeze drags the average down, so a display that correctly read
        zero would be scored as wrong.

        Samples whose truth is below :data:`_MIN_TRUTH` are excluded: comparing
        two numbers near zero produces ratios like 800 % that say nothing about
        the estimator.

        ``recovery_seconds`` is the other half of the story — how long after the
        freeze ends the display is back within 20 % of reality, and stays there.
        """
        lo_t = 0.4 * end_at
        hi_t = 0.85 * end_at
        # A truth window that straddles the freeze is not a fair reference: for
        # a second after the freeze ends it still contains frozen time, so it
        # reports roughly half the real rate and scores a correct display as a
        # 200 % error.  Those samples are excluded here and covered instead by
        # ``recovery_seconds``, which is the measurement that actually applies
        # to that period.  The margin is one truth window plus one display
        # window (1 s each), so neither average spans the boundary.
        skip_until = (freeze_at + freeze_seconds + 1.5) if freeze_at is not None else -1.0
        errors = []
        for at, _done, shown, _eta in self.samples:
            if at < lo_t or at > hi_t:
                continue
            if freeze_at is not None and freeze_at - 1.0 <= at <= skip_until:
                continue
            truth = self.truth_rate(at)
            if truth < _MIN_TRUTH:
                continue
            errors.append(abs(shown - truth) / truth)
        errors.sort()
        median = statistics.median(errors) * 100 if errors else float("nan")
        p95 = errors[min(len(errors) - 1, int(len(errors) * 0.95))] * 100 if errors else float("nan")

        stall_speed = float("nan")
        stall_zero = float("nan")
        recovery = float("nan")
        if freeze_at is not None:
            # Inside the freeze the true rate is zero, so anything above zero
            # is fabrication.  Measured over the *last second* of the freeze,
            # not the whole window: the estimator deliberately holds its last
            # value for the idle grace (1.2 s) so one delayed update cannot
            # zero a healthy transfer, and taking the maximum over the whole
            # freeze would just report that grace period.
            during = [
                s for s in self.samples
                if freeze_at + freeze_seconds - 1.0 <= s[0] <= freeze_at + freeze_seconds
            ]
            stall_speed = max((s[2] for s in during), default=float("nan"))
            at_end = [s for s in self.samples if s[0] >= freeze_at + freeze_seconds - 0.1]
            stall_zero = at_end[0][2] if at_end else float("nan")
            # Time from the end of the freeze until the display is within 20 %
            # of reality *and stays there*.  Scanned forwards: a backwards scan
            # breaks on the first noisy sample in the ramp and reports that as
            # the recovery point, which is meaningless.
            resumed = freeze_at + freeze_seconds
            after = [s for s in self.samples if s[0] >= resumed]
            for index, (at, _done, shown, _eta) in enumerate(after):
                settled = True
                for later in after[index:]:
                    if later[0] - at > 1.0:
                        break
                    truth = self.truth_rate(later[0])
                    if truth < _MIN_TRUTH:
                        continue
                    if abs(later[2] - truth) / truth > 0.2:
                        settled = False
                        break
                if settled:
                    recovery = max(0.0, at - resumed)
                    break

        return {
            "samples": len(self.samples),
            "regressions": self.regressions,
            "speed_err_median_pct": median,
            "speed_err_p95_pct": p95,
            "steady_samples": len(errors),
            "stall_speed_mb": stall_speed / (1024 * 1024) if stall_speed == stall_speed else float("nan"),
            "stall_end_mb": stall_zero / (1024 * 1024) if stall_zero == stall_zero else float("nan"),
            "recovery_seconds": recovery,
        }


def run_telemetry(size: int, threads: int, per_conn_bps: int, payload: bytes,
                  freeze_seconds: float) -> dict:
    directory = _work_dir("n13-bench-")
    cfg = test_config(num_threads=threads, verify_size=True)
    cfg.min_part_size = 1024 * 1024
    cfg.buffer_size = 1024 * 1024
    probe = TelemetryProbe()
    # Freeze early enough that it lands inside the transfer on a fast link.
    freeze_at = 0.4 if freeze_seconds else 0.0
    started = time.monotonic()

    controller = _make_controller(cfg)
    with BenchServer(payload, 0.0, per_conn_bps,
                     freeze_after=freeze_at,
                     freeze_seconds=freeze_seconds) as srv:
        with probe:
            ok = controller.download_file(srv.url, directory,
                                          progress_callback=probe.on_progress)
            end_at = time.monotonic() - started
            # Keep sampling for 3 s after completion so the "stale speed"
            # tail is measured rather than assumed.
            time.sleep(3.0)
        peak_conns = srv.peak_connections
    _cleanup(directory)
    actual_rate = size / end_at if end_at > 0 else 0.0
    out = probe.report(freeze_at if freeze_seconds else None, end_at,
                       actual_rate, freeze_seconds)
    out["ok"] = ok
    out["seconds"] = end_at
    out["actual_mb_s"] = actual_rate / (1024 * 1024)
    out["server_peak_conns"] = peak_conns
    out["client_peak_conns"] = (
        controller.last_report.peak_connections
        if controller.last_report is not None else 0
    )
    out["callbacks"] = len(probe._truth)
    return out


# ---------------------------------------------------------------------------
# Concurrent scenario
# ---------------------------------------------------------------------------


def run_concurrent(payload: bytes, downloads: int, threads: int,
                   per_conn_bps: int, connection_mode: str = "manual",
                   smart_max: int = 8) -> dict:
    size = len(payload)
    dirs = [_work_dir("n13-bench-c-") for _ in range(downloads)]
    cfg = test_config(num_threads=threads, verify_size=True,
                      connection_mode=connection_mode)
    cfg.smart_max_connections = smart_max
    cfg.min_part_size = 1024 * 1024
    cfg.buffer_size = 1024 * 1024

    speeds: dict[int, list[tuple[float, int]]] = {i: [] for i in range(downloads)}
    results: dict[int, bool] = {}
    client_peaks: dict[int, int] = {}
    started = time.monotonic()

    def run(index: int) -> None:
        def on_progress(done: int, total: int) -> None:
            speeds[index].append((time.monotonic() - started, int(done)))

        controller = _make_controller(cfg)
        results[index] = controller.download_file(
            srv.url, dirs[index], progress_callback=on_progress
        )
        if controller.last_report is not None:
            client_peaks[index] = controller.last_report.peak_connections

    with BenchServer(payload, 0.0, per_conn_bps) as srv:
        with _RssSampler() as sampler:
            workers = [threading.Thread(target=run, args=(i,)) for i in range(downloads)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
        elapsed = time.monotonic() - started
        peak_conns = srv.peak_connections
        rss_growth = sampler.growth_mb

    # Per-download speeds are measured over a *common* window — from the last
    # first-byte to the first last-byte — because averaging each download over
    # its own window and comparing that sum with a wall-clock aggregate is not
    # a comparison at all.  One download finishing half a second early shrinks
    # its own window, inflating its average, and the two numbers then differ by
    # tens of percent even when the allocation was perfectly fair.
    #
    # ``finish_spread`` reports the thing that actually varied: how much later
    # the slowest download finished than the fastest.
    firsts = [p[0][0] for p in speeds.values() if len(p) >= 2]
    lasts = [p[-1][0] for p in speeds.values() if len(p) >= 2]
    window_start = max(firsts) if firsts else 0.0
    window_end = min(lasts) if lasts else 0.0
    window = max(0.0, window_end - window_start)

    def bytes_between(points: list[tuple[float, int]], at: float) -> int:
        """Bytes reported at or before *at* (the series is monotonic)."""
        last = 0
        for when, done in points:
            if when > at:
                break
            last = done
        return last

    per_download = []
    for i, points in speeds.items():
        if len(points) >= 2 and window > 0:
            moved = bytes_between(points, window_end) - bytes_between(points, window_start)
            per_download.append(max(0, moved) / window)
        else:
            per_download.append(0.0)
    total_bytes = size * downloads
    aggregate = sum(per_download)
    wall_aggregate = total_bytes / elapsed if elapsed > 0 else 0.0
    spread = (max(per_download) / min(per_download)) if min(per_download or [0]) > 0 else float("nan")
    finish_spread = (max(lasts) / min(lasts)) if lasts and min(lasts) > 0 else float("nan")

    ok = all(results.values())
    for d in dirs:
        _cleanup(d)
    return {
        "ok": ok,
        "seconds": elapsed,
        "window_seconds": window,
        "aggregate_mb_s": aggregate / (1024 * 1024),
        "wall_aggregate_mb_s": wall_aggregate / (1024 * 1024),
        "per_download_mb_s": [round(v / (1024 * 1024), 2) for v in per_download],
        "fairness_spread": spread,
        "finish_spread": finish_spread,
        "server_peak_conns": peak_conns,
        "client_peak_conns": max(client_peaks.values()) if client_peaks else 0,
        "rss_mb": rss_growth,
        "sum_check": (
            abs(sum(per_download) - aggregate) / aggregate if aggregate else float("nan")
        ),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarise(label: str, runs: list) -> None:
    if not runs:
        return
    secs = [r["seconds"] for r in runs]
    post = [r.get("post_seconds", 0.0) for r in runs]
    print(
        f"{label:<34} "
        f"{statistics.mean(secs):7.2f}s  "
        f"{statistics.mean([r['mb_per_s'] for r in runs]):7.1f} MB/s  "
        f"post {statistics.mean(post):5.2f}s  "
        f"ttfb {statistics.mean([r['ttfb'] for r in runs]):5.2f}s  "
        f"req {statistics.mean([r['requests'] for r in runs]):5.1f}  "
        f"conn {statistics.mean([r.get('client_peak_conns', 0) for r in runs]):4.1f}  "
        f"rss {max(r['rss_mb'] for r in runs):6.1f} MB  "
        f"ok {sum(1 for r in runs if r['ok'])}/{len(runs)}"
    )


def _median(values):
    return statistics.median(values) if values else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all",
                    choices=["all", "overhead", "latency", "wan", "telemetry",
                             "concurrent", "startup", "slow"])
    ap.add_argument("--size-mb", type=int, default=64)
    ap.add_argument("--threads", default="1,2,4,8")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.02,
                    help="per-256KB artificial delay for the latency scenario")
    ap.add_argument("--per-conn-mb", type=float, default=4.0,
                    help="per-connection bandwidth cap for the wan scenario (MB/s)")
    ap.add_argument("--write-buffer-mb", type=float, default=0.0,
                    help="override the per-part write buffer (A/B measurement)")
    ap.add_argument("--downloads", type=int, default=4,
                    help="number of simultaneous downloads for the concurrent scenario")
    ap.add_argument("--mode", default="manual",
                    choices=["manual", "auto", "balanced", "high_performance",
                             "conservative", "smart"],
                    help="connection mode / download mode to exercise")
    ap.add_argument("--json", default="", help="write raw results to this path")
    ap.add_argument("--work-dir", default="",
                    help="volume to write downloads to (storage is a controlled "
                         "variable; the default system temp volume may be much "
                         "slower than the target disk)")
    args = ap.parse_args()

    global WORK_DIR
    if args.work_dir:
        WORK_DIR = args.work_dir

    if args.write_buffer_mb > 0:
        import core.download as _dl
        _dl._PART_WRITE_BUFFER = int(args.write_buffer_mb * 1024 * 1024)
        print(f"write buffer overridden to {args.write_buffer_mb:g} MB/part")

    size = args.size_mb * 1024 * 1024
    payload = _payload(size)
    thread_counts = [int(t) for t in args.threads.split(",") if t.strip()]
    per_conn = int(args.per_conn_mb * 1024 * 1024)
    raw: dict = {"config": vars(args)}

    print(f"payload {args.size_mb} MB · repeats {args.repeat} · "
          f"host {os.cpu_count()} cores · mode {args.mode}")
    print(f"storage {WORK_DIR or tempfile.gettempdir()}")
    print("-" * 132)

    if args.scenario in ("all", "overhead"):
        print("scenario: engine overhead (no artificial delay, no cap)")
        rows = []
        for n in thread_counts:
            runs = [run_once(size, n, 0.0, payload, 0, args.mode) for _ in range(args.repeat)]
            summarise(f"  {n} part(s)", runs)
            rows.append({"threads": n, "runs": runs})
        raw["overhead"] = rows

    if args.scenario in ("all", "latency"):
        print(f"scenario: parallelism under {args.delay * 1000:.0f} ms/256KB latency")
        rows = []
        for n in thread_counts:
            runs = [run_once(size, n, args.delay, payload, 0, args.mode)
                    for _ in range(args.repeat)]
            summarise(f"  {n} part(s)", runs)
            rows.append({"threads": n, "runs": runs})
        raw["latency"] = rows

    if args.scenario in ("all", "wan"):
        print(f"scenario: per-connection cap {args.per_conn_mb:g} MB/s "
              f"(ceiling ≈ {args.per_conn_mb * max(thread_counts):.0f} MB/s)")
        rows = []
        for n in thread_counts:
            runs = [run_once(size, n, 0.0, payload, per_conn, args.mode)
                    for _ in range(args.repeat)]
            summarise(f"  {n} part(s)", runs)
            rows.append({"threads": n, "runs": runs})
        raw["wan"] = rows

    if args.scenario in ("all", "startup"):
        print("scenario: startup breakdown (engine timeline, seconds)")
        runs = [run_once(size, 8, 0.0, payload, per_conn, args.mode)
                for _ in range(args.repeat)]
        keys = ["requested", "metadata", "connect", "headers", "first_byte"]
        for key in keys:
            vals = [r["timeline"].get(key) for r in runs if r["timeline"].get(key) is not None]
            if vals:
                print(f"  {key:<12} median {_median(vals):6.3f}s   "
                      f"max {max(vals):6.3f}s")
        raw["startup"] = runs

    if args.scenario == "slow":
        # A server that stalls its HEAD past the probe read timeout but then
        # serves the body normally.  This is the shape of the reported 20-40 s
        # "nothing happens" delay: metadata discovery is what costs the time,
        # not the transfer.
        print("scenario: slow metadata (HEAD stalls past the probe timeout)")
        for head_delay in (9.0,):
            runs = [run_once(size, 8, 0.0, payload, per_conn, args.mode,
                             head_delay=head_delay) for _ in range(args.repeat)]
            ttfb = [r["ttfb"] for r in runs if r["ttfb"] == r["ttfb"]]
            print(f"  HEAD +{head_delay:g}s   ttfb median {_median(ttfb):6.2f}s  "
                  f"max {max(ttfb) if ttfb else float('nan'):6.2f}s  "
                  f"total {_median([r['seconds'] for r in runs]):6.2f}s  "
                  f"ok {sum(1 for r in runs if r['ok'])}/{len(runs)}")
            raw.setdefault("slow", []).append({"head_delay": head_delay, "runs": runs})

    if args.scenario in ("all", "telemetry"):
        print("scenario: speed telemetry (displayed vs actual bytes)")
        for freeze in (0.0, 3.0):
            res = run_telemetry(size, 8, per_conn, payload, freeze)
            tag = f"freeze {freeze:g}s" if freeze else "no freeze"
            print(f"  {tag:<12} err median {res['speed_err_median_pct']:5.1f}%  "
                  f"p95 {res['speed_err_p95_pct']:5.1f}%  "
                  f"during freeze {res['stall_speed_mb']:6.2f} MB/s  "
                  f"at freeze end {res['stall_end_mb']:6.2f} MB/s  "
                  f"recover {res['recovery_seconds']:5.2f}s  "
                  f"actual {res['actual_mb_s']:6.1f} MB/s  "
                  f"conn {res.get('client_peak_conns', 0):3d}  cb {res['callbacks']:5d}  "
                  f"out-of-order {res['regressions']:4d}  ok {res['ok']}")
            raw.setdefault("telemetry", []).append({"freeze": freeze, **res})

    if args.scenario in ("all", "concurrent"):
        print(f"scenario: {args.downloads} simultaneous downloads, "
              f"{args.per_conn_mb:g} MB/s per connection")
        for dl in (2, 4):
            if dl > args.downloads and args.scenario == "concurrent":
                continue
            res = run_concurrent(payload, dl, 8, per_conn, args.mode)
            print(f"  {dl} downloads  aggregate {res['aggregate_mb_s']:7.2f} MB/s  "
                  f"per-download {res['per_download_mb_s']}  "
                  f"spread {res['fairness_spread']:5.2f}x  "
                  f"finish {res['finish_spread']:5.2f}x  "
                  f"window {res['window_seconds']:5.2f}s  "
                  f"conn {res.get('client_peak_conns', 0):3d}  rss {res['rss_mb']:5.1f} MB  "
                  f"ok {res['ok']}")
            raw.setdefault("concurrent", []).append({"downloads": dl, **res})

    if args.json:
        Path(args.json).write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        print(f"raw results → {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
