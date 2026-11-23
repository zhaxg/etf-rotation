"""ETF 轮动 · Web 核心 (与 HTTP 无关)。

数据 / 行情快照 / 日线保鲜 / 沪深300 防守腿 / 双策略回测与试算, 全部在这里;
HTTP 路由见 web/api/*.py, 应用装配见 web/main.py。

- 价格用不复权价 (真实成交价); 策略计算走 TR 全收益矩阵 (data_tr/)
- 实时口径: 交易日把快照价按 TR/真实价 因子换算后并入末行, 非交易时间自动走日线
- 回测按数据指纹缓存 (_calc_cache), 数据变了才重算
"""
from __future__ import annotations

import csv
import dataclasses
import json
import math
import sys
import threading
import time
import asyncio
from datetime import date, datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

import pandas as pd

import engine as E
from pipeline import build_tr
from pipeline.fetch_raw import ensure_data, fetch_bundle, write_bundle
from strategies import STRATEGIES, fresh_target
from universe import DEFAULT_CODES, WEB_POOL

DATA = ROOT / "data_tr"
WEB = Path(__file__).resolve().parent
NAMES_FILE = WEB / "names.json"

# ---------------------------------------------------------------- 标的登记

def _seed_names() -> dict[str, str]:
    names = {row[1]: row[0] for row in E.INSTRUMENTS}
    if NAMES_FILE.exists():
        names.update(json.loads(NAMES_FILE.read_text(encoding="utf-8")))
    names.update({c: n for c, n, _ in WEB_POOL})  # 用户清单 (官方名) 优先
    return names


NAMES: dict[str, str] = _seed_names()
CATEGORY = {row[1]: row[2] for row in E.INSTRUMENTS}
CATEGORY.update({c: g for c, _, g in WEB_POOL})
ENTRY_DEFAULT = {row[1]: row[5] for row in E.INSTRUMENTS}  # 原表"指标可用"月


def _save_names() -> None:
    NAMES_FILE.write_text(json.dumps(NAMES, ensure_ascii=False, indent=1), encoding="utf-8")


