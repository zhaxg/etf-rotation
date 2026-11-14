"""ETF 轮动 · Web 入口 (uvicorn web.main:app)。

装配顺序: API 路由 (/api/*) → 静态前端 (挂 "/", 放在最后, 兜住其余路径)。

- web/core.py     数据 / 行情 / 回测计算 (与 HTTP 无关)
- web/api/*.py    业务路由 (instruments / strategy; 以后加 user / portfolio)
- web/static/     前端 (index.html + app.js + style.css)
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web import core  # noqa: F401  (先行导入: 初始化名称/池/缓存)
from web.api import ROUTERS

WEB = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时准备数据；网络失败不阻塞页面，首个接口请求会再次尝试补齐。"""
    try:
        await core.ensure_initial_data()
        await core.ensure_index()
    except Exception as exc:
        print(f"行情数据启动预热失败，将由接口按需重试: {type(exc).__name__}: {exc}")
    yield


app = FastAPI(title="ETF 轮动", lifespan=lifespan)

for router in ROUTERS:
    app.include_router(router, prefix="/api")

# 静态前端必须最后挂: 它匹配 "/", 会把未命中的路径都收走
app.mount("/", StaticFiles(directory=WEB / "static", html=True), name="static")
