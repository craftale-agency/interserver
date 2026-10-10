#!/usr/bin/env bash
# notify.sh — send a bus message from ANY process (not just Claude sessions).
#
#   notify.sh "text..." --to <seat>     # direct
#   notify.sh "text..." --all           # broadcast
#
# Gateway pattern: send.py needs a live seat's credentials, so we borrow the
# state of the first healthy monitor on this machine (the message will show
# that seat as sender; the text should say who/what it really is, e.g.
# "from git-hook: ..."). Priority seats: INTERSERVER_GATEWAY_SEAT env.
#
# Use cases: cron briefs, git post-receive hooks, CI, file watchers —
# anything that should ACTIVATE an agent.
set -uo pipefail

DATA="$HOME/.claude/data/inter-session"
BIN=$(ls -d "$HOME"/.claude/plugins/cache/inter-session/inter-session/*/skills/inter-session/bin 2>/dev/null | sort -V | tail -1)

TEXT=""; TO=""; ALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TO="$2"; shift 2 ;;
    --all) ALL=1; shift ;;
    *) TEXT="${TEXT:+$TEXT }$1"; shift ;;
  esac
done
[ -n "$TEXT" ] || { echo "usage: notify.sh <text> --to <seat> | --all" >&2; exit 2; }
[ -n "$TO" ] || [ "$ALL" = "1" ] || { echo "specify --to <seat> or --all" >&2; exit 2; }

# find a live seat state (own gateway seat first, then any healthy monitor)
pick_gateway() {
  local cand key
  for cand in ${INTERSERVER_GATEWAY_SEAT:-} $(ls "$DATA"/clients/*.session 2>/dev/null | sort -r); do
    key="${cand%.session}"; key="${key##*/}"
    case "$cand" in
      *.session) ;;
      *) key="$cand"; cand="$DATA/clients/$cand.session" ;;
    esac
    [ -f "$cand" ] || continue
    lp=$(python3 -c "import json;print(json.load(open('$cand')).get('listener_pid',0))" 2>/dev/null)
    [ -n "$lp" ] && [ "$lp" != "0" ] && kill -0 "$lp" 2>/dev/null && { echo "$key"; return 0; }
  done
  return 1
}

GW=$(pick_gateway) || { echo "notify: no live seat on this machine (watchdog ran?)" >&2; exit 3; }

if [ "$ALL" = "1" ]; then
  INTER_SESSION_PPID_OVERRIDE="$GW" python3 "$BIN/send.py" --all --text "$TEXT"
else
  INTER_SESSION_PPID_OVERRIDE="$GW" python3 "$BIN/send.py" --to "$TO" --text "$TEXT"
fi
# success is silence (send.py contract)
