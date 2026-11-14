"""RAM (30日动量/20日波动率) Top2 验证 + 规则C避险腿: 红利低波 vs 十年国债。

规则C ≈ 市场跌破趋势均线 → 切避险资产; 触发分别测 沪深300 MA20/60/120。
执行规则沿用项目标准: band 0.2, min_weight 0.05, 成本 5bp。
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
E.DATA_DIR = ROOT / "data_tr"
E.CODES = POOL
E.NAME = {c: W.NAMES.get(c, c) for c in POOL}
E.ENTRY_IND = {c: W.default_entry(c) for c in POOL}

BOND, DIV = "511260", "512890"


def reg(index, ma: int) -> pd.Series:
    f = pd.read_csv(ROOT / "data_tr" / "000300_raw.csv", parse_dates=["date"]).set_index("date")
    c = f["close"].astype(float)
    return (c > c.rolling(ma).mean()).reindex(index).ffill().fillna(False).astype(bool)


def run(mkt, *, freq, weight, buffer_mode, regime=None, safe=BOND):
    E.SAFE = safe
    return E.simulate_rotation(mkt, sig="ram30", filt="none", top_k=2, freq=freq,
                               buffer_mode=buffer_mode, weight_mode=weight,
                               no_trade_band=0.2, min_weight=0.05, regime=regime,
                               name=f"ram30|{freq}|{weight}|{buffer_mode}|{safe}")


def row(label: str, r) -> dict:
    m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
    mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
    mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
    return {"配置": label, "年化": f"{m['cagr']:.1%}", "夏普": f"{m['sharpe']:.2f}",
            "回撤": f"{m['mdd']:.1%}", "年交易": m["trades"],
            "训练夏普": f"{mt['sharpe']:.2f}", "验证夏普": f"{mv['sharpe']:.2f}",
            "验证回撤": f"{mv['mdd']:.1%}"}


def main() -> None:
    mkt = E.load_market()
    regs = {ma: reg(mkt.close.index, ma) for ma in (20, 60, 120)}
    cfgs = list(itertools.product(("week", "month"), ("equal", "invvol"), ("none", "soft")))

    print("=== 1. RAM(30日动量/20日波动率) Top2 基线 (无避险) ===")
    rows = []
    for freq, wmode, buf in cfgs:
        rows.append(row(f"{freq}|{wmode}|{buf}",
                        run(mkt, freq=freq, weight=wmode, buffer_mode=buf, regime=None, safe=BOND)))
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== 2. 规则C避险腿: 红利低波 vs 十年国债 (触发=沪深300跌破均线) ===")
    rec = []
    for ma in (20, 60, 120):
        for safe, sname in ((BOND, "国债"), (DIV, "红利低波")):
            for freq, wmode, buf in cfgs:
                r = run(mkt, freq=freq, weight=wmode, buffer_mode=buf, regime=regs[ma], safe=safe)
                m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
                mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
                mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
                rec.append({"触发": f"MA{ma}", "避险": sname, "配置": f"{freq}|{wmode}|{buf}",
                            "sharpe": m["sharpe"], "cagr": m["cagr"], "mdd": m["mdd"],
                            "train": mt["sharpe"], "test": mv["sharpe"], "test_mdd": mv["mdd"]})
    df = pd.DataFrame(rec)
    agg = (df.groupby(["触发", "避险"])
             .agg(平均夏普=("sharpe", "mean"), 平均年化=("cagr", "mean"), 平均回撤=("mdd", "mean"),
                  训练夏普=("train", "mean"), 验证夏普=("test", "mean"), 验证回撤=("test_mdd", "mean"))
             .round(3))
    print(agg.to_string())
    piv = df.pivot_table(index=["触发", "配置"], columns="避险", values="sharpe")
    piv["胜者"] = (piv["红利低波"] > piv["国债"]).map({True: "红利", False: "国债"})
    print("\n同配置下夏普对比 (红利 vs 国债) 胜负计数:")
    print(piv.groupby("触发")["胜者"].value_counts().to_string())

    print("\n=== 3. 典型配置 week|equal|none: 避险腿逐年对比 ===")
    for label, safe, rg in [("无避险(基线)", BOND, None),
                            ("MA60→国债", BOND, regs[60]),
                            ("MA60→红利", DIV, regs[60]),
                            ("MA120→国债", BOND, regs[120]),
                            ("MA120→红利", DIV, regs[120])]:
        r = run(mkt, freq="week", weight="equal", buffer_mode="none", regime=rg, safe=safe)
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
        mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
        yr = E.yearly_returns(r.equity.loc[E.BACKTEST_START:])
        print(f"  {label:14s} 年化 {m['cagr']:6.1%}  夏普 {m['sharpe']:.2f}  回撤 {m['mdd']:6.1%}  "
              f"训练 {mt['sharpe']:.2f}→验证 {mv['sharpe']:.2f} | " +
              " ".join(f"{y} {v:+.1%}" for y, v in yr.items()))

    print("\n=== 4. 避险期间行为对比 (MA60 触发时段) ===")
    idx = mkt.close.index
    on = regs[60]
    span = idx[(~on) & (idx >= pd.Timestamp(E.BACKTEST_START))]
    print(f"  MA60 触发天数: {len(span)} / {len(idx[idx >= pd.Timestamp(E.BACKTEST_START)])}")
    for safe, sname in ((BOND, "国债"), (DIV, "红利低波")):
        tr = mkt.close[safe]
        r = tr.pct_change().fillna(0.0)
        for ma in (60,):
            mask = ~regs[ma]
            seg = r[mask & (idx >= pd.Timestamp(E.BACKTEST_START))]
            print(f"  规则C触发期间 {sname}: 累计 {(1 + seg).prod() - 1:+.1%}  日均 {seg.mean() * 244:+.1%}  天数 {len(seg)}")

    print("\n=== 5. 跨池验证: 旧 18 池 (week|equal|none) ===")
    old_codes = [r[1] for r in E.INSTRUMENTS]
    E.CODES = old_codes
    E.NAME = {r[1]: r[0] for r in E.INSTRUMENTS}
    E.ENTRY_IND = {r[1]: r[5] for r in E.INSTRUMENTS}
    mkt_old = E.load_market()
    regs_old = {ma: reg(mkt_old.close.index, ma) for ma in (20, 60, 120)}
    for label, safe, rg in [("无避险(基线)", BOND, None),
                            ("MA60→国债", BOND, regs_old[60]),
                            ("MA60→红利", DIV, regs_old[60]),
                            ("MA20→国债", BOND, regs_old[20]),
                            ("MA20→红利", DIV, regs_old[20])]:
        r = run(mkt_old, freq="week", weight="equal", buffer_mode="none", regime=rg, safe=safe)
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        mt = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
        mv = E.metrics(r.equity, r.trades_log, "2024-01-01")
        yr = E.yearly_returns(r.equity.loc[E.BACKTEST_START:])
        print(f"  {label:14s} 年化 {m['cagr']:6.1%}  夏普 {m['sharpe']:.2f}  回撤 {m['mdd']:6.1%}  "
              f"训练 {mt['sharpe']:.2f}→验证 {mv['sharpe']:.2f} | " +
              " ".join(f"{y} {v:+.1%}" for y, v in yr.items()))

    # 切回新 30 池做后续对照
    E.CODES = POOL
    E.NAME = {c: W.NAMES.get(c, c) for c in POOL}
    E.ENTRY_IND = {c: W.default_entry(c) for c in POOL}

    print("\n=== 6. 对照: high52 冠军 ===")
    E.SAFE = BOND
    champ = E.simulate_rotation(mkt, sig="high52", filt="abs120", top_k=2, freq="week",
                                buffer_mode="soft", weight_mode="invvol", no_trade_band=0.2,
                                min_weight=0.05, name="champ")
    m = E.metrics(champ.equity, champ.trades_log, E.BACKTEST_START)
    print(f"  high52|abs120|k2|week|soft|invvol  年化 {m['cagr']:.1%}  夏普 {m['sharpe']:.2f}  回撤 {m['mdd']:.1%}")
    for bname, alloc in [("等权全池", {c: 1.0 for c in POOL}), ("国债持有", {BOND: 1.0}), ("红利低波持有", {DIV: 1.0})]:
        rr = E.simulate_static(mkt, alloc, name=bname)
        mm = E.metrics(rr.equity, rr.trades_log, E.BACKTEST_START)
        print(f"  {bname:14s}            年化 {mm['cagr']:.1%}  夏普 {mm['sharpe']:.2f}  回撤 {mm['mdd']:.1%}")


if __name__ == "__main__":
    main()
