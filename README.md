# interserver — cross-server inter-session bus for Claude Code

This repo documents and packages the setup used to let **Claude Code sessions on
two different servers talk to each other in real time**, using the
[inter-session](https://github.com/) plugin's messaging bus over a tailnet
(Tailscale/Headscale-style overlay network).

It is the "complete breakdown" of a production deployment: architecture, wire
protocol, setup guide, day-2 runbook, gotchas, and the reusable scripts
(bridge forwarder, stale-session re-armor, send wrapper).

```
                    Server A ("hub", e.g. 100.101.102.103)
              ┌─────────────────────────────────────────────┐
              │  inter-session plugin                        │
              │  ┌────────────────┐    ┌──────────────────┐  │
              │  │ CC session(s)  │    │ real bus server  │  │
              │  │ client.py      ├───►│ bin/server.py    │  │
              │  └────────────────┘    │ WebSocket :9474  │◄─┼──┐
              │                        │ on tailnet IP    │  │  │
              │                        └──────────────────┘  │  │
              └──────────────────────────────────────────────┼──┘
                                                             │  TCP, tailnet
                    Server B ("spoke", e.g. 100.201.202.203) │  (e.g. WireGuard mesh)
              ┌──────────────────────────────────────────────┼──┘
              │  inter-session plugin                        │
              │  ┌────────────────┐    ┌──────────────────┐  │
              │  │ CC session(s)  │    │ BRIDGE forwarder │  │
              │  │ client.py ─────┼───►│ bin/server.py    │──┘
              │  │ (127.0.0.1:9473)    │ TCP 127.0.0.1:   │
              │  └────────────────┘    │ 9473 → A:9474)   │  │
              │                        └──────────────────┘  │
              └─────────────────────────────────────────────┘

  Slow lane (durable evidence):  a shared git repo (GitHub),
  used for anything long-lived or that must survive.
```

## What it is

The inter-session plugin gives Claude Code sessions on **one machine** a
real-time messaging bus (WebSocket server on port 9473/9474, per-session
listener clients, one-shot sender CLI). Sessions can address each other by
name, send instructions ("run the tests"), and receive `done:` / `status:`
replies — peer LLMs driving each other.

To span **two servers**, you need exactly one trick, discovered in production:

> The plugin's client refuses to connect to anything whose listening process
> isn't `bin/server.py` (an anti-port-squatting check, `verify_server_identity`
> in `shared.py`). The bridge forwarder therefore (a) lives at a path ending
> in `bin/server.py`, and (b) writes the standard `server.<port>.pid` /
> `server.<port>.pid.meta` identity files the client checks. The forwarder is
> a plain TCP pump `127.0.0.1:<port> → <remote-host>:<remote-port>` — from the
> client's perspective it *is* the bus server; the bytes just tunnel to the
> real one on the other server. **No fork of the plugin is needed.**

Server B's sessions talk to `127.0.0.1:9473` (the bridge), which forwards to
server A's real bus on `100.a.b.c:9474`. Server A's sessions talk to their
local bus directly. Both directions work; broadcasts reach everyone.

