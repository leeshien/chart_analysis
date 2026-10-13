#!/bin/bash
cd "$(dirname "$0")/.."
set -a; . ./.env >/dev/null 2>&1; set +a
PY=~/anaconda3/envs/py312/bin/python
TICKERS=$(cat state/need_levels.txt)
for tf in daily weekly; do
  echo "[$(date +%H:%M:%S)] submitting $tf"
  $PY refresh_levels.py --timeframe $tf --submit --tickers $TICKERS
done
