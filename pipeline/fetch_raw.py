"""取备选池全部标的的不复权日线 + XDXR 分红送配数据 (用于重建全收益序列)。

抓取结果先校验、再与本地已有历史合并、最后原子落盘。行情源偶发返回
`0000-00-00` / 负价格 / 分页缺失，绝不能用它覆盖可用数据。
"""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
import json
import math
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data_tr"

from universe import DEFAULT_CODES as CODES  # noqa: E402  当前备选池全量

FIELDS = ["date", "open", "high", "low", "close", "volume", "amount"]
MIN_DATE = date(1990, 1, 1)


def _parse_date(value: object) -> date | None:
    try:
        d = date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None
    if d < MIN_DATE or d > date.today() + timedelta(days=1):
        return None
    return d


def rows_to_records(rows: list) -> list[dict]:
    """行情对象 → CSV 行。日期字符串由字段拼接，不依赖对象自带格式化。"""
    return [{"date": f"{r.year:04d}-{r.month:02d}-{r.day:02d}",
             "open": r.open, "high": r.high, "low": r.low,
             "close": r.close, "volume": r.vol, "amount": r.amount}
            for r in rows]


def _valid_record(r: dict) -> bool:
    if _parse_date(r.get("date")) is None:
        return False
    try:
        o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
        v, a = float(r["volume"]), float(r["amount"])
    except (KeyError, TypeError, ValueError):
        return False
    if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
        return False
    if h < l or not math.isfinite(v) or v < 0 or not math.isfinite(a) or a < 0:
        return False
    return True


def validate_records(recs: list[dict], *, min_rows: int = 2) -> None:
    """严格校验新抓取的数据；任何异常都抛错，让调用方保留旧文件。"""
    if len(recs) < min_rows:
        raise ValueError(f"日线只有 {len(recs)} 行, 少于 {min_rows}")
    prev: date | None = None
    for r in recs:
        if not _valid_record(r):
            raise ValueError(f"非法日线行: {r}")
        d = _parse_date(r["date"])
        if prev is not None and d <= prev:
            raise ValueError(f"日期未严格递增: {prev} → {d}")
        prev = d
    last = _parse_date(recs[-1]["date"])
    if last is None or (date.today() - last).days > 14:
        raise ValueError(f"最新日线过旧: {recs[-1]['date']}")


def _load_existing(path: Path) -> list[dict]:
    """读取已有 CSV；坏行直接丢弃，损坏文件视为空。"""
    if not path.exists():
        return []
    try:
        with path.open(newline="") as h:
            rows = list(csv.DictReader(h))
    except (OSError, csv.Error):
        return []
    out = []
    for r in rows:
        if _valid_record(r):
            out.append({k: r.get(k, "") for k in FIELDS})
    out.sort(key=lambda r: _parse_date(r["date"]))
    return out


def merge_records(*groups: list[dict]) -> list[dict]:
    """按日期合并，后传入的组优先；结果按日期升序。"""
    by_date: dict[date, dict] = {}
    for group in groups:
        for r in group:
            d = _parse_date(r.get("date"))
            if d is not None and _valid_record(r):
                by_date[d] = {k: r.get(k, "") for k in FIELDS}
    return [by_date[d] for d in sorted(by_date)]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as h:
            h.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_csv_atomic(path: Path, recs: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(recs)
    _atomic_write(path, buf.getvalue())


def write_bundle(code: str, recs: list[dict], events: list[dict]) -> None:
    """raw / xdxr 原子落盘后重建 TR。任一步失败都保留上一版可用的 TR。"""
    from pipeline import build_tr

    write_csv_atomic(DATA_DIR / f"{code}_raw.csv", recs)
    _atomic_write(DATA_DIR / f"{code}_xdxr.json",
                  json.dumps(events, ensure_ascii=False, indent=1))
    build_tr.build(code, DATA_DIR)


async def fetch_bundle(data, code: str) -> tuple[list[dict], list[dict]]:
    """网络取数 + 校验 + 与本地历史合并；不落盘。

    返回 (merged_recs, events)。新数据必须覆盖到已有数据的最后一天，
    分页缺失时宁可报错也不缩短历史。
    """
    from pytdxdata.models import KlinePeriod, Adjust

    symbol = ("sh" if code.startswith(("5", "6")) else "sz") + code
    rows = await data.get_bars(symbol, KlinePeriod.DAY, start=0,
                               count=5000, adjust=Adjust.NONE)
    fresh = rows_to_records(rows)
    validate_records(fresh)

    events = [dataclasses.asdict(x) for x in await data.get_xdxr(symbol)]
    if not isinstance(events, list):
        raise ValueError("XDXR 返回格式异常")

    existing = _load_existing(DATA_DIR / f"{code}_raw.csv")
    merged = merge_records(existing, fresh)
    old_last = _parse_date(existing[-1]["date"]) if existing else None
    new_last = _parse_date(merged[-1]["date"]) if merged else None
    if old_last and (new_last is None or new_last < old_last):
        raise ValueError(f"新数据比本地旧: {old_last} → {new_last}")
    return merged, events


async def fetch_one(data, code: str) -> bool:
    """拉取单个标的；失败时保留原有文件，避免部分刷新破坏可用数据。"""
    merged, events = await fetch_bundle(data, code)
    write_bundle(code, merged, events)
    print(f"{code}: bars={len(merged)} xdxr={len(events)} "
          f"(除权除息 {sum(1 for e in events if e.get('category') == 1)})")
    return True


async def ensure_data(codes: list[str] | None = None,
                      required: list[str] | None = None,
                      force: bool = False) -> bool:
    """补齐缺失的历史数据与 TR 序列。

    required 中的代码是运行必需项；任一必需代码失败时返回 False，
    不会阻塞服务启动。其余代码继续尝试补齐，尽量让页面展示已有部分。
    force=True 时强制重抓所有标的 (仍会校验、合并, 不会缩短历史)。
    """
    from pytdxdata import TdxData

    targets = list(codes if codes is not None else CODES)
    required_codes = set(required or [])
    for code in sorted(required_codes):
        if code not in targets:
            targets.append(code)
    if not targets:
        return True

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    failed_required: set[str] = set()
    async with TdxData() as data:
        for code in targets:
            raw_file = DATA_DIR / f"{code}_raw.csv"
            tr_file = DATA_DIR / f"{code}.csv"
            if not force and raw_file.exists() and tr_file.exists():
                continue
            try:
                if not await fetch_one(data, code):
                    if code in required_codes:
                        failed_required.add(code)
                    continue
            except Exception as exc:
                if code in required_codes:
                    failed_required.add(code)
                print(f"{code}: 初始化补齐失败 ({type(exc).__name__}: {exc})")
    return not failed_required


def main() -> None:
    force = "--force" in sys.argv[1:]
    ok = asyncio.run(ensure_data(required=list(CODES), force=force))
    if not ok:
        raise SystemExit("初始化失败：至少一个必需标的的数据未能补齐")


if __name__ == "__main__":
    main()
