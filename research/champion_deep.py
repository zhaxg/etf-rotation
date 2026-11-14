"""冠军组合深挖: 逐年、回撤时间线、当前持仓、2026 表现、参数邻域、防守腿敏感性。"""
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


def regime(index, ma=60):
    f = pd.read_csv(ROOT / "data_tr" / "000300_raw.csv", parse_dates=["date"]).set_index("date")
    close = f["close"].astype(float)
    return (close > close.rolling(ma).mean()).reindex(index).ffill().fillna(False).astype(bool)


def run(mkt, sig, filt, k, freq, buf, w, rg=None, band=0.2):
    return E.simulate_rotation(mkt, sig=sig, filt=filt, top_k=k, freq=freq, buffer_mode=buf,
                               weight_mode=w, no_trade_band=band, min_weight=0.05, regime=rg,
                               name=f"{sig}|{filt}|k{k}|{freq}|{buf}|{w}")


def report(mkt, label, r):
    m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
    print(f"\n—— {label} ——")
    print(f"  全期: 年化 {m['cagr']:.1%}  夏普 {m['sharpe']:.2f}  回撤 {m['mdd']:.1%}  Calmar {m['calmar']:.2f}  年交易 {m['trades']}")
    print("  逐年:", "  ".join(f"{y} {v:+.1%}" for y, v in E.yearly_returns(r.equity.loc[E.BACKTEST_START:]).items()))
    eq = r.equity.loc[E.BACKTEST_START:]
    dd = eq / eq.cummax() - 1
    trough = dd.idxmin()
    peak = eq.loc[:trough].idxmax()
    print(f"  最深回撤: {peak.date()} → {trough.date()}  {dd.min():.1%}, 当前离峰值 {(eq.iloc[-1] / eq.cummax().iloc[-1] - 1):.1%}")
    hold = [h for h in r.holdings_log if h[0] >= "2025-01-01"]
    print("  近两年换仓:", "; ".join(f"{d}→{'/'.join(h)}" for d, h in hold[-6:]))
    # 当前持仓权重
    last_w = r.weights.iloc[-1]
    cur = {E.NAME[c]: f"{v:.0%}" for c, v in last_w[last_w > 0].items()}
    print(f"  当前持仓: {cur}")
    return m


def main():
    mkt = E.load_market()
    reg = regime(mkt.close.index, 60)

    champ = run(mkt, "high52", "abs120", 2, "week", "soft", "invvol")
    report(mkt, "冠军 high52|abs120|k2|week|soft|invvol (无防守腿)", champ)
    report(mkt, "同参数 + hs300_ma60 防守腿", run(mkt, "high52", "abs120", 2, "week", "soft", "invvol", reg))
    report(mkt, "次选 high52|none|k5|month|strong|invvol (低换手)", run(mkt, "high52", "none", 5, "month", "strong", "invvol"))
    report(mkt, "次选 high52|abs120|k5|month|soft|invvol", run(mkt, "high52", "abs120", 5, "month", "soft", "invvol"))
    report(mkt, "对照 mom250|none|k2|week|strong|invvol (上轮最优)", run(mkt, "mom250", "none", 2, "week", "strong", "invvol"))
    report(mkt, "对照 冻结A voladj60|abs120|k2|week|soft|invvol", run(mkt, "voladj60", "abs120", 2, "week", "soft", "invvol"))

    print("\n—— 冠军参数邻域 (robustness) ——")
    rows = []
    for sig in ("high52",):
        for filt in ("none", "abs60", "abs120"):
            for k in (2, 3, 5):
                for freq in ("month", "week"):
                    for buf in ("soft", "strong"):
                        for w in ("equal", "invvol"):
                            r = run(mkt, sig, filt, k, freq, buf, w)
                            m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
                            mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
                            mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
                            rows.append({"变体": f"{sig}|{filt}|k{k}|{freq}|{buf}|{w}",
                                         "年化": f"{m['cagr']:.1%}", "夏普": f"{m['sharpe']:.2f}",
                                         "回撤": f"{m['mdd']:.1%}", "年交易": m["trades"],
                                         "训练夏普": f"{mt['sharpe']:.2f}", "验证夏普": f"{mv['sharpe']:.2f}"})
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print(f"\n邻域 {len(df)} 变体: 夏普中位 {df['夏普'].astype(float).median():.2f}, "
          f"最差 {df['夏普'].astype(float).min():.2f}, 训练→验证都>0.5 的比例 "
          f"{((df['训练夏普'].astype(float) > 0.5) & (df['验证夏普'].astype(float) > 0.5)).mean():.0%}")

    print("\n—— 防守腿 MA 周期敏感性 (冠军) ——")
    rows = []
    for label, rg in [("none", None), ("MA20", regime(mkt.close.index, 20)),
                      ("MA60", reg), ("MA120", regime(mkt.close.index, 120))]:
        r = run(mkt, "high52", "abs120", 2, "week", "soft", "invvol", rg)
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        rows.append({"防守腿": label, "年化": f"{m['cagr']:.1%}", "夏普": f"{m['sharpe']:.2f}",
                     "回撤": f"{m['mdd']:.1%}"})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n—— 对照组 ——")
    for name, alloc in [("等权全池", {c: 1.0 for c in POOL}),
                        ("国债 511260", {"511260": 1.0}),
                        ("黄金 518880", {"518880": 1.0}),
                        ("纳指 513100", {"513100": 1.0})]:
        r = E.simulate_static(mkt, alloc, name=name)
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        print(f"  {name:12s} 年化 {m['cagr']:6.1%}  夏普 {m['sharpe']:.2f}  回撤 {m['mdd']:.1%}")


if __name__ == "__main__":
    main()
