"""整数仓问题: 98/2 型持仓 vs 直接 100/0, 收益影响几何 (ETF 池, TR 口径, 冻结 A 信号)。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

import engine as E
from common import ETF_UNIVERSE, FROZEN, patch, mdd_detail, run_one

A = dict(FROZEN["A 稳健优选"])

VARIANTS = {
    "A 原版(98/2允许)": {**A, "min_weight": 0.0},
    "A-整数5%": {**A, "min_weight": 0.05},
    "A-整数10%": {**A, "min_weight": 0.10},
    "A-等权50/50(对照)": {**A, "weight_mode": "equal", "min_weight": 0.0},
}


def main() -> None:
    patch(ETF_UNIVERSE, {c: m for _, c, m in ETF_UNIVERSE})
    mkt = E.load_market()

    runs = {}
    for name, p in VARIANTS.items():
        runs[name] = run_one(mkt, name, p)

    print("=== 同一信号(A), 只改仓位取整 (ETF 池, 2020-01→2026-11-13) ===")
    print(f"{'变体':<18} {'年化':>7} {'波动':>7} {'夏普':>6} {'回撤':>8} {'21→今':>8} "
          f"{'10万→':>8} {'年交易':>6} {'年换手':>7}")
    for name, d in runs.items():
        f = d["full"]
        print(f"{name:<18} {f['cagr']:>7.1%} {f['vol']:>7.1%} {f['sharpe']:>6.2f} "
              f"{d['mdd']:>8.1%} {d['total_2021']:>8.1%} {10*(1+d['total_2021']):>7.2f}万 "
              f"{f['trades']:>6.1f} {f['turnover_yr']:>7.1f}x")

    print("\n=== 训练/验证 ===")
    for name, d in runs.items():
        print(f"{name:<18} 训练夏普 {d['train']['sharpe']:>5.2f}  验证年化 {d['test']['cagr']:>6.1%}  "
              f"验证夏普 {d['test']['sharpe']:>5.2f}  最差滚动一年 "
              f"{float((d['result'].equity.loc[E.BACKTEST_START:] / d['result'].equity.loc[E.BACKTEST_START:].shift(244) - 1).min()):>7.1%}")

    print("\n=== 逐年 ===")
    years = sorted({y for d in runs.values() for y in d["yearly"]})
    print(f"{'变体':<18} " + " ".join(f"{y:>7}" for y in years))
    for name, d in runs.items():
        print(f"{name:<18} " + " ".join(f"{d['yearly'].get(y, float('nan')):>7.1%}" for y in years))

    # ---------- 98/2 状态统计 (原版)
    r0 = runs["A 原版(98/2允许)"]["result"]
    w = r0.weights.loc[E.BACKTEST_START:]
    two = (w > 0).sum(axis=1)
    minw = w.where(w > 0).min(axis=1)
    print("\n=== 原版 A 的仓位形态 (2020 以来交易日) ===")
    print(f"  双仓且小仓<5% 的日子(即98/2型): {(two.eq(2) & minw.lt(0.05)).mean():.1%}")
    print(f"  双仓且小仓 5-30% 的日子(正常双仓): {(two.eq(2) & minw.ge(0.05) & minw.lt(0.30)).mean():.1%}")
    print(f"  单仓的日子: {two.eq(1).mean():.1%}")

    # 小仓(卫星)的贡献: 原版 vs 整数5% 的净值差
    e0 = r0.equity.loc[E.BACKTEST_START:]
    e1 = runs["A-整数5%"]["result"].equity.loc[E.BACKTEST_START:]
    both = pd.DataFrame({"原版": e0, "整数5%": e1}).dropna()
    spread = both["原版"] / both["整数5%"]
    print(f"\n  原版/整数5% 净值比: 期末 {spread.iloc[-1]:.4f} (即卫星仓累计{'增厚' if spread.iloc[-1]>1 else '拖累'} "
          f"{abs(spread.iloc[-1]-1)*100:.1f}%), 最低 {spread.min():.4f}, 最高 {spread.max():.4f}")

    # 当前持仓
    print("\n=== 当前持仓 (2026-11-13) ===")
    for name, d in runs.items():
        w_last = d["result"].weights.loc["2026-11-13"]
        pos = " + ".join(f"{E.NAME[c]} {v:.0%}" for c, v in w_last.items() if v > 0.001)
        print(f"  {name:<18} {pos}")


if __name__ == "__main__":
    main()
