"""Liveness + readiness + metrics snapshot."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.metrics import snapshot
from app.deps import DBDep
from app.streaming.asp_setup import asp_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(db: DBDep) -> dict:
    await db.command("ping")
    return {"status": "ready", "db": db.name}


@router.get("/metrics/snapshot")
async def metrics_snapshot() -> dict:
    return {"metrics": snapshot(), "asp": asp_status()}
