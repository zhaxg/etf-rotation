"""为什么失效: 拆解"30 只总有上涨的, 选得对就能赚"这一命题。

三层数据验证 (月度调仓, 信号 T-1 收盘, 未来 20 个交易日收益):
  1 信号质量: 按信号选前2 → 未来收益比"全池等权"强多少? 胜率多少?
  2 赢家持续性: 今天排名靠前的, 未来还靠前吗 (Spearman 排名相关)?
  3 上帝视角: 每期事后选最强2只的收益 (证明"选得对"确实赚钱——但那是事后);
     以及事前信号与事后最强的重合率。
对照: 旧 18 只池 (动量有效) vs 新 30 只池 (失效), 同一套统计。
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

SIGNALS = ("voladj60", "mom60", "mom120", "mom250")


def load(codes: list[str], entry: dict[str, str], names: dict[str, str]) -> E.Market:
    E.DATA_DIR = W.DATA
    E.CODES = codes
    E.NAME = names
    E.ENTRY_IND = entry
    return E.load_market()


def study(mkt: E.Market, label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = mkt.close.index
    firsts = pd.Series(np.arange(len(dates)), index=dates).groupby(dates.to_period("M")).min().tolist()
    fwd20 = mkt.close.shift(-20) / mkt.close - 1
    rows, yearly_rows = [], []
    for sig in SIGNALS:
        sc = E.signal_scores(mkt, sig)
        ex, hit, persist, god, overlap, eq, top_r, god_r, chg = [], [], [], [], [], [], [], [], []
        prev_top: list[str] | None = None
        for di in firsts:
            t = di - 1
            if t < 250 or t + 20 >= len(dates):
                continue
            tdate = dates[t]
            row = sc.loc[tdate].where(mkt.elig.loc[tdate]).dropna()
            if len(row) < 5:
                continue
            ranked = row.sort_values(ascending=False)
            pool = list(ranked.index)
            top2 = pool[:2]
            best2 = list(fwd20.loc[tdate, pool].nlargest(2).index)
            f = fwd20.loc[tdate, pool]
            t_r, p_r, g_r = float(f[top2].mean()), float(f.mean()), float(f[best2].mean())
            ex.append(t_r - p_r)
            hit.append(t_r > p_r)
            god.append(g_r)
            top_r.append(t_r)
            god_r.append(g_r)
            eq.append(p_r)
            overlap.append(len(set(top2) & set(best2)) / 2)
            sr = pd.Series(np.arange(1, len(pool) + 1), index=ranked.index)
            persist.append(float(sr.corr(f.rank())))  # 两排名序列的 Pearson = Spearman
            if prev_top is not None:
                chg.append(len(set(top2) ^ set(prev_top)) / 2)  # 换掉的份数(0/0.5/1)
            prev_top = top2
        rows.append({
            "池": label, "信号": sig, "月数": len(ex),
            "top2超额/月": f"{np.mean(ex) * 100:+.2f}%",
            "top2跑赢池": f"{np.mean(hit) * 100:.0f}%",
            "排名持续性": f"{np.nanmean(persist):+.2f}",
            "上帝top2/月": f"{np.mean(god) * 100:+.2f}%",
            "重合率": f"{np.mean(overlap) * 100:.0f}%",
            "池等权/月": f"{np.mean(eq) * 100:+.2f}%",
            "月均换仓": f"{np.mean(chg) * 100:.0f}%" if chg else "—",
        })
        # 年度: 池等权 / 信号top2 / 上帝top2 (月度复利, 无成本)
        idx = [dates[d - 1] for d in firsts if 250 <= d - 1 and d + 20 < len(dates)][-len(eq):]
        for name, series in (("池等权", eq), (f"信号top2({sig})", top_r), (f"上帝top2({sig})", god_r)):
            s = pd.Series(series, index=pd.DatetimeIndex(idx))
            for y, grp in s.groupby(s.index.year):
                yearly_rows.append({"池": label, "组合": name, "年": y, "收益": f"{(1 + grp).prod() - 1:+.1%}"})
    return pd.DataFrame(rows), pd.DataFrame(yearly_rows)


def main() -> None:
    # 旧 18 只池 (engine 默认)
    old_codes = list(E.INSTRUMENTS and [r[1] for r in E.INSTRUMENTS])
    old_entry = {r[1]: r[5] for r in E.INSTRUMENTS}
    old_names = {r[1]: r[0] for r in E.INSTRUMENTS}
    mkt_old = load(old_codes, old_entry, old_names)
    r1, y1 = study(mkt_old, "旧18")

    # 新 30 只池
    new_codes = list(W.DEFAULT_CODES)
    new_entry = {c: W.default_entry(c) for c in new_codes}
    new_names = {c: W.NAMES.get(c, c) for c in new_codes}
    mkt_new = load(new_codes, new_entry, new_names)
    r2, y2 = study(mkt_new, "新30")

    print("=== 信号质量 (月度调仓, 未来20日收益, 2020→2026) ===")
    print(pd.concat([r1, r2]).to_string(index=False))

    y = pd.concat([y1, y2])
    for label in ("旧18", "新30"):
        print(f"\n=== {label}: 年度收益对比 (纯信号, 无成本) ===")
        sub = y[y["池"] == label]
        pv = sub.pivot_table(index="年", columns="组合", values="收益", aggfunc="first")
        print(pv.to_string())


if __name__ == "__main__":
    main()
