"""全参数网格 (2700 组合) — 检验冻结参数在标的池上的排名 (TR 数据)。

用法: python pool_grid.py etf
输出: results/grid_{pool}.csv + top15
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

import engine as E
from common import ETF_UNIVERSE, patch, natural_entries


def main(pool: str) -> None:
    universe = ETF_UNIVERSE
    patch(universe, {c: m for _, c, m in universe})
    mkt = E.load_market()
    out = ROOT / "results"
    out.mkdir(exist_ok=True)

    rows, curves = [], {}
    for sig in E.SIGNALS:
        for filt in E.FILTERS:
            for top_k in (1, 2, 3):
                for freq in ("quarter", "month", "week"):
                    for bufmode in ("none", "soft", "strong"):
                        for wmode in ("equal", "invvol"):
                            if wmode == "invvol" and top_k == 1:
                                continue
                            for band in (0.0, 0.2):
                                name = f"{sig}|{filt}|k{top_k}|{freq}|{bufmode}|{wmode}|band{band:g}"
                                r = E.simulate_rotation(mkt, sig=sig, filt=filt, top_k=top_k,
                                                        freq=freq, buffer_mode=bufmode,
                                                        weight_mode=wmode, no_trade_band=band,
                                                        name=name)
                                m_full = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
                                m_train = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
                                m_test = E.metrics(r.equity, r.trades_log, "2024-01-01")
                                if not m_full:
                                    continue
                                rows.append({
                                    "name": name, "sig": sig, "filt": filt, "top_k": top_k,
                                    "freq": freq, "buffer": bufmode, "weight": wmode, "band": band,
                                    **{f"full_{k}": v for k, v in m_full.items()},
                                    **{f"train_{k}": v for k, v in m_train.items()},
                                    **{f"test_{k}": v for k, v in m_test.items()},
                                })
                                curves[name] = r.equity
        print(f"[{pool}] 完成 sig={sig} ({len(rows)} 组合)", flush=True)

    grid = pd.DataFrame(rows)
    grid.to_csv(out / f"grid_{pool}.csv", index=False)
    pd.DataFrame(curves).to_csv(out / f"equity_{pool}.csv")

    frozen_names = ["voladj60|abs120|k2|week|soft|invvol|band0.2",
                    "mom20|none|k1|month|strong|equal|band0",
                    "mom250|ma120|k1|quarter|soft|equal|band0",
                    "voladj60|ma60|k3|month|strong|invvol|band0.2"]
    print(f"\n=== [{pool}] 冻结参数在 {len(grid)} 组合中的排名 ===")
    for nm in frozen_names:
        row = grid[grid["name"] == nm]
        if not len(row):
            print(f"  {nm}: 未找到"); continue
        r = row.iloc[0]
        rank_full = int((grid["full_sharpe"] > r["full_sharpe"]).sum()) + 1
        rank_train = int((grid["train_sharpe"] > r["train_sharpe"]).sum()) + 1
        rank_test = int((grid["test_sharpe"] > r["test_sharpe"]).sum()) + 1
        print(f"  {nm}: 全期夏普 {r['full_sharpe']:.2f} (第{rank_full}名) | "
              f"训练期 {r['train_sharpe']:.2f} (第{rank_train}名) | "
              f"验证期 {r['test_sharpe']:.2f} (第{rank_test}名)")
    print(f"\n=== [{pool}] 全期夏普 Top8 ===")
    cols = ["name", "full_cagr", "full_sharpe", "full_mdd", "train_sharpe", "test_sharpe"]
    print(grid.sort_values("full_sharpe", ascending=False)[cols].head(8).to_string(index=False))
    print(f"\n=== [{pool}] 训练期夏普 Top8 及其验证期表现 ===")
    cols2 = ["name", "train_sharpe", "test_cagr", "test_sharpe", "full_cagr", "full_sharpe", "full_mdd"]
    print(grid.sort_values("train_sharpe", ascending=False)[cols2].head(8).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "etf")
