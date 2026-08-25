# Day-2 runbook

Operating the two-server inter-session bus. Symptom-first troubleshooting
lives in [gotchas.md](gotchas.md); this is procedure.

## Monitoring: `monitor.log`

Each listener's stdout (every delivered message, sanitized) is appended to
`~/.claude/data/inter-session/monitor.log` when you spawn via `rearm.sh`.
Lines look like:

```
[inter-session msg=5777e322 from="hub" truncated=407] status: update past your 8dac8e5e …
[inter-session msg=5777e322 cont] full text 407 bytes at ~/.claude/data/inter-session/messages.log
```

Triage tips:
- `grep -F '<msg_id>' messages.log | tail -1` — full body of a truncated
  message **on the host running the real bus**. On a bridged host the file
  may not exist; ask the peer to re-send or check git.
- Lines starting `[inter-session]` (no `msg=`) are operational notices
  ("another monitor already running", "server identity check failed",
  "dependencies missing") — treat as alerts.
- Operational notices (`[inter-session] server identity check failed …`) at
  the top of the log are historical incidents, not live state — check dates.

## Compaction / session-restart recovery

After `/compact`, `claude -c`, or a session swap, the CC process dies; the
`.session` file points at a listener whose `for_session` is no longer
registered → sends fail. Fix:

```bash
rearm.sh <name>            # kills stale listener, clears state, respawns
```

Then re-verify from the session:
```bash
sendmsg.sh --status        # list.py; expect yourself + remote peers
```

Note `send.py` self-heals mild staleness: on `unknown_peer`/`unauthorized`
hello it deletes the stale `.session` (race-safely, via the listener's
flock) — but the next send still needs a live listener, hence rearm.

## Reboot re-arm

The bridge and (if you run it standalone) the hub server are plain nohup'd
processes. After a reboot on the **spoke** server:

```bash
nohup python3 ~/.claude/data/inter-session/bin/server.py \
  127.0.0.1 9473 100.a.b.c 9474 >> /tmp/bus-bridge.log 2>&1 & disown
rearm.sh <name>
```

On the **hub**: re-launch the real server bound to the tailnet IP (see
README), then `rearm.sh <hub-name>`. Consider a systemd user unit or
`@reboot` crontab if this happens often.

## Pairing a new server securely (one-shot token handover)

Both sides of the bus must present the same `token` file. Never paste the
token into chat, an LLM prompt, or a commit. Production procedure:

1. **Serve** (on the hub, from the token's host):
   ```bash
   cd ~/.claude/data/inter-session
   timeout 3600 python3 -m http.server 8899 --bind 100.a.b.c &
   ```
   One-shot: bound to the tailnet IP only, wrapped in `timeout`, killed
   immediately after fetch.
2. **Commit the fingerprint** to the shared evidence repo *before* fetching:
   ```bash
   sha256sum ~/.claude/data/inter-session/token   # publish only this hash
   ```
3. **Fetch** (on the new spoke):
   ```bash
   curl -s http://100.a.b.c:8899/token -o ~/.claude/data/inter-session/token
   chmod 600 ~/.claude/data/inter-session/token
   echo '<expected-sha256>' | sha256sum -c - <<<"$(sha256sum token | awk '{print $1}')  token"
   ```
4. **Shred the server side evidence**: kill the `http.server`, confirm the
   serve directory shows no unexpected access, and — if you served a bundle
   from a temp dir — `shred -u` it.
5. Confirm both sides with a ping/pong over the bus.

The token file is only read by the client/server; overwriting an existing
one rotates fleet-wide (every participant must re-fetch).

## Bus vs git evidence channel

Use the bus when: sub-second coordination, GO/NO-GO, "pull commit X",
status pings, driving a peer session interactively.

Use the shared git repo when: anything that must survive (plans, evidence,
handover manifests with sha256s), anything >256 KiB, anything you may need
to audit later, or when a peer might be offline. The bus has **no delivery
guarantees and no durable log on bridged hosts** — a git commit is the only
commit.

Convention that worked in production: bus message says "GO: fetch
http://…:8899/… sha256 …, commit <sha> has the details" — the bus carries
the pointer, git carries the payload.

## Health checks

```bash
# bridge alive + identity sane (spoke)
cat ~/.claude/data/inter-session/server.9473.pid.meta
ps -p "$(cat ~/.claude/data/inter-session/server.9473.pid)" -o args=   # must show bin/server.py path
ss -tn 'sport = :9473' | head        # established conns to the hub

# bus reachable end-to-end
sendmsg.sh --status                   # lists peers on both servers
```

If `list.py` shows only local peers, the bridge or hub is down — see
gotchas.
