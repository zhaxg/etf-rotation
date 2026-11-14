from __future__ import annotations

import csv
import asyncio
import json
from pathlib import Path

from pytdxdata import TdxData
from pytdxdata.models import KlinePeriod, Adjust


ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402  (脚本入口: 把项目根加入 sys.path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


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


def main() -> None:
    async def fetch() -> list[dict]:
        summary = []
        async with TdxData() as data:
            for name, code, category, listed, turnover, indicator in INSTRUMENTS:
                path = DATA_DIR / f"{code}.csv"
                symbol = ("sh" if code.startswith(("5", "6")) else "sz") + code
                try:
                    rows = await data.get_bars(
                        symbol,
                        KlinePeriod.DAY,
                        start=0,
                        count=5000,
                        adjust=Adjust.QFQ,
                    )
                    error = ""
                except Exception as exc:
                    rows = []
                    error = f"{type(exc).__name__}: {exc}"
                records = [
                    {
                        "date": f"{row.year:04d}-{row.month:02d}-{row.day:02d}",
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": row.vol,
                        "amount": row.amount,
                    }
                    for row in rows
                ]
                if records:
                    with path.open("w", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
                        writer.writeheader()
                        writer.writerows(records)
                summary.append({
                    "name": name,
                    "code": code,
                    "category": category,
                    "listed": listed,
                    "turnover_yi": turnover,
                    "indicator_available": indicator,
                    "rows": len(records),
                    "error": error,
                })
                print(json.dumps(summary[-1], ensure_ascii=False))
        return summary

    summary = asyncio.run(fetch())
    (DATA_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
