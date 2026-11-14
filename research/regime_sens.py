"""防守腿敏感性: 对指定策略, 比较 沪深300 均线周期 none/MA20/MA60/MA120 与逐年表现。

用法: python regime_sens.py sig filt k freq buffer weight [sig2 ...]
例:   python regime_sens.py mom250 none 2 week strong invvol
"""
from __future__ import annotations

import importlib.util
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
E.DATA_DIR = ROOT / "data_tr"
E.CODES = POOL
E.NAME = {c: W.NAMES.get(c, c) for c in POOL}
E.ENTRY_IND = {c: W.default_entry(c) for c in POOL}


def regime(index: pd.DatetimeIndex, ma: int) -> pd.Series:
    f = pd.read_csv(ROOT / "data_tr" / "000300_raw.csv", parse_dates=["date"]).set_index("date")
    close = f["close"].astype(float)
    return (close > close.rolling(ma).mean()).reindex(index).ffill().fillna(False).astype(bool)


def main() -> None:
    args = sys.argv[1:]
    mkt = E.load_market()
    regs = {m: regime(mkt.close.index, m) for m in (20, 60, 120)}
    for i in range(0, len(args), 6):
        sig, filt, k, freq, buf, w = args[i:i + 6]
        k = int(k)
        print(f"\n════ {sig}|{filt}|k{k}|{freq}|{buf}|{w} ════")
        rows = []
        for label, rg in [("none", None), ("MA20", regs[20]), ("MA60", regs[60]), ("MA120", regs[120])]:
            r = E.simulate_rotation(mkt, sig=sig, filt=filt, top_k=k, freq=freq, buffer_mode=buf,
                                    weight_mode=w, no_trade_band=0.2, min_weight=0.05,
                                    regime=rg, name=f"{label}")
            m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
            mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
            mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
            rows.append({"防守腿": label, "年化": f"{m['cagr']:.1%}", "夏普": f"{m['sharpe']:.2f}",
                         "回撤": f"{m['mdd']:.1%}", "年交易": m["trades"],
                         "训练夏普": f"{mt['sharpe']:.2f}", "验证夏普": f"{mv['sharpe']:.2f}",
                         "验证回撤": f"{mv['mdd']:.1%}"})
        print(pd.DataFrame(rows).to_string(index=False))
        best_label = max([("none", None), ("MA20", regs[20]), ("MA60", regs[60]), ("MA120", regs[120])],
                         key=lambda lr: E.metrics(E.simulate_rotation(
                             mkt, sig=sig, filt=filt, top_k=k, freq=freq, buffer_mode=buf,
                             weight_mode=w, no_trade_band=0.2, min_weight=0.05,
                             regime=lr[1], name="x").equity, None, E.BACKTEST_START)["sharpe"])
        r = E.simulate_rotation(mkt, sig=sig, filt=filt, top_k=k, freq=freq, buffer_mode=buf,
                                weight_mode=w, no_trade_band=0.2, min_weight=0.05,
                                regime=(regs[60] if best_label[0] != "none" else None), name="y")
        yr = E.yearly_returns(r.equity.loc[E.BACKTEST_START:])
        print("  逐年 (MA60):", "  ".join(f"{y} {v:+.1%}" for y, v in yr.items()))


if __name__ == "__main__":
    main()
