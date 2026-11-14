"""最优策略重找: 新 30 池 × 全参数 × [沪深300 跌破60日均线 → 切防御腿]。

用法:
  python best_search.py <chunk> <n>   分片计算 (开多个进程并行)
  python best_search.py merge         汇总报告
搜索维度: 信号(8) × 过滤(3) × 持仓数(3) × 频率(3) × 缓冲(2) × 权重(2) × 防守腿(2)
固定: band=0.2, min_weight=0.05, 成本5bp, 2020-01→2026-11-13, TR 口径。
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine as E

spec = importlib.util.spec_from_file_location("webmain", ROOT / "web" / "main.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

POOL = list(W.DEFAULT_CODES)
OUT = ROOT / "results" / "best_search"
OUT.mkdir(parents=True, exist_ok=True)

SIGNALS = ("mom20", "mom40", "mom60", "mom120", "mom250", "voladj20", "voladj60", "high52")
FILTERS = ("none", "abs60", "abs120")
KS = (2, 3, 5)
FREQS = ("quarter", "month", "week")
BUFFERS = ("soft", "strong")
WEIGHTS = ("equal", "invvol")
REGIMES = ("none", "hs300_ma60")


def hs300_regime(index: pd.DatetimeIndex, ma: int = 60) -> pd.Series:
    f = pd.read_csv(ROOT / "data_tr" / "000300_raw.csv", parse_dates=["date"]).set_index("date")
    close = f["close"].astype(float)
    return (close > close.rolling(ma).mean()).reindex(index).ffill().fillna(False).astype(bool)


def patch() -> None:
    E.DATA_DIR = ROOT / "data_tr"
    E.CODES = POOL
    E.NAME = {c: W.NAMES.get(c, c) for c in POOL}
    E.ENTRY_IND = {c: W.default_entry(c) for c in POOL}


def all_combos():
    return list(itertools.product(SIGNALS, FILTERS, KS, FREQS, BUFFERS, WEIGHTS, REGIMES))


def run_chunk(cid: int, n: int) -> None:
    patch()
    mkt = E.load_market()
    reg = hs300_regime(mkt.close.index, 60)
    rows = []
    for i, (sig, filt, k, freq, buf, w, rg) in enumerate(all_combos()):
        if i % n != cid:
            continue
        name = f"{sig}|{filt}|k{k}|{freq}|{buf}|{w}|{rg}"
        r = E.simulate_rotation(mkt, sig=sig, filt=filt, top_k=k, freq=freq, buffer_mode=buf,
                                weight_mode=w, no_trade_band=0.2, min_weight=0.05,
                                regime=(reg if rg != "none" else None), name=name)
        mf = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
        mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
        if not mf:
            continue
        rows.append({"name": name, "sig": sig, "filt": filt, "k": k, "freq": freq, "buffer": buf,
                     "weight": w, "regime": rg,
                     **{f"full_{x}": y for x, y in mf.items()},
                     **{f"train_{x}": y for x, y in mt.items()},
                     **{f"test_{x}": y for x, y in mv.items()}})
        if len(rows) % 60 == 0:
            pd.DataFrame(rows).to_csv(OUT / f"chunk_{cid}.csv", index=False)
            print(f"chunk {cid}: {len(rows)} done", flush=True)
    pd.DataFrame(rows).to_csv(OUT / f"chunk_{cid}.csv", index=False)
    print(f"chunk {cid}: finished {len(rows)}", flush=True)


def merge() -> None:
    files = sorted(OUT.glob("chunk_*.csv"))
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df.to_csv(OUT / "all.csv", index=False)
    df["robust"] = df[["train_sharpe", "test_sharpe"]].min(axis=1)  # 防过拟合: 训练/验证的短板
    print(f"合并 {len(df)} 个组合 (来自 {len(files)} 片)")
    cols = ["name", "full_cagr", "full_sharpe", "full_mdd", "full_trades",
            "train_sharpe", "test_cagr", "test_sharpe", "test_mdd"]

    print("\n=== 稳健之王: min(训练,验证)夏普 Top10 (防偶然排序) ===")
    print(df.sort_values("robust", ascending=False)[cols + ["robust"]].head(10).to_string(index=False))
    print("\n=== 全期夏普 Top10 ===")
    print(df.sort_values("full_sharpe", ascending=False)[cols].head(10).to_string(index=False))
    print("\n=== 回撤 ≤30% 约束下 全期夏普 Top10 ===")
    print(df[df.full_mdd > -0.30].sort_values("full_sharpe", ascending=False)[cols].head(10).to_string(index=False))
    print("\n=== 回撤 ≤25% 约束下 全期夏普 Top10 ===")
    print(df[df.full_mdd > -0.25].sort_values("full_sharpe", ascending=False)[cols].head(10).to_string(index=False))
    print("\n=== 低换手 (年交易≤15) 全期夏普 Top8 ===")
    print(df[df.full_trades <= 15].sort_values("full_sharpe", ascending=False)[cols].head(8).to_string(index=False))

    print("\n=== 防守腿效果: 同参数 有/无 hs300_ma60 (夏普提升 Top8) ===")
    key = ["sig", "filt", "k", "freq", "buffer", "weight"]
    on = df[df.regime == "hs300_ma60"].set_index(key)
    off = df[df.regime == "none"].set_index(key)
    j = on.join(off, lsuffix="_on", rsuffix="_off")
    j["d_sharpe"] = j["full_sharpe_on"] - j["full_sharpe_off"]
    show = ["full_cagr_on", "full_sharpe_on", "full_mdd_on", "full_cagr_off",
            "full_sharpe_off", "full_mdd_off", "d_sharpe"]
    print(j.sort_values("d_sharpe", ascending=False)[show].head(8).to_string())
    print("\n防守腿平均效果: 夏普 %+.2f, 回撤 %+.1f%%, 年化 %+.1f%%" % (
        j["d_sharpe"].mean(), (j["full_mdd_on"] - j["full_mdd_off"]).mean() * 100,
        (j["full_cagr_on"] - j["full_cagr_off"]).mean() * 100))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "merge":
        merge()
    else:
        run_chunk(int(sys.argv[1]), int(sys.argv[2]))
