"""策略注册表: 每个策略一个文件, 文件里定义 `SPEC = StrategySpec(...)`。

新增策略 = 在本目录新建一个 xxx.py (参考 ram.py), 写上 SPEC 即可 —— 注册表自动
发现, 网页策略页签与 /api/strategy 会自动多出一个策略, 无需改 web/main.py。
展示顺序由 StrategySpec.order (小在前) 再按 id 决定。

模块文件名以 _ 开头的会被忽略 (可用于草稿/实验)。
"""
from __future__ import annotations

import importlib
import pkgutil

from .spec import StrategySpec, fresh_target  # noqa: F401  (fresh_target 对外复用)

_SKIP = {"spec"}


def load() -> dict[str, StrategySpec]:
    specs: list[StrategySpec] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name in _SKIP or info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        spec = getattr(mod, "SPEC", None)
        if isinstance(spec, StrategySpec):
            specs.append(spec)
    specs.sort(key=lambda s: (s.order, s.id))
    return {s.id: s for s in specs}


STRATEGIES: dict[str, StrategySpec] = load()