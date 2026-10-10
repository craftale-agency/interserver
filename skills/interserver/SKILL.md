---
name: interserver
description: Federate the inter-session plugin's agent bus across tailnet machines. Use for "/interserver" commands: setup a hub or spoke, doctor a broken lane, rearm after /compact, enroll a new machine. Triggers on fleet/bus/inter-session federation problems across machines.
---

# interserver

Multi-machine federation for the `inter-session` plugin: one bus across the
tailnet instead of one island per machine.

```
HUB (binds tailnet IP :9473)  ←—— spoke lanes (forwarder) ←—— spoke machines
```

Everything below assumes the upstream plugin (`inter-session`) is installed.

## Subcommands

| User input | Action |
| :--- | :--- |
| `/interserver list` | List seats on the federated bus (via the lane — you see ALL machines) |
| `/interserver connect <name>` | Connect this session as a seat (same as upstream connect; the lane does federation) |
| `/interserver help` | Show this command overview |
| `/interserver setup hub` | Make THIS machine the hub |
| `/interserver setup spoke --hub <ip>` | Make THIS machine a spoke of that hub |
| `/interserver setup hooks` | Install the guaranteed-delivery inbox hooks (Stop + SessionStart) |
| `/interserver doctor` | Diagnose + auto-repair the local lane |
| `/interserver rearm` | Clean stale seats + respawn clients (after `/compact`) |

## list — federated seat list

Run the upstream list through the lane:

```
Bash("python3 <upstream-bin>/list.py")
```

`<upstream-bin>` = the inter-session plugin's `skills/inter-session/bin`
directory. On a healthy spoke this returns seats from ALL machines (hub side
included) — that's the federation working. Only local seats = lane problem →
suggest `/interserver doctor`.

## connect — seat on the federated bus

Identical to upstream `/inter-session connect <name>`, but on a spoke it lands
on the global bus automatically (lane or `INTER_SESSION_HOST` does it):

```
Monitor(command="python3 <upstream-bin>/client.py --name <name>",
        description="inter-session messages", persistent=true, timeout_ms=3600000)
```

Validate the name (`^[a-z0-9][a-z0-9-]{0,39}$`); on "another monitor already
running" surface the existing name and stop.

## help

Reply with the subcommand table above, plus one line on architecture:
hub binds the tailnet IP; spokes forward `127.0.0.1:9473` to it; seats are the
agents. Point to `setup` for new machines and `doctor` when messages don't flow.

## setup hub

1. Launch the bus server bound to the **tailnet IP** (not localhost), never idle:
   ```bash
   nohup <venv-python> <plugin>/bin/server.py --host <TAILNET_IP> --port 9473 \
     --idle-shutdown-minutes 525600 >> ~/.inter-session/server.log 2>&1 & disown
   ```
   The hub must run from a path ending in `bin/server.py` (anti-squatting check
   of remote clients). The plugin path satisfies this.
2. Add a restarter loop (`sleep 30 && ... server.py ...`) so the hub survives crashes.
3. Firewall: 9473 must be reachable only inside the tailnet — never bind 0.0.0.0
   on a machine with a public interface.

## setup spoke --hub <ip>

1. **Forwarder as the local server** — install the lane where the plugin expects
   its server, so the plugin's own self-healing deploys the lane instead of an
   island hub (this is the key trick; discovered 2026-10-09):
   ```bash
   mkdir -p ~/.claude/data/inter-session/bin ~/.inter-session
   cp <plugin-root>/scripts/forwarder.py ~/.claude/data/inter-session/forwarder.py
   cp ~/.claude/data/inter-session/forwarder.py ~/.claude/data/inter-session/bin/server.py
   ```
2. **Wrapper the plugin's server.py** (same path trick, co-opts any respawn):
   ```bash
   cp <plugin>/bin/server.py ~/.claude/data/inter-session/plugin-server.py.orig  # backup
   cp <plugin-root>/scripts/wrapper-server.py <plugin>/bin/server.py            # forwards to hub
   ```
   Re-apply after every plugin update (doctor checks this).
3. **Start the lane** (endpoint args MUST appear in the cmdline — the identity
   check requires them when using the legacy pidfile):
   ```bash
   nohup python3 ~/.claude/data/inter-session/bin/server.py 127.0.0.1 9473 <HUB_IP> 9473 \
     >> ~/.inter-session/forwarder.log 2>&1 & disown
   ```
