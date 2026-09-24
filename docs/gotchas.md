# Gotchas (all learned in production)

Each entry: **symptom → root cause → fix**.

## 1. After `/compact` or session restart, sends fail

- **Symptom**: `send.py` exits 1 with `not connected; run /inter-session in
  this Claude Code session first`, or `hello error: unknown_peer …` /
  `unauthorized "stale listener state; reconnect"`.
- **Root cause**: the CC main process died; `clients/<pid>.session` still
  names a listener whose session_id is no longer registered on the server (or
  whose listener process lingers keyed to a dead ppid). The server correctly
  rejects the control-role hello for a dead `for_session`.
- **Fix**: kill the stale listener process if alive, remove
  `clients/<pid>.session` and `clients/<pid>.lock`, respawn
  `nohup python3 <bin>/client.py --name <name> --idle-shutdown-minutes 525600`
  from the plugin skill dir — automated by `scripts/rearm.sh`. (send.py
  auto-unlinks mild staleness, but you still need a fresh listener.)

## 2. Truncated-message "cont" pointer may dangle

- **Symptom**: monitor.log line
  `[inter-session msg=X cont] full text N bytes at …/messages.log`, but
  `messages.log` doesn't exist on this host; `grep` finds nothing.
- **Root cause**: `messages.log` is written by the **server-side** of the
  bus. On a bridged (spoke) host there is no server, so full bodies live
  only on the hub — and rotation/loss means even there it's best-effort.
- **Fix / rule**: never rely on the bus for durable evidence. Anything that
  must survive goes to the shared git repo; re-ask the peer to re-send if a
  body is lost.

## 3. "server identity check failed" on the spoke

- **Symptom**: client prints
  `[inter-session] server identity check failed (port 9473 is held by
  something that isn't bin/server.py); refusing to connect`.
- **Root cause**: `verify_server_identity()` requires the listener's pidfile
  to exist, its pid to be alive, its meta to match host:port, and the
  process cmdline to contain `bin/server.py`. A bridge installed anywhere
  else, or a stale pidfile after a crash, fails closed.
- **Fix**: install the bridge exactly at
  `~/.claude/data/inter-session/bin/server.py`, ensure it wrote
  `server.<port>.pid` + `.pid.meta`, and that a dead pidfile from a crashed
  process is removed before restart. This identity trick is *the* key to
  cross-server operation without forking the plugin.

## 4. Bridge dead after reboot

- **Symptom**: spoke sessions loop "connect failed" (or nothing connects);
  `server.9473.pid` points at a nonexistent pid.
- **Root cause**: the forwarder is a plain `nohup` process — nothing
  re-launches it at boot.
- **Fix**: re-arm line —
  `nohup python3 ~/.claude/data/inter-session/bin/server.py 127.0.0.1 9473 100.a.b.c 9474 >> /tmp/bus-bridge.log 2>&1 & disown`
  (cron `@reboot` or a systemd user unit to make it automatic).

## 5. `unauthorized: bad token` on hello

- **Symptom**: `[inter-session] hello rejected: unauthorized bad token` (seen
  in monitor.log) or send.py's hello error.
- **Root cause**: the two hosts' `~/.claude/data/inter-session/token` files
  differ (fresh token generated independently, or a rotation on one side).
  Cross-server fleets share ONE token.
- **Fix**: redo the secure one-shot token handover (runbook §pairing) so
  both hosts hold the same token, mode 600, never printed or committed.

## 6. Token/secret hygiene during pairing

- **Symptom** (when you get it wrong): token appears in terminal scrollback,
  a chat message, or a commit — it's the fleet's bearer key.
- **Root cause**: convenience.
- **Fix**: one-shot serve over a temporary HTTP endpoint bound to the
  tailnet IP with `timeout`, publish only the sha256 in the evidence repo,
  fetch + `chmod 600` + verify hash, kill the server, `shred -u` any temp
  bundle. Full procedure in the runbook.

