"""System metrics schema (Phase B.3).

`system_metrics` documents are written every minute by the
`metrics_recorder` worker. They power the BurstModeTile on the dashboard
and let analysts replay TPS-vs-p99 ramps after a burst run finishes.

Retention is 7 days via TTL on `recorded_at` — the demo never needs more.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SystemMode = Literal["steady", "burst", "idle"]


class SystemMetricsDoc(BaseModel):
    """One sample of platform-level health, written every minute."""
    model_config = ConfigDict(populate_by_name=True)

    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Wall-clock ts; TTL anchors here.",
    )
    mode: SystemMode = "steady"
    burst_run_id: str | None = None

    # Throughput / latency
    observed_tps: float = 0.0
    p50_ms_ingest: float = 0.0
    p99_ms_ingest: float = 0.0

    # Queue depth signals (rule eval, quarantine flow)
    quarantine_per_sec: float = 0.0
    rule_eval_p99_ms: float = 0.0

    # Volume counters (deltas over the recorder interval)
    txns_in_window: int = 0
    cases_in_window: int = 0
