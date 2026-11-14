"""新池诊断: A 稳健优选 在 31 只新池上为何退化为"短融90%"?

对比变体 (冻结 A 的其余设定: abs120 | k2 | week | soft | band0.2 | min_weight0.05):
  1 冻结原样        2 去短融        3 信号+权重 vol下限
  4 仅信号 vol下限   5 等权          6 去短融+vol下限
vol 下限 = 波动率调整动量的分母与逆波动率权重都取 max(vol60, F), 压制准现金标的。
"""
from __future__ import annotations

import dataclasses
import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

import engine as E

spec = importlib.util.spec_from_file_location("webmain", ROOT / "web" / "main.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

FLOOR = 0.08
BASE = dict(sig="voladj60", filt="abs120", top_k=2, freq="week",
            buffer_mode="soft", no_trade_band=0.2, min_weight=0.05)


def patch(codes: list[str]) -> None:
    E.DATA_DIR = W.DATA
    E.CODES = codes
    E.NAME = {c: W.NAMES.get(c, c) for c in codes}
    E.ENTRY_IND = {c: W.default_entry(c) for c in codes}


def apply_floor(mkt: E.Market, f: float, *, sig: bool = True, wt: bool = True) -> E.Market:
    """vol下限: 信号分母与/或权重用 max(vol60, f)。"""
    v_sig = mkt.vol60.clip(lower=f) if sig else mkt.vol60
    v_wt = mkt.vol60.clip(lower=f) if wt else mkt.vol60
    return dataclasses.replace(mkt, vol60=v_wt, voladj=mkt.mom[60] / v_sig)


def one(name: str, codes: list[str], *, floor: float | None = None,
        floor_sig: bool = True, floor_wt: bool = True,
        weight: str = "invvol") -> dict:
    patch(codes)
    mkt = E.load_market()
    if floor:
        mkt = apply_floor(mkt, floor, sig=floor_sig, wt=floor_wt)
    r = E.simulate_rotation(mkt, name=name, weight_mode=weight, **BASE)
    m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
    w = r.weights.loc[E.BACKTEST_START:]
    cash = float((w.get("511360", 0) > 0).mean()) if "511360" in w.columns else float("nan")
    bond = float((w.get("511260", 0) > 0).mean()) if "511260" in w.columns else float("nan")
    return {"name": name, "m": m, "cash_days": cash, "bond_days": bond,
            "yearly": E.yearly_returns(r.equity.loc[E.BACKTEST_START:])}


def show(rows: list[dict]) -> None:
    print(f"{'变体':<22} {'年化':>7} {'夏普':>6} {'回撤':>8} {'年交易':>6} "
          f"{'短融在手%':>8} {'国债在手%':>8}")
    for d in rows:
        m = d["m"]
        print(f"{d['name']:<22} {m['cagr']:>7.1%} {m['sharpe']:>6.2f} {m['mdd']:>8.1%} "
              f"{m['trades']:>6.1f} {d['cash_days']:>8.0%} {d['bond_days']:>8.0%}")
    print()
    years = sorted({y for d in rows for y in d["yearly"]})
    print(f"{'变体':<22} " + " ".join(f"{y:>7}" for y in years))
    for d in rows:
        print(f"{d['name']:<22} " +
              " ".join(f"{d['yearly'].get(y, float('nan')):>7.1%}" for y in years))


def score_probe(codes: list[str], dates: list[str]) -> None:
    patch(codes)
    mkt = E.load_market()
    print("=== 信号探针: voladj60 = mom60/vol60 (原始, 无下限) ===")
    for d in dates:
        t = pd.Timestamp(d)
        sc = mkt.voladj.loc[t].dropna().sort_values(ascending=False)
        print(f"\n{d} 前 6 名:")
        for c in sc.index[:6]:
            print(f"  {W.NAMES.get(c, c):<8} {c}  score {sc[c]:>7.2f}  "
                  f"mom60 {mkt.mom[60].loc[t, c]:>7.1%}  vol60 {mkt.vol60.loc[t, c]:>6.1%}")


def main() -> None:
    pool = W.DEFAULT_CODES
    no_cash = [c for c in pool if c != "511360"]
    score_probe(pool, ["2025-06-30", "2026-11-13"])
    print()
    rows = [
        one("1 冻结原样(新池)", pool),
        one("2 去短融", no_cash),
        one(f"3 信号+权重 vol下限{FLOOR:.0%}", pool, floor=FLOOR),
        one(f"4 仅信号 vol下限{FLOOR:.0%}", pool, floor=FLOOR, floor_wt=False),
        one("5 等权(新池)", pool, weight="equal"),
        one(f"6 去短融+vol下限{FLOOR:.0%}", no_cash, floor=FLOOR),
    ]
    show(rows)


if __name__ == "__main__":
    main()