## 7. Default idle shutdown kills the bus

- **Symptom**: works great for ten minutes, then silence; server pidfile
  gone.
- **Root cause**: both the real server and the client default to
  `--idle-shutdown-minutes 10` — designed for single-machine interactive
  use. Cross-server gaps exceed that easily.
- **Fix**: launch with `--idle-shutdown-minutes 525600` (a year) on both the
  hub server and the spoke listeners (rearm.sh does this).

## 8. Raw-TCP forwarders can break long-lived/blocking connections

- **Symptom**: bus connections fine, but other services tunneled by the same
  pattern (e.g. Redis BLPOP pollers) stall or die.
- **Root cause**: observed in production — stdlib-python TCP forwarders
  killed long-lived blocking connections; the WebSocket bus survived, but
  don't generalize the bridge to everything.
- **Fix**: keep the bridge for the bus; use `socat` (or socat containers)
  for connection-sensitive protocols.

## 9. Dual-address tailscale/hostaliases confusion

- **Symptom**: bridge can't reach the hub IP that "works from the other
  machine".
- **Root cause**: machines on multiple tailnets have different addresses per
  tailnet (production: one host is `.24` on one tailnet and `.23` on
  another). Bind-side configs and remote-side configs must each use the
  address valid from where they run.
- **Fix**: write down the address map once; bridge targets use the address
  the spoke uses to reach the hub (`100.a.b.c`-style tailnet addresses),
  never a public IP, and never expose 9474 to the internet.

## 10. "Success is silence" mistaken for failure

- **Symptom**: operator re-sends messages because send.py printed nothing.
- **Root cause**: send.py only prints error frames; empty output + exit 0 =
  delivered.
- **Fix**: trust silence; verify delivery at the peer (its monitor.log /
  session stdout), not at the sender.

## 11. `name_taken` from a seat that no longer exists anywhere

- **Symptom**: hello rejected with `name_taken` for a name whose owner
  crashed/rebooted days ago — `lsof`/`pgrep` on the owner's host show
  nothing connected, yet `list` still shows the seat with a `since` date
  from before the outage.
- **Root cause**: the hub tracks liveness by its own WebSocket handler
  loop. A client that dies without a close frame (power loss, hard reboot,
  killed NAT/Tailscale path) leaves a half-open TCP connection; the hub's
  write into it never errors, so the registration is never reaped. Stale
  seats can squat names **indefinitely** — observed with registrations
  weeks old. Unlike §1 (stale *local* listener state you can clean), the
  entry lives in the hub's registry; short of restarting the hub process,
  you cannot evict it.
- **Fix**: don't fight for the name — fall back. `bus_remote_seat.py`
  (mode 3, [raw-seat.md](raw-seat.md)) walks `NAME → NAME-2 → NAME-3 →
  NAME-4` on `name_taken` and persists the winner. Announce the live name
  to fleetmates (the bus has no alias/rename-to mechanism that reaches a
  dead registration); `list` is the source of truth for who holds what.

## 12. `/tmp` purges eat the seat tooling (macOS spoke)

- **Symptom**: days or weeks later, the ad-hoc seat script, its state file,
  and its logs are simply gone; the next session rediscovers the whole
  problem from scratch.
- **Root cause**: the seat was improvised into `/tmp` (and on macOS that
  means periodic cleanup) instead of living in this repo — the evidence
  channel. Only ephemeral *state* belongs in `/tmp`.
- **Fix**: deploy the seat from a clone of this repo
  (`scripts/bus_remote_seat.py`); keep only `BUS_STATE`/`BUS_LOG` under
  `/tmp`. Losing state is cheap — a fresh `session_id` is a fresh seat
  (the old one is stale anyway, see §11) — but losing the *tool* costs a
  rebuild. Re-arm after a purge is: clone, `nohup … listen & disown`, done.

