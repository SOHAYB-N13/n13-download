"""Adaptive download optimizer.

What was wrong before
=====================
The old controller decided parallelism from **file size alone** (1 segment below
1 MB, 2 below 16 MB, 4 below 128 MB, 6 below 1 GB, 8 above) and could never run
more connections than segments existed.  For the overwhelming majority of real
downloads — 10 MB to 500 MB — that meant 4 connections, permanently.  Measured
on a link that caps each connection at 4 MB/s, that cost **42 % of the available
throughput** (13.7 MB/s instead of 23.8 MB/s) with no benefit to anyone.

Growth was also gated on a 20 % throughput improvement sustained across a 10 s
window, while each step added two connections.  A single extra connection almost
never buys 20 % of the *total*, so the ramp effectively never fired.

What this does instead
======================
**Segment planning** creates more ranges than there are connections.  Ranges are
cheap (a queued range is just a number pair); connections are not.  Having
``2 × ceiling`` ranges in flight means a finished connection immediately picks up
the next range instead of idling, which is what removes the long single-segment
tail at the end of a download.

**Concurrency** is decided by measurement, in a probe cycle:

1. take a baseline throughput at the current connection count,
2. add one connection,
3. after a settle period, compare the gain against what one more connection
   *should* have bought (the current per-connection rate),
4. if it bought at least a third of that, probe again; otherwise declare the
   link saturated and stop for a while.

The expectation is per-connection, not per-total, so the test is the same whether
one connection delivers 200 KB/s or 20 MB/s.  Growth is +1 at a time rather than
+2, so the controller can stop at 5 or 6 when that is where the link tops out.

Safety
------
Only *concurrency* is adjusted, at range boundaries.  No range is split while a
connection is writing into it, no ``.part`` file is deleted or invalidated, and
resume state is untouched — byte-range ownership is fixed for the whole
download, which is what keeps the integrity guarantees intact.
"""

from __future__ import annotations

import math
from collections import deque
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

_MB = 1024 * 1024

#: Preferred minimum size of one byte range.  Smaller ranges cost a request each
#: and make merging chattier without buying anything on a normal link.
TARGET_SEGMENT = 4 * _MB

#: How many ranges to create per allowed connection.  The surplus is the
#: headroom that keeps connections busy and absorbs the tail.
SEGMENTS_PER_CONNECTION = 2

#: Connections to open before any measurement exists.
#:
#: The ceiling, not a fraction of it.  A per-connection cap is the normal case
#: (it is the reason a download manager exists at all), and ramping toward the
#: ceiling costs the whole ramp: measured on a link capped at 4 MB/s per
#: connection, starting at 4 and adding one every 1.5 s finished a 48 MB file in
#: 3.5 s, while starting at 8 finished it in 1.9 s.  The ramp had not even
#: reached 6 before the transfer ended.
#:
#: Starting high is not "blind growth" — the ceiling is the user's configured
#: maximum, every range is already owned, and the controller actively *reduces*
#: concurrency when the evidence says more connections hurt (see observe()).
START_CONNECTIONS = 0   # 0 means "use the ceiling"

#: Time after a change before its effect can be judged.
SETTLE = 1.5

#: Minimum spacing between two evaluations.
EVAL_INTERVAL = 1.0

#: No adaptation at all for this long after the transfer starts: TCP slow start
#: and TLS handshakes make the first second's throughput meaningless.
WARMUP = 1.0

#: Fraction of the *expected* per-connection gain that must materialise for the
#: controller to conclude that more connections still help.
GAIN_THRESHOLD = 0.35

#: How long to stop probing after the link is found to be saturated.
SATURATION_COOLDOWN = 20.0

#: How long to stop probing after backing off from a collapse or a server error.
BACKOFF_COOLDOWN = 15.0

#: A drop below this fraction of the best observed rate, with no change pending,
#: is treated as the server throttling us rather than a normal fluctuation.
COLLAPSE_RATIO = 0.45

