from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  (脚本入口: 把项目根加入 sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)


INSTRUMENTS = [
    ("黄金ETF华安", "518880", "商品", "2013-07-29", 36.6, "2014-01"),
    ("广发纳指100ETF", "159941", "美股", "2015-07-13", 11.6, "2016-01"),
    ("证券ETF国泰", "512880", "A股行业", "2016-08-08", 13.8, "2017-02"),
    ("银行ETF华宝", "512800", "A股行业", "2017-08-03", 13.2, "2018-02"),
    ("十年国债ETF国泰", "511260", "债券", "2017-08-24", 32.1, "2018-02"),
    ("传媒ETF广发", "512980", "A股行业", "2018-01-19", 4.8, "2018-07"),
    ("红利低波ETF华泰柏瑞", "512890", "A股风格", "2019-01-18", 9.1, "2019-07"),
    ("酒ETF鹏华", "512690", "A股行业", "2019-05-06", 6.1, "2019-11"),
    ("通信ETF国泰", "515880", "A股行业", "2019-09-06", 32.5, "2020-03"),
    ("创新药ETF银华", "159992", "A股行业", "2020-04-10", 5.7, "2020-10"),
    ("游戏ETF华夏", "159869", "A股行业", "2021-03-05", 5.6, "2021-09"),
    ("华夏恒生科技ETF", "513180", "港股", "2021-05-25", 22.4, "2021-11"),
    ("标普油气ETF富国", "513350", "商品", "2023-11-28", 14, "2024-06"),
    ("机器人ETF易方达", "159530", "A股行业", "2024-01-18", 17.2, "2024-07"),
    ("创业板人工智能ETF华宝", "159363", "A股行业", "2024-12-16", 9.8, "2025-06"),
    ("卫星ETF永赢", "159206", "A股行业", "2025-03-14", 4.6, "2025-09"),
    ("科创半导体ETF华夏", "588170", "A股行业", "2025-04-08", 67.4, "2025-10"),
    ("创业板算力ETF易方达", "158050", "A股行业", "2026-09-18", 4.3, "2027-03"),
]

NAME_BY_CODE = {row[1]: row[0] for row in INSTRUMENTS}


def load_prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    close_frames = []
    turnover_frames = []
    for _, code, _, _, _, _ in INSTRUMENTS:
        frame = pd.read_csv(DATA_DIR / f"{code}.csv", parse_dates=["date"])
        frame = frame.set_index("date").sort_index()
        close_frames.append(frame["close"].rename(code))
        turnover_frames.append(frame["amount"].rename(code))
    close = pd.concat(close_frames, axis=1).sort_index()
    amount = pd.concat(turnover_frames, axis=1).sort_index()
    close.index = pd.to_datetime(close.index)
    amount.index = pd.to_datetime(amount.index)
    return close, amount


def prepare_features(close: pd.DataFrame, amount: pd.DataFrame) -> pd.DataFrame:
    daily_return = close.pct_change()
    dollar_volume = amount / 1e8
    features = {}
    for lookback in [5, 10, 20, 60, 120]:
        features[f"ret_{lookback}"] = close.pct_change(lookback)
    for short, long_ in [(5, 20), (10, 30), (20, 60), (60, 120)]:
        features[f"ma_gap_{short}_{long_}"] = close.rolling(short).mean() / close.rolling(long_).mean() - 1
    features["risk_adj"] = daily_return.rolling(20).mean() / daily_return.rolling(20).std() * np.sqrt(252)
    features["vol20"] = daily_return.rolling(20).std() * np.sqrt(252)
    features["avg_turnover_20"] = dollar_volume.rolling(20).mean()
    features["turnover_ok"] = features["avg_turnover_20"] >= 3
    features["price_above_ma20"] = close > close.rolling(20).mean()
    return pd.concat(features, axis=1)


def pick_assets(
    date: pd.Timestamp,
    close: pd.DataFrame,
    features: pd.DataFrame,
    *,
    lookback: int,
    top_n: int,
    momentum_type: str,
    risk_filter: bool,
    absolute_filter: bool,
) -> list[str]:
    signal_key = f"ret_{lookback}"
    date_features = features.loc[date].swaplevel()
    signal = date_features[signal_key].reindex(close.columns).dropna()
    signal = signal.dropna()
    if risk_filter:
        vol = date_features["vol20"].reindex(signal.index)
        signal = signal[vol < 1.0]
    if absolute_filter:
        above = date_features["price_above_ma20"].reindex(signal.index)
        signal = signal[above.astype(bool)]
    if momentum_type == "risk_adj":
        risk_adj = date_features["risk_adj"].reindex(signal.index)
        signal = signal + 0.25 * risk_adj
    elif momentum_type == "blend":
        medium = date_features["ret_20"].reindex(signal.index)
        signal = 0.65 * signal + 0.35 * medium
    elif momentum_type == "trend":
        ma = date_features["ma_gap_20_60"].reindex(signal.index)
        signal = signal + 0.3 * ma
    ranked = signal.sort_values(ascending=False)
    if absolute_filter:
        ranked = ranked[ranked > 0]
    return list(ranked.head(top_n).index)


def rebalance_weights(
    target: list[str],
    current: dict[str, float],
    *,
    threshold: float,
    min_hold_days: int,
    last_change_day: int,
    day_index: int,
) -> dict[str, float] | None:
    target_set = set(target)
    current_set = set(current.keys())
    if target_set == current_set:
        return None
    if len(current) < int(math.ceil(1 / threshold)) and current_set.issubset(target_set):
        return None
    if day_index - last_change_day < min_hold_days:
        return None
    if not target_set:
        return {}
    return {code: 1 / len(target) for code in target}


def run_strategy(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    features: pd.DataFrame,
    *,
    rebalance_period: str,
    top_n: int,
    lookback: int,
    momentum_type: str,
    risk_filter: bool,
    absolute_filter: bool,
    min_hold_days: int,
    cost_rate: float = 0.0005,
    turnover_penalty: float = 0.0,
) -> dict:
    dates = close.index
    date_series = dates.to_series()
    if rebalance_period == "week":
        period_key = pd.MultiIndex.from_arrays([
            [date.year for date in dates],
            [date.isocalendar().week for date in dates],
        ])
    else:
        period_key = pd.MultiIndex.from_arrays([
            [date.year for date in dates],
            [date.month for date in dates],
        ])
    group_dates = date_series.groupby(period_key).first().sort_index()
    current: dict[str, float] = {}
    equity = 1.0
    last_change_day = -99999
    weights = pd.DataFrame(0.0, index=dates, columns=close.columns)
    trades = []
    daily_returns = close.pct_change().fillna(0)
    change_history = []
    for day_index, trade_date in enumerate(group_dates):
        signal_date_idx = dates.get_loc(trade_date)
        if signal_date_idx < 130:
            continue
        signal_date = dates[signal_date_idx - 1]
        target = pick_assets(
            signal_date,
            close,
            features,
            lookback=lookback,
            top_n=top_n,
            momentum_type=momentum_type,
            risk_filter=risk_filter,
            absolute_filter=absolute_filter,
        )
        decision = rebalance_weights(
            target,
            current,
            threshold=1 / max(top_n, 1),
            min_hold_days=min_hold_days,
            last_change_day=last_change_day,
            day_index=day_index,
        )
        if decision is None:
            continue
        new_weights = pd.Series(0.0, index=close.columns)
        for code, weight in decision.items():
            new_weights[code] = weight
        turnover = float((new_weights - pd.Series(current).reindex(new_weights.index).fillna(0)).abs().sum())
        cost = turnover * cost_rate
        next_day_idx = signal_date_idx + 1
        if next_day_idx >= len(dates):
            continue
        apply_date = dates[next_day_idx]
        equity *= 1 - cost
        current = decision
        last_change_day = day_index
        weights.loc[apply_date:] = 0
        for code, weight in current.items():
            weights.loc[apply_date:, code] = weight
        change_history.append((apply_date, target))
        trades.append({
            "date": apply_date,
            "turnover": turnover,
            "cost": cost,
            "target": target,
        })
    weights = weights.shift(0).fillna(0)
    gross = (weights * daily_returns).sum(axis=1)
    cumulative = pd.Series(index=dates, dtype=float)
    current_value = 1.0
    cumulative_values = []
    change_dates = set(row[0] for row in change_history)
    for date in dates:
        if date in change_dates:
            current_value = current_value
        daily_ret = gross.loc[date]
        current_value *= 1 + daily_ret
        cumulative_values.append(current_value)
    cumulative = pd.Series(cumulative_values, index=dates)
    return {
        "cumulative": cumulative,
        "weights": weights,
        "trades": trades,
    }


