"""GET /api/instruments — 备选池目录 (现价 / 涨跌幅 / 分时 / 信号量)。"""
from __future__ import annotations

from fastapi import APIRouter

from web import core

router = APIRouter(tags=["instruments"])


@router.get("/instruments")
async def instruments():
    return await core.build_instruments()