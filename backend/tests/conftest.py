"""Shared pytest fixtures.

These tests run without a live Mongo or MSK by default; the integration
tests (under tests/integration/) opt in via a marker if/when needed.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _quiet_logging() -> None:
    import logging

    logging.getLogger().setLevel(logging.WARNING)
