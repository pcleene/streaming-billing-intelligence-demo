"""Rule Studio API: list/create/update rules + test against history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.deps import DBDep
from app.repositories.rule_repo import RuleRepository
from app.repositories.transaction_repo import TransactionRepository
from app.schemas.rule import RuleCreate, RuleUpdate
from app.services.rule_service import RuleService

router = APIRouter(prefix="/api/rules", tags=["rules"])


class TestRequest(BaseModel):
    rule_type: str
    parameters: dict
    sample_size: int = 1000


class ModeChange(BaseModel):
    mode: str  # shadow | active | disabled


def _service(db: DBDep) -> RuleService:
    return RuleService(RuleRepository(db), TransactionRepository(db))


@router.get("")
async def list_rules(svc: RuleService = Depends(_service)) -> dict:
    return {"items": await svc.list_rules()}


@router.get("/{rule_id}")
async def get_rule(rule_id: str, svc: RuleService = Depends(_service)) -> dict:
    return await svc.get(rule_id)


@router.post("")
async def create_rule(payload: RuleCreate, svc: RuleService = Depends(_service)) -> dict:
    return await svc.create(payload)


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: str, payload: RuleUpdate, svc: RuleService = Depends(_service)
) -> dict:
    return await svc.update(rule_id, payload)


@router.post("/{rule_id}/mode")
async def set_mode(
    rule_id: str, payload: ModeChange, svc: RuleService = Depends(_service)
) -> dict:
    return await svc.set_mode(rule_id, mode=payload.mode)


@router.post("/test")
async def test_rule(
    payload: TestRequest,
    sample_size: int | None = Query(None, ge=10, le=10_000),
    svc: RuleService = Depends(_service),
) -> dict:
    return await svc.test_against_history(
        rule_type=payload.rule_type,
        parameters=payload.parameters,
        sample_size=sample_size or payload.sample_size,
    )
