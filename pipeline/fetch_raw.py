"""取备选池全部标的的不复权日线 + XDXR 分红送配数据 (用于重建全收益序列)。"""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import json
from pathlib import Path

from pytdxdata import TdxData
from pytdxdata.models import KlinePeriod, Adjust

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  (脚本入口: 把项目根加入 sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data_tr"
DATA_DIR.mkdir(exist_ok=True)

from universe import DEFAULT_CODES as CODES  # 当前备选池全量


def main() -> None:
    async def fetch() -> None:
        async with TdxData() as data:
            for code in CODES:
                symbol = ("sh" if code.startswith(("5", "6")) else "sz") + code
                try:
                    rows = await data.get_bars(symbol, KlinePeriod.DAY, start=0,
                                               count=5000, adjust=Adjust.NONE)
                    recs = [{"date": f"{r.year:04d}-{r.month:02d}-{r.day:02d}",
                             "open": r.open, "high": r.high, "low": r.low,
                             "close": r.close, "volume": r.vol, "amount": r.amount}
                            for r in rows]
                    err1 = ""
                except Exception as exc:
                    recs, err1 = [], f"{type(exc).__name__}: {exc}"
                if recs:
                    with (DATA_DIR / f"{code}_raw.csv").open("w", newline="") as h:
                        w = csv.DictWriter(h, fieldnames=list(recs[0].keys()))
                        w.writeheader()
                        w.writerows(recs)
                try:
                    xdxr = await data.get_xdxr(symbol)
                    evs = [dataclasses.asdict(x) for x in xdxr]
                    err2 = ""
                except Exception as exc:
                    evs, err2 = [], f"{type(exc).__name__}: {exc}"
                (DATA_DIR / f"{code}_xdxr.json").write_text(
                    json.dumps(evs, ensure_ascii=False, indent=1), encoding="utf-8")
                n1 = sum(1 for e in evs if e.get("category") == 1)
                print(f"{code}: bars={len(recs)} xdxr={len(evs)} (除权除息 {n1}) "
                      f"{err1} {err2}")

    asyncio.run(fetch())


if __name__ == "__main__":
    main()
