"""Charge code catalog schemas (PR-1; cache + change-stream in PR-5).

Every transaction line carries a `charge_code`. The catalog is small (tens
of entries), changes infrequently, and is hot-read on every transaction
write. PR-5 introduces an in-memory cache with change-stream invalidation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RevenueCategory = Literal[
    "subscription",
    "addon",
    "ppv",
    "device",
    "fee",
    "discount",
    "tax",
    "adjustment",
    "refund",
]


class ChargeCodeApproval(BaseModel):
    created_by: str
    created_at: datetime
    approved_by: str
    approved_at: datetime


class EffectivePeriod(BaseModel):
    starts_at: datetime
    ends_at: datetime | None = None


class ChargeCodeUsage(BaseModel):
    hit_count_30d: int = 0
    hit_count_total: int = 0


class ChargeCodeTax(BaseModel):
    taxable: bool = False
    tax_code: str | None = None
    tax_rate: float = 0.0


class ChargeCodeDocument(BaseModel):
    """Persisted shape for `charge_codes`."""
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    code: str = Field(..., min_length=1, max_length=64)
    name: str
    description: str = ""
    revenue_category: RevenueCategory
    gl_account: str
    tax: ChargeCodeTax = Field(default_factory=ChargeCodeTax)
    applies_to: list[str] = Field(default_factory=list)

    approval: ChargeCodeApproval
    effective_period: EffectivePeriod
    deprecated: bool = False
    deprecated_at: datetime | None = None

    usage: ChargeCodeUsage = Field(default_factory=ChargeCodeUsage)
    runbook_url: str | None = None