#: Retry-pressure policy.  A transfer that keeps re-issuing the same range is
#: telling us something a throughput sample cannot: the sockets it holds are not
#: being served.  Three retries inside twenty seconds halves the connection
#: count.  This is the signal a constrained link produces — every socket is
#: equally slow, so no throughput *collapse* is ever detected, yet the plan is
#: still far too wide (measured: 12 sockets on a 1 MB/s link, 12-14 retries per
#: download, one failure in eight).
RETRY_WINDOW = 20.0
RETRY_STORM = 3


# ---------------------------------------------------------------------------
# Download modes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModePolicy:
    """How the adaptive controller behaves for one download mode.

    The modes are not connection-count presets.  Each one changes *policy*:
    how large a byte range should be, how much parallelism headroom to create
    per connection, how long to wait before judging a change, how much gain a
    probe must produce to be kept, and how long to stop probing after the link
    turns out to be saturated or hostile.

    ``ceiling_factor`` scales the user's ``smart_max_connections`` rather than
    replacing it, so the mode never silently overrides an explicit limit.
    """

    name: str
    label: str
    description: str
    target_segment: int
    segments_per_connection: int
    start_connections: int          # 0 means "start at the ceiling"
    settle: float
    eval_interval: float
    warmup: float
    gain_threshold: float
    saturation_cooldown: float
    backoff_cooldown: float
    collapse_ratio: float
    ceiling_factor: float
    ceiling_cap: int

    def ceiling_for(self, configured: int) -> int:
        """Resolve the connection ceiling for a configured maximum."""
        base = max(1, int(configured))
        scaled = int(round(base * self.ceiling_factor))
        return max(1, min(self.ceiling_cap, scaled))

    def start_for(self, ceiling: int) -> int:
        """Connections to open before any measurement exists."""
        if self.start_connections <= 0:
            return ceiling
        return max(1, min(ceiling, self.start_connections))


#: The tuned default: full adaptivity, the shipped segment sizing.
AUTO_POLICY = ModePolicy(
    name="auto",
    label="Auto",
    description="Adapts to the link and the server; the recommended default.",
    target_segment=TARGET_SEGMENT,
    segments_per_connection=SEGMENTS_PER_CONNECTION,
    start_connections=START_CONNECTIONS,
    settle=SETTLE,
    eval_interval=EVAL_INTERVAL,
    warmup=WARMUP,
    gain_threshold=GAIN_THRESHOLD,
    saturation_cooldown=SATURATION_COOLDOWN,
    backoff_cooldown=BACKOFF_COOLDOWN,
    collapse_ratio=COLLAPSE_RATIO,
    ceiling_factor=1.0,
    ceiling_cap=16,
)

#: Same engine, calmer: fewer connections, slower judgement, longer pauses
#: after saturation.  For shared or metered links where a visible ramp is worse
#: than a slightly lower peak.
BALANCED_POLICY = ModePolicy(
    name="balanced",
    label="Balanced",
    description="Fewer connections and slower adjustments; steady over peak.",
    target_segment=4 * _MB,
    segments_per_connection=2,
    start_connections=0,
    settle=2.0,
    eval_interval=1.5,
    warmup=1.5,
    gain_threshold=0.5,
    saturation_cooldown=30.0,
    backoff_cooldown=20.0,
    collapse_ratio=0.5,
    ceiling_factor=0.75,
    ceiling_cap=12,
)

#: Smaller ranges and more headroom, so the tail of a download keeps every
#: connection busy; probes sooner and gives up on a saturated link sooner.
HIGH_PERFORMANCE_POLICY = ModePolicy(
    name="high_performance",
    label="High performance",
    description="Smaller ranges, more headroom, faster probing; for fast links.",
    target_segment=2 * _MB,
    segments_per_connection=3,
    start_connections=0,
    settle=1.0,
    eval_interval=0.75,
    warmup=0.6,
    gain_threshold=0.25,
    saturation_cooldown=15.0,
    backoff_cooldown=10.0,
    collapse_ratio=0.4,
    ceiling_factor=1.0,
    ceiling_cap=16,
)

#: Few connections, large ranges, long pauses.  For fragile servers, strict
#: per-IP limits, and links where extra connections make things worse.
CONSERVATIVE_POLICY = ModePolicy(
    name="conservative",
    label="Conservative",
    description="Few connections, large ranges, long pauses; for fragile servers.",
    target_segment=8 * _MB,
    segments_per_connection=1,
    start_connections=2,
    settle=2.5,
    eval_interval=2.0,
    warmup=2.0,
    gain_threshold=0.6,
    saturation_cooldown=60.0,
    backoff_cooldown=30.0,
    collapse_ratio=0.55,
    ceiling_factor=0.4,
    ceiling_cap=4,
)

MODES = {
    p.name: p
    for p in (
        AUTO_POLICY,
        BALANCED_POLICY,
        HIGH_PERFORMANCE_POLICY,
        CONSERVATIVE_POLICY,
    )
}

#: Values the engine accepts for ``connection_mode``.  ``smart`` is the legacy
#: name for Auto and is kept so saved settings keep working; ``manual`` means
#: "use the fixed thread count", which is not a policy and is handled outside
#: the optimizer.
ADAPTIVE_MODES = frozenset(MODES)
MANUAL_MODE = "manual"

_MODE_ALIASES = {
    "smart": "auto",
    "auto": "auto",
    "balanced": "balanced",
    "high": "high_performance",
    "high_performance": "high_performance",
    "performance": "high_performance",
    "conservative": "conservative",
    "safe": "conservative",
}


def normalise_mode(mode: str) -> str:
    """Map a stored value to a canonical mode name, or ``manual``."""
    key = str(mode or "").strip().lower()
    if key == MANUAL_MODE:
        return MANUAL_MODE
    return _MODE_ALIASES.get(key, "auto")


def is_adaptive(mode: str) -> bool:
    """True when *mode* should run the adaptive controller."""
    return normalise_mode(mode) != MANUAL_MODE


def policy_for(mode: str) -> ModePolicy:
    """The policy for *mode*; unknown values fall back to Auto."""
    return MODES.get(normalise_mode(mode), AUTO_POLICY)


class ConnectionGovernor:
    """Thread-safe limiter controlling how many segments run concurrently.

    Raising ``max_active`` lets more queued segments start; lowering it waits
    for active segments to finish (a natural segment boundary) before letting
    new ones in.  No in-flight segment is ever interrupted.
    """

    def __init__(self, max_active: int = 1) -> None:
        self._cond = threading.Condition()
        self._max_active = max(1, int(max_active))
        self._active = 0

    def set_max_active(self, value: int) -> None:
        with self._cond:
            self._max_active = max(1, int(value))
            self._cond.notify_all()

    @property
    def max_active(self) -> int:
        with self._cond:
            return self._max_active

    @property
    def active(self) -> int:
        with self._cond:
            return self._active

    def acquire(self) -> None:
        with self._cond:
            while self._active >= self._max_active:
                self._cond.wait()
            self._active += 1

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()


