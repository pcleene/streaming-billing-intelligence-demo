"""Acme Malaysia subscription packages used by the seed.

Mirrors the public Acme lineup at a high level (price points are
illustrative, not authoritative). Used to give the demo a
recognisable, market-correct feel.
"""

from __future__ import annotations

from typing import Final

# (package_id, name, monthly_price_myr, segment_hint)
PACKAGES: Final[list[tuple[str, str, float, str]]] = [
    ("pkg_basic",        "Acme Family Pack",        59.99,  "value"),
    ("pkg_movies",       "Acme Movies Pack",        99.99,  "standard"),
    ("pkg_sports",       "Acme Sports Pack",       129.99,  "standard"),
    ("pkg_premium",      "Acme Premium Pack",      189.99,  "premium"),
    ("pkg_ultimate",     "Acme Ultimate",          249.99,  "premium"),
    ("pkg_go",           "Acme GO Streaming",       29.90,  "value"),
    ("pkg_prepaid_plus",    "PREPAID Plus",                49.90,  "value"),
    ("pkg_box_office",   "Acme Box Office (PPV)",    0.00,  "ppv"),
]


def package_by_id(pid: str) -> tuple[str, str, float, str] | None:
    for row in PACKAGES:
        if row[0] == pid:
            return row
    return None
