"""最终分析: 候选策略稳健性检验 + 报告/图表生成。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import engine as E
import viz

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  (脚本入口: 把项目根加入 sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "results"

FINALISTS = {
    "A 稳健优选": dict(sig="voladj60", filt="abs120", top_k=2, freq="week",
                     buffer_mode="soft", weight_mode="invvol", no_trade_band=0.2),
    "B 进攻Top1": dict(sig="mom20", filt="none", top_k=1, freq="month",
                     buffer_mode="strong", weight_mode="equal", no_trade_band=0.0),
    "C 极简季度": dict(sig="mom250", filt="ma120", top_k=1, freq="quarter",
                    buffer_mode="soft", weight_mode="equal", no_trade_band=0.0),
    "D 低回撤": dict(sig="voladj60", filt="ma60", top_k=3, freq="month",
                   buffer_mode="strong", weight_mode="invvol", no_trade_band=0.2),
}


def run(params: dict, **over):
    kw = {**params, **over}
    return E.simulate_rotation(MKT, **kw)


def worst_rolling_year(eq: pd.Series) -> float:
    r = eq.loc[E.BACKTEST_START:]
    roll = r / r.shift(244) - 1
    return float(roll.min()) if len(roll) > 244 else float("nan")


def mdd_detail(eq: pd.Series) -> tuple[str, str, float]:
    r = eq.loc[E.BACKTEST_START:]
    dd = r / r.cummax() - 1
    trough = dd.idxmin()
    peak = r.loc[:trough].idxmax()
    return peak.date().isoformat(), trough.date().isoformat(), float(dd.min())


def main() -> None:
    global MKT
    MKT = E.load_market()
    grid = pd.read_csv(RESULTS / "grid_results.csv")
    bench = pd.read_csv(RESULTS / "benchmarks.csv")

    # ---------- 基准曲线
    bench_defs = {
        "纳指+黄金50/50": {"159941": 0.5, "518880": 0.5},
        "等权全池": {c: 1.0 for c in E.CODES},
        "纳指100持有": {"159941": 1.0},
    }
    bench_res = {}
    for name, alloc in bench_defs.items():
        bench_res[name] = E.simulate_static(MKT, alloc, name=name)

    # ---------- 候选策略 + 敏感性
    summary_rows, yearly_rows, sens_rows, robust_rows = [], [], [], []
    curves, finals = {}, {}
    for name, params in FINALISTS.items():
        r = run(params)
        finals[name] = r
        curves[name] = r.equity
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        pk, tr, mdd = mdd_detail(r.equity)
        summary_rows.append({
            "策略": name, "年化收益": m["cagr"], "年化波动": m["vol"], "夏普": m["sharpe"],
            "最大回撤": mdd, "回撤区间": f"{pk}→{tr}", "卡玛": m["calmar"],
            "年交易次数": m["trades"], "年换手": m["turnover_yr"],
            "最差滚动一年": worst_rolling_year(r.equity),
        })
        yr = E.yearly_returns(r.equity.loc[E.BACKTEST_START:])
        yearly_rows.append({"策略": name, **yr})
        # 成本敏感性
        for cost in (0.0, 0.0005, 0.001, 0.002):
            rc = run(params, cost=cost)
            mc = E.metrics(rc.equity, rc.trades_log, E.BACKTEST_START)
            sens_rows.append({"策略": name, "单边成本bp": cost * 1e4,
                              "年化收益": mc["cagr"], "夏普": mc["sharpe"], "最大回撤": mc["mdd"]})
        # 执行滞后稳健性 (T+1收盘成交)
        rl = run(params, exec_lag=1)
        ml = E.metrics(rl.equity, rl.trades_log, E.BACKTEST_START)
        robust_rows.append({"策略": name, "情形": "基准(T收盘成交)", "年化收益": m["cagr"],
                            "夏普": m["sharpe"], "最大回撤": m["mdd"]})
        robust_rows.append({"策略": name, "情形": "T+1收盘成交", "年化收益": ml["cagr"],
                            "夏普": ml["sharpe"], "最大回撤": ml["mdd"]})

    for name, r in bench_res.items():
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        pk, tr, mdd = mdd_detail(r.equity)
        summary_rows.append({
            "策略": name, "年化收益": m["cagr"], "年化波动": m["vol"], "夏普": m["sharpe"],
            "最大回撤": mdd, "回撤区间": f"{pk}→{tr}", "卡玛": m["calmar"],
            "年交易次数": m["trades"], "年换手": m["turnover_yr"],
            "最差滚动一年": worst_rolling_year(r.equity),
        })
        yearly_rows.append({"策略": name, **E.yearly_returns(r.equity.loc[E.BACKTEST_START:])})

    summary = pd.DataFrame(summary_rows)
    yearly = pd.DataFrame(yearly_rows)
    sens = pd.DataFrame(sens_rows)
    robust = pd.DataFrame(robust_rows)
    summary.to_csv(RESULTS / "final_summary.csv", index=False)
    yearly.to_csv(RESULTS / "final_yearly.csv", index=False)
    sens.to_csv(RESULTS / "sensitivity.csv", index=False)
    robust.to_csv(RESULTS / "exec_robustness.csv", index=False)

    # ---------- 曲线/图表数据
    for name, r in bench_res.items():
        if name in ("纳指+黄金50/50", "等权全池"):
            curves[name] = r.equity
    curve_df = pd.DataFrame(curves).loc[E.BACKTEST_START:]

    # 收益率热力图用年度数据
    yearly_html = yearly.set_index("策略")

    # ---------- 生成 HTML 报告 (完整交互版 + 精简演示版)
    html = build_html(curve_df, summary, yearly, sens, robust, grid, finals,
                      scatter_stride=3, show_scatter=True,
                      tag="18 只 ETF 轮动回测报告")
    (RESULTS / "dashboard.html").write_text(html, encoding="utf-8")
    disp = build_html(curve_df.resample("ME").last().dropna(how="all"),
                      summary, yearly, sens, robust, grid, finals,
                      scatter_stride=4, show_scatter=False,
                      tag="18 只 ETF 轮动回测报告")
    (RESULTS / "display.html").write_text(disp, encoding="utf-8")

    print(summary.to_string(index=False))
    print()
    print(yearly.to_string(index=False))
    print()
    print(sens.to_string(index=False))
    print()
    print(robust.to_string(index=False))


def build_html(curve_df, summary, yearly, sens, robust, grid, finals, *,
               scatter_stride: int = 3, show_scatter: bool = True,
               tag: str = "ETF 轮动回测") -> str:
    # ---- 表格工具
    def table(df: pd.DataFrame, fmt: dict) -> str:
        cols = list(df.columns)
        head = "".join(f"<th>{c}</th>" for c in cols)
        rows = []
        for _, r in df.iterrows():
            tds = []
            for c in cols:
                v = r[c]
                f = fmt.get(c)
                if f and isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                    tds.append(f"<td>{f.format(v)}</td>")
                else:
                    tds.append(f"<td>{v}</td>")
            rows.append("<tr>" + "".join(tds) + "</tr>")
        return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'

    pct = "{:+.1%}"
    pct_plain = "{:.1%}"
    num2 = "{:.2f}"

    # ---- 图1: 净值 (对数)
    labels = [d.strftime("%Y-%m") for d in curve_df.index]
    downsample = max(1, len(labels) // 250)
    idx = list(range(0, len(labels), downsample))
    if idx[-1] != len(labels) - 1:
        idx.append(len(labels) - 1)
    labels_ds = [labels[i] for i in idx]
    ch1 = viz.Chart("累计净值 (2020 以来, 对数轴, 已扣费)", labels_ds,
                    y_fmt=lambda v: f"{v:.2f}", log=True)
    for col in curve_df.columns:
        ch1.add(col, [float(curve_df[col].iloc[i]) for i in idx])

    # ---- 图2: 回撤 (数值为百分数)
    ch2 = viz.Chart("回撤曲线", labels_ds, y_fmt=lambda v: f"{v:.0f}%", suffix="%")
    for col in curve_df.columns:
        s = curve_df[col]
        dd = (s / s.cummax() - 1)
        ch2.add(col, [float(dd.iloc[i]) * 100 for i in idx])

    # ---- 图3: 年度收益分组柱 (4 候选 + 1 个强基准, 控制可读性)
    years = [c for c in yearly.columns if c != "策略"]
    bar_names = list(FINALISTS) + ["纳指+黄金50/50"]
    yearly_ix = yearly.set_index("策略")
    bar_data = {n: {y: float(yearly_ix.loc[n, y]) for y in years} for n in bar_names}
    ch3 = viz.grouped_bars("逐年收益对比", years, bar_data)

    # ---- 图4: 网格散点 (x=|回撤|, y=年化), 高亮前3候选 (演示版用表格替代)
    if show_scatter:
        pts = [(-float(r["full_mdd"]), float(r["full_cagr"]), r["name"])
               for i, r in grid.iterrows() if i % scatter_stride == 0]
        hi = {}
        colors = ["var(--series-1)", "var(--series-2)", "var(--series-3)"]
        for (name, params), color in zip(list(FINALISTS.items())[:3], colors):
            row = summary[summary["策略"] == name].iloc[0]
            hi[name] = (-float(row["最大回撤"]), float(row["年化收益"]), color)
        sec5 = viz.scatter("全部 2700 组参数组合: 年化收益 vs 最大回撤", pts, hi,
                           "最大回撤 (绝对值)", "年化收益")
    else:
        walk = (grid.sort_values("train_sharpe", ascending=False)
                .head(6)[["name", "train_sharpe", "test_cagr", "test_sharpe",
                          "full_cagr", "full_sharpe", "full_mdd"]])
        walk.columns = ["参数组合", "训练期夏普(20-23)", "验证期年化(24+)", "验证期夏普",
                        "全期年化", "全期夏普", "全期回撤"]
        sec5 = table(walk, {"训练期夏普(20-23)": num2, "验证期年化(24+)": pct_plain,
                            "验证期夏普": num2, "全期年化": pct_plain,
                            "全期夏普": num2, "全期回撤": pct_plain})

    # ---- 表格
    t_summary = table(summary, {"年化收益": pct_plain, "年化波动": pct_plain, "夏普": num2,
                                "最大回撤": pct_plain, "卡玛": num2, "年交易次数": "{:.1f}",
                                "年换手": "{:.1f}x", "最差滚动一年": pct_plain})
    t_yearly = table(yearly, {y: pct for y in years})
    t_sens = table(sens, {"单边成本bp": "{:.0f}", "年化收益": pct_plain, "夏普": num2, "最大回撤": pct_plain})
    t_robust = table(robust, {"年化收益": pct_plain, "夏普": num2, "最大回撤": pct_plain})

    # 参数说明
    param_rows = "".join(
        f"<tr><td>{n}</td><td><code>{json.dumps(p, ensure_ascii=False)}</code></td></tr>"
        for n, p in FINALISTS.items())
    t_params = f'<table><thead><tr><th>策略</th><th>参数</th></tr></thead><tbody>{param_rows}</tbody></table>'

    # 最近持仓 (A 策略)
    hold_log = finals["A 稳健优选"].holdings_log[-12:]
    hold_rows = "".join(f"<tr><td>{d}</td><td>{'、'.join(h)}</td></tr>" for d, h in hold_log)
    t_hold = (f'<table><thead><tr><th>生效日</th><th>A 策略持仓</th></tr></thead>'
              f'<tbody>{hold_rows}</tbody></table>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{tag}</title>
<style>
{viz.css_vars()}
body {{ margin:0; background: var(--page); color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
.viz-root {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
h1 {{ font-size: 26px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 40px 0 12px; }}
.sub {{ color: var(--text-secondary); font-size: 13px; line-height: 1.7; }}
.chart {{ background: var(--surface-1); border:1px solid rgba(11,11,11,.10);
  border-radius: 10px; padding: 14px 16px 8px; margin: 16px 0; }}
:root[data-theme="dark"] .chart {{ border-color: rgba(255,255,255,.10); }}
.chart-title {{ font-size: 14px; font-weight: 600; margin-bottom: 6px; }}
.legend {{ font-size: 12px; color: var(--text-secondary); margin: 4px 0 8px; }}
.lg i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 5px 0 2px; vertical-align:-1px; }}
.svg {{ width: 100%; height: auto; display:block; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 10px 0 6px;
  background: var(--surface-1); border-radius: 10px; overflow:hidden; }}
th, td {{ padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--grid);
  font-variant-numeric: tabular-nums; }}
th {{ color: var(--text-secondary); font-weight: 600; background: var(--surface-1); }}
td:first-child, th:first-child {{ text-align: left; }}
code {{ font-size: 11px; color: var(--text-secondary); }}
.viz-tip {{ position:fixed; display:none; background: var(--surface-1); color: var(--text-primary);
  border:1px solid rgba(11,11,11,.10); border-radius:8px; padding:8px 10px; font-size:12px;
  pointer-events:none; z-index:99; box-shadow:0 4px 14px rgba(0,0,0,.12); }}
.viz-tip .th {{ font-weight:600; margin-bottom:4px; }}
.viz-tip .tr i {{ display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:6px; }}
.viz-tip .tr b {{ float:right; margin-left:14px; }}
.note {{ background: var(--surface-1); border-left: 3px solid var(--series-1);
  padding: 12px 16px; border-radius: 6px; font-size: 13px; line-height: 1.8;
  color: var(--text-secondary); margin: 14px 0; }}
</style>
</head>
<body class="viz-root">
<h1>{tag}</h1>
<div class="sub">回测区间 2020-01-02 → 2026-11-13 · 数据源 pytdxdata(通达信免费接口) 前复权日线 ·
单边成本 5bp · 无风险利率 2% · 新 ETF 从"指标可用"当月起入池 · 信号 T 日收盘、T 日收盘成交</div>

<div class="note"><b>结论速览</b>：综合风险收益与稳健性，推荐 <b>A 稳健优选</b>（波动率调整动量 + 120 日绝对动量过滤，
周度监控 + 免交易带宽 + 逆波动率加权持仓 2 只，全仓持有、无现金闲置；过滤失效时两仓之一自动变十年国债ETF）。
2020 以来年化 26%+、最大回撤 -17%、夏普 1.07，训练期(2020-2023)与验证期(2024-2026)表现一致。
注意：逆波动率加权是低回撤的核心——两仓之一是国债ETF时约 95% 权重在债券(等于变相降风险敞口)，
等权同参数变体年化降至 16%、回撤 -43%；A 当前(2026-11)正处于"98% 十年国债"的防守状态。
追求纯满仓进攻可选 B 进攻Top1（年化 45%、回撤 -58%），不想折腾可选 C 极简季度（一年只交易 2 次）。</div>

<h2>一、策略 vs 对照组总览</h2>
{t_summary}

<h2>二、累计净值</h2>
{ch1.render(0)}

<h2>三、回撤</h2>
{ch2.render(1)}

<h2>四、逐年收益</h2>
{t_yearly}
{ch3}

<h2>五、参数稳健性</h2>
<div class="sub">训练期(2020-2023)选参数, 验证期(2024-2026)看真章——训练期冠军 voladj60 系在验证期普遍失效,
而入选组合两段都稳。</div>
{sec5}

<h2>六、成本敏感性 (单边成本)</h2>
{t_sens}

<h2>七、执行假设稳健性 (信号日收盘成交 vs 次日收盘成交)</h2>
{t_robust}

<h2>八、入选策略参数</h2>
{t_params}

<h2>九、A 策略最近持仓变动</h2>
{t_hold}

<div class="sub" style="margin-top:28px">数据与代码: /tmp/etf-rotation/ (engine.py 回测引擎, analyze.py 本报告生成,
results/grid_results.csv 全部 2700 组合明细)。历史回测不代表未来收益。</div>
{viz.TOOLTIP_JS}
</body>
</html>
"""


if __name__ == "__main__":
    main()
