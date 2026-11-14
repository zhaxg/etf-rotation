#!/bin/sh
# 启动 ETF 轮动 Web: http://<本机IP>:9696
cd "$(dirname "$0")/.." || exit 1
exec /tmp/etf-backtest-venv/bin/python -m uvicorn web.main:app --host 0.0.0.0 --port 9696
