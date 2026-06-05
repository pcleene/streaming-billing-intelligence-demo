"""Shared Pydantic primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Disposition(str, Enum):
    LEGITIMATE = "legitimate"
    DATA_ERROR = "data_error"
    FRAUD = "fraud"


class RuleMode(str, Enum):
    ACTIVE = "active"
    SHADOW = "shadow"
    DISABLED = "disabled"


class TimestampedModel(BaseModel):
    """Convention: created_at / updated_at on persistable docs."""
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Generic paginated response."""
    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool


# --- GeoJSON --------------------------------------------------------------

class GeoJSONPoint(BaseModel):
    """A GeoJSON Point. `coordinates` is `[longitude, latitude]` per the spec.

    Used for `customers_*.address.location` (2dsphere index) and for
    `transactions.location.point`.
    """
    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(..., min_length=2, max_length=2)


# --- Schema versioning helper --------------------------------------------

class VersionedDocument(BaseModel):
    """Mixin for any persisted doc that should carry `_schema_version`.

    Marshallers should set this from `app.core.constants.SCHEMA_VERSION_V3`
    when writing the new shape; existing legacy docs don't carry it.
    """
    schema_version: int = Field(default=3, alias="_schema_version")
    model_config = ConfigDict(populate_by_name=True)
