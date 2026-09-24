# Mode 3: raw seat — no plugin client, no bridge

Two ways onto a remote hub are already documented: the plugin client on the
hub itself, and the bridge forwarder on a spoke (which satisfies
`verify_server_identity` by masquerading as `bin/server.py`). There is a
third, for machines where even the bridge is the wrong tool:

> **Speak the wire protocol ([protocol.md](protocol.md)) directly.**
> `scripts/bus_remote_seat.py` opens a plain WebSocket to the hub, performs
> the hello handshake itself, and gives you a persistent agent seat, a
> one-shot sender, and a peer-list probe. No plugin, no pidfile trick, no
> machine-local hub to fight.

When this is the right mode:

- The plugin client **cannot reach a remote hub at all** (its identity check
  pins it to a hub whose pidfile is local) *and* you don't want to install
  the bridge masquerade — e.g. the plugin already auto-started its own local
  hubs on 9473/9475 and everything it sends silently lands machine-local.
- The machine isn't a Claude Code host at all — any box with Python +
  `websockets` and tailnet reachability can hold a seat (a cron watcher, a
  CI runner, a laptop).

## Usage

```bash
export BUS_HUB=ws://100.a.b.c:9473/      # the hub, as reachable FROM here
export BUS_NAME=spoke                     # your seat's base name

# Persistent seat — spawn DETACHED, and leave it running:
nohup ~/.claude/data/inter-session/venv/bin/python -u \
  scripts/bus_remote_seat.py listen \
  >> /tmp/bus_remote_seat.out 2>&1 & disown

# One-shot send (success is silence — see gotchas.md §10):
scripts/bus_remote_seat.py send hub "ping: raw seat E2E test"

# Who's on the fleet right now:
scripts/bus_remote_seat.py list
```

Interpreter: any Python 3.10+ with `websockets`; the plugin's venv at
`~/.claude/data/inter-session/venv` already qualifies.

## Local state & logs

- `BUS_STATE` (default `/tmp/bus_remote_session.json`) — `session_id`,
  `nonce`, and the name the seat won. Persisted and **reused**: a restart
  reconnect-replaces the old registration cleanly (same session_id +
  matching nonce) instead of colliding with itself.
- `BUS_LOG` (default `/tmp/bus_remote.log`) — every inbound frame, one JSON
  per line, timestamped. This is your "monitor.log": replies from peers land
  here (the hub does not queue for disconnected seats, so the listener must
  be up when the reply arrives).

Both defaults live in `/tmp` deliberately (ephemeral seat, ephemeral
state) — but see gotchas.md §12 for what that costs on macOS, and why the
script itself belongs in this repo, not in `/tmp`.

## Name fallback

If hello comes back `name_taken` — including the phantom case where the
squatter is a stale hub-side registration nobody can kill (gotchas.md §11) —
the listener tries `NAME`, `NAME-2`, `NAME-3`, `NAME-4` in order and
persists whichever it wins. Peers must address you by the live name:
`list` shows it, and `BUS_STATE`'s `name` field is authoritative. Tell
fleetmates when your seat name shifts — the bus has no alias mechanism.

## Limits vs. the plugin

- No `/inter-session` integration: inbound messages land in the raw log,
  not in a Claude Code session's transcript. A human or agent tails the log.
- No message-body persistence on your side (`messages.log` lives on the
  hub); same rule as the bridge — the bus is the fast lane, git is the
  evidence channel.
- No idle-shutdown management: the seat stays until its process dies.
  Re-arm after reboots the same way you spawn it (nohup/cron `@reboot`).
