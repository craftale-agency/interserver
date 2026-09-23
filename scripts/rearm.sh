#!/usr/bin/env bash
# rearm.sh — recover the inter-session listener after Claude Code /compact,
# session restart, or reboot. Production-tested recovery for the #1 failure:
# the `.session` state file references a dead listener, so send.py fails with
# "not connected" or hello errors (unknown_peer / unauthorized).
#
# What it does:
#   1. For each ~/.claude/data/inter-session/clients/<pid>.session:
#      - kill the listener_pid if alive (it belongs to a dead CC session)
#      - remove the stale <pid>.session and <pid>.lock
#   2. Respawn a fresh listener:
#      nohup python3 <skill>/bin/client.py --name <NAME> \
#        --idle-shutdown-minutes 525600
#
# Usage:
#   rearm.sh <name> [skill_bin_dir]
#   NAME        listener name (e.g. "server-a", "server-b")
#   SKILL_BIN   optional: plugin bin/ dir (defaults to the newest
#               ~/.claude/plugins/cache/inter-session/*/*/skills/inter-session/bin)
#
# Notes:
# - client.py re-execs under the inter-session venv automatically if present.
# - The new listener is keyed to THIS script's CC-session ancestor if run
#   from inside a Claude Code session (parent-PID walk in shared.py); run it
#   from the session you want re-armed.
# - <pid>.lock removal is safe here because we killed the holder first; the
#   plugin itself deliberately never unlinks locks (TOCTOU), but a stale
#   flock with a dead holder is released by the kernel anyway — removing the
#   file just keeps the dir tidy.
set -euo pipefail

NAME="${1:?usage: rearm.sh <listener-name> [skill_bin_dir]}"
DATA_DIR="${INTER_SESSION_DATA_DIR:-$HOME/.claude/data/inter-session}"

if [[ $# -ge 2 ]]; then
    BIN="$2"
else
    BIN="$(ls -d "$HOME"/.claude/plugins/cache/inter-session/*/*/skills/inter-session/bin 2>/dev/null | sort -V | tail -1)"
fi
[[ -d "$BIN" ]] || { echo "rearm: plugin bin dir not found; pass it explicitly" >&2; exit 1; }
[[ -x "$BIN/client.py" ]] || { echo "rearm: $BIN/client.py missing" >&2; exit 1; }

echo "rearm: cleaning stale listener state in $DATA_DIR/clients/"
shopt -s nullglob
for sess in "$DATA_DIR"/clients/*.session; do
    pid="${sess##*/}"; pid="${pid%.session}"
    lpid="$(python3 -c "import json,sys;print(json.load(open('$sess')).get('listener_pid',0))" 2>/dev/null || echo 0)"
    if (( lpid > 0 )) && kill -0 "$lpid" 2>/dev/null; then
        echo "rearm: killing stale listener pid $lpid (CC session $pid gone)"
        kill "$lpid" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$sess" "$DATA_DIR/clients/$pid.lock"
    echo "rearm: removed state for CC session $pid"
done

echo "rearm: respawning listener '$NAME' from $BIN"
nohup python3 "$BIN/client.py" --name "$NAME" --idle-shutdown-minutes 525600 \
    >> "$DATA_DIR/monitor.log" 2>&1 & disown

sleep 2
if ls "$DATA_DIR"/clients/*.session >/dev/null 2>&1; then
    echo "rearm: OK — new listener state written."
else
    echo "rearm: WARNING — no .session appeared; check $DATA_DIR/monitor.log" >&2
    exit 1
fi
