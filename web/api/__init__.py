"""API 路由集合 (统一挂前缀 /api)。

新增业务模块 = 本目录新建文件 (定义 router = APIRouter()) + 下面 ROUTERS 加一项。
以后做用户设置 / 持仓跟踪, 就是 web/api/user.py 之类, 与计算层 (web/core.py) 分开。
"""
from __future__ import annotations

from .instruments import router as instruments
from .strategy import router as strategy

ROUTERS = [instruments, strategy]