"""策略规格 (StrategySpec) + 尾盘实时试算。

一个策略 = 一份参数声明: 信号 / 过滤 / 持仓数 / 频率 / 缓冲 / 权重 / 不交易带 /
整数仓下限 / 防守资产 / 防守腿模式。引擎 (engine.simulate_rotation) 只认参数,
不认具体策略, 所以新增策略只需新增一份 SPEC。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import engine as E

DEFENSE_MODES = ("always", "toggle")


@dataclass(frozen=True)
class StrategySpec:
    id: str                       # API / 缓存键 (high52 / ram / ...)
    label: str                    # 页面显示名 (登峰HIGH / 致远RAM)
    desc: str                     # 一句话规则
    params: dict                  # 传给 engine.simulate_rotation 的参数
    safe: str                     # 防守资产 (无候选 / 防守腿触发时停泊)
    defense_mode: str = "always"  # always=内置规则C防守腿; toggle=由请求 defense 开关
    order: int = 0                # 页面展示顺序 (小在前)

    def __post_init__(self) -> None:
        if self.defense_mode not in DEFENSE_MODES:
            raise ValueError(f"{self.id}: defense_mode 必须是 {DEFENSE_MODES}")

    @property
    def toggleable(self) -> bool:
        return self.defense_mode != "always"

    def regime_enabled(self, defense: bool) -> bool:
        """这次回测是否启用防守腿 (决定 simulate_rotation 的 regime 入参)。"""
        return self.defense_mode == "always" or bool(defense)


def fresh_target(mkt: E.Market, spec: StrategySpec, tdate,
                 *, regime_off: bool, prev: pd.Series) -> tuple[dict, list]:
    """与引擎同口径的最新信号试算 (buffer / 权重 / min_weight)。

    regime_off=True → 无视信号, 强制防守仓。返回 (目标权重 dict, 排名前 5 行)。
    """
    p = spec.params
    scores = E.signal_scores(mkt, p["sig"])
    fmask = E.filter_mask(mkt, p["filt"])
    elig = mkt.elig.loc[tdate]
    sc = scores.loc[tdate].where(elig & fmask.loc[tdate]).dropna()
    ranked = sc.sort_values(ascending=False)
    rank_rows = [(c, float(v)) for c, v in ranked.head(5).items()]
    if regime_off:
        ranked = ranked.iloc[0:0]
    tol = {"none": 0, "soft": 1, "strong": 2}[p["buffer_mode"]]
    k = p["top_k"]
    rank_pos = {c: i for i, c in enumerate(ranked.index)}
    picked: list[str] = []
    if tol > 0:
        picked = [c for c in ranked.index
                  if prev.get(c, 0) > 0 and rank_pos[c] < k + tol][:k]
    for c in ranked.index:
        if len(picked) >= k:
            break
        if c not in picked:
            picked.append(c)
    if not picked:
        picked = [spec.safe] if bool(elig.get(spec.safe, False)) else []
    target = {c: 0.0 for c in mkt.close.columns}
    if picked:
        if p["weight_mode"] == "invvol" and len(picked) > 1:
            v = mkt.vol60.loc[tdate, picked].replace(0, float("nan"))
            inv = (1.0 / v).fillna(0.0)
            w = (inv / inv.sum()) if inv.sum() > 0 else pd.Series(1.0 / len(picked), index=picked)
        else:
            w = pd.Series(1.0 / len(picked), index=picked)
        for c, x in w.items():
            target[c] = float(x)
        keep = {c: x for c, x in target.items() if x >= p["min_weight"]}
        if keep and len(keep) < len([x for x in target.values() if x > 0]):
            s = sum(keep.values())
            target = {c: (x / s if c in keep else 0.0) for c, x in target.items()}
    return target, rank_rows