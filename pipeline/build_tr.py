"""用 不复权原始价 + XDXR 分红送配 重建真实全收益(TR)序列。

验证: 用同一套事件正向复权重建 TDX QFQ 序列, 与 pytdxdata 直接取的 QFQ 对比,
偏差大说明 XDXR 漏事件或单位错。输出 data_tr/{code}.csv (close = TR 指数)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  (脚本入口: 把项目根加入 sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_TR = ROOT / "data_tr"
QFQ_DIRS = [ROOT / "data"]  # 早前取的 QFQ, 用于验证


def load_events(code: str) -> pd.DataFrame:
    evs = json.loads((DATA_TR / f"{code}_xdxr.json").read_text(encoding="utf-8"))
    rows = []
    for e in evs:
        if e["category"] == 1:
            rows.append({"date": pd.Timestamp(e["year"], e["month"], e["day"]),
                         "d": e["fenhong"], "s": e["songzhuangu"],
                         "r": e["peigu"], "pr": e["peigujia"], "k": 1.0})
        elif e["category"] == 11 and e["suogu"] > 0:
            rows.append({"date": pd.Timestamp(e["year"], e["month"], e["day"]),
                         "d": 0.0, "s": 0.0, "r": 0.0, "pr": 0.0, "k": e["suogu"]})
    if not rows:
        return pd.DataFrame(columns=["date", "d", "s", "r", "pr", "k"])
    df = pd.DataFrame(rows)
    out = df.groupby("date", as_index=False).agg(
        d=("d", "sum"), s=("s", "sum"), r=("r", "sum"),
        pr=("pr", "max"), k=("k", "prod"))
    return out


def build(code: str) -> dict:
    raw = pd.read_csv(DATA_TR / f"{code}_raw.csv", parse_dates=["date"]).set_index("date").sort_index()
    P = raw["close"].astype(float)
    ev = load_events(code)

    # 事件日 -> 交易日映射 (取 >= 事件日的首个交易日)
    ev_on: dict[pd.Timestamp, dict] = {}
    for _, e in ev.iterrows():
        future = P.index[P.index >= e["date"]]
        if not len(future):
            continue
        td = future[0]
        cur = ev_on.setdefault(td, {"d": 0.0, "s": 0.0, "r": 0.0, "pr": 0.0, "k": 1.0})
        cur["d"] += float(e["d"]); cur["s"] += float(e["s"])
        cur["r"] += float(e["r"]); cur["k"] *= float(e["k"])
        if e["pr"] > 0:
            cur["pr"] = float(e["pr"])

    # --- TR 财富指数: I(t) = I(t-1) * (f_t*P_t + d_t - r_t*pr) / P_{t-1}
    # --- Q 链 (前复权等价): Q(last)=P(last); Q(t-1) = f_t*Q(t) + d_t - r_t*pr
    dates = list(P.index)
    n = len(dates)
    NONE_EV = {"d": 0.0, "s": 0.0, "r": 0.0, "pr": 0.0, "k": 1.0}
    tr = [1.0] * n
    for i in range(1, n):
        e = ev_on.get(dates[i], NONE_EV)
        f = (1.0 + e["s"] + e["r"]) * e["k"]
        tr[i] = tr[i - 1] * (f * P.iloc[i] + e["d"] - e["r"] * e["pr"]) / P.iloc[i - 1]

    q = [0.0] * n
    q[-1] = float(P.iloc[-1])
    for i in range(n - 2, -1, -1):
        e = ev_on.get(dates[i + 1], NONE_EV)
        f = (1.0 + e["s"] + e["r"]) * e["k"]
        q[i] = f * q[i + 1] + e["d"] - e["r"] * e["pr"]

    # --- 验证: XDXR 未来分红之和 vs QFQ 隐含 V(t) = raw - qfq (无份额变动区间)
    qfq = None
    for d0 in QFQ_DIRS:
        p = d0 / f"{code}.csv"
        if p.exists():
            fq = pd.read_csv(p, parse_dates=["date"]).set_index("date")["close"].astype(float)
            qfq = fq if qfq is None else qfq.combine_first(fq)
    dev = float("nan")
    if qfq is not None and len(ev):
        share_ev = ev[(ev["s"] > 0) | (ev["r"] > 0) | (ev["k"] != 1.0)]
        after = share_ev["date"].max() if len(share_ev) else pd.Timestamp("1900-01-01")
        cand = [d for d in dates if d > after]
        devs = []
        for td in cand[:: max(1, len(cand) // 3)][:3]:
            try:
                v_impl = float(raw.loc[td, "close"]) - float(qfq.loc[td])
            except KeyError:
                continue
            future = float(ev.loc[ev["date"] > td, "d"].sum())
            devs.append(abs(future - v_impl))
        if devs:
            dev = max(devs)
    elif qfq is not None:
        # 无任何事件: QFQ 应与 raw 完全一致
        both = pd.DataFrame({"raw": P, "qfq": qfq}).dropna()
        dev = float(((both["raw"] / both["qfq"]) - 1).abs().max()) if len(both) else float("nan")

    out = raw.copy()
    out["close"] = pd.Series(tr, index=dates) * 100.0  # TR 指数, 标度无关紧要
    out.reset_index().to_csv(DATA_TR / f"{code}.csv", index=False)
    return {"code": code, "bars": n, "events": len(ev_on), "qfq_max_dev": dev}


def main() -> None:
    codes = sorted(p.name.replace("_raw.csv", "") for p in DATA_TR.glob("*_raw.csv"))
    rows = [build(c) for c in codes]
    rep = pd.DataFrame(rows)
    rep.to_csv(DATA_TR / "tr_validation.csv", index=False)
    bad = rep[rep["qfq_max_dev"] > 0.02]
    print(rep.to_string(index=False))
    print("\n偏差>0.02 元的标的 (XDXR 可能漏分红事件):")
    print(bad.to_string(index=False) if len(bad) else "  无")


if __name__ == "__main__":
    main()
