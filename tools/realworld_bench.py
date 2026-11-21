"""Real-network validation harness for the download engine.

Unlike ``tools/engine_bench.py`` (a controlled loopback fixture), this drives the
engine against **public servers** and measures what a user would experience:

* time to first byte, from the engine's own timeline;
* average throughput over the transfer;
* throughput stability, as the coefficient of variation of 1 s byte-rate samples;
* total duration, split into transfer and post-processing (merge + validation);
* peak RSS and CPU time for the process;
* HTTP request count, and retries as ``requests - distinct (method, url, range)``;
* the speed the UI would have shown, sampled every 100 ms through the shipped
  ``DownloadTask`` path, including how often it read zero while bytes were
  actually arriving.

The script has no dependency on the rest of ``tools/`` so the identical file can
be copied into a baseline worktree and run against the old engine.

    python tools/realworld_bench.py --url URL --mode auto --repeat 3

Nothing here writes outside the work directory it is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil
import requests
from requests.adapters import HTTPAdapter

from config.settings import AppConfig
from core.download import DownloadController
from core.session import SessionManager
from core.task import DownloadTask, TaskStatus

_MB = 1024 * 1024

#: A truth window below this is noise, not a measurement.  Calibrated for a
#: slow link: at 1 MB/s a 1 MB threshold only ever passes the burst phase of
#: the quantum staircase, which is exactly the sample a biased estimator
#: gets wrong, so it hid the defect instead of exposing it.
_MIN_TRUTH = 256 * 1024


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------


class RequestCounter:
    """Count HTTP requests and detect retries, without touching the engine.

    A retry is a request for a ``(method, url, Range)`` triple that has already
    been issued **by this download**.  That definition is implementation-
    agnostic: it does not depend on how the engine numbers its attempts, and a
    legitimate resume counts as a retry because it is one.

    Attribution is by ``HTTPAdapter`` instance, i.e. by the download's own
    ``requests.Session``.  Counting everything the process sends instead was
    wrong in a way that produced a fake defect: four concurrent downloads of the
    same URL request the same ranges, so each one's "retry" was the other three
    doing legitimate work, and every counter saw every request because the
    patches nest.  Register the sessions with :meth:`own` before starting.
    """

    def __init__(self) -> None:
        self.count = 0
        self.keys: set[tuple] = set()
        self.repeats = 0
        self._lock = threading.Lock()
        self._orig = None
        self._adapter_ids: set[int] = set()

    def own(self, session: requests.Session) -> None:
        """Attribute *session*'s traffic to this counter."""
        with self._lock:
            for adapter in session.adapters.values():
                self._adapter_ids.add(id(adapter))

    def __enter__(self) -> "RequestCounter":
        self._orig = HTTPAdapter.send
        counter = self

        def send(adapter, request, *args, **kwargs):
            with counter._lock:
                mine = id(adapter) in counter._adapter_ids
                if mine:
                    key = (
                        getattr(request, "method", "?"),
                        getattr(request, "url", "?"),
                        request.headers.get("Range") if hasattr(request, "headers") else None,
                    )
                    counter.count += 1
                    if key in counter.keys:
                        counter.repeats += 1
                    else:
                        counter.keys.add(key)
            return counter._orig(adapter, request, *args, **kwargs)

        HTTPAdapter.send = send
        return self

    def __exit__(self, *exc):
        if self._orig is not None:
            HTTPAdapter.send = self._orig
        return False


class ResourceSampler:
    """Peak RSS and CPU time for the whole process, sampled in the background."""

    def __init__(self, interval: float = 0.1) -> None:
        self.interval = interval
        self.proc = psutil.Process()
        self.peak_rss = 0
        self.start_rss = 0
        self.cpu_seconds = 0.0
        self.wall = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                self.peak_rss = max(self.peak_rss, self.proc.memory_info().rss)
            except psutil.Error:
                pass
            self._stop.wait(self.interval)

    def __enter__(self) -> "ResourceSampler":
        try:
            self.start_rss = self.proc.memory_info().rss
        except psutil.Error:
            self.start_rss = 0
        self.peak_rss = self.start_rss
        self._cpu_start = sum(self.proc.cpu_times()[:2])
        self._t_start = time.monotonic()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        self.wall = time.monotonic() - self._t_start
        try:
            self.cpu_seconds = sum(self.proc.cpu_times()[:2]) - self._cpu_start
        except psutil.Error:
            self.cpu_seconds = 0.0
        return False

    @property
    def rss_delta_mb(self) -> float:
        return max(0.0, (self.peak_rss - self.start_rss)) / _MB

    @property
    def cpu_percent(self) -> float:
        """CPU as a percentage of one core."""
        if self.wall <= 0:
            return 0.0
        return 100.0 * self.cpu_seconds / self.wall