4. **Pidfile** — the forwarder writes it; if empty after a crash, repair:
   ```bash
   echo <listener_pid> > ~/.claude/data/inter-session/server.9473.pid
   printf '{"pid": %s, "host": "127.0.0.1", "port": 9473}\n' <listener_pid> \
     > ~/.claude/data/inter-session/server.9473.pid.meta
   ```
5. **Auto-connect seats to the hub**: edit the plugin's `monitors/monitors.json`:
   `"command": "INTER_SESSION_HOST=<HUB_IP> python3 ${CLAUDE_PLUGIN_ROOT}/skills/inter-session/bin/client.py"`,
   `"when": "always"`. (Direct-to-hub works without a lane too; the lane keeps
   localhost-default clients working.)
6. **Enroll the token** (one shared fleet token): copy the hub's
   `~/.claude/data/inter-session/token` via ssh+scp (mode 600). Compare hashes
   (`md5sum`/`md5 -q`), never print the value.

## setup hooks — guaranteed-delivery inbox

Turns "message that maybe wakes the agent" into "message that definitely
activates the agent". Two Claude Code hooks + one script:

1. **Install the script** to a plugin-update-proof path:
   ```bash
   cp <plugin-root>/scripts/inbox.py ~/.claude/data/inter-session/inbox.py
   ```
2. **Merge the hooks** into `~/.claude/settings.json` (backup first; merge,
   never overwrite existing hook groups):
   ```json
   {"hooks": {
     "Stop":        [{"matcher":"","hooks":[{"type":"command",
        "command":"python3 $HOME/.claude/data/inter-session/inbox.py --hook stop"}]}],
     "SessionStart":[{"matcher":"","hooks":[{"type":"command",
        "command":"python3 $HOME/.claude/data/inter-session/inbox.py --hook start"}]}]
   }}
   ```
3. **Env** (where to read the authoritative log — the HUB writes messages.log,
   spokes' local copy is stale island-era data):
   - `INTERSERVER_HUB_SSH` (default `nebula`) — ssh target of the hub
   - `INTERSERVER_HUB_LOG` (default `/home/ppezz/.claude/data/inter-session/messages.log`)
   - On the hub machine itself: set `INTERSERVER_HUB_SSH=none` (ssh fails fast → falls back to the local, authoritative log)

**How it works:** `inbox.py` keeps a per-session watermark
(`~/.claude/data/inter-session/inbox-<session_id>.watermark`), pulls the hub's
`messages.log` tail over ssh (this also covers messages missed while a monitor
was dead — message loss becomes impossible), and:
- **Stop hook**: unread at end of turn → `{"decision":"block","reason":<msgs>}`
  → the agent does NOT stop, it processes them and then finishes
- **SessionStart hook**: unread accumulated while closed → injected as
  `additionalContext` on open
- **`--hook check`**: manual CLI debugging (`unread for <sid>: N ...`)

Idempotent: the watermark advances on handover, so replays never re-trigger.
Reactions follow the standard protocol (`request:`/`done:`/`question:`).

## doctor

Run `bash <plugin-root>/scripts/doctor.sh` — checks: listener on 9473, pidfile
coherent (non-empty, alive, is the listener), cmdline contains `bin/server.py`
and the endpoint args, upstream lane actually speaks the bus protocol (detects
the "non-inter-session service" stale-lane failure), hub reachable, wrapper
still installed in the plugin path. Auto-repairs what it can (restart lane,
rewrite pidfile, re-apply wrapper from backup).

If `send.py`/`list.py` say **"not connected; run /inter-session first"** from a
plain ssh shell: use the state of an existing session —
`INTER_SESSION_PPID_OVERRIDE=<ppid> python3 .../send.py ...` (valid ppids in
`~/.claude/data/inter-session/clients/*.session`).

## rearm

Run `bash <plugin-root>/scripts/rearm.sh` (or the repo's `scripts/rearm.sh`):
cleans stale seat registrations and respawns clients — needed after `/compact`
on a remote session, and after lane restarts (monitors back off exponentially;
rearm accelerates reconnection).

## Known failure modes (field-tested 2026-10-08/09)

- **Island resurrection**: killing the local server makes the plugin respawn an
  island hub within ~1s. Don't race it — make every respawn path produce the
  forwarder (steps 1-2 of setup spoke).
- **Stale lane**: forwarder accepts TCP but upstream is dead → clients see
  "connected to a non-inter-session service". Fix: restart the forwarder.
- **Empty pidfile** after crash churn → identity check fails closed. Rewrite it.
- **Messages >400 chars** arrive truncated; full text in the hub's
  `messages.log` (on the HUB machine, not the spoke).
- **Lane IP typos**: verify with `tailscale status` before believing any recipe.
