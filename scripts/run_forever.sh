#!/bin/bash
# Watchdog: keep the dashboard server alive forever.
#
# The simulation is a fully local application — it does not depend on Claude
# or any usage limits once built.  This loop restarts the server within 5
# seconds of any crash or accidental kill, so the operations centre stays up.
#
#   ./scripts/run_forever.sh            # foreground
#   nohup ./scripts/run_forever.sh &    # survive the terminal closing
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/server.log"
cd "$ROOT"
while true; do
  echo "[watchdog] $(date '+%F %T') starting dashboard on :8642" >> "$LOG"
  "$ROOT/.venv/bin/python" run_live.py --port 8642 >> "$LOG" 2>&1
  echo "[watchdog] $(date '+%F %T') server exited — restarting in 5 s" >> "$LOG"
  sleep 5
done
