"""POST /api/strategy — 本周目标 + 信号排名 + 换仓历史 + 回测 (双策略一次返回)。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from web import core

router = APIRouter(tags=["strategy"])


class PoolItem(BaseModel):
    code: str
    entry: str | None = None


class StrategyReq(BaseModel):
    pool: list[PoolItem] = Field(default_factory=list)
    defense: bool = False      # 防守腿开关 (defense_mode="toggle" 的策略才生效)
    live: bool | None = None   # None=自适应 (交易日用实时价), True/False 可强制


@router.post("/strategy")
async def strategy(req: StrategyReq):
    pool = [p.model_dump() for p in req.pool] or [{"code": c} for c in core.POOL_CODES]
    return await core.run_strategy(pool, defense=req.defense, live=req.live)