"""取备选池全部标的的不复权日线 + XDXR 分红送配数据 (用于重建全收益序列)。"""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  (脚本入口: 把项目根加入 sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data_tr"

from universe import DEFAULT_CODES as CODES  # 当前备选池全量


async def fetch_one(data, code: str) -> bool:
    """拉取单个标的；失败时保留原有文件，避免部分刷新破坏可用数据。"""
    from pytdxdata.models import KlinePeriod, Adjust

    symbol = ("sh" if code.startswith(("5", "6")) else "sz") + code
    rows = await data.get_bars(symbol, KlinePeriod.DAY, start=0,
                               count=5000, adjust=Adjust.NONE)
    if not rows:
        raise ValueError("没有取到日线")
    recs = [{"date": f"{r.year:04d}-{r.month:02d}-{r.day:02d}",
             "open": r.open, "high": r.high, "low": r.low,
             "close": r.close, "volume": r.vol, "amount": r.amount}
            for r in rows]
    with (DATA_DIR / f"{code}_raw.csv").open("w", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=list(recs[0].keys()))
        writer.writeheader()
        writer.writerows(recs)

    xdxr = await data.get_xdxr(symbol)
    events = [dataclasses.asdict(x) for x in xdxr]
    (DATA_DIR / f"{code}_xdxr.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{code}: bars={len(recs)} xdxr={len(events)} "
          f"(除权除息 {sum(1 for e in events if e.get('category') == 1)})")
    return True


async def ensure_data(codes: list[str] | None = None,
                      required: list[str] | None = None) -> bool:
    """补齐缺失的历史数据与 TR 序列。

    required 中的代码是运行必需项；任一必需代码失败时返回 False，
    不会阻塞服务启动。其余代码继续尝试补齐，尽量让页面展示已有部分。
    """
    from pytdxdata import TdxData
    from pipeline import build_tr

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
            if raw_file.exists() and tr_file.exists():
                continue
            try:
                if not await fetch_one(data, code):
                    if code in required_codes:
                        failed_required.add(code)
                    continue
                build_tr.build(code)
            except Exception as exc:
                if code in required_codes:
                    failed_required.add(code)
                print(f"{code}: 初始化补齐失败 ({type(exc).__name__}: {exc})")
    return not failed_required


def main() -> None:
    ok = asyncio.run(ensure_data(required=list(CODES)))
    if not ok:
        raise SystemExit("初始化失败：至少一个必需标的的数据未能补齐")



if __name__ == "__main__":
    main()
