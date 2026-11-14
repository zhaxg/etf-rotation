"""ETF 轮动回测引擎。

数据: data/{code}.csv (pytdxdata 前复权日线)。
约定: 信号用 T 日收盘数据, T 日收盘成交, 收益从 T+1 日起计入;
     成本 = 单边换手 * cost_rate (默认 5bp/边, 买卖都计)。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from universe import LEGACY_INSTRUMENTS as INSTRUMENTS

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

COST_RATE = 0.0005  # 单边
RF = 0.02           # 无风险利率 (用于夏普)
BACKTEST_START = "2020-01-01"
TRAIN_END = "2023-12-31"

NAME = {row[1]: row[0] for row in INSTRUMENTS}
CODES = [row[1] for row in INSTRUMENTS]
ENTRY_IND = {row[1]: row[5] for row in INSTRUMENTS}  # 指标可用月份
SAFE = "511260"  # 十年国债ETF, 防守仓


@dataclass
class Market:
    close: pd.DataFrame
    amount: pd.DataFrame
    ret: pd.DataFrame
    elig: pd.DataFrame
    mom: dict[int, pd.DataFrame]
    vol60: pd.DataFrame
    ma: dict[int, pd.DataFrame]
    blend: pd.DataFrame
    voladj: pd.DataFrame


def load_market() -> Market:
    close_frames, amount_frames = [], []
    for code in CODES:
        f = pd.read_csv(DATA_DIR / f"{code}.csv", parse_dates=["date"]).set_index("date").sort_index()
        close_frames.append(f["close"].rename(code))
        amount_frames.append(f["amount"].rename(code))
    close = pd.concat(close_frames, axis=1).sort_index()
    amount = pd.concat(amount_frames, axis=1).sort_index()
    ret = close.pct_change()

    # 入池: 不早于"指标可用"当月首个交易日
    elig = pd.DataFrame(False, index=close.index, columns=close.columns)
    for code in CODES:
        month = ENTRY_IND[code]
        start = close.index[close.index >= pd.Timestamp(month + "-01")]
        if len(start):
            elig.loc[start[0]:, code] = True

    mom = {L: close / close.shift(L) - 1 for L in (20, 60, 120, 250)}
    vol60 = ret.rolling(60).std() * math.sqrt(252)
    ma = {M: close.rolling(M).mean() for M in (20, 60, 120)}

    # 多周期混合动量: 20/60/120/250 日收益的横截面平均排名
    ranks = [m.rank(axis=1, pct=True) for m in mom.values()]
    blend = sum(ranks) / len(ranks)

    voladj = mom[60] / vol60.replace(0, np.nan)
    return Market(close, amount, ret, elig, mom, vol60, ma, blend, voladj)


# ---------------------------------------------------------------- 信号定义

SIGNALS = ("mom20", "mom60", "mom120", "mom250", "blend", "voladj60")
FILTERS = ("none", "abs60", "abs120", "ma60", "ma120")


def signal_scores(mkt: Market, sig: str) -> pd.DataFrame:
    if sig == "mom40":
        return mkt.close / mkt.close.shift(40) - 1
    if sig == "voladj20":
        return mkt.mom[20] / (mkt.ret.rolling(20).std() * math.sqrt(252))
    if sig == "ram30":  # RAM: 30日动量 / 20日波动率
        mom30 = mkt.close / mkt.close.shift(30) - 1
        return mom30 / (mkt.ret.rolling(20).std() * math.sqrt(252))
    if sig == "high52":  # 接近年内新高 = 趋势强
        return mkt.close / mkt.close.rolling(250, min_periods=60).max()
    return {
        "mom20": mkt.mom[20], "mom60": mkt.mom[60], "mom120": mkt.mom[120],
        "mom250": mkt.mom[250], "blend": mkt.blend, "voladj60": mkt.voladj,
    }[sig]


def filter_mask(mkt: Market, filt: str) -> pd.DataFrame:
    if filt == "none":
        return pd.DataFrame(True, index=mkt.close.index, columns=mkt.close.columns)
    if filt == "abs60":
        return mkt.mom[60] > 0
    if filt == "abs120":
        return mkt.mom[120] > 0
    if filt == "ma60":
        return mkt.close > mkt.ma[60]
    if filt == "ma120":
        return mkt.close > mkt.ma[120]
    raise ValueError(filt)


# ---------------------------------------------------------------- 回测核心

@dataclass
class Result:
    name: str
    params: dict
    equity: pd.Series
    weights: pd.DataFrame          # 各日实际持仓权重
    n_trades: int
    turnover: float                # 区间累计单边换手
    holdings_log: list = field(default_factory=list)
    trades_log: list = field(default_factory=list)  # (iso日期, 换手)


def simulate_rotation(
    mkt: Market, *, sig: str, filt: str, top_k: int, freq: str,
    buffer_rule: bool = False, cost: float = COST_RATE, name: str = "",
    buffer_mode: str | None = None, weight_mode: str = "equal",
    no_trade_band: float = 0.0, exec_lag: int = 0,
    min_weight: float = 0.0,
    regime: pd.Series | None = None,
) -> Result:
    """regime: 市场级防守开关 (布尔序列, 对齐交易日)。False → 无视信号, 全仓防守资产。"""
    dates = mkt.close.index
    n = len(dates)
    codes = mkt.close.columns
    scores = signal_scores(mkt, sig)
    fmask = filter_mask(mkt, filt)

    # buffer_mode: none/soft(前k+1名保留)/strong(前k+2名保留)
    if buffer_mode is None:
        buffer_mode = "soft" if buffer_rule else "none"
    tol = {"none": 0, "soft": 1, "strong": 2}[buffer_mode]

    # 调仓日: 每季/每月/每周第一个交易日
    if freq == "quarter":
        key = dates.to_period("Q")
    elif freq == "month":
        key = dates.to_period("M")
    elif freq == "week":
        key = dates.to_period("W")
    else:
        raise ValueError(freq)
    rebal_idx = [int(v) for v in pd.Series(np.arange(n), index=dates).groupby(key).min()]

    wmat = np.zeros((n, len(codes)))
    ret_arr = mkt.ret.fillna(0.0).to_numpy()
    cost_factor = np.ones(n)
    holdings_log = []
    trades_log = []
    n_trades = 0
    turnover_total = 0.0
    prev_w = pd.Series(0.0, index=codes)

    for pos, di in enumerate(rebal_idx):
        t = di - 1  # 信号日 = 上一交易日; 成交按该日收盘
        if t < 250:
            continue
        tdate = dates[t]
        elig_row = mkt.elig.loc[tdate]
        sc = scores.loc[tdate].where(elig_row & fmask.loc[tdate])
        sc = sc.dropna()
        if regime is not None and not bool(regime.loc[tdate]):
            sc = sc.iloc[0:0]  # 市场级防守: 强制走 SAFE
        if len(sc) == 0:
            # 无候选: 防守仓
            target_codes = [SAFE] if bool(elig_row.get(SAFE, False)) else []
        else:
            ranked = sc.sort_values(ascending=False)
            rank_pos = {c: i for i, c in enumerate(ranked.index)}
            picked: list[str] = []
            if tol > 0:
                picked = [c for c in ranked.index
                          if prev_w.get(c, 0) > 0 and rank_pos[c] < top_k + tol][:top_k]
            for c in ranked.index:
                if len(picked) >= top_k:
                    break
                if c not in picked:
                    picked.append(c)
            target_codes = picked
        target = pd.Series(0.0, index=codes)
        if weight_mode == "invvol" and len(target_codes) > 1:
            v = mkt.vol60.loc[tdate, target_codes].replace(0, np.nan)
            inv = (1.0 / v).fillna(0.0)
            if inv.sum() > 0:
                target[target_codes] = (inv / inv.sum()).to_numpy()
            else:
                target[target_codes] = 1.0 / len(target_codes)
        else:
            for c in target_codes:
                target[c] = 1.0 / len(target_codes)
        # min_weight: 砍掉权重过小的仓 (整数仓/易操作变体), 其余重新归一
        if min_weight > 0:
            held = target[target > 0]
            keep = held[held >= min_weight]
            if 0 < len(keep) < len(held):
                target = pd.Series(0.0, index=codes)
                target[keep.index] = (keep / keep.sum()).to_numpy()
        turnover = float((target - prev_w).abs().sum())
        end = rebal_idx[pos + 1] if pos + 1 < len(rebal_idx) else n
        if turnover > max(no_trade_band, 1e-9):
            apply = min(di + exec_lag, n - 1)
            wmat[di:apply] = prev_w.to_numpy()  # 执行前维持旧仓
            cost_factor[apply] = 1.0 - cost * turnover
            turnover_total += turnover
            n_trades += 1
            trades_log.append((dates[apply].date().isoformat(), turnover))
            holdings_log.append((dates[apply].date().isoformat(), [NAME[c] for c in target_codes]))
            wmat[apply:end] = target.to_numpy()
            prev_w = target
        else:
            wmat[di:end] = prev_w.to_numpy()

    port_ret = (ret_arr * wmat).sum(axis=1)
    growth = np.cumprod(1.0 + port_ret)
    equity = pd.Series(growth * np.cumprod(cost_factor), index=dates, name=name or f"{sig}_{filt}_k{top_k}_{freq}")
    weights = pd.DataFrame(wmat, index=dates, columns=codes)
    return Result(equity.name, {"sig": sig, "filt": filt, "top_k": top_k, "freq": freq,
                                "buffer": buffer_mode, "weight": weight_mode, "cost": cost},
                  equity, weights, n_trades, turnover_total, holdings_log, trades_log)


def simulate_static(mkt: Market, alloc: dict[str, float], *, freq: str = "month",
                    cost: float = COST_RATE, name: str = "static") -> Result:
    """固定配置再平衡 (对照组): alloc 全为非负权重, 归一化。"""
    dates = mkt.close.index
    n = len(dates)
    codes = mkt.close.columns
    target = pd.Series(0.0, index=codes)
    for c, w in alloc.items():
        target[c] = w
    target = target / target.sum()
    key = dates.to_period("M") if freq == "month" else dates.to_period("W")
    rebal_idx = [int(v) for v in pd.Series(np.arange(n), index=dates).groupby(key).min()]
    wmat = np.zeros((n, len(codes)))
    cost_factor = np.ones(n)
    ret_arr = mkt.ret.fillna(0.0).to_numpy()
    prev_w = pd.Series(0.0, index=codes)
    n_trades, turnover_total = 0, 0.0
    trades_log = []
    for pos, di in enumerate(rebal_idx):
        t = di - 1
        if t < 0:
            continue
        tdate = dates[t]
        # 未上市部分权重按已上市部分归一
        live = target.where(mkt.elig.loc[tdate]).fillna(0.0)
        if live.sum() <= 0:
            continue
        live = live / live.sum()
        turnover = float((live - prev_w).abs().sum())
        end = rebal_idx[pos + 1] if pos + 1 < len(rebal_idx) else n
        if turnover > 1e-9:
            cost_factor[di] = 1.0 - cost * turnover
            turnover_total += turnover
            n_trades += 1
            trades_log.append((tdate.date().isoformat(), turnover))
        wmat[di:end] = live.to_numpy()
        prev_w = live
    port_ret = (ret_arr * wmat).sum(axis=1)
    equity = pd.Series(np.cumprod(1.0 + port_ret) * np.cumprod(cost_factor), index=dates, name=name)
    return Result(name, {"static": alloc, "cost": cost}, equity,
                  pd.DataFrame(wmat, index=dates, columns=codes), n_trades, turnover_total,
                  [], trades_log)


# ---------------------------------------------------------------- 指标

def metrics(equity: pd.Series, trades_log: list | None = None,
            start: str | None = None, end: str | None = None) -> dict:
    eq = equity.loc[start:end] if start or end else equity
    if len(eq) < 2:
        return {}
    daily = eq.pct_change().dropna()
    years = len(eq) / 244.0
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1
    vol = float(daily.std() * math.sqrt(252))
    sharpe = (cagr - RF) / vol if vol > 0 else 0.0
    downside = float(daily[daily < 0].std() * math.sqrt(252))
    sortino = (cagr - RF) / downside if downside > 0 else 0.0
    dd = eq / eq.cummax() - 1
    mdd = float(dd.min())
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    lo = start or "0000"
    hi = end or "9999"
    tr = [x for x in (trades_log or []) if lo <= x[0] <= hi]
    n_tr = len(tr)
    turnover = sum(x[1] for x in tr)
    return {
        "total": total, "cagr": cagr, "vol": vol, "sharpe": sharpe, "sortino": sortino,
        "mdd": mdd, "calmar": calmar,
        "trades": round(n_tr / max(years, 1e-9), 1),
        "turnover_yr": turnover / max(years, 1e-9),
    }


def yearly_returns(equity: pd.Series) -> dict[str, float]:
    daily = equity.pct_change().fillna(0.0)
    out = {}
    for y, grp in daily.groupby(daily.index.year):
        out[str(y)] = float((1 + grp).prod() - 1)
    return out


# ---------------------------------------------------------------- 主流程

def main() -> None:
    mkt = load_market()
    rows = []
    curves = {}
    results = {}

    # ---- 网格: 轮动策略
    for sig in SIGNALS:
        for filt in FILTERS:
            for top_k in (1, 2, 3):
                for freq in ("quarter", "month", "week"):
                    for bufmode in ("none", "soft", "strong"):
                        for wmode in ("equal", "invvol"):
                            if wmode == "invvol" and top_k == 1:
                                continue
                            for band in (0.0, 0.2):
                                name = f"{sig}|{filt}|k{top_k}|{freq}|{bufmode}|{wmode}|band{band:g}"
                                r = simulate_rotation(mkt, sig=sig, filt=filt, top_k=top_k,
                                                      freq=freq, buffer_mode=bufmode,
                                                      weight_mode=wmode, no_trade_band=band,
                                                      name=name)
                                m_full = metrics(r.equity, r.trades_log, BACKTEST_START)
                                m_train = metrics(r.equity, r.trades_log, BACKTEST_START, TRAIN_END)
                                m_test = metrics(r.equity, r.trades_log, "2024-01-01")
                                if not m_full:
                                    continue
                                rows.append({
                                    "name": name, "sig": sig, "filt": filt, "top_k": top_k,
                                    "freq": freq, "buffer": bufmode, "weight": wmode, "band": band,
                                    **{f"full_{k}": v for k, v in m_full.items()},
                                    **{f"train_{k}": v for k, v in m_train.items()},
                                    **{f"test_{k}": v for k, v in m_test.items()},
                                })
                                results[name] = r
                                curves[name] = r.equity

    grid = pd.DataFrame(rows)
    grid.to_csv(RESULTS_DIR / "grid_results.csv", index=False)

    # ---- 对照组
    bench_defs = {
        "等权全池(月度)": None,  # 特殊处理
        "纳指+黄金 50/50": {"159941": 0.5, "518880": 0.5},
        "纳指100 买入持有": {"159941": 1.0},
        "黄金 买入持有": {"518880": 1.0},
        "红利低波 买入持有": {"512890": 1.0},
        "十年国债 买入持有": {"511260": 1.0},
    }
    bench_rows = []
    # 等权全池: 每次调仓对所有已入池标的等权
    r = simulate_static(mkt, {c: 1.0 for c in CODES}, name="等权全池(月度)")
    results["等权全池(月度)"] = r
    curves["等权全池(月度)"] = r.equity
    bench_rows.append({"name": "等权全池(月度)", **metrics(r.equity, r.trades_log, BACKTEST_START)})
    for bname, alloc in bench_defs.items():
        if alloc is None:
            continue
        r = simulate_static(mkt, alloc, name=bname)
        results[bname] = r
        curves[bname] = r.equity
        bench_rows.append({"name": bname, **metrics(r.equity, r.trades_log, BACKTEST_START)})
    pd.DataFrame(bench_rows).to_csv(RESULTS_DIR / "benchmarks.csv", index=False)

    # ---- 曲线
    curve_df = pd.DataFrame(curves)
    curve_df.to_csv(RESULTS_DIR / "equity_curves.csv")

    # ---- 训练期优选 / 测试期验证
    grid["train_score"] = grid["train_sharpe"]
    top_train = grid.sort_values("train_score", ascending=False).head(15)
    top_train.to_csv(RESULTS_DIR / "top15_train.csv", index=False)
    top_full = grid.sort_values("full_sharpe", ascending=False).head(15)
    top_full.to_csv(RESULTS_DIR / "top15_full.csv", index=False)

    # 保存 top 结果明细
    keep = set(top_train["name"]) | set(top_full["name"])
    detail = {n: {"params": results[n].params, "n_trades": results[n].n_trades,
                  "turnover": results[n].turnover,
                  "yearly": yearly_returns(results[n].equity.loc[BACKTEST_START:]),
                  "holdings": results[n].holdings_log}
              for n in keep if n in results}
    (RESULTS_DIR / "detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=1))

    print("网格组合数:", len(grid))
    print("\n=== 按全期夏普 Top10 ===")
    cols = ["name", "full_cagr", "full_vol", "full_sharpe", "full_mdd", "full_calmar",
            "full_trades", "full_turnover_yr"]
    print(top_full[cols].head(10).to_string(index=False))
    print("\n=== 按训练期(2020-2023)夏普 Top10, 看其 2024+ 表现 ===")
    cols2 = ["name", "train_sharpe", "test_cagr", "test_sharpe", "test_mdd",
             "full_cagr", "full_sharpe", "full_mdd"]
    print(top_train[cols2].head(10).to_string(index=False))
    print("\n=== 对照组 ===")
    print(pd.DataFrame(bench_rows).to_string(index=False))

    # ---- 低换手榜单 (年换手 <= 12, 即平均每月最多换一次仓)
    low = grid[grid["full_turnover_yr"] <= 12].sort_values("full_sharpe", ascending=False)
    low.head(15).to_csv(RESULTS_DIR / "top15_low_turnover.csv", index=False)
    cols3 = ["name", "full_cagr", "full_vol", "full_sharpe", "full_mdd", "full_calmar",
             "full_trades", "full_turnover_yr", "train_sharpe", "test_sharpe"]
    print("\n=== 低换手榜 (年换手<=12) Top10 ===")
    print(low[cols3].head(10).to_string(index=False))

    # ---- 少交易榜 (年交易次数 <= 6)
    quiet = grid[grid["full_trades"] <= 6].sort_values("full_sharpe", ascending=False)
    quiet.head(15).to_csv(RESULTS_DIR / "top15_quiet.csv", index=False)
    print("\n=== 少交易榜 (年交易<=6次) Top10 ===")
    print(quiet[cols3].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