def _read_daily(code: str, kind: str = "raw") -> pd.DataFrame:
    """读取日线并清洗；文件缺失 / 损坏 / 坏日期统一返回空表, 由调用方兜底。"""
    path = DATA / (f"{code}_raw.csv" if kind == "raw" else f"{code}.csv")
    try:
        f = pd.read_csv(path, dtype={"date": str})
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return pd.DataFrame()
    if "date" not in f.columns:
        return pd.DataFrame()
    f["date"] = pd.to_datetime(f["date"], errors="coerce", format="%Y-%m-%d")
    f = f.dropna(subset=["date"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in f.columns:
            f[col] = pd.to_numeric(f[col], errors="coerce")
    if "close" in f.columns:
        f = f[f["close"].notna() & (f["close"] > 0)]
    return f.sort_values("date").reset_index(drop=True)


def default_entry(code: str) -> str:
    """原表口径: 指标可用 = 上市后约 6 个月; 不早于 2020-01。"""
    if code in ENTRY_DEFAULT:
        return ENTRY_DEFAULT[code]
    f = _read_daily(code)
    if f.empty:
        return "2020-01"
    start = f["date"].min() + pd.DateOffset(months=6)
    return max(start.strftime("%Y-%m"), "2020-01")


# ---------------------------------------------------------------- 备选池 (固定清单)

POOL_FILE = WEB / "pool.json"


def _load_pool() -> list[str]:
    if POOL_FILE.exists():
        try:
            codes = [c for c in json.loads(POOL_FILE.read_text(encoding="utf-8"))
                     if (DATA / f"{c}_raw.csv").exists()]
            if codes:
                return codes
        except Exception:
            pass
    return list(DEFAULT_CODES)


POOL_CODES: list[str] = _load_pool()
INITIALIZING_DATA: bool = False


def _bundle_ok(code: str) -> bool:
    """raw / TR 都能解析出至少 2 根有效日线, 且 XDXR 文件存在。"""
    return (len(_read_daily(code)) >= 2
            and len(_read_daily(code, "tr")) >= 2
            and (DATA / f"{code}_xdxr.json").exists())


_data_check: dict = {"ts": 0.0, "missing": []}
_DATA_CHECK_TTL = 30.0


def _missing_bundles() -> list[str]:
    """全池有效性检查较重 (30 只 × raw+TR), 30 秒内复用结果。"""
    now = time.time()
    if now - _data_check["ts"] < _DATA_CHECK_TTL:
        return _data_check["missing"]
    missing = [code for code in POOL_CODES if not _bundle_ok(code)]
    _data_check.update(ts=now, missing=missing)
    return missing


async def ensure_initial_data() -> None:
    """首次启动自动补齐本地日线 / XDXR / TR 数据；已有且有效的文件不会重复拉取。"""
    global INITIALIZING_DATA
    required = _missing_bundles()
    if not required:
        return
    if INITIALIZING_DATA:
        raise HTTPException(503, "行情数据正在初始化，请稍后刷新")
    INITIALIZING_DATA = True
    try:
        ok = await ensure_data(codes=POOL_CODES, required=required)
    except Exception as exc:
        raise HTTPException(503, f"行情数据初始化失败：{type(exc).__name__}") from exc
    finally:
        INITIALIZING_DATA = False
    if not ok:
        missing = [code for code in required if not _bundle_ok(code)]
        raise HTTPException(503, f"行情数据初始化失败，缺少：{', '.join(missing)}")


# ---------------------------------------------------------------- 当日行情 (分时/成交额)

QUOTE_TTL = 180  # 秒: 普通请求的缓存有效期
QUOTE_MIN_INTERVAL = 10  # 秒: 并发/强制刷新的最小间隔 (两个请求同时进来只拉一次)
_quote_cache: dict = {"ts": 0.0, "data": {}}
_quote_lock = asyncio.Lock()


def _valid_snapshot(sp) -> bool:
    """行情源偶发返回垃圾字段 (日期越界 / 价格 0 / 负值), 先校验再使用。"""
    try:
        d = int(getattr(sp, "date", 0) or 0)
        price = float(sp.close)
        pre = float(sp.pre_close)
        amount = float(getattr(sp, "amount", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return False
    if not (20000101 <= d <= 21001231):
        return False
    if not (math.isfinite(price) and price > 0 and math.isfinite(pre) and pre > 0):
        return False
    if not (math.isfinite(amount) and amount >= 0):
        return False
    return 0.5 <= price / pre <= 1.5


def _valid_bar(b) -> bool:
    try:
        price = float(b.price)
        d = int(b.date)
        h = int(b.hour)
        m = int(b.minute)
    except (TypeError, ValueError, AttributeError):
        return False
    return (math.isfinite(price) and price > 0 and 20000101 <= d <= 21001231
            and 0 <= h <= 23 and 0 <= m <= 59)


def _fallback_quote(code: str) -> dict:
    """分时接口不可用时退化为日线快照；无可用日线时返回空行情, 不抛错。"""
    f = _read_daily(code)
    if f.empty:
        return {"price": 0.0, "change_pct": 0.0, "amount": 0.0, "spark": [],
                "pre": 0.0, "day": ""}
    close = float(f["close"].iloc[-1])
    prev = float(f["close"].iloc[-2]) if len(f) > 1 else close
    amount = float(f["amount"].iloc[-1]) if "amount" in f else 0.0
    return {"price": close, "change_pct": (close / prev - 1) if prev else 0.0,
            "amount": amount if pd.notna(amount) else 0.0,
            "spark": [], "pre": round(prev, 4),
            "day": f["date"].iloc[-1].date().isoformat()}


async def load_quotes(force: bool = False) -> dict:
    """当日分时快照: 现价 / 涨跌幅(对昨收) / 成交额 / 分时价格路径 + 昨收。

    TTL 内走缓存; force 跳过 TTL, 但仍服从 QUOTE_MIN_INTERVAL (并发请求共用一次拉取)。
    """
    now = time.time()
    if _quote_cache["data"] and now - _quote_cache["ts"] < QUOTE_MIN_INTERVAL:
        return _quote_cache["data"]
    if not force and _quote_cache["data"] and now - _quote_cache["ts"] < QUOTE_TTL:
        return _quote_cache["data"]
    async with _quote_lock:  # 同一时刻只拉一次: 页面两个请求并发进来时共用结果
        now = time.time()
        if _quote_cache["data"] and now - _quote_cache["ts"] < QUOTE_MIN_INTERVAL:
            return _quote_cache["data"]
        return await _fetch_quotes()


async def _fetch_quotes() -> dict:
    from pytdxdata import TdxData
    symbols = [("sh" if c.startswith(("5", "6")) else "sz") + c for c in POOL_CODES]
    minutes, snaps = [], []
    try:
        async with TdxData() as d:
            snaps = await d.get_snapshot(symbols)
            snap_by = {s.symbol: s for s in snaps}
            # 快照自带交易日 (非交易日 = 最近交易日); 分时按该日取, 周末/节假日也能
            # 画最近交易日的分时路径, 与快照的现价/涨跌幅同口径
            groups: dict[int, list[str]] = {}
            for sym in symbols:
                sp = snap_by.get(sym)
                ymd = int(sp.date) if sp is not None and _valid_snapshot(sp) else 0
                groups.setdefault(ymd, []).append(sym)
            for ymd, syms in groups.items():
                try:
                    minutes += await d.get_minutes(syms, date=ymd or None)
                except Exception:
                    pass  # 单组失败不拖垮其余标的
            # 行情源偶发只返回部分标的: 对没有分时的标的补抓两次
            for _ in range(2):
                got = {b.symbol for b in minutes if _valid_bar(b)}
                missing = [s for s in symbols if s not in got]
                if not missing:
                    break
                by_date: dict[int, list[str]] = {}
                for sym in missing:
                    sp = snap_by.get(sym)
                    ymd = int(sp.date) if sp is not None and _valid_snapshot(sp) else 0
                    by_date.setdefault(ymd, []).append(sym)
                for ymd, syms in by_date.items():
                    try:
                        minutes += await d.get_minutes(syms, date=ymd or None)
                    except Exception:
                        pass
    except Exception:
        pass
    snap_by = {s.symbol: s for s in snaps}
    bars_by: dict[str, list] = {}
    for b in minutes:
        bars_by.setdefault(b.symbol, []).append(b)

    out: dict[str, dict] = {}
    for code, sym in zip(POOL_CODES, symbols):
        q = _fallback_quote(code)
        sp = snap_by.get(sym)
        pre = 0.0
        if sp is not None and _valid_snapshot(sp):
            pre = float(sp.pre_close)
            q["price"] = float(sp.close)
            q["change_pct"] = (float(sp.close) / pre - 1) if pre else 0.0
            q["amount"] = float(getattr(sp, "amount", 0) or 0)
        if not pre:
            pre = q["price"] / (1 + q["change_pct"]) if q["change_pct"] != -1 else q["price"]
        bars = sorted((b for b in bars_by.get(sym, []) if _valid_bar(b)),
                      key=lambda b: (b.date, b.hour, b.minute))
        if bars and pre > 0:
            latest = max(b.date for b in bars)
            pts = [float(b.price) for b in bars if b.date == latest]
            step = max(1, len(pts) // 80)  # 抽样至 ~80 根
            q["spark"] = [round(pts[i], 4) for i in range(0, len(pts), step)]
        q["pre"] = round(pre, 4) if pre else 0.0
        # 行情所属交易日 (非交易时间 = 最近交易日): 现价/涨跌/成交额/分时同属这一天
        ymd = int(sp.date) if sp is not None and _valid_snapshot(sp) else 0
        if not ymd and bars:
            ymd = int(max(b.date for b in bars))
        if ymd:  # 无有效实时行情时保留日线兜底的 day
            q["day"] = f"{ymd // 10000:04d}-{ymd // 100 % 100:02d}-{ymd % 100:02d}"
        out[code] = q
    _quote_cache.update(ts=time.time(), data=out)
    return out


# ---------------------------------------------------------------- 价格 / 指标

_price_cache: dict[str, float] = {}


def latest_price(code: str) -> float:
    if code not in _price_cache:
        f = _read_daily(code)
        _price_cache[code] = float(f["close"].iloc[-1]) if not f.empty else 0.0
    return _price_cache[code]


def futunn_link(code: str) -> str:
    """ETF 用 /etfs/ 路径, 个股用 /stock/; 沪市 5/6 开头。"""
    kind = "etfs" if code[0] in "15" else "stock"
    mkt = "SH" if code[0] in "56" else "SZ"
    return f"https://www.futunn.com/{kind}/{code}-{mkt}"


def code_stats(code: str) -> dict:
    """两个策略的决策量 (与引擎同口径): high52 = 收盘/250日最高 (52周新高接近度),
    ram30 = 30日动量/20日波动率, mom120 = 120日动量 (abs120 过滤门限)。"""
    f = _read_daily(code, "tr")
    tr = f.set_index("date")["close"].astype(float) if not f.empty else pd.Series(dtype=float)
    ret = tr.pct_change().dropna()
    high52 = float(tr.iloc[-1] / tr.tail(250).max()) if len(tr) >= 60 else float("nan")
    mom120 = float(tr.iloc[-1] / tr.iloc[-121] - 1) if len(tr) > 120 else float("nan")
    mom30 = float(tr.iloc[-1] / tr.iloc[-31] - 1) if len(tr) > 30 else float("nan")
    vol20 = float(ret.tail(20).std() * math.sqrt(252)) if len(ret) >= 20 else float("nan")
    ram30 = mom30 / vol20 if vol20 else float("nan")
    return {"high52": high52, "ram30": ram30, "mom120": mom120}


def num(v, nd: int = 4):
    """JSON 友好: NaN/inf → None。"""
    x = float(v)
    return round(x, nd) if math.isfinite(x) else None


# ---------------------------------------------------------------- 策略

LOCK = threading.Lock()
SAFE_CODES = sorted({spec.safe for spec in STRATEGIES.values()})  # 防守仓始终并入池

# 策略来自 strategies/ 注册表: 每个策略一个文件 (high52.py / ram.py),
# 新增策略只需新增文件, 这里与 web/api 都不用改。
# ---------------------------------------------------------------- 沪深300 防守腿

INDEX_FILE = DATA / "000300_raw.csv"


async def ensure_index() -> None:
    """沪深300 指数日线: 本地文件 1 天内不重拉 (按文件 mtime 判断, 重启也不重拉);
    拉取失败沿用本地缓存。"""
    now = time.time()
    if INDEX_FILE.exists() and now - INDEX_FILE.stat().st_mtime < 86400:
        return
    try:
        from pytdxdata import TdxData
        from pytdxdata.models import KlinePeriod, Adjust
        async with TdxData() as d:
            rows = await d.get_bars("sh000300", KlinePeriod.DAY, start=0, count=2000, adjust=Adjust.NONE)
        if rows:
            recs = [{"date": f"{r.year:04d}-{r.month:02d}-{r.day:02d}", "open": r.open,
                     "high": r.high, "low": r.low, "close": r.close,
                     "volume": r.vol, "amount": r.amount} for r in rows]
            with INDEX_FILE.open("w", newline="") as h:
                w = csv.DictWriter(h, fieldnames=list(recs[0].keys()))
                w.writeheader()
                w.writerows(recs)
    except Exception:
        pass  # 沿用缓存


def hs300_regime(index: pd.DatetimeIndex, ma: int = 60) -> pd.Series:
    """True = 沪深300 在均线之上 (进攻); False = 跌破均线 (切防御腿)。"""
    f = pd.read_csv(INDEX_FILE, parse_dates=["date"]).set_index("date")
    close = f["close"].astype(float)
    return (close > close.rolling(ma).mean()).reindex(index).ffill().fillna(False).astype(bool)


def hs300_status(ma: int = 60) -> dict:
    f = pd.read_csv(INDEX_FILE, parse_dates=["date"]).set_index("date")
    close = f["close"].astype(float)
    line = float(close.rolling(ma).mean().iloc[-1])
    last = float(close.iloc[-1])
    return {"close": round(last, 2), "ma": round(line, 2),
            "defense": bool(last < line),
            "state": "defense" if last < line else "attack"}


# ---------------------------------------------------------------- 实时试算 (尾盘)

def _new_session_day(last_date: pd.Timestamp) -> bool:
    """快照价是否代表一根新日线: 工作日 09:15 后 (盘中/收盘后) 且文件尚未含今日。"""
    today = pd.Timestamp(date.today())
    return bool(today > last_date and today.weekday() < 5
                and datetime.now().time() >= dtime(9, 15))


def _in_trading_hours() -> bool:
    """盘中/尾盘 (工作日 09:15–15:05): 此时段实时价才有增量信息。"""
    now = datetime.now()
    return bool(now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 5))


def market_with_live(mkt: E.Market, live: dict[str, float]) -> tuple:
    """实时价并入日线矩阵: 新交易日 → 追加'今日'行; 否则替换末行。
    快照价是真实价, 需按 TR/真实价 因子换算进 TR 标度后混入。
    信号/过滤/波动率按引擎同口径重算。返回 (新 Market, 试算日期)。"""
    close = mkt.close.astype(float).copy()
    live_tr: dict[str, float] = {}
    for c, v in live.items():
        if c not in close.columns or not v:
            continue
        try:
            raw_last = latest_price(c)          # 真实价 (raw 日线末值)
            factor = float(close[c].iloc[-1]) / raw_last  # TR 标度换算
        except Exception:
            continue
        if raw_last and math.isfinite(factor) and factor > 0:
            live_tr[c] = float(v) * factor
    live_s = pd.Series(live_tr)
    if _new_session_day(close.index[-1]):
        row = close.iloc[-1].copy()
        row.loc[live_s.index] = live_s
        row.name = pd.Timestamp(date.today())
        close = pd.concat([close, row.to_frame().T]).astype(float)
        elig = pd.concat([mkt.elig, mkt.elig.iloc[[-1]]])
        amount = pd.concat([mkt.amount, mkt.amount.iloc[[-1]]])
    else:
        close.loc[close.index[-1], live_s.index] = live_s
        elig, amount = mkt.elig.copy(), mkt.amount.copy()
    elig.index = close.index
    amount.index = close.index
    ret = close.pct_change()
    mom = {L: close / close.shift(L) - 1 for L in (20, 60, 120, 250)}
    vol60 = ret.rolling(60).std() * math.sqrt(252)
    ma = {M: close.rolling(M).mean() for M in (20, 60, 120)}
    ranks = [r.rank(axis=1, pct=True) for r in mom.values()]
    blend = sum(ranks) / len(ranks)
    voladj = mom[60] / vol60.replace(0, float("nan"))
    mkt2 = dataclasses.replace(mkt, close=close, amount=amount, ret=ret, elig=elig,
                               mom=mom, vol60=vol60, ma=ma, blend=blend, voladj=voladj)
    return mkt2, close.index[-1]


# ---------------------------------------------------------------- 日线数据保鲜

_refresh_state: dict = {"running": False, "ts": 0.0, "checked": 0.0, "task": None}


async def _pull_history(code: str) -> bool:
    """取一只标的的日线+XDXR → 校验/合并后落盘并重建 TR。

    网络 I/O 在锁外, 落盘全程在锁内; 抓取数据不合法时直接抛错,
    调用方保留旧文件, 绝不用坏数据覆盖。
    """
    from pytdxdata import TdxData
    symbol = ("sh" if code.startswith(("5", "6")) else "sz") + code
    async with TdxData() as d:
        merged, events = await fetch_bundle(d, code)
        try:
            snaps = await d.get_snapshot([symbol])
        except Exception:
            snaps = []
    with LOCK:  # 落盘段全程无 await, 不会与回测计算互相卡死
        write_bundle(code, merged, events)
        _price_cache.pop(code, None)
        _data_check["ts"] = 0.0
    if snaps and getattr(snaps[0], "name", None) and NAMES.get(code, code) == code:
        NAMES[code] = snaps[0].name
        _save_names()
    return True


def _data_stale(codes: list[str]) -> list[str]:
    """日线缺数超过 1 个自然日、或文件损坏无法解析的标的。"""
    today = pd.Timestamp(date.today())
    stale = []
    for c in codes:
        f = _read_daily(c)
        if f.empty:
            stale.append(c)
            continue
        last = f["date"].max()
        if pd.isna(last) or (today - last).days > 1:
            stale.append(c)
    return stale


async def _refresh_stale(stale: list[str]) -> None:
    for c in stale:
        try:
            await _pull_history(c)
        except Exception:
            pass  # 个别失败不打断, 冷却后下次重试
    _quote_cache["ts"] = 0.0
    _data_check["ts"] = 0.0
    _refresh_state.update(running=False, ts=time.time())


def maybe_refresh_data() -> None:
    """日线过期 → 后台补齐 (10 分钟内不重复检查, 30 分钟冷却, 同时只跑一个任务)。"""
    now = time.time()
    if _refresh_state["running"] or now - _refresh_state["ts"] < 1800:
        return
    if now - _refresh_state["checked"] < 600:
        return
    _refresh_state["checked"] = now
    stale = _data_stale(POOL_CODES)
    if not stale:
        return
    _refresh_state["running"] = True
    try:
        _refresh_state["task"] = asyncio.create_task(_refresh_stale(stale))
    except RuntimeError:  # 无运行中的事件循环 (脚本/测试环境)
        _refresh_state["running"] = False


async def live_hs300_status(ma: int = 60) -> dict:
    """实时口径 HS300 vs MA60 (快照价补上今日; 停市时段退回文件口径)。"""
    f = pd.read_csv(INDEX_FILE, parse_dates=["date"]).set_index("date")
    close = f["close"].astype(float)
    live = None
    if _new_session_day(close.index[-1]):
        try:
            from pytdxdata import TdxData
            async with TdxData() as d:
                snaps = await d.get_snapshot(["sh000300"])
            if snaps:
                cand = float(snaps[0].close)
                if math.isfinite(cand) and 500 < cand < 50000:
                    live = cand
        except Exception:
            live = None
    if live is None:
        h = hs300_status(ma)
        h["live"] = False
        return h
    today = pd.Timestamp(date.today())
    c = close.iloc[:-1] if close.index[-1] >= today else close
    tail = c.iloc[-(ma - 1):]
    line = (float(tail.sum()) + live) / (len(tail) + 1)
    return {"close": round(live, 2), "ma": round(line, 2),
            "defense": bool(live < line),
            "state": "defense" if live < line else "attack", "live": True}


def _data_fingerprint(codes: list[str]) -> tuple:
    """数据文件 mtime 指纹 (TR 日线 / 原始价 / 沪深300): 数据变了缓存才失效。"""
    names = {f"{c}.csv" for c in codes} | {f"{c}_raw.csv" for c in codes} | {INDEX_FILE.name}
    parts = []
    for name in sorted(names):
        try:
            parts.append((name, (DATA / name).stat().st_mtime_ns))
        except OSError:
            parts.append((name, -1))
    return tuple(parts)


_calc_cache: dict = {"key": None, "val": None}

# 回测结果落地: 同一数据 + 同一策略参数 → 直接读盘, 不重算 (进程重启也不用预热)
CACHE_DIR = WEB / "cache"
HEAVY_FILE = CACHE_DIR / "heavy.json"
CALC_VERSION = 2  # 引擎/口径变化时手动 +1, 旧落地缓存自动失效


def _calc_key(codes: list[str], entry: dict, defense: bool) -> str:
    """缓存键: 口径版本 + 池 + 入池月 + 策略参数/防守开关 + 数据文件指纹。"""
    return json.dumps([
        CALC_VERSION, list(codes), sorted(entry.items()),
        [[sid, spec.regime_enabled(defense), sorted(spec.params.items())]
         for sid, spec in STRATEGIES.items()],
        [list(x) for x in _data_fingerprint(codes)],
    ], ensure_ascii=False)


def _load_heavy(key: str) -> dict | None:
    try:
        d = json.loads(HEAVY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return d.get("payload") if d.get("key") == key else None


def _save_heavy(key: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        tmp = HEAVY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "payload": payload}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(HEAVY_FILE)
    except Exception:
        pass  # 落盘失败不影响请求 (下次照常重算)


def _market(codes: list[str], entry: dict) -> tuple:
    """日线矩阵 + 防守状态序列 (约 0.13s): 每请求都要用 (实时口径依赖它)。"""
    E.DATA_DIR = DATA
    E.CODES = codes
    E.NAME = {c: NAMES.get(c, c) for c in codes}
    E.ENTRY_IND = entry
    mkt = E.load_market()
    return mkt, hs300_regime(mkt.close.index)


def _backtest(mkt: E.Market, reg: pd.Series, defense: bool) -> dict:
    """双策略全历史回测 → 可落盘的 payload (约 1.4s, 数据/参数不变时只跑一次)。"""
    per: dict[str, dict] = {}
    for sid, spec in STRATEGIES.items():
        use_reg = spec.regime_enabled(defense)
        E.SAFE = spec.safe
        r = E.simulate_rotation(mkt, name=sid, regime=(reg if use_reg else None),
                                **spec.params)
        prev = r.weights.iloc[-1]
        m = E.metrics(r.equity, r.trades_log, E.BACKTEST_START)
        # 回测净值曲线 (2020-01 起, 归一到 1, 按日取点: 平滑且回撤不失真)
        eq = r.equity.loc[E.BACKTEST_START:].dropna()
        eq = eq / float(eq.iloc[0])
        curve = [{"d": str(d.date()), "v": round(float(v), 4)}
                 for d, v in eq.items()]
        history = []
        for dt, names in r.holdings_log[::-1][:24]:
            row = r.weights.loc[dt]
            history.append({
                "date": dt,
                "items": [{"code": c, "name": NAMES.get(c, c), "weight": round(float(row[c]), 4),
                           "price": latest_price(c)}
                          for c in row.index if row[c] > 0],
            })
        per[sid] = {"curve": curve, "history": history, "use_reg": use_reg,
                    "m": {"total": m["total"], "cagr": m["cagr"], "sharpe": m["sharpe"],
                          "mdd": m["mdd"], "trades": m["trades"]},
                    "prev": {c: float(v) for c, v in prev.items() if v > 0}}

    # 沪深300 基准 (价格指数, 指标与策略同口径)
    idx = pd.read_csv(INDEX_FILE, parse_dates=["date"]).set_index("date")["close"].astype(float)
    idx = idx.loc[E.BACKTEST_START:].dropna()
    bm_m = E.metrics(idx)
    idx = idx / float(idx.iloc[0])
    bm_curve = [{"d": str(d.date()), "v": round(float(v), 4)}
                for d, v in idx.items()]
    benchmark = {"name": "沪深300", "curve": bm_curve,
                 "total": round(bm_m["total"], 4),
                 "cagr": round(bm_m["cagr"], 4), "sharpe": round(bm_m["sharpe"], 3),
                 "mdd": round(bm_m["mdd"], 4)}
    return {"per": per, "benchmark": benchmark}


def _calc(codes: list[str], entry: dict, defense: bool, key: str) -> dict:
    """组装一次请求所需的重计算结果: 有落地缓存就读盘, 否则回测并落盘。"""
    mkt, reg = _market(codes, entry)
    payload = _load_heavy(key)
    if payload is None:
        payload = _backtest(mkt, reg, defense)
        _save_heavy(key, payload)
    per: dict[str, dict] = {}
    for sid, d in payload["per"].items():
        if sid not in STRATEGIES:  # 策略被删掉后旧缓存里的条目
            continue
        per[sid] = dict(spec=STRATEGIES[sid], use_reg=d["use_reg"], m=d["m"],
                        curve=d["curve"], history=d["history"],
                        prev=pd.Series(d["prev"]).reindex(codes).fillna(0.0))
    return {"mkt": mkt, "reg": reg, "per": per, "benchmark": payload["benchmark"]}


def _light(calc: dict, codes: list[str], entry: dict, live: bool,
           hs: dict, quotes: dict, added: list[str]) -> dict:
    """每请求轻计算: 实时价试算信号 / 防守状态 / 行情价签; 回测部分直接用缓存。"""
    mkt, reg, per = calc["mkt"], calc["reg"], calc["per"]
    if live:
        px = {c: (quotes.get(c) or {}).get("price") or latest_price(c) for c in codes}
        mkt_sig, tdate = market_with_live(mkt, px)
    else:
        mkt_sig, tdate = mkt, mkt.close.index[-1]

    def item(c: str, w: float) -> dict:
        q = quotes.get(c) or {}
        return {"code": c, "name": NAMES.get(c, c), "weight": round(w, 4),
                "price": round(float(q.get("price", latest_price(c))), 4),
                "entry": entry.get(c, ""), "link": futunn_link(c)}

    strategies = {}
    for sid, d in per.items():
        spec, prev = d["spec"], d["prev"]
        p = spec.params
        use_reg = d["use_reg"]
        if live:
            regime_off = use_reg and bool(hs["defense"])
        else:
            regime_off = use_reg and not bool(reg.loc[tdate])
        target, rank_rows = fresh_target(mkt_sig, spec, tdate, regime_off=regime_off, prev=prev)
        turnover = sum(abs(target[c] - float(prev.get(c, 0.0))) for c in codes)
        trade = turnover > p["no_trade_band"]

        # 最新信号试算组合 (未过换手阈值时只是参考, 不代表应立即调仓)
        signal_items = [item(c, w) for c, w in target.items() if w > 0]
        # 可执行答案: 触发调仓 → 试算组合; 未触发 → 维持模型现仓 (与回测状态机一致)
        exec_w = target if trade else prev
        target_items = [item(c, float(exec_w[c])) for c in codes if exec_w[c] > 0]
        prev_items = [item(c, float(prev[c])) for c in codes if prev[c] > 0]

        # 信号排名前 5: 在目标组合内的行 = 标准仓位 (绿勾), 其余为候补
        rank = []
        for c, sc_ in rank_rows:
            it = item(c, float(target.get(c, 0.0)))
            it.update(score=num(sc_, 4), picked=bool(target.get(c, 0.0) > 0))
            rank.append(it)
        if regime_off or not rank:
            safe_it = item(spec.safe, float(target.get(spec.safe, 1.0)))
            safe_it.update(score=None, picked=bool(target.get(spec.safe, 0.0) > 0))
            rank = [x for x in rank if x["code"] != spec.safe]  # 与防守仓行去重
            if regime_off:
                rank.insert(0, safe_it)
            else:
                rank.append(safe_it)

        safe_name = NAMES.get(spec.safe, spec.safe)
        if spec.defense_mode == "always":
            dnote = (f"防守中 · 停泊{safe_name}" if regime_off else "进攻中")
        else:
            dnote = (f"防守腿已切{safe_name}" if (use_reg and regime_off)
                     else "防守腿触发中(当前未启用自动切换)" if hs["defense"]
                     else "市场在均线上方, 进攻状态")

        strategies[sid] = {
            "id": sid, "label": spec.label,
            "defense": {"enabled": use_reg, "toggleable": spec.toggleable,
                        "hs300": hs["close"], "ma60": hs["ma"], "state": hs["state"],
                        "active": regime_off, "note": dnote,
                        "safe_name": safe_name},
            "target": target_items, "rank": rank, "signal": signal_items,
            "trade": trade, "prev": prev_items, "turnover": round(turnover, 4),
            "backtest": {"total": round(d["m"]["total"], 4),
                         "cagr": round(d["m"]["cagr"], 4), "sharpe": round(d["m"]["sharpe"], 3),
                         "mdd": round(d["m"]["mdd"], 4), "trades_yr": d["m"]["trades"]},
            "curve": d["curve"],
            "history": d["history"],
        }

    src_live = bool(live and _in_trading_hours())  # 仅盘中标"实时测算", 其余按日线口径标注
    return {
        "as_of": (f"{datetime.now():%Y-%m-%d %H:%M}" if src_live else tdate.date().isoformat()),
        "source": "live" if src_live else "close",
        "data_as_of": str(mkt.close.index[-1].date()),
        "active": "high52",
        "strategies": strategies,
        "benchmark": calc["benchmark"],
        "pool_note": (f"已自动并入 {', '.join(added)}" if added else ""),
    }


async def run_strategy(pool: list[dict], defense: bool = False, live: bool | None = None) -> dict:
    """pool: [{code, entry?}] → 本周目标 + 换仓历史 + 回测摘要。
    live=None 自适应: 交易日 (快照属于今天) 用实时价并入日线矩阵试算 (追加/替换末行),
    非交易时间自动回日线口径 — 两者数字一致, 无需用户区分。live=True/False 可强制。"""
    codes = [p["code"] for p in pool]
    missing = [c for c in codes if not (DATA / f"{c}.csv").exists()]
    if missing:
        raise HTTPException(400, f"以下代码缺少数据, 请先在备选池中拉取: {', '.join(missing)}")
    for req_safe in SAFE_CODES:  # 各策略的防守仓始终在池内
        if req_safe not in codes:
            codes = codes + [req_safe]
    entry = {c: (next((p.get("entry") for p in pool if p["code"] == c), None) or default_entry(c))
             for c in codes}
    added = [c for c in SAFE_CODES if c not in [p["code"] for p in pool]]

    await ensure_index()
    maybe_refresh_data()
    quotes = await load_quotes(force=bool(live) or _in_trading_hours())
    if live is None:  # 自适应: 快照属于今天 → 今天有行情, 实时价有增量信息
        today_s = date.today().isoformat()
        use_live = any((q or {}).get("day") == today_s for q in quotes.values())
    else:
        use_live = bool(live)
    hs = await live_hs300_status() if use_live else hs300_status()

    key = _calc_key(codes, entry, defense)
    with LOCK:
        if _calc_cache["key"] != key:
            _calc_cache["key"] = key
            _calc_cache["val"] = _calc(codes, entry, defense, key)
        return _light(_calc_cache["val"], codes, entry, use_live, hs, quotes, added)

# ---------------------------------------------------------------- 对外接口 (供 web/api 调用)

async def build_instruments() -> list[dict]:
    """标的目录: 现价 / 涨跌幅 / 成交额 / 分时路径 + 52周新高 / RAM / 120日动量。"""
    await ensure_initial_data()
    missing = [code for code in POOL_CODES
               if not (DATA / f"{code}_raw.csv").exists()]
    if missing:
        raise HTTPException(503, f"行情数据初始化失败，缺少：{', '.join(missing)}")
    maybe_refresh_data()
    quotes = await load_quotes()
    out = []
    for code in POOL_CODES:
        try:
            raw = pd.read_csv(DATA / f"{code}_raw.csv", parse_dates=["date"]).set_index("date")
            st = code_stats(code)
            q = quotes.get(code) or _fallback_quote(code)
            out.append({
                "code": code, "name": NAMES.get(code, code),
                "category": CATEGORY.get(code, ""),
                "price": round(q["price"], 4),
                "change_pct": round(q["change_pct"], 4),
                "amount": q["amount"],
                "spark": q["spark"],
                "pre": q.get("pre", 0.0),
                "day": q.get("day", ""),
                "first": raw.index[0].date().isoformat(),
                "last": raw.index[-1].date().isoformat(),
                "high52": num(st["high52"]), "ram30": num(st["ram30"]),
                "mom120": num(st["mom120"]),
                "link": futunn_link(code),
            })
        except Exception as exc:
            raise HTTPException(503, f"{NAMES.get(code, code)} 数据读取失败：{type(exc).__name__}") from exc
    return out