class SpeedProbe:
    """What the UI would have shown, sampled every ``interval`` seconds.

    Mirrors ``TaskManager._snap``: progress callbacks feed the task, and a
    sampler reads ``current_speed`` at its own cadence.  ``refresh_speed`` only
    exists in the current engine, so the baseline reads the field directly.
    """

    def __init__(self, interval: float = 0.1) -> None:
        self.task = DownloadTask(url="realworld://probe", filename="probe.bin")
        self.task.transition(TaskStatus.ANALYZING)
        self.task.mark_starting()
        self.task.mark_downloading()
        self.interval = interval
        self.samples: list[tuple[float, int, float]] = []
        self._truth: list[tuple[float, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self.started = time.monotonic()

    def on_progress(self, done: int, total: int) -> None:
        self.task.total_size = max(0, int(total))
        self.task.update_speed(done)
        self._truth.append((time.monotonic() - self.started, int(done)))

    def _sample(self) -> None:
        refresh = getattr(self.task, "refresh_speed", None)
        while not self._stop.is_set():
            if refresh is not None:
                refresh()
            self.samples.append(
                (
                    time.monotonic() - self.started,
                    int(self.task.downloaded_size),
                    float(self.task.current_speed),
                )
            )
            self._stop.wait(self.interval)

    def __enter__(self) -> "SpeedProbe":
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        return False

    def truth_rate(self, at: float, window: float = 1.0) -> float:
        """Bytes per second the ledger received over the window ending *at*.

        Returns zero when nothing arrived inside that window at all.  Without
        that guard the newest older point is used as the window's right edge, so
        a stall gets credited with the bytes that preceded it and the engine's
        (correct) zero during a genuine gap is scored as a defect.
        """
        hi = [p for p in self._truth if p[0] <= at]
        if not hi:
            return 0.0
        if at - hi[-1][0] > window:
            return 0.0
        lo = [p for p in self._truth if p[0] <= at - window]
        if not lo:
            return 0.0
        dt = hi[-1][0] - lo[-1][0]
        return (hi[-1][1] - lo[-1][1]) / dt if dt > 0 else 0.0

    def summary(self, end_at: float) -> dict:
        """Accuracy and the zero-while-active count."""
        errors: list[float] = []
        signed_errors: list[float] = []
        zero_while_active = 0
        active_samples = 0
        for at, _done, shown in self.samples:
            if at < 0.4 * end_at or at > 0.95 * end_at:
                continue
            truth = self.truth_rate(at)
            if truth < _MIN_TRUTH:
                continue
            active_samples += 1
            if shown <= 0.0:
                zero_while_active += 1
            errors.append(abs(shown - truth) / truth)
            signed_errors.append((shown - truth) / truth * 100.0)
        errors.sort()
        signed = statistics.median(signed_errors) if signed_errors else float("nan")
        return {
            "speed_err_signed_pct": signed,
            "speed_err_median_pct": (
                statistics.median(errors) * 100 if errors else float("nan")
            ),
            "speed_err_p95_pct": (
                errors[min(len(errors) - 1, int(len(errors) * 0.95))] * 100
                if errors
                else float("nan")
            ),
            "active_samples": active_samples,
            "zero_while_active": zero_while_active,
            "zero_while_active_pct": (
                100.0 * zero_while_active / active_samples if active_samples else 0.0
            ),
        }


def stability(series: list[tuple[float, int]]) -> dict:
    """Coefficient of variation of 1 s throughput samples."""
    if len(series) < 2:
        return {"cv_pct": float("nan"), "samples": 0}
    start = series[0][0]
    buckets: dict[int, int] = {}
    for at, done in series:
        buckets[int(at - start)] = done
    ordered = [buckets[k] for k in sorted(buckets)]
    rates = [
        (ordered[i] - ordered[i - 1])
        for i in range(1, len(ordered))
        if ordered[i] >= ordered[i - 1]
    ]
    if len(rates) < 2:
        return {"cv_pct": float("nan"), "samples": len(rates)}
    mean = statistics.mean(rates)
    if mean <= 0:
        return {"cv_pct": float("nan"), "samples": len(rates)}
    return {
        "cv_pct": 100.0 * statistics.pstdev(rates) / mean,
        "samples": len(rates),
        "min_mb_s": min(rates) / _MB,
        "max_mb_s": max(rates) / _MB,
    }


# ---------------------------------------------------------------------------
# One download
# ---------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def supports_modes() -> bool:
    """True when this tree knows the four named connection modes.

    The baseline (v1.4.6) only understands ``smart`` and ``manual``.  Rather
    than silently feed it ``auto`` and get manual behaviour, the harness maps
    the requested mode onto what the tree can actually do and records that.
    """
    try:
        from core.optimizer import policy_for  # noqa: F401
    except Exception:
        return False
    return True


def effective_mode(mode: str) -> str:
    """The mode this tree will really run, for a requested *mode*."""
    if supports_modes():
        return mode
    # Baseline: "smart" is its adaptive mode, which is what auto/balanced/
    # high_performance were intended to replace; it has no conservative.
    return "smart"


def make_config(mode: str, threads: int, smart_max: int, budget: int,
                stall_timeout: float = 20.0) -> AppConfig:
    """Shipped defaults, with only the knobs under test overridden."""
    cfg = AppConfig()
    cfg.connection_mode = mode
    cfg.num_threads = threads
    cfg.smart_max_connections = smart_max
    cfg.smart_adaptive = True
    # Absent on the baseline, where they are simply unused attributes.
    cfg.max_total_connections = budget
    cfg.stall_timeout = stall_timeout
    return cfg


def _saved_file(directory: Path) -> Path:
    """The finished payload, ignoring part files and resume sidecars.

    Part names are ``<name>.partN`` and sidecars ``<name>.dlstate``; picking by
    suffix alone would confuse ``.part1`` with a real extension.
    """
    best: Path | None = None
    best_size = -1
    for candidate in directory.iterdir():
        if not candidate.is_file():
            continue
        name = candidate.name
        if ".part" in name or name.endswith(".dlstate") or name.endswith(".tmp"):
            continue
        size = candidate.stat().st_size
        if size > best_size:
            best, best_size = candidate, size
    return best or (directory / "missing.bin")


def run_download(url: str, directory: Path, cfg: AppConfig,
                 probe: bool = True, control=None) -> dict:
    """Download *url* into *directory* and return the measurements."""
    directory.mkdir(parents=True, exist_ok=True)
    speed_probe = SpeedProbe() if probe else None
    counter = RequestCounter()
    result: dict = {}

    with ResourceSampler() as resources, counter:
        if speed_probe is not None:
            speed_probe.__enter__()
        started = time.monotonic()
        controller = None
        ok = False
        try:
            controller = DownloadController(
                cfg, SessionManager(cfg), show_progress=False
            )
            # Attribute this download's traffic before it starts; the sessions
            # are built by the constructor and the probe session lazily.
            try:
                counter.own(controller.session.session)
                counter.own(controller.session.probe_session)
            except Exception:  # pragma: no cover - counting must never block a run
                pass
            ok = controller.download_file(
                url,
                directory,
                progress_callback=speed_probe.on_progress if speed_probe else None,
                control=control,
            )
        finally:
            end_at = time.monotonic() - started
            if speed_probe is not None:
                speed_probe.__exit__(None, None, None)

    report = controller.last_report if controller is not None else None
    timeline = dict(report.timeline) if report is not None else {}
    transfer = timeline.get("transferred", end_at)
    saved = _saved_file(directory)

    size = saved.stat().st_size if saved.exists() else 0
    result.update(
        {
            "ok": bool(ok),
            "seconds": end_at,
            "transfer_seconds": transfer,
            "post_seconds": max(0.0, end_at - transfer),
            "bytes": size,
            "mb_per_s": size / _MB / transfer if transfer > 0 else 0.0,
            "ttfb": timeline.get("first_byte"),
            "requests": counter.count,
            "retries": counter.repeats,
            "rss_delta_mb": resources.rss_delta_mb,
            "rss_peak_mb": resources.peak_rss / _MB,
            "cpu_percent": resources.cpu_percent,
            "cpu_seconds": resources.cpu_seconds,
            "strategy": report.strategy if report is not None else "",
            "integrity": report.integrity if report is not None else "",
            # Genuine client-side measurement from the engine's ConnectionMeter,
            # not the loopback server's accepted-socket counter.
            "peak_conns": (
                report.peak_connections if report is not None else 0
            ),
            "segments": len(report.segments) if report is not None else 0,
            "sha256": sha256_of(saved) if saved.exists() else "",
            "path": str(saved),
        }
    )
    if speed_probe is not None:
        result.update(speed_probe.summary(end_at))
        result.update(stability(speed_probe._truth))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt(value, spec=".2f", dash="  n/a"):
    if value is None:
        return dash
    if isinstance(value, float) and value != value:
        return dash
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return dash


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "balanced", "high_performance",
                             "conservative", "manual", "smart"])
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--smart-max", type=int, default=8)
    ap.add_argument("--budget", type=int, default=32)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrent", type=int, default=1)
    ap.add_argument("--work-dir", default="")
    ap.add_argument("--reference", default="",
                    help="path to an independently downloaded copy for the hash check")
    ap.add_argument("--json", default="")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    work_root = Path(args.work_dir) if args.work_dir else Path(tempfile.gettempdir())
    runs: list[dict] = []
    reference_hash = sha256_of(Path(args.reference)) if args.reference else ""
    mode = effective_mode(args.mode)
    if mode != args.mode:
        print(f"note: this tree has no '{args.mode}' mode; running '{mode}'")

    for index in range(args.repeat):
        dirs = [Path(tempfile.mkdtemp(prefix="n13-real-", dir=work_root))
                for _ in range(args.concurrent)]
        results: list[dict] = [{} for _ in range(args.concurrent)]

        def worker(slot: int) -> None:
            # Each concurrent download gets its own config object so the shared
            # budget is the only thing they have in common.
            slot_cfg = make_config(mode, args.threads, args.smart_max,
                                   args.budget)
            results[slot] = run_download(args.url, dirs[slot], slot_cfg)
            results[slot]["mode"] = mode

        started = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(args.concurrent)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wall = time.monotonic() - started

        aggregate = sum(r.get("mb_per_s", 0.0) for r in results)
        print(f"run {index + 1}/{args.repeat}  mode={mode} "
              f"concurrent={args.concurrent}  wall={wall:.2f}s  "
              f"aggregate={aggregate:.2f} MB/s")
        for slot, res in enumerate(results):
            if args.concurrent > 1:
                print(f"  [{slot}] ", end="")
            else:
                print("  ", end="")
            print(f"{_fmt(res.get('mb_per_s'), '.2f'):>7} MB/s  "
                  f"ttfb {_fmt(res.get('ttfb'), '.3f'):>6}s  "
                  f"dur {_fmt(res.get('seconds'), '.2f'):>6}s  "
                  f"post {_fmt(res.get('post_seconds'), '.2f'):>5}s  "
                  f"req {res.get('requests', 0):>3}  retry {res.get('retries', 0):>2}  "
                  f"cv {_fmt(res.get('cv_pct'), '.1f'):>5}%  "
                  f"conn {res.get('peak_conns', 0):>2}  "
                  f"rss +{_fmt(res.get('rss_delta_mb'), '.1f'):>5}MB  "
                  f"cpu {_fmt(res.get('cpu_percent'), '.1f'):>5}%  "
                  f"zero {res.get('zero_while_active', 0):>2}/{res.get('active_samples', 0):<3} "
                  f"err {_fmt(res.get('speed_err_median_pct'), '.1f'):>5}%  "
                  f"{'OK' if res.get('ok') else 'FAIL'}")
            if reference_hash:
                match = res.get("sha256") == reference_hash
                print(f"       sha256 {'MATCH' if match else 'MISMATCH'} "
                      f"{res.get('sha256', '')[:16]}… "
                      f"ref {reference_hash[:16]}…")
        runs.append({"concurrent": args.concurrent, "wall": wall,
                     "aggregate_mb_s": aggregate, "results": results})

        if not args.keep:
            for directory in dirs:
                for leftover in directory.iterdir():
                    try:
                        leftover.unlink()
                    except OSError:
                        pass
                try:
                    directory.rmdir()
                except OSError:
                    pass

    if args.json:
        Path(args.json).write_text(
            json.dumps({"url": args.url, "mode": mode, "requested_mode": args.mode,
                        "reference_sha256": reference_hash, "runs": runs},
                       indent=2, default=str),
            encoding="utf-8",
        )
        print(f"raw results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
