"""新池(31 只) 策略重跑: 全网格(2700 组合) + 训练/验证 + 对照组。

跑两组: 冻结机制(与 A 稳健优选同族) 与 vol下限8%(信号与权重都取 max(vol60,8%),
防短融类准现金劫持)。每组打印: 全期夏普 Top8、训练期优选→验证期表现、冻结 A 排名、对照组。
结果 CSV 落在 results/grid_newpool_{frozen,floor8}.csv。
"""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

import engine as E

spec = importlib.util.spec_from_file_location("webmain", ROOT / "web" / "main.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

POOL = list(W.DEFAULT_CODES)
FROZEN_A_NAME = "voladj60|abs120|k2|week|soft|invvol|band0.2"


def patch() -> E.Market:
    E.DATA_DIR = W.DATA
    E.CODES = POOL
    E.NAME = {c: W.NAMES.get(c, c) for c in POOL}
    E.ENTRY_IND = {c: W.default_entry(c) for c in POOL}
    return E.load_market()


def apply_floor(mkt: E.Market, f: float) -> E.Market:
    v = mkt.vol60.clip(lower=f)
    return dataclasses.replace(mkt, vol60=v, voladj=mkt.mom[60] / v)


def run_grid(mkt: E.Market, label: str) -> pd.DataFrame:
    rows = []
    for sig in E.SIGNALS:
        for filt in E.FILTERS:
            for top_k in (1, 2, 3):
                for freq in ("quarter", "month", "week"):
                    for buf in ("none", "soft", "strong"):
                        for wmode in ("equal", "invvol"):
                            if wmode == "invvol" and top_k == 1:
                                continue
                            for band in (0.0, 0.2):
                                name = f"{sig}|{filt}|k{top_k}|{freq}|{buf}|{wmode}|band{band:g}"
                                r = E.simulate_rotation(mkt, sig=sig, filt=filt, top_k=top_k,
                                                        freq=freq, buffer_mode=buf, weight_mode=wmode,
                                                        no_trade_band=band, min_weight=0.05, name=name)
                                mf = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
                                mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
                                mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
                                if not mf:
                                    continue
                                rows.append({"name": name, "sig": sig, "filt": filt, "top_k": top_k,
                                             "freq": freq, "buffer": buf, "weight": wmode, "band": band,
                                             **{f"full_{k}": v for k, v in mf.items()},
                                             **{f"train_{k}": v for k, v in mt.items()},
                                             **{f"test_{k}": v for k, v in mv.items()}})
    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "results" / f"grid_newpool_{label}.csv", index=False)
    return df


def report(df: pd.DataFrame) -> None:
    cols_full = ["name", "full_cagr", "full_sharpe", "full_mdd", "full_trades", "test_cagr", "test_sharpe"]
    print("—— 全期夏普 Top8 ——")
    print(df.sort_values("full_sharpe", ascending=False)[cols_full].head(8).to_string(index=False))
    print("\n—— 训练期(2020-2023)夏普 Top8, 看 2024+ 验证 ——")
    cols_tr = ["name", "train_sharpe", "test_cagr", "test_sharpe", "test_mdd", "full_cagr", "full_sharpe"]
    print(df.sort_values("train_sharpe", ascending=False)[cols_tr].head(8).to_string(index=False))
    hit = df[df["name"] == FROZEN_A_NAME]
    if len(hit):
        rank_full = int(df["full_sharpe"].rank(ascending=False)[hit.index[0]])
        rank_train = int(df["train_sharpe"].rank(ascending=False)[hit.index[0]])
        row = hit.iloc[0]
        print(f"\n—— 冻结 A ({FROZEN_A_NAME}) ——")
        print(f"  全期 {row['full_cagr']:.1%}/{row['full_sharpe']:.2f}/{row['full_mdd']:.1%}"
              f"  训练夏普 {row['train_sharpe']:.2f}  验证 {row['test_cagr']:.1%}/{row['test_sharpe']:.2f}"
              f"  排名 全期#{rank_full}/训练#{rank_train} (共 {len(df)})")


def benches(mkt: E.Market) -> None:
    defs = {"等权全池(月度)": {c: 1.0 for c in POOL},
            "短融 511360 持有": {"511360": 1.0},
            "国债 511260 持有": {"511260": 1.0},
            "黄金 518880 持有": {"518880": 1.0},
            "纳指 513100 持有": {"513100": 1.0}}
    print("\n—— 对照组 ——")
    for n, alloc in defs.items():
        r = E.simulate_static(mkt, alloc, name=n)
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        print(f"  {n:<16} 年化 {m['cagr']:.1%}  夏普 {m['sharpe']:.2f}  回撤 {m['mdd']:.1%}")


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for label, floor in (("frozen", None), ("floor8", 0.08)):
        if only and only != label:
            continue
        mkt = patch()
        if floor:
            mkt = apply_floor(mkt, floor)
        print(f"\n======== 网格: {label} ({'冻结机制' if floor is None else f'vol下限{floor:.0%}'}) ========")
        df = run_grid(mkt, label)
        report(df)
        benches(mkt)


if __name__ == "__main__":
    main()
