#!/usr/bin/env bash
# listen.sh — spawn the durable raw seat (follow-up to gotchas.md §12)
#
# Wraps scripts/bus_remote_seat.py listen with nohup/disown and the env it
# needs. State and log default to ~/.claude/data/inter-session/seats/<name>.{json,log}
# so they survive /tmp purges; override with BUS_STATE/BUS_LOG if you really
# want ephemeral. Re-run this after a reboot to re-arm the seat.
#
# Usage:   listen.sh <seat-name> [hub-endpoint]
# Example: ./listen.sh craftlead ws://100.70.94.8:9473/
set -euo pipefail

NAME="${1:?usage: listen.sh <seat-name> [hub-endpoint]}"
HUB="${2:-${BUS_HUB:-ws://127.0.0.1:9473/}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY="$HOME/.claude/data/inter-session/venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

STATE="${BUS_STATE:-$HOME/.claude/data/inter-session/seats/${NAME}.json}"
LOG="${BUS_LOG:-$HOME/.claude/data/inter-session/seats/${NAME}.log}"
mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"

BUS_HUB="$HUB" BUS_NAME="$NAME" BUS_STATE="$STATE" BUS_LOG="$LOG" \
  nohup "$PY" -u "$SCRIPT_DIR/bus_remote_seat.py" listen \
  >> "$LOG" 2>&1 & disown

echo "listen: seat '$NAME' → $HUB"
echo "  state: $STATE"
echo "  log:   $LOG (stderr lands here too)"
