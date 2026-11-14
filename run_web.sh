#!/bin/sh
# 网页服务看护: uvicorn 退出 (改代码手动重启 / 异常崩溃) 后 2 秒自动拉起
# 用法: setsid nohup ./run_web.sh > /dev/null 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
PY=/tmp/etf-backtest-venv/bin/python
while true; do
  "$PY" -m uvicorn web.main:app --host 0.0.0.0 --port 9696 >> /tmp/etf-server.log 2>&1
  echo "[supervisor $(date '+%F %T')] uvicorn 退出, 2 秒后重启" >> /tmp/etf-server.log
  sleep 2
done