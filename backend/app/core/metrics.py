"""In-memory metric counters powering the dashboard live tiles.

Trade-off: in-memory means single-process counts; for the demo this is fine
and avoids dragging in Prometheus/StatsD. A `MetricsService` reads these and
publishes via SSE.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RollingWindow:
    """Tracks events over a sliding time window for rate + percentile calc."""
    window_seconds: float = 60.0
    samples: deque[tuple[float, float]] = field(default_factory=deque)  # (ts, latency_ms)
    _lock: Lock = field(default_factory=Lock)

    def record(self, latency_ms: float | None = None) -> None:
        now = time.time()
        with self._lock:
            self.samples.append((now, latency_ms if latency_ms is not None else 0.0))
            self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def rate_per_sec(self) -> float:
        now = time.time()
        with self._lock:
            self._evict(now)
            if not self.samples:
                return 0.0
            return len(self.samples) / self.window_seconds

    def percentile_ms(self, p: float = 0.99) -> float:
        with self._lock:
            self._evict(time.time())
            if not self.samples:
                return 0.0
            ordered = sorted(s[1] for s in self.samples)
            idx = max(0, min(len(ordered) - 1, int(len(ordered) * p) - 1))
            return ordered[idx]


# --- Module-level singletons ------------------------------------------
transactions_window: RollingWindow = RollingWindow(window_seconds=60.0)
quarantine_window: RollingWindow   = RollingWindow(window_seconds=60.0)
rule_eval_window: RollingWindow    = RollingWindow(window_seconds=60.0)


def snapshot() -> dict:
    """Snapshot of all live metrics for the dashboard."""
    return {
        "transactions_per_sec": round(transactions_window.rate_per_sec(), 2),
        "quarantine_per_sec":   round(quarantine_window.rate_per_sec(), 2),
        "p99_eval_ms":          round(rule_eval_window.percentile_ms(0.99), 1),
        "p50_eval_ms":          round(rule_eval_window.percentile_ms(0.50), 1),
        "ts": time.time(),
    }
