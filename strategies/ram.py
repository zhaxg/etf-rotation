"""致远RAM — 30 日动量 / 20 日波动率 Top2 + 等权, 内置规则 C 防守腿。

回测 2020-01 至今 (TR 全收益, 单边 5bp): 年化 33.8% / 夏普 1.32 / 最大回撤 −14.4%。
换手比登峰HIGH 更勤, 对"拖到周一才成交"更敏感 (拖一天年化 −9.3pct)。见 STRATEGY.md。
"""
from __future__ import annotations

from .spec import StrategySpec

SPEC = StrategySpec(
    id="ram",
    label="致远RAM",
    desc="30日动量/20日波动率 Top2 · 规则C: 沪深300<MA60 → 停泊红利低波",
    safe="512890",              # 红利低波ETF华泰柏瑞
    defense_mode="always",
    order=2,
    params=dict(
        sig="ram30",            # 30 日动量 / 20 日波动率
        filt="none",            # 不看趋势过滤 (RAM 本身已含波动率归一)
        top_k=2,
        freq="week",
        buffer_mode="none",     # 不设缓冲: 排名前 2 就换
        weight_mode="equal",
        no_trade_band=0.2,
        min_weight=0.05,
    ),
)