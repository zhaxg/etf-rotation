"""登峰HIGH — 52 周新高接近度 + 120 日趋势过滤 + 逆波动率加权, 内置规则 C 防守腿。

回测 2020-01 至今 (TR 全收益, 单边 5bp): 年化 26.2% / 夏普 1.29 / 最大回撤 −14.6%。
见 STRATEGY.md。
"""
from __future__ import annotations

from .spec import StrategySpec

SPEC = StrategySpec(
    id="high52",
    label="登峰HIGH",
    desc="52周新高接近度 · abs120 趋势过滤 · 逆波动率加权 · 规则C: 沪深300<MA60 → 停泊红利低波",
    safe="512890",              # 红利低波ETF华泰柏瑞
    defense_mode="always",
    order=1,
    params=dict(
        sig="high52",           # 收盘价 / 250 日最高价 (52 周新高接近度)
        filt="abs120",          # 120 日动量 > 0 才买
        top_k=2,
        freq="week",
        buffer_mode="soft",     # 已持有且仍在前 3 名 → 优先留
        weight_mode="invvol",   # 1/60 日波动率归一
        no_trade_band=0.2,      # 换手 ≤20% 不动手
        min_weight=0.05,        # 权重 <5% 砍掉后重归一 (整数手可执行)
    ),
)