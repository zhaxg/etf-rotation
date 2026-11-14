# 山海双策 · ETF 轮动回测与信号网站

30 只 ETF 池的周度轮动策略：**登峰HIGH**（52 周新高接近度）与 **致远RAM**（30 日动量 / 20 日波动），
均带**规则 C 防守腿**——沪深300 跌破 60 日均线时全仓停泊红利低波（512890）。

回测 2020-01-01 起，**TR 全收益口径**（含分红，已扣单边 5bp 成本），基准沪深300 价格指数不含股息。

| 策略 | 年化 | 夏普 | 最大回撤 | 总收益 |
|---|---|---|---|---|
| 登峰HIGH | 26.2% | 1.29 | −14.6% | +387.8% |
| 致远RAM | 33.8% | 1.32 | −14.4% | +627.8% |
| 沪深300（基准） | −0.5% | −0.13 | −45.6% | −3.3% |

策略规则的完整说明、调仓时点与可信度验证见 [STRATEGY.md](STRATEGY.md)。

## 目录

```
engine.py          回测引擎: 信号 / 过滤 / 轮动 / 成本 / 指标 (不认识具体策略)
universe.py        标的池登记: WEB_POOL (30 只) + 旧 18 只池元数据
strategies/        策略定义, 一个策略一个文件 (StrategySpec + 自动发现)
pipeline/          数据管线: 取不复权日线+XDXR → 重建 TR 序列
research/          寻优 / 验证脚本 (一次性, 非线上依赖)
web/               FastAPI: main.py 装配, core.py 计算, api/ 路由, static/ 前端
data_tr/           TR 全收益序列 (线上数据源) + *_raw.csv 真实价 + *_xdxr.json 分红配股
```

性能约定：回测结果落盘在 `web/cache/heavy.json`（不入库、可删、自动重建），
缓存键 = 口径版本 + 池 + 策略参数 + 数据文件 mtime 指纹。

## 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python pipeline/fetch_raw.py     # 拉不复权日线 + XDXR（离线跑，需要通达信接口）
python pipeline/build_tr.py      # 重建 TR 全收益序列到 data_tr/

uvicorn web.main:app --host 0.0.0.0 --port 9696
```

打开 `http://127.0.0.1:9696/`：`#pool` 备选池（实时行情 + 分时），`#strategy` 策略持仓（信号排名 / 防守状态 / 历史表现 / 换仓记录）。

接口：`GET /api/instruments`、`POST /api/strategy`。

## 数据口径

- 通达信 `Adjust.QFQ` 是**等差前复权**，会扭曲分红股的收益与动量，因此只用来取**不复权原始价**，
  再用 XDXR 事件重建 TR（`pipeline/build_tr.py`，与 QFQ 序列做过正向验证）。
- TR 矩阵**只含全收益序列**，不要把真实成交价混进去显示或计算。

## 免责声明

回测不等于未来收益。仓位请按自己能承受的回撤折算。