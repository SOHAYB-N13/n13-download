"""Fair, reproducible N13 vs Internet Download Manager benchmark.

Both engines are driven through the same public URL(s) and measured with the
same client-side observers:

* file growth every 50 ms (used for observed first-byte/first-write time and
  one-second throughput stability);
* wall-clock duration until all requested files reach the expected size;
* process-tree peak RSS and CPU seconds;
* final size and SHA-256 against an independent reference;
* N13's genuine client-side connection peak from its ``last_report``.

IDM is driven only through its documented command line
(``https://www.internetdownloadmanager.com/support/command_line.html``).

Metrics that cannot be observed from another vendor's CLI (HTTP retries,
failed requests, socket count) are reported as ``null`` rather than guessed.

Example::

    python tools/idm_compare_bench.py --engine n13 --url URL --name file.bin \
        --repeat 3 --work-dir H:/app/n13-download/docs/realworld/idm_bench \
        --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil

_MB = 1024 * 1024


# ---------------------------------------------------------------------------
# Common observers
# ---------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class FileGrowthMonitor:
    """Sample the sum of completed-file bytes across all target directories."""

    def __init__(self, directories: list[Path], interval: float = 0.05) -> None:
        self.directories = directories
        self.interval = interval
        self.samples: list[tuple[float, int]] = []
        self.first_write: Optional[float] = None
        self._start = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _targets(self) -> list[Path]:
        targets: list[Path] = []
        for directory in self.directories:
            if not directory.exists():
                continue
            for candidate in directory.iterdir():
                name = candidate.name
                if not candidate.is_file():
                    continue
                if ".part" in name or name.endswith(".dlstate") or name.endswith(".tmp"):
                    continue
                if name.endswith(".merging"):
                    continue
                targets.append(candidate)
        return targets

    def _run(self) -> None:
        while not self._stop.is_set():
            total = 0
            for target in self._targets():
                try:
                    total += target.stat().st_size
                except OSError:
                    pass
            at = time.monotonic() - self._start
            self.samples.append((at, total))
            if self.first_write is None and total > 0:
                self.first_write = at
            self._stop.wait(self.interval)

    def __enter__(self) -> "FileGrowthMonitor":
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        return False

    def stability(self) -> dict:
        """Coefficient of variation of one-second byte deltas."""
        buckets: dict[int, int] = {}
        for at, done in self.samples:
            buckets[int(at)] = done
        ordered = [buckets[k] for k in sorted(buckets)]
        rates = [
            ordered[i] - ordered[i - 1]
            for i in range(1, len(ordered))
            if ordered[i] >= ordered[i - 1]
        ]
        rates = [value for value in rates if value > 0]
        if len(rates) < 2:
            return {"cv_pct": None, "samples": len(rates)}
        mean = statistics.mean(rates)
        return {
            "cv_pct": 100.0 * statistics.pstdev(rates) / mean if mean else None,
            "samples": len(rates),
            "min_mb_s": min(rates) / _MB,
            "max_mb_s": max(rates) / _MB,
        }


class ProcessSampler:
    """Peak RSS and CPU of one process tree (or this process for N13)."""

    def __init__(self, idm_mode: bool = False, started_at: float = 0.0) -> None:
        self.idm_mode = idm_mode
        self.started_at = started_at
        self.interval = 0.1
        self.peak_rss_mb = 0.0
        self.cpu_seconds = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._cpu_before = 0.0

    def _procs(self) -> list[psutil.Process]:
        if not self.idm_mode:
            return [psutil.Process()]
        found: list[psutil.Process] = []
        for proc in psutil.process_iter(["name", "create_time"]):
            try:
                if proc.info["name"] not in ("IDMan.exe", "IDM1.tmp"):
                    continue
                if proc.info["create_time"] + 1.0 < self.started_at:
                    continue
                found.append(proc)
            except (psutil.Error, TypeError):
                continue
        return found

    def _metrics(self) -> tuple[float, float]:
        rss = 0
        cpu = 0.0
        for proc in self._procs():
            try:
                rss += proc.memory_info().rss
                cpu += sum(proc.cpu_times()[:2])
            except psutil.Error:
                continue
        return rss, cpu

    def _run(self) -> None:
        initial_rss, self._cpu_before = self._metrics()
        self.peak_rss_mb = initial_rss / _MB
        while not self._stop.is_set():
            rss, _cpu = self._metrics()
            self.peak_rss_mb = max(self.peak_rss_mb, rss / _MB)
            self._stop.wait(self.interval)
        _end_rss, end_cpu = self._metrics()
        self.cpu_seconds = max(0.0, end_cpu - self._cpu_before)

    def __enter__(self) -> "ProcessSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        return False


# ---------------------------------------------------------------------------
# Engine runners
# ---------------------------------------------------------------------------


def run_n13(
    urls: list[str],
    directories: list[Path],
    mode: str,
    threads: int,
    smart_max: int,
    budget: int,
) -> list[dict]:
    from config.settings import AppConfig
    from core.download import DownloadController
    from core.session import SessionManager

    results: list[dict] = [{} for _ in urls]
    controllers: list[DownloadController] = []
    for _ in urls:
        cfg = AppConfig()
        cfg.connection_mode = mode
        cfg.num_threads = threads
        cfg.smart_max_connections = smart_max
        cfg.smart_adaptive = True
        cfg.max_total_connections = budget
        cfg.block_private_urls = False
        cfg.verify_size = True
        cfg.show_progress = False
        controllers.append(
            DownloadController(cfg, SessionManager(cfg), show_progress=False)
        )

    def worker(slot: int) -> None:
        ok = controllers[slot].download_file(urls[slot], directories[slot])
        report = controllers[slot].last_report
        results[slot] = {
            "ok": bool(ok),
            "engine_ttfb": report.ttfb if report else None,
            "peak_conns": report.peak_connections if report else 0,
            "integrity": report.integrity if report else "",
            "strategy": report.strategy if report else "",
            "segments": len(report.segments) if report else 0,
            "failure_reason": report.failure_reason if report else "",
        }

    threads_list = [threading.Thread(target=worker, args=(i,)) for i in range(len(urls))]
    for thread in threads_list:
        thread.start()
    for thread in threads_list:
        thread.join()
    return results


def close_idm() -> None:
    """Close IDM processes started by this harness, gracefully first."""
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] not in ("IDMan.exe", "IDM1.tmp"):
            continue
        try:
            proc.terminate()
        except psutil.Error:
            pass
    time.sleep(0.5)
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] not in ("IDMan.exe", "IDM1.tmp"):
            continue
        try:
            proc.kill()
        except psutil.Error:
            pass


def run_idm(
    urls: list[str],
    directories: list[Path],
    names: list[str],
    idman: Path,
    direct: bool = False,
) -> list[dict]:
    """Queue *urls* through IDM's documented CLI and wait for the files."""
    close_idm()
    # Both launchers stay resident instead of exiting, so these must be
    # fire-and-forget Popen calls; waiting on them would hang the harness.
    for url, directory, name in zip(urls, directories, names):
        directory.mkdir(parents=True, exist_ok=True)
        command = [str(idman), "/n", "/d", url, "/p", str(directory), "/f", name]
        if not direct:
            command.append("/a")
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if not direct:
        time.sleep(1.0)
        subprocess.Popen(
            [str(idman), "/s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    results: list[dict] = []
    for url, directory, name in zip(urls, directories, names):
        results.append({
            "ok": False,
            "engine_ttfb": None,
            "peak_conns": None,
            "integrity": "",
            "strategy": "idm-cli",
            "segments": 0,
            "failure_reason": "",
        })
    return results


def wait_for_files(
    directories: list[Path],
    names: list[str],
    expected_size: int,
    timeout: float,
) -> tuple[bool, float]:
    """Wait until every target file exists with the expected size."""
    start = time.monotonic()
    targets = [directory / name for directory, name in zip(directories, names)]
    while time.monotonic() - start < timeout:
        if all(
            target.exists() and target.stat().st_size >= expected_size
            for target in targets
        ):
            return True, time.monotonic() - start
        time.sleep(0.1)
    return False, time.monotonic() - start


# ---------------------------------------------------------------------------
# Optional controlled loopback source
# ---------------------------------------------------------------------------


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # IDM opens speculative connections that it closes before sending a
        # request line; that is normal and must not fill stderr.
        return


def _make_local_handler(payload: bytes, supports_range: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def handle_error(self, request, client_address):
            # IDM probes with speculative connections that it closes before
            # sending a request line.  That is normal; do not bury the results
            # in socketserver tracebacks.
            return

        def _headers(self, status: int, length: int, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Type", "application/octet-stream")
            if supports_range:
                self.send_header("Accept-Ranges", "bytes")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def do_HEAD(self):
            self._headers(200, len(payload))

        def do_GET(self):
            if supports_range:
                match = re.match(r"bytes=(\d+)-(\d*)$", self.headers.get("Range", ""))
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else len(payload) - 1
                    end = min(end, len(payload) - 1)
                    if start > end:
                        self._headers(416, 0, {"Content-Range": f"bytes */{len(payload)}"})
                        return
                    chunk = payload[start:end + 1]
                    self._headers(
                        206, len(chunk),
                        {"Content-Range": f"bytes {start}-{end}/{len(payload)}"},
                    )
                    self.wfile.write(chunk)
                    return
            self._headers(200, len(payload))
            self.wfile.write(payload)

    return Handler


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["n13", "idm"], required=True)
    ap.add_argument("--url", action="append", required=True)
    ap.add_argument("--name", action="append", required=True)
    ap.add_argument("--expected-size", type=int, required=True)
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--smart-max", type=int, default=8)
    ap.add_argument("--budget", type=int, default=32)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--reference", default="")
    ap.add_argument("--idman", default=r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe")
    ap.add_argument("--idm-direct", action="store_true",
                    help="start each IDM task immediately instead of queueing it")
    ap.add_argument("--local-size-mb", type=int, default=0,
                    help="serve a deterministic payload from loopback")
    ap.add_argument("--local-range", action="store_true",
                    help="loopback server honours Range; otherwise it always sends 200")
    ap.add_argument("--json", required=True)
    args = ap.parse_args()

    if len(args.url) != len(args.name):
        raise SystemExit("--url and --name counts must match")
    if args.engine == "idm" and not Path(args.idman).exists():
        raise SystemExit(f"IDM executable not found: {args.idman}")

    local_server: Optional[ThreadingHTTPServer] = None
    if args.local_size_mb:
        payload = bytes((i * 31 + 7) % 251 for i in range(args.local_size_mb * _MB))
        local_server = _QuietThreadingHTTPServer(
            ("127.0.0.1", 0), _make_local_handler(payload, args.local_range)
        )
        threading.Thread(target=local_server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local_server.server_port}"
        # Distinct paths/names prevent IDM from de-duplicating what is meant to
        # be a concurrent set of independent transfers.
        args.url = [f"{base}/payload{i}.bin" for i in range(len(args.url))]
        args.name = [f"payload{i}.bin" for i in range(len(args.name))]
        args.expected_size = len(payload)

    work_root = Path(args.work_dir)
    work_root.mkdir(parents=True, exist_ok=True)
    reference_hash = (
        hashlib.sha256(payload).hexdigest()
        if args.local_size_mb
        else sha256_of(Path(args.reference)) if args.reference else ""
    )
    runs: list[dict] = []

    for index in range(args.repeat):
        directories = [
            Path(tempfile.mkdtemp(prefix=f"{args.engine}-", dir=work_root))
            for _ in args.url
        ]
        started_at = time.monotonic()
        with FileGrowthMonitor(directories) as monitor:
            sampler = ProcessSampler(args.engine == "idm", started_at)
            with sampler:
                wall_start = time.monotonic()
                if args.engine == "n13":
                    results = run_n13(
                        args.url, directories, args.mode, args.threads,
                        args.smart_max, args.budget,
                    )
                else:
                    results = run_idm(
                        args.url, directories, args.name, Path(args.idman),
                        direct=args.idm_direct,
                    )
                if args.engine == "n13":
                    # A direct-layout partial file can reach the declared size
                    # before every range has been written, so only the engine's
                    # own completion result is authoritative.
                    complete = all(bool(result.get("ok")) for result in results)
                    wait_seconds = 0.0
                else:
                    complete, wait_seconds = wait_for_files(
                        directories, args.name, args.expected_size, args.timeout
                    )
                wall = time.monotonic() - wall_start

        hash_matches: list[bool] = []
        sizes: list[int] = []
        for slot, directory in enumerate(directories):
            target = directory / args.name[slot]
            exists = target.exists()
            size = target.stat().st_size if exists else 0
            sizes.append(size)
            engine_ok = (
                bool(results[slot].get("ok"))
                if args.engine == "n13"
                else complete
            )
            results[slot]["ok"] = engine_ok and complete and size == args.expected_size
            results[slot]["bytes"] = size
            digest = sha256_of(target) if exists else ""
            results[slot]["sha256"] = digest
            results[slot]["hash_match"] = (
                bool(reference_hash) and digest == reference_hash
            )
            hash_matches.append(results[slot]["hash_match"])
        aggregate = sum(sizes) / _MB / wall if wall > 0 else 0.0
        run = {
            "engine": args.engine,
            "repeat_index": index,
            "concurrent": len(args.url),
            "wall": wall,
            "wait_seconds": wait_seconds,
            "aggregate_mb_s": aggregate,
            "observed_first_write": monitor.first_write,
            "rss_peak_mb": sampler.peak_rss_mb,
            "cpu_seconds": sampler.cpu_seconds,
            "cpu_percent": 100.0 * sampler.cpu_seconds / wall if wall else 0.0,
            "stability": monitor.stability(),
            "hash_matches": hash_matches,
            "results": results,
            "directories": [str(path) for path in directories],
        }
        runs.append(run)
        print(
            f"{args.engine} run {index + 1}/{args.repeat} n={len(args.url)} "
            f"wall={wall:.2f}s agg={aggregate:.2f}MB/s "
            f"first={monitor.first_write!s:>8} "
            f"rss={sampler.peak_rss_mb:.1f}MB cpu={sampler.cpu_seconds:.2f}s "
            f"hash={hash_matches}"
        )
        for directory in directories:
            for leftover in directory.iterdir():
                try:
                    if leftover.is_file():
                        leftover.unlink()
                    else:
                        leftover.rmdir()
                except OSError:
                    pass
            try:
                directory.rmdir()
            except OSError:
                pass

    Path(args.json).write_text(
        json.dumps(
            {
                "engine": args.engine,
                "urls": args.url,
                "names": args.name,
                "expected_size": args.expected_size,
                "reference_sha256": reference_hash,
                "mode": args.mode,
                "runs": runs,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if args.engine == "idm":
        close_idm()
    if local_server is not None:
        local_server.shutdown()
        local_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
