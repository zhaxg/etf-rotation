"""研究脚本共用件: 标的池、冻结参数组、一次性跑批 / 打印 / 敞口工具。

原来这里是"ETF 换成对应个股"的机制检验 (swap_test.py); 个股路线已确认放弃,
只保留与个股无关的公共部分, 供 pool_grid / int_weight_test 等脚本复用。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

import engine as E

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

ETF_UNIVERSE = [(r[0], r[1], r[5]) for r in E.INSTRUMENTS]  # 旧 18 只 ETF (名称, 代码, 入池月)

# 冻结参数: 寻优期入选的组合, 后续检验一律不重新优化
FROZEN = {
    "A 稳健优选": dict(sig="voladj60", filt="abs120", top_k=2, freq="week",
                   buffer_mode="soft", weight_mode="invvol", no_trade_band=0.2),
    "B 进攻Top1": dict(sig="mom20", filt="none", top_k=1, freq="month",
                   buffer_mode="strong", weight_mode="equal", no_trade_band=0.0),
    "C 极简季度": dict(sig="mom250", filt="ma120", top_k=1, freq="quarter",
                  buffer_mode="soft", weight_mode="equal", no_trade_band=0.0),
    "D 低回撤": dict(sig="voladj60", filt="ma60", top_k=3, freq="month",
                 buffer_mode="strong", weight_mode="invvol", no_trade_band=0.2),
}


def patch(universe: list[tuple], entry: dict[str, str]) -> None:
    E.DATA_DIR = ROOT / "data_tr"
    E.CODES = [c for _, c, _ in universe]
    E.NAME = {c: n for n, c, _ in universe}
    E.ENTRY_IND = dict(entry)


def natural_entries(universe: list[tuple]) -> dict[str, str]:
    out = {}
    for _, code, _ in universe:
        f = pd.read_csv(ROOT / "data_tr" / f"{code}.csv", parse_dates=["date"])
        d = f["date"].sort_values()
        start = d.iloc[min(250, len(d) - 1)]
        out[code] = max(start.strftime("%Y-%m"), "2020-01")
    return out


def mdd_detail(eq: pd.Series) -> tuple[str, str, float]:
    r = eq.loc[E.BACKTEST_START:]
    dd = r / r.cummax() - 1
    trough = dd.idxmin()
    peak = r.loc[:trough].idxmax()
    return peak.date().isoformat(), trough.date().isoformat(), float(dd.min())


def run_one(mkt, name: str, params: dict) -> dict:
    r = E.simulate_rotation(mkt, name=name, **params)
    m_full = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
    m_train = E.metrics(r.equity, r.trades_log, E.BACKTEST_START, E.TRAIN_END)
    m_test = E.metrics(r.equity, r.trades_log, "2024-01-01")
    pk, tr, mdd = mdd_detail(r.equity)
    eq21 = r.equity.loc["2021-01-01":]
    total21 = float(eq21.iloc[-1] / eq21.iloc[0] - 1) if len(eq21) > 1 else float("nan")
    return {
        "name": name, "result": r,
        "full": m_full, "train": m_train, "test": m_test,
        "mdd": mdd, "mdd_win": f"{pk}→{tr}", "total_2021": total21,
        "yearly": E.yearly_returns(r.equity.loc[E.BACKTEST_START:]),
    }


def show(tag: str, runs: dict) -> None:
    print(f"\n########## {tag} ##########")
    print(f"{'策略':<10} {'全期年化':>8} {'波动':>7} {'夏普':>6} {'回撤':>8} {'卡玛':>6} "
          f"{'训练夏普':>8} {'验证年化':>8} {'验证夏普':>8} {'21→今总收益':>10} {'年交易':>6} {'年换手':>7}")
    for name, d in runs.items():
        f, tr_, te = d["full"], d["train"], d["test"]
        print(f"{name:<10} {f['cagr']:>8.1%} {f['vol']:>7.1%} {f['sharpe']:>6.2f} "
              f"{d['mdd']:>8.1%} {f['calmar']:>6.2f} {tr_['sharpe']:>8.2f} "
              f"{te['cagr']:>8.1%} {te['sharpe']:>8.2f} {d['total_2021']:>10.1%} "
              f"{f['trades']:>6.1f} {f['turnover_yr']:>7.1f}x")
    years = sorted({y for d in runs.values() for y in d["yearly"]})
    print("\n逐年收益:")
    print(f"{'策略':<10} " + " ".join(f"{y:>8}" for y in years))
    for name, d in runs.items():
        print(f"{name:<10} " + " ".join(f"{d['yearly'].get(y, float('nan')):>8.1%}" for y in years))


def exposure(r: E.Result) -> pd.DataFrame:
    w = r.weights.loc[E.BACKTEST_START:]
    df = pd.DataFrame({
        "时间占比": (w > 0.001).mean(),
        "平均敞口": w.mean(),
        "持仓时权重": w.where(w > 0.001).mean(),
    })
    df.index = [E.NAME[c] for c in df.index]
    return df.sort_values("平均敞口", ascending=False)


def bench_row(name: str, r: E.Result) -> str:
    m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
    _, _, mdd = mdd_detail(r.equity)
    eq21 = r.equity.loc["2021-01-01":]
    t21 = float(eq21.iloc[-1] / eq21.iloc[0] - 1)
    return f"{name:<16} {m['cagr']:>8.1%} {m['sharpe']:>6.2f} {mdd:>8.1%} {t21:>10.1%}"