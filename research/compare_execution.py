"""比较同一信号的三种可执行性不同的成交口径。

- close:        周五收盘算信号, 周五收盘成交 (同 bar, 实盘无法精确复制)
- close_prev:   周四收盘算信号, 周五收盘成交 (实盘可实现, 信号老一天)
- next_open:    周五收盘算信号, 周一开盘成交 (实盘可实现)

close 是偏乐观的基准; 真正可落地的是 close_prev 与 next_open。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine as E
from strategies import STRATEGIES
from web import core

VARIANTS: dict[str, dict] = {
    "close": {"execution": "close", "signal_lag": 0},
    "close_prev": {"execution": "close", "signal_lag": 1},
    "next_open": {"execution": "next_open", "signal_lag": 0},
}
METRIC_KEYS = ("total", "cagr", "vol", "sharpe", "sortino", "mdd", "calmar",
               "trades", "turnover_yr")


def compare() -> dict:
    E.DATA_DIR = ROOT / "data_tr"
    E.CODES = core.POOL_CODES
    E.NAME = {code: core.NAMES.get(code, code) for code in E.CODES}
    E.ENTRY_IND = {code: core.default_entry(code) for code in E.CODES}
    mkt = E.load_market()
    regime = core.hs300_regime(mkt.close.index, 60)

    output = {}
    for strategy_id, spec in STRATEGIES.items():
        E.SAFE = spec.safe
        results = {}
        for variant, opts in VARIANTS.items():
            result = E.simulate_rotation(
                mkt,
                regime=regime,
                name=f"{strategy_id}_{variant}",
                **opts,
                **spec.params,
            )
            metrics = E.metrics(result.equity, result.trades_log, E.BACKTEST_START)
            yearly = E.yearly_returns(result.equity.loc[E.BACKTEST_START:])
            eq = result.equity.loc[E.BACKTEST_START:]
            results[variant] = {
                "metrics": {k: float(v) for k, v in metrics.items()},
                "yearly": {str(k): float(v) for k, v in yearly.items()},
                "trades_log": [(d, float(t)) for d, t in result.trades_log],
                "equity": {d.strftime("%Y-%m-%d"): float(v) for d, v in eq.items()},
            }

        base = results["close"]
        output[strategy_id] = {
            "label": spec.label,
            "execution": results,
            "difference": {
                variant: {
                    key: results[variant]["metrics"].get(key, 0.0) - base["metrics"].get(key, 0.0)
                    for key in METRIC_KEYS
                }
                for variant in VARIANTS if variant != "close"
            },
            "yearly_difference": {
                variant: {
                    year: results[variant]["yearly"].get(year, 0.0) - base["yearly"].get(year, 0.0)
                    for year in sorted(set(base["yearly"]) | set(results[variant]["yearly"]))
                }
                for variant in VARIANTS if variant != "close"
            },
        }
    return output


if __name__ == "__main__":
    result = compare()
    print(json.dumps(result, ensure_ascii=False, indent=2))
