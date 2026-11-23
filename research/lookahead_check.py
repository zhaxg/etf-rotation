"""未来函数因果性检查。

做法: 在割点之后篡改 / 截断行情, 重跑回测, 检查割点之前的净值、权重、
交易记录是否完全不变。若策略偷看了未来数据, 未来一改历史就会跟着变。

    python research/lookahead_check.py [割点日期]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine as E
from strategies import STRATEGIES
from web import core

CUTOFF = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else pd.Timestamp("2025-06-30")
# 留出缓冲, 避免截断边界的最后一期调仓影响对比
COMPARE_UNTIL = CUTOFF - pd.Timedelta(days=20)


def slice_market(m: E.Market, end: pd.Timestamp) -> E.Market:
    def s(df: pd.DataFrame) -> pd.DataFrame:
        return df.loc[:end]

    return E.Market(
        close=s(m.close), open=s(m.open), amount=s(m.amount), ret=s(m.ret), elig=s(m.elig),
        mom={k: s(v) for k, v in m.mom.items()}, vol60=s(m.vol60),
        ma={k: s(v) for k, v in m.ma.items()}, blend=s(m.blend), voladj=s(m.voladj),
    )


def scramble_after(m: E.Market, start: pd.Timestamp, seed: int = 0) -> E.Market:
    """把割点之后的收盘/开盘价换成随机游走, 日期与长度保持不变。"""
    rng = np.random.default_rng(seed)
    mask = m.close.index > start
    factor = pd.DataFrame(
        np.exp(rng.normal(0, 0.5, (int(mask.sum()), len(m.close.columns)))),
        index=m.close.index[mask], columns=m.close.columns,
    )
    factor.iloc[0] = 1.0  # 割点当天保持不变, 之后才开始漂移
    factor = factor.cumprod()

    def adj(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.loc[mask] = out.loc[mask] * factor
        return out

    close, opn = adj(m.close), adj(m.open)
    ret = close.pct_change()
    mom = {L: close / close.shift(L) - 1 for L in (20, 60, 120, 250)}
    vol60 = ret.rolling(60).std() * np.sqrt(252)
    ma = {M: close.rolling(M).mean() for M in (20, 60, 120)}
    ranks = [v.rank(axis=1, pct=True) for v in mom.values()]
    blend = sum(ranks) / len(ranks)
    voladj = mom[60] / vol60.replace(0, np.nan)
    return E.Market(close, opn, m.amount.copy(), ret, m.elig, mom, vol60, ma, blend, voladj)


def run(mkt: E.Market, sid: str, execution: str = "next_open"):
    spec = STRATEGIES[sid]
    E.SAFE = spec.safe
    regime = core.hs300_regime(mkt.close.index, 60)
    return E.simulate_rotation(
        mkt, regime=regime, execution=execution, **spec.params)


def compare_label(a: list, b: list) -> bool:
    lo = COMPARE_UNTIL.date().isoformat()
    return [x for x in a if x[0] <= lo] == [x for x in b if x[0] <= lo]


def main() -> None:
    E.DATA_DIR = ROOT / "data_tr"
    E.CODES = core.POOL_CODES
    E.NAME = {code: core.NAMES.get(code, code) for code in E.CODES}
    E.ENTRY_IND = {code: core.default_entry(code) for code in E.CODES}
    full = E.load_market()
    dates = full.close.index[full.close.index <= COMPARE_UNTIL]

    for sid in STRATEGIES:
        base = run(full, sid)
        cases = {
            "截断未来数据": run(slice_market(full, CUTOFF), sid),
            "篡改未来数据": run(scramble_after(full, CUTOFF), sid),
        }
        print(f"\n=== {sid} ({STRATEGIES[sid].label}) ===")
        print(f"割点 {CUTOFF.date()}, 对比区间 ≤ {COMPARE_UNTIL.date()}")
        for label, got in cases.items():
            eq_gap = float((got.equity.reindex(dates) - base.equity.reindex(dates)).abs().max())
            w_gap = float((got.weights.reindex(dates) - base.weights.reindex(dates)).abs().max().max())
            trades_ok = compare_label(got.trades_log, base.trades_log)
            flag = "PASS" if eq_gap == 0 and w_gap == 0 and trades_ok else "FAIL"
            print(f"  [{flag}] {label}: 净值偏差 {eq_gap:.1e}, 权重偏差 {w_gap:.1e}, 交易记录一致 {trades_ok}")


if __name__ == "__main__":
    main()
