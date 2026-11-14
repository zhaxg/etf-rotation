"""两个假设的验证: ① A股行业轮动 ~1-2 个月换人; ② 池里 ETF 成交小/波动大, 不适合纳入。

输出三块:
  1 轮动周期: 月度冠军更替、top25% 连续在榜月数、"热榜身份"在 20/40/60/120/250 日后的排名相关
     (正=动量可用, ≈0=身份不持续, 负=反转)
  2 流动性/波动体检: 30 只逐只日均成交额、年化波动、按"单笔<日成交1%"的可容纳资金
  3 信号×子集: 短/中/长动量与反转信号, 在 全池 / 额大 / 低波 / 额大且低波 四个子集上的选择超额
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine as E

spec = importlib.util.spec_from_file_location("webmain", ROOT / "web" / "main.py")
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

DATA = ROOT / "data_tr"


def load(codes, entry, names):
    E.DATA_DIR = DATA
    E.CODES = list(codes)
    E.NAME = names
    E.ENTRY_IND = entry
    return E.load_market()


def month_firsts(dates) -> list[int]:
    return pd.Series(np.arange(len(dates)), index=dates).groupby(dates.to_period("M")).min().tolist()


# ───────── 1. 轮动周期 ─────────

def rotation_lens(mkt: E.Market, label: str) -> None:
    dates = mkt.close.index
    firsts = month_firsts(dates)
    m20 = mkt.mom[20]
    tops, q_sets = [], []
    for di in firsts:
        t = di - 1
        if t < 250:
            continue
        row = m20.loc[dates[t]].where(mkt.elig.loc[dates[t]]).dropna()
        if len(row) < 5:
            continue
        tops.append(row.idxmax())
        q_sets.append(set(row.nlargest(max(1, len(row) // 4)).index))

    streaks = []
    for c in mkt.close.columns:
        prev = 0
        for q in q_sets:
            if c in q:
                prev += 1
            else:
                if prev:
                    streaks.append(prev)
                prev = 0
        if prev:
            streaks.append(prev)

    print(f"\n— {label} —")
    print(f"  月度样本 {len(tops)} 个月 | 当过月度冠军(20日动量最强)的标的: {len(set(tops))} 只")
    print(f"  top25% 连续在榜: 中位 {np.median(streaks):.0f} 个月, 平均 {np.mean(streaks):.1f} 个月")

    print("  '热榜身份'存续 (今天 20 日动量排名 vs N 天后排名的相关):")
    for lag in (20, 40, 60, 120, 250):
        cors = []
        for di in firsts:
            t = di - 1
            if t < 250 or t + lag >= len(dates):
                continue
            a = m20.loc[dates[t]].where(mkt.elig.loc[dates[t]]).dropna()
            b = m20.loc[dates[t + lag]].reindex(a.index).dropna()
            idx = a.index.intersection(b.index)
            if len(idx) < 5:
                continue
            cors.append(float(a[idx].rank().corr(b[idx].rank())))
        print(f"    lag {lag:>3} 日: {np.mean(cors):+.2f}")


# ───────── 2. 流动性/波动体检 ─────────

def liq_table() -> None:
    rows = []
    for c in W.DEFAULT_CODES:
        f = pd.read_csv(DATA / f"{c}_raw.csv", parse_dates=["date"]).set_index("date")
        tr = pd.read_csv(DATA / f"{c}.csv", parse_dates=["date"]).set_index("date")["close"].astype(float)
        amt = float(f["amount"].tail(250).mean()) / 1e8
        vol = float(tr.pct_change().tail(250).std() * np.sqrt(252))
        rows.append({"代码": c, "名称": W.NAMES.get(c, c), "日均成交额(亿)": round(amt, 2),
                     "年化波动": f"{vol:.0%}", "1%成交额可容纳(万)": round(amt * 1e4 * 0.01, 0)})
    df = pd.DataFrame(rows).sort_values("日均成交额(亿)")
    print("\n=== 流动性/波动体检 (近 250 交易日, 按成交额升序) ===")
    print(df.to_string(index=False))
    n_thin = (df["日均成交额(亿)"] < 2).sum()
    n_hi = (df["年化波动"].str.rstrip("%").astype(float) > 35).sum()
    print(f"  日均成交额 <2亿: {n_thin} 只 | 年化波动 >35%: {n_hi} 只")


# ───────── 3. 信号×子集 ─────────

def subset_study(mkt: E.Market, label: str) -> None:
    dates = mkt.close.index
    firsts = month_firsts(dates)
    fwd20 = mkt.close.shift(-20) / mkt.close - 1
    ret = mkt.close.pct_change()
    liq60 = mkt.amount.rolling(60).mean()
    sigs = {
        "mom20": mkt.mom[20],
        "mom40": mkt.close / mkt.close.shift(40) - 1,
        "mom120": mkt.mom[120],
        "voladj20": mkt.mom[20] / (ret.rolling(20).std() * np.sqrt(252)),
        "voladj60": mkt.voladj,
        "-mom20反转": -mkt.mom[20],
    }
    subs = {
        "全池": None,
        "额≥5亿": liq60 >= 5e8,
        "波≤30%": mkt.vol60 <= 0.30,
        "额≥5亿&波≤30%": (liq60 >= 5e8) & (mkt.vol60 <= 0.30),
    }
    out = []
    for sname, sig in sigs.items():
        for subname, mask in subs.items():
            ex, hit, ov = [], [], []
            for di in firsts:
                t = di - 1
                if t < 250 or t + 20 >= len(dates):
                    continue
                m = mkt.elig.loc[dates[t]]
                if mask is not None:
                    m = m & mask.loc[dates[t]].fillna(False)
                row = sig.loc[dates[t]].where(m).dropna()
                if len(row) < 5:
                    continue
                ranked = row.sort_values(ascending=False)
                pool = list(ranked.index)
                top2, best2 = pool[:2], list(fwd20.loc[dates[t], pool].nlargest(2).index)
                f = fwd20.loc[dates[t], pool]
                ex.append(float(f[top2].mean() - f.mean()))
                hit.append(float(f[top2].mean()) > float(f.mean()))
                ov.append(len(set(top2) & set(best2)) / 2)
            if ex:
                out.append({"池": label, "信号": sname, "子集": subname, "月数": len(ex),
                            "top2超额/月": f"{np.mean(ex) * 100:+.2f}%",
                            "跑赢全买": f"{np.mean(hit) * 100:.0f}%",
                            "撞上最强": f"{np.mean(ov) * 100:.0f}%"})
    print(f"\n=== 信号×子集: 选择超额 (月度调仓, 未来20日, {label}) ===")
    print(pd.DataFrame(out).to_string(index=False))


def main() -> None:
    new_codes = list(W.DEFAULT_CODES)
    mkt_new = load(new_codes, {c: W.default_entry(c) for c in new_codes},
                   {c: W.NAMES.get(c, c) for c in new_codes})
    old_codes = [r[1] for r in E.INSTRUMENTS]
    mkt_old = load(old_codes, {r[1]: r[5] for r in E.INSTRUMENTS}, {r[1]: r[0] for r in E.INSTRUMENTS})

    rotation_lens(mkt_old, "旧18 池")
    rotation_lens(mkt_new, "新30 池")
    liq_table()
    subset_study(mkt_new, "新30")


if __name__ == "__main__":
    main()
