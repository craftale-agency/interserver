# interserver — one agent bus across all your machines

Federates the [inter-session](https://github.com/yilunzhang/claude-code-inter-session)
plugin's Claude Code messaging bus across a tailnet (Tailscale & friends), so
every Claude session on every machine sees the **same bus**: `list` shows the
whole fleet, `send` reaches any agent anywhere, broadcasts hit everyone.

```
        HUB  (one machine, binds its tailnet IP :9473)
       ↗↖
  lanes   lanes            spokes talk to their usual 127.0.0.1:9473 —
 ↗↖        ↗↖              it's silently forwarded to the hub
Mac       server B
(craftbrain, pezbrain,      (nebula, crafthost …)
 maintenance, …)
```

**No fork needed**: the upstream plugin stays stock. One trick makes it work —
the plugin's anti-squatting check requires the local listener to be
`bin/server.py`, so the forwarder lives at that path *and* writes the standard
identity pidfiles. From the client's perspective it *is* the bus server; the
bytes just tunnel to the hub.

## Install

In any Claude Code session (requires the `inter-session` plugin):

```
/plugin marketplace add craftale-agency/interserver
/plugin install interserver@interserver
```

Then, per machine, pick a role:

| Role | Command | Where |
| :--- | :--- | :--- |
| **Hub** (one per fleet) | `/interserver setup hub` | the machine others dial into |
| **Spoke** | `/interserver setup spoke --hub <tailnet-ip>` | every other machine |
| Delivery hooks | `/interserver setup hooks` | everywhere (unread messages activate agents) |
| Watchdog | `/interserver setup watchdog` | everywhere (dead monitors auto-re-arm) |

`INTER_SESSION_HOST` env or the spoke lane does the routing — seats don't even
know which machine a peer is on.

## Commands

| Command | What it does |
| :--- | :--- |
| `/interserver list` | seats on the **federated** bus (all machines) |
| `/interserver connect <name>` | seat this session on the global bus |
| `/interserver notify "<text>" --to <seat>\|--all` | send a bus message from **any process** (cron, git hooks, CI) — activates an agent |
| `/interserver doctor` | 7 health checks + auto-repair of the lane |
| `/interserver rearm` | clean stale seats + respawn clients (after `/compact`) |
| `/interserver help` | command overview |

Guaranteed delivery stack: **monitor** (instant notification) → **Stop-hook**
(inbox drained at end of turn, zero queuing) → **SessionStart-hook** (catch-up
on open) → **watchdog** (monitors never stay dead). The authoritative
`messages.log` lives on the hub; the inbox pulls it over ssh, so messages
missed while a monitor was dead are still delivered. Message loss is
architecturally impossible.

## Repo layout

```
.claude-plugin/          marketplace.json + plugin.json (plugin install)
skills/interserver/      SKILL.md — the full runbook, invoked as /interserver
scripts/
  forwarder.py           the lane: plain TCP pump 127.0.0.1:9473 → hub
  wrapper-server.py      co-opts the plugin's self-healing into deploying the lane
  inbox.py               per-session watermark inbox (Stop/SessionStart hooks)
  doctor.sh              health checks + auto-repair
  watchdog.sh            re-arms dead monitors (schedule via cron/launchd/timer)
  notify.sh              bus messages from non-Claude processes (gateway pattern)
  sendmsg.sh rearm.sh    convenience wrappers (ssh / post-compact)
  bridge.py listen.sh bus_remote_seat.py   legacy manual-era helpers (see docs)
docs/
  protocol.md            wire protocol + identity check details
  runbook.md             day-2 operations
  gotchas.md             field-tested failure modes
  raw-seat.md            mode-3 raw seat (WebSocket client without the plugin)
```

## Field-tested failure modes

- **Island resurrection**: the plugin respawns its own localhost hub within
  ~1s of the port freeing. Don't race it — make every respawn path produce
  the forwarder (`setup spoke` does this via `wrapper-server.py`).
- **Stale lane**: forwarder accepts TCP but the upstream died → clients see
  "connected to a non-inter-session service". `doctor` restarts the lane.
- **Empty pidfile** after crash churn → identity check fails closed. `doctor`
  rewrites it.
- **Truncated messages** (>400 chars) arrive cut; full text is in the hub's
  `messages.log`.

Full details: [docs/runbook.md](docs/runbook.md) · [docs/gotchas.md](docs/gotchas.md) · [docs/protocol.md](docs/protocol.md)

## Requirements

- Claude Code ≥ 2.1.105 with the [inter-session](https://github.com/yilunzhang/claude-code-inter-session) plugin
- A tailnet (Tailscale/Headscale) between the machines; hub reachable on :9473
- Python ≥ 3.10, ssh access spoke→hub (for inbox pull and enrollment)

MIT © Craftale