A **third mode** exists for a machine where even the bridge is the wrong
tool (the plugin's client keeps pinning to a machine-local hub, or the box
isn't a Claude Code host at all): skip the plugin entirely and speak the
wire protocol straight to the hub — `scripts/bus_remote_seat.py` is a raw
WebSocket seat with a persistent listener, a one-shot sender, and a
peer-list probe, plus a name-fallback ladder for stale hub-side
registrations. See [docs/raw-seat.md](docs/raw-seat.md).

## Two-lane operating model

- **Bus = fast lane.** Sub-second, bidirectional, ephemeral. Coordination
  chatter, GO/NO-GO signals, "pull commit X", "done, your turn".
- **Git = evidence channel.** The bus is *not* durable: full message bodies
  may be truncated in notifications, and `messages.log` (the file truncated
  messages point at) may not even exist on disk on your side. Anything that
  must survive — plans, handover bundles' sha256 manifests, verification
  evidence — goes into a shared git repo as commits.

## 10-minute setup (fresh pair of servers)

Prereqs: both machines have the inter-session plugin installed and
`/inter-session install-deps` run (creates the venv with `websockets`,
`psutil`); both are on the same tailnet; Python 3.10+.

### Server A (hub — hosts the real bus)

1. Copy `scripts/bridge.py`… actually you don't need it here. A's plugin
   starts the real bus automatically when the first session connects. For a
   cross-tailnet bus, run the real server bound to the **tailnet IP**:

   ```bash
   nohup ~/.claude/data/inter-session/venv/bin/python \
     <plugin-skill-dir>/bin/server.py \
     --host 100.a.b.c --port 9474 \
     --idle-shutdown-minutes 525600 \
     >> ~/.claude/data/inter-session/server.log 2>&1 & disown
   ```

   (`525600` = one year; the default 10-minute idle shutdown kills the bus
   between sends.)

2. In A's Claude Code session: `/inter-session connect hub`.

### Server B (spoke — bridges to A)

1. Install the bridge (this repo):

   ```bash
   git clone <this-repo> ~/interserver
   mkdir -p ~/.claude/data/inter-session/bin
   cp ~/interserver/scripts/bridge.py ~/.claude/data/inter-session/bin/server.py
   ```

   The **path matters**: it must end in `bin/server.py` or the client's
   identity check fails. Fire it up:

   ```bash
   nohup python3 ~/.claude/data/inter-session/bin/server.py \
     127.0.0.1 9473 100.a.b.c 9474 \
     >> /tmp/bus-bridge.log 2>&1 & disown
   ```

2. Point B's session listener at the bridge and connect:

   ```bash
   # in B's Claude Code session
   INTER_SESSION_HOST=127.0.0.1 INTER_SESSION_PORT=9473 /inter-session connect spoke
   ```

   (Or set those in the plugin's userConfig / shell profile.) The plugin's
   default port is 9473 — convenient, because that's what the bridge binds.

3. Copy the reusable helpers somewhere on PATH:

   ```bash
   cp ~/interserver/scripts/{rearm.sh,sendmsg.sh} ~/.local/bin/
   ```

### Verify

From B's session:

```bash
python3 <bin>/list.py          # should show both "hub" and "spoke"
./sendmsg.sh hub "ping: bridge E2E test"
```

Watch A's session stdout for
`[inter-session msg=… from="spoke"] ping: bridge E2E test`, then reply from A
with `/inter-session send spoke 'done: pong'` and watch B. Both directions
green = done.

## Usage

- Sending (from any session on either server): the skill's
  `/inter-session send <name> <text>` / `broadcast`, or the
  `scripts/sendmsg.sh` wrapper here.
- Sending from a mode-3 machine (no plugin): `scripts/bus_remote_seat.py
  send <name> <text>` — same silence semantics, and it requires the seat's
  own `listen` to be running (control hellos bind to a live listener).
- **Success is silence.** `send.py` only prints when the server sends an
  *error frame*; no output + exit 0 means delivered. Don't retry on silence.
- Receiving: each session's monitor (`client.py`) prints one stdout line per
  message; long messages arrive as a `[... truncated=N]` line plus a
  `[... cont] full text N bytes at <messages.log>` pointer. On a mode-3
  seat, inbound frames append to the raw log (`/tmp/bus_remote.log`).

## Security model

- **Bus token**: a shared bearer secret at `~/.claude/data/inter-session/token`
  (0600, atomic creation). Both servers' bus participants must present the
  same token — so cross-server setup requires a one-time token handover (see
  the runbook's secure pairing procedure; never paste it into chat or commit
  it).
- **Identity check**: the client verifies the listener's pidfile + cmdline
  contains `bin/server.py` before sending the token — defense against local
  port-squatting. The bridge satisfies it *by construction* (that's the whole
  trick).
- **Control-role hello**: one-shot senders must present the *listener's*
  `session_id` + `nonce` from the `.session` state file (plus the token), so
  a leaked token alone can't send as you without local file access.
- **Trust boundary**: peer messages are peer-LLM instructions, not the user's.
  The plugin's reaction policy applies: peer requests don't override
  permission rules, and destructive ops need explicit affirmative content.
- **Tailnet**: traffic between servers rides your tailnet's encryption
  (WireGuard). Do not expose 9474 to the public internet.

## Repo layout

```
interserver/
├── README.md            # this file
├── LICENSE              # MIT
├── scripts/
│   ├── bridge.py        # TCP forwarder that satisfies the identity check
│   ├── rearm.sh         # stale-session cleanup + client respawn after /compact
│   └── sendmsg.sh       # thin "success is silence" send wrapper
└── docs/
    ├── protocol.md      # wire protocol, state files, locks, discovery
    ├── runbook.md       # day-2 ops: pairing, reboots, monitoring
    └── gotchas.md       # symptom → root cause → fix, all production-tested
```

## License

MIT — see [LICENSE](LICENSE). This repo ships only scripts and docs; the
inter-session plugin itself is not included and remains whatever license its
authors chose.