def metrics(cumulative: pd.Series, trades: list[dict], start: str, end: str) -> dict:
    series = cumulative.loc[start:end].dropna()
    if len(series) < 2:
        return {}
    daily_return = series.pct_change().dropna()
    years = len(series) / 252
    total_return = series.iloc[-1] / series.iloc[0] - 1
    annual_return = (1 + total_return) ** (1 / max(years, 1e-9)) - 1
    annual_vol = daily_return.std() * math.sqrt(252)
    downside = daily_return[daily_return < 0].std() * math.sqrt(252)
    max_drawdown = (series / series.cummax() - 1).min()
    trades_in_range = [row for row in trades if start <= row["date"].strftime("%Y-%m-%d") <= end]
    total_turnover = sum(row["turnover"] for row in trades_in_range)
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "sharpe": annual_return / annual_vol if annual_vol else 0,
        "sortino": annual_return / downside if downside else 0,
        "max_drawdown": max_drawdown,
        "calmar": annual_return / abs(max_drawdown) if max_drawdown else 0,
        "trades": len(trades_in_range),
        "annual_trades": len(trades_in_range) / max(years, 1e-9),
        "annual_turnover": total_turnover / max(years, 1e-9),
    }


def parameter_grid() -> list[dict]:
    grid = []
    for period, top_n, lookback, momentum_type, risk_filter, absolute_filter, min_hold_days in itertools.product(
        ["week", "month"],
        [1, 2, 3, 5],
        [5, 10, 20, 60, 120],
        ["plain", "risk_adj", "blend", "trend"],
        [False, True],
        [False, True],
        [0, 5, 10, 20],
    ):
        grid.append({
            "rebalance_period": period,
            "top_n": top_n,
            "lookback": lookback,
            "momentum_type": momentum_type,
            "risk_filter": risk_filter,
            "absolute_filter": absolute_filter,
            "min_hold_days": min_hold_days,
        })
    return grid


def main() -> None:
    close, amount = load_prices()
    features = prepare_features(close, amount)
    all_results = []
    cumulative_store = {}
    for params in parameter_grid():
        result = run_strategy(close, amount, features, **params)
        start_date = "2021-01-04"
        end_date = close.index[-1].strftime("%Y-%m-%d")
        row = metrics(result["cumulative"], result["trades"], start_date, end_date)
        if not row:
            continue
        all_results.append({**params, **row})
        key = json.dumps(params, sort_keys=True)
        cumulative_store[key] = result
    frame = pd.DataFrame(all_results)
    frame.to_csv(RESULTS_DIR / "all_backtests.csv", index=False)
    frame = frame.sort_values(["sharpe", "annual_return"], ascending=False)
    frame.to_csv(RESULTS_DIR / "all_backtests_sorted.csv", index=False)
    frame.head(30).to_csv(RESULTS_DIR / "top30.csv", index=False)
    print(frame.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
