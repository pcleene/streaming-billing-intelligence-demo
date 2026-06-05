"""Operational helpers for Atlas Stream Processing processors.

The actual processor definitions live as JS scripts under `infra/asp/`
(deploy_all.js / stop_all.js) so they can be run via `mongosh` against the
ASP workspace — that's the canonical path. This module just exposes a
status struct the API can render in the dashboard.

We don't query the ASP control-plane here (the Atlas Admin API is owned by
the make targets); we report the *expected* processor list + last-known
heartbeat from the metrics rolling window.
"""

from __future__ import annotations

from typing import Any

from app.core.constants import ASP_PROCESSORS
from app.core.metrics import transactions_window


def asp_status() -> dict[str, Any]:
    return {
        "expected_processors": list(ASP_PROCESSORS),
        "kafka_topic": "acme-billing-events",
        "kafka_connection": "UtilitymskKafkaConnection",
        "atlas_connection": "FuelRetail_cluster",
        "throughput_tps": round(transactions_window.rate_per_sec(), 2),
    }
