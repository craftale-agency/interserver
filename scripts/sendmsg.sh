#!/usr/bin/env bash
# sendmsg.sh — thin wrapper around the inter-session plugin's send.py.
#
# SUCCESS IS SILENCE: send.py opens a WebSocket, sends
#   {op:"hello", for_session, nonce, token, role:"control"},
# reads the welcome, sends {op:"send"|"broadcast", text, to}, then waits
# 1 second for an error frame. If none arrives, it exits 0 with NO output —
# the server only speaks up on failure. So:
#   - empty stdout + exit 0  = delivered (fire-and-forget; do NOT retry)
#   - anything on stderr     = error; the message tells you what to do
#
# Usage:
#   sendmsg.sh <target-name-or-prefix> <text>     # direct send
#   sendmsg.sh --all <text>                       # broadcast
#   sendmsg.sh --status                           # list connected sessions
set -euo pipefail

BIN="${INTER_SESSION_BIN:-$(ls -d "$HOME"/.claude/plugins/cache/inter-session/*/*/skills/inter-session/bin 2>/dev/null | sort -V | tail -1)}"
[[ -d "$BIN" ]] || { echo "sendmsg: plugin bin dir not found (set INTER_SESSION_BIN)" >&2; exit 1; }

case "${1:-}" in
    --all)
        shift
        exec python3 "$BIN/send.py" --all --text "$*"
        ;;
    --status)
        exec python3 "$BIN/list.py"
        ;;
    -*)
        echo "usage: sendmsg.sh <target> <text> | sendmsg.sh --all <text> | sendmsg.sh --status" >&2
        exit 2
        ;;
esac

[[ $# -ge 2 ]] || { echo "usage: sendmsg.sh <target> <text>" >&2; exit 2; }
target="$1"; shift
exec python3 "$BIN/send.py" --to "$target" --text "$*"