class SmartOptimizer:
    """Segment planning plus a measurement-driven concurrency ramp."""

    def __init__(
        self,
        max_connections: int = 8,
        adaptive: bool = True,
        on_status: Optional[Callable[[str], None]] = None,
        log: Optional[Callable[..., None]] = None,
        clock: Callable[[], float] = time.monotonic,
        policy: Optional[ModePolicy] = None,
    ) -> None:
        self.policy = policy or AUTO_POLICY
        # The mode scales the configured maximum instead of replacing it, so an
        # explicit limit is never silently exceeded.
        self.ceiling = self.policy.ceiling_for(max_connections)
        self.adaptive = bool(adaptive)
        self._on_status = on_status
        if log is None:
            self._log = lambda *a, **k: None
        else:
            # Call sites use printf-style placeholders.  Rich's console.print
            # (the packaged CLI's logger) does not apply %-formatting, so it
            # printed raw placeholders followed by loose values.  Format here
            # once and hand the logger a single finished message.
            def _log(message: str, *args: Any, **kwargs: Any) -> None:
                if args:
                    try:
                        message = message % args
                    except (TypeError, ValueError):
                        message = " ".join(
                            [message] + [str(value) for value in args]
                        )
                log(message, **kwargs)

            self._log = _log
        self._clock = clock

        self._lock = threading.Lock()
        self._governor: Optional[ConnectionGovernor] = None
        self._segments = 1
        self._segment_size = 0
        self._initial = 1
        self._start = 0.0
        self._last_error = -1e9
        self._last_change = 0.0
        self._blocked_until = 0.0
        self._speed_limited = False

        # Probe state
        self._last_eval = 0.0
        self._last_completed = 0
        self._baseline_rate = 0.0
        self._baseline_conns = 0
        self._best_rate = 0.0
        self._probing = False

        # Retry-pressure state (see RETRY_STORM), and the hard cap imposed by
        # the process-wide budget's fair share (0 = no cap).
        self._retry_marks: deque = deque()
        self._last_retry_backoff = -1e9
        self._fair_ceiling = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _effective_ceiling(self) -> int:
        """The connection ceiling after the budget's fair share is applied."""
        if self._fair_ceiling > 0:
            return max(1, min(self.ceiling, self._fair_ceiling))
        return max(1, self.ceiling)

    def segment_count(
        self, total_size: int, supports_range: bool, min_part: int = 2 * _MB
    ) -> int:
        """How many byte ranges to create for this file.

        Not the connection count — that is the governor's job.  This is the
        parallelism *headroom*: enough ranges that a finishing connection always
        has another one waiting, without making merging chatty.
        """
        if not supports_range or total_size <= 0:
            return 1
        size = int(total_size)
        min_part = max(64 * 1024, int(min_part))
        target = max(min_part, self.policy.target_segment)
        wanted = math.ceil(size / target)
        ceiling = self._effective_ceiling()
        wanted = max(1, min(ceiling * self.policy.segments_per_connection, wanted))
        # Round up to a whole number of "waves" of `ceiling` connections.
        # A remainder wave runs with fewer connections than allowed, so the last
        # stretch of every download is under-utilised: 12 ranges over 8
        # connections spends its final second with only 4 sockets busy, which
        # measured 19.1 MB/s instead of the 23.8 MB/s a whole 16-range plan gets.
        #
        # Only when there is more than one wave to align.  Below that, rounding
        # up *is* the mistake: an 8 MB file wants 2 ranges, and rounding it to a
        # wave of 8 turns it into eight 1 MB requests — three times the requests
        # and a chattier merge for a file that never needed the headroom.
        if wanted >= ceiling:
            waves = math.ceil(wanted / ceiling)
            seg = waves * ceiling
        else:
            seg = wanted
        # Never create a range smaller than the configured minimum part size:
        # that is what keeps a 2 MB file from being cut into eight requests.
        seg = min(seg, max(1, size // min_part))
        return max(1, seg)

    def start(
        self, total_size: int, supports_range: bool, min_part: int = 2 * _MB
    ) -> tuple:
        """Begin a smart download.

        Returns ``(segment_count, governor)``.  ``governor`` is ``None`` when
        adaptivity is off or the file is a single range, in which case every
        segment runs concurrently.
        """
        seg = self.segment_count(total_size, supports_range, min_part)
        self._segments = seg
        self._segment_size = (int(total_size) // seg) if seg else 0
        if not self.adaptive:
            initial = seg
        else:
            initial = min(seg, self.policy.start_for(self._effective_ceiling()))
        self._initial = initial
        now = self._clock()
        self._start = now
        self._last_eval = 0.0
        self._last_completed = 0
        self._baseline_rate = 0.0
        self._baseline_conns = 0
        self._best_rate = 0.0
        self._probing = False
        self._retry_marks.clear()
        self._last_retry_backoff = -1e9
        self._blocked_until = 0.0
        self._last_change = 0.0
        self._last_error = -1e9
        governor = ConnectionGovernor(initial) if (self.adaptive and seg > 1) else None
        self._governor = governor
        self._emit_status(str(initial))
        self._log(
            "Smart optimizer: %d range(s), %d connection(s) initially (ceiling %d)",
            seg, initial, self.ceiling,
        )
        return seg, governor

    def set_speed_limited(self, limited: bool) -> None:
        """Tell the optimizer that an artificial bandwidth cap is active.

        A cap makes throughput unresponsive to connection count by design, so
        probing for more would chase a limit the user asked for.
        """
        self._speed_limited = bool(limited)

    # ------------------------------------------------------------------ #
    # Feedback
    # ------------------------------------------------------------------ #

    def on_server_error(self, status: Optional[int]) -> None:
        """Reduce parallelism after a server-imposed failure and cool down."""
        now = self._clock()
        with self._lock:
            self._last_error = now
            gov = self._governor
            if gov is None:
                return
            cur = gov.max_active
            new = max(1, cur // 2)
            if new < cur:
                gov.set_max_active(new)
                self._last_change = now
                self._blocked_until = now + self.policy.backoff_cooldown
                self._probing = False
                self._log(
                    "Smart optimizer: server instability (%s) — connections "
                    "%d -> %d",
                    status if status is not None else "connection error", cur, new,
                )
                self._emit_status(f"{cur}->{new}")

    def note_retry(self) -> None:
        """Record one re-issued range; back off when they come in a storm.

        Throughput collapse cannot see a link that serves every socket equally
        badly: the rate is low but stable, so the probe never concludes that the
        plan is too wide.  Retries can see it — a range that has to be requested
        again is a socket that was not being served.  Halving on the third retry
        inside :data:`RETRY_WINDOW` turns a 12-socket plan into 6 and then 3
        before it turns into a failed download.
        """
        now = self._clock()
        with self._lock:
            marks = self._retry_marks
            marks.append(now)
            while marks and now - marks[0] > RETRY_WINDOW:
                marks.popleft()
            if len(marks) < RETRY_STORM:
                return
            if now - self._last_retry_backoff < self.policy.backoff_cooldown:
                return
            gov = self._governor
            if gov is None:
                return
            cur = gov.max_active
            # The fair share is an *upper* bound, never a floor: using it here
            # made the backoff a no-op whenever the share exceeded the current
            # count, which is exactly the 4-download case it exists for.
            new = max(1, cur // 2)
            self._last_retry_backoff = now
            marks.clear()
            if new >= cur:
                return
            gov.set_max_active(new)
            self._last_change = now
            self._blocked_until = now + self.policy.backoff_cooldown
            self._probing = False
            self._log(
                "Smart optimizer: %d retries in %.0fs — connections %d -> %d",
                RETRY_STORM, RETRY_WINDOW, cur, new,
            )
            self._emit_status(f"{cur}->{new}")

    def set_fair_ceiling(self, share: int) -> None:
        """Cap this download at *share* connections (0 = uncapped).

        The process-wide budget bounds the machine, but a semaphore hands its
        slots to whoever asks first, so one wide download can starve a small one
        (measured: 22.7 s to first byte while a peer finished).  Capping each
        download at ``budget // active_downloads`` gives every transfer a floor
        instead of a race.
        """
        share = max(0, int(share))
        with self._lock:
            self._fair_ceiling = share
            gov = self._governor
            if gov is not None and share > 0:
                gov.set_max_active(min(gov.max_active, share))

    def observe(self, completed: int, total: int) -> None:
        """Adaptive decision hook — called from the (coalesced) progress path.

        Policy, in order:

        1. Ignore everything for the first second (slow start is not a signal).
        2. Ignore everything near completion (rearranging the last ranges makes
           the tail noisier, not faster).
        3. If a change is being judged, compare its gain with what the measured
           per-connection rate said it should have bought.
        4. Otherwise reduce on a collapse, and — only when there is room below
           the ceiling, which happens after a reduction — probe back upward.
        """
        gov = self._governor
        if gov is None or not self.adaptive:
            return
        now = self._clock()
        with self._lock:
            if self._speed_limited:
                return
            if now - self._last_error < self.policy.backoff_cooldown:
                return
            if now - self._start < self.policy.warmup:
                return
            if completed <= 0 or total <= 0:
                return
            if now - self._last_eval < self.policy.eval_interval:
                return

            elapsed = now - self._last_eval if self._last_eval else 0.0
            if elapsed <= 0:
                self._last_eval = now
                self._last_completed = completed
                return
            rate = max(0.0, (completed - self._last_completed) / elapsed)
            self._last_eval = now
            self._last_completed = completed
            self._best_rate = max(self._best_rate, rate)

            cur = gov.max_active

            if self._segment_size and (total - completed) <= self._segment_size * 2:
                return

            if self._probing:
                self._probing = False
                added = cur - self._baseline_conns
                expected = 0.0
                if self._baseline_conns > 0 and added > 0:
                    expected = (self._baseline_rate / self._baseline_conns) * added
                gained = rate - self._baseline_rate
                if expected > 0 and gained >= expected * self.policy.gain_threshold:
                    self._log(
                        "Smart optimizer: +%d connection(s) bought %.0f%% of the "
                        "expected gain — keeping %d",
                        added, 100.0 * gained / expected, cur,
                    )
                    self._emit_status(f"{cur}")
                else:
                    # The extra connection bought (almost) nothing: go back and
                    # stop trying for a while.  Undoing it also keeps the
                    # server-side connection count honest.
                    back = max(1, cur - max(1, added))
                    if back < cur:
                        gov.set_max_active(back)
                    self._last_change = now
                    self._blocked_until = now + self.policy.saturation_cooldown
                    self._log(
                        "Smart optimizer: saturated at %d connection(s) "
                        "(+%.0f%% from %d extra) — holding",
                        back,
                        100.0 * gained / self._baseline_rate if self._baseline_rate else 0.0,
                        max(1, added),
                    )
                    self._emit_status(f"{cur}->{back}")
                return

            if (
                cur > 1
                and self._best_rate > 0
                and rate < self._best_rate * self.policy.collapse_ratio
                and now - self._last_change > self.policy.settle * 2
            ):
                new = cur - 1
                if new >= 1:
                    gov.set_max_active(new)
                    self._last_change = now
                    self._blocked_until = now + self.policy.backoff_cooldown
                    self._log(
                        "Smart optimizer: throughput collapsed to %.0f%% of peak "
                        "— connections %d -> %d",
                        100.0 * rate / self._best_rate, cur, new,
                    )
                    self._emit_status(f"{cur}->{new}")
                    return

            if now < self._blocked_until:
                return
            if cur >= self.ceiling or cur >= self._segments:
                return
            self._grow(gov, cur, now, rate)

    def _grow(self, gov: ConnectionGovernor, cur: int, now: float,
              rate: float) -> None:
        """Add one connection and arm the probe that will judge it."""
        new = cur + 1
        gov.set_max_active(new)
        self._last_change = now
        self._baseline_rate = rate
        self._baseline_conns = cur
        self._probing = True
        self._log("Smart optimizer: connections %d -> %d (probing)", cur, new)
        self._emit_status(f"{cur}->{new}")

    def _emit_status(self, text: str) -> None:
        if self._on_status:
            try:
                self._on_status(text)
            except Exception:
                pass
