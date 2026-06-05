"""System metrics schema re-export (PR-1).

The Phase B.3 `SystemMetricsDoc` already lives in `system_metrics.py` and is
unchanged. This module exists to satisfy the master plan's `metrics.py`
naming and to give services a single import path for the SystemMetric type.
"""

from __future__ import annotations

from .system_metrics import SystemMetricsDoc as SystemMetric
from .system_metrics import SystemMode

__all__ = ["SystemMetric", "SystemMode"]
