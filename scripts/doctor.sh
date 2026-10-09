#!/usr/bin/env bash
# doctor.sh — diagnose (+ best-effort repair) the interserver lane on this machine.
# Field-tested on the 2026-10-08/09 federation epic. Output: PASS/FAIL + fixes.
set -uo pipefail

DATA="$HOME/.claude/data/inter-session"
PLUGIN=$(ls -d "$HOME/.claude/plugins/cache/inter-session/inter-session"/*/skills/inter-session 2>/dev/null | sort -V | tail -1)
HUB="${INTERSERVER_HUB:-100.70.94.8}"
PORT=9473
FAIL=0

say() { printf '%s\n' "$*"; }
ok()  { say "PASS: $*"; }
bad() { say "FAIL: $*"; FAIL=$((FAIL+1)); }

# 1. Is anything listening on the bus port?
LISTENER=$(lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null | head -1)
[ -n "$LISTENER" ] || { bad "no listener on $PORT"; }

# 2. Pidfile coherent?
PIDFILE="$DATA/server.$PORT.pid"
if [ ! -s "$PIDFILE" ]; then
  bad "pidfile missing/empty ($PIDFILE)"
  if [ -n "$LISTENER" ]; then
    echo "$LISTENER" > "$PIDFILE"
    printf '{"pid": %s, "host": "127.0.0.1", "port": %s}\n' "$LISTENER" "$PORT" > "$PIDFILE.meta"
    say "  fixed: pidfile rewritten -> $LISTENER"
  fi
else
  PFPID=$(cat "$PIDFILE" 2>/dev/null || echo 0)
  if ! kill -0 "$PFPID" 2>/dev/null; then
    bad "pidfile pid $PFPID dead"
    if [ -n "$LISTENER" ]; then
      echo "$LISTENER" > "$PIDFILE"; printf '{"pid": %s, "host": "127.0.0.1", "port": %s}\n' "$LISTENER" "$PORT" > "$PIDFILE.meta"
      say "  fixed: pidfile -> $LISTENER"
    fi
  elif [ "$PFPID" != "$LISTENER" ]; then
    bad "pidfile pid ($PFPID) != listener ($LISTENER)"
    echo "$LISTENER" > "$PIDFILE"; printf '{"pid": %s, "host": "127.0.0.1", "port": %s}\n' "$LISTENER" "$PORT" > "$PIDFILE.meta"
    say "  fixed: aligned to listener"
  else
    ok "pidfile coherent (pid $PFPID)"
  fi
fi

# 3. Identity: cmdline must include bin/server.py + endpoint
if [ -n "${LISTENER:-}" ]; then
  CMD=$(ps -o command= -p "$LISTENER" 2>/dev/null || true)
  case "$CMD" in
    *bin/server.py*) ok "listener path passes identity (bin/server.py)" ;;
    *) bad "listener cmdline lacks bin/server.py: ${CMD:0:80}" ;;
  esac
  case "$CMD" in
    *127.0.0.1*) ok "endpoint host in cmdline" ;;
    *) bad "cmdline missing 127.0.0.1 (legacy identity check needs it)" ;;
  esac
fi

# 4. Upstream actually speaks the bus protocol? (stale-lane detector)
#    A live lane's upstream completes a WebSocket handshake; a stale one
#    returns nothing/garbage. Cheap probe: an HTTP request must NOT hang
#    forever and the lane must hold the connection open.
if [ -n "${LISTENER:-}" ]; then
  if echo -e "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" \
     | nc -w 4 127.0.0.1 $PORT >/dev/null 2>&1; then
    ok "lane accepts and answers on $PORT"
  else
    bad "lane stale (accepts TCP, upstream dead) — restarting forwarder"
    kill "$LISTENER" 2>/dev/null
    sleep 1
    nohup python3 "$DATA/bin/server.py" 127.0.0.1 $PORT "$HUB" $PORT >> "$HOME/.inter-session/forwarder.log" 2>&1 & disown
    sleep 2
    NEW=$(lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null | head -1)
    [ -n "$NEW" ] && echo "$NEW" > "$PIDFILE" && printf '{"pid": %s, "host": "127.0.0.1", "port": %s}\n' "$NEW" "$PORT" > "$PIDFILE.meta" && say "  fixed: lane restarted (pid $NEW)"
  fi
fi

# 5. Hub reachable?
if nc -z -w 4 "$HUB" $PORT 2>/dev/null; then ok "hub $HUB:$PORT reachable"
else bad "hub $HUB:$PORT NOT reachable (tailscale up? hub dead?)"; fi

# 6. Wrapper still installed in the plugin path? (survives plugin updates?)
if [ -n "$PLUGIN" ] && grep -q "runpy" "$PLUGIN/bin/server.py" 2>/dev/null; then
  ok "plugin server.py is the federation wrapper"
else
  bad "plugin server.py is NOT the wrapper (plugin updated?) — re-apply from scripts/wrapper-server.py"
fi

# 7. Token present?
[ -f "$DATA/token" ] && ok "token file present" || bad "token missing — enroll from the hub"

say "---"
[ "$FAIL" -eq 0 ] && say "VERDICT: healthy" || say "VERDICT: $FAIL issue(s) — see fixes above (some auto-applied)"
exit "$FAIL"
