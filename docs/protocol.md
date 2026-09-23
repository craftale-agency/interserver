# inter-session wire protocol & local state

Reverse-documented from the plugin source (`inter-session` 0.1.3,
`skills/inter-session/bin/{server,client,send,discover,shared}.py`). Ported
here so cross-server operators understand what the bridge carries.

## Transport

- Real bus: WebSocket server (`websockets` library), default port **9473**
  (`shared.DEFAULT_PORT`); cross-server hubs often use **9474** on the
  tailnet IP.
- Frame cap 16 MiB (`WS_FRAME_CAP`); direct-send text cap 10 MiB; broadcast
  cap 256 KiB; broadcasts rate-limited to 60/min per listener session.

## Frames (JSON objects with `op`)

### hello (first frame on every connection — mandatory)

Agent role (the per-session listener, `client.py`):

```json
{"op": "hello", "session_id": "<uuid>", "name": "server-a", "label": "",
 "cwd": "/home/x", "pid": 1234, "role": "agent",
 "token": "<bus token>", "nonce": "<urlsafe-16>"}
```

Control role (one-shot senders, `send.py`): acts *on behalf of* an existing
listener, must prove continuity:

```json
{"op": "hello", "session_id": "<fresh uuid>", "name": "", "label": "",
 "cwd": "...", "pid": 5678, "role": "control",
 "for_session": "<listener session_id>", "nonce": "<listener nonce>",
 "token": "<bus token>"}
```

Responses: `{"op":"welcome","session_id":"...","assigned_name":"..."}`
(plus `for_session` for control), or an **error frame** — notably
`unauthorized` (bad token, or stale nonce → "stale listener state;
reconnect") and `unknown_peer` (no live listener for `for_session`).

### send / broadcast (post-welcome)

```json
{"op": "send", "text": "...", "to": "<name | name-prefix | session_id-prefix≥4>"}
{"op": "broadcast", "text": "..."}
```

Target resolution: exact session_id → exact name → unique name prefix →
unique session_id prefix (≥4 chars). Ambiguity → `ambiguous` error with
`matches`. Delivery is fire-and-forget: **the server sends nothing back on
success** — senders wait ~1s for an error frame and treat silence as success.

### Delivered message

```json
{"op": "msg", "msg_id": "8-hex", "from": "<sender session_id>", "from_name": "...",
 "from_label": "...", "to": "<target name>", "text": "...", "ts": "<iso8601>"}
```

The listener prints to stdout (Claude Code monitor notifications):

```
[inter-session msg=<id> from="<name>" "<label>"] <first ~400 chars>
[inter-session msg=<id> cont] full text N bytes at <data_dir>/messages.log
```

(400-char cap because CC clips notifications at ~512 bytes; the full body is
a JSONL record in `messages.log` on the **server's** host.)

### Other ops

- `ping` → `pong` (listener every 15s)
- `list` → `list_ok {sessions: [{session_id, name, label, cwd, since}]}`
- `rename {name}` → `renamed`; `bye` (close); server events `peer_joined`,
  `peer_left`, `renamed`.
- Error frame shape: `{"op":"error","code":"...","message":"...", ...extras}`
  — codes: `invalid_name`, `invalid_label`, `invalid_payload`, `name_taken`
  (+ `candidates`), `unknown_peer`, `ambiguous` (+ `matches`),
  `text_too_long`, `unauthorized`, `rate_limited`, `unknown_op`.

## Local state (`~/.claude/data/inter-session/`, mode 0700)

| File | Meaning |
|---|---|
| `token` | shared bearer secret (0600, O_CREAT\|O_EXCL atomic) |
| `clients/<ppid>.session` | listener state: `session_id, name, label, token, nonce, listener_pid, host, port, created_at` — written atomically (tempfile + fsync + rename) after `welcome` |
| `clients/<ppid>.lock` | per-CC-session dedup flock; **never unlinked** by design (kernel releases flock on holder death; unlinking opens a TOCTOU window) |
| `server.<port>.pid` / `.pid.meta` | listener identity: pid + `{pid, host, port}` JSON |
| `monitor.log` | listener stdout = the message log (see runbook) |
| `messages.log` | server-side JSONL full-text log (size-rotated, 50 MiB × 5) — **only on the host running the real bus** |
| `venv/` | isolated deps; every `bin/*.py` re-execs under it |

## Discovery: parent-PID walk

Helpers (`send.py`, `list.py`) and the listener (`client.py`) are spawned by
*different* subshells — siblings, not parent/child. They find each other by
keying state on the **Claude Code main process pid**:
`resolve_listener_key()` = `INTER_SESSION_PPID_OVERRIDE` → first ancestor
whose `cmdline[0]` basename is `claude` (incl. versioned paths like
`~/.local/share/claude/versions/2.x`) → `getppid()` fallback. `discover.py`
then reads `clients/<key>.session`, falling back to walking `/proc` parents
if psutil is absent.

## Server identity check (why the bridge works)

`verify_server_identity(host, port)` fails closed: no pidfile → reject; dead
pid → reject; meta must match the exact endpoint; and the pid's **cmdline
must contain `bin/server.py`**. This stops a local squatter from harvesting
the bus token. The bridge forwarder satisfies all of it *by construction* —
it lives at `~/.claude/data/inter-session/bin/server.py` and writes the same
pidfile/meta before it `listen()`s. Caveat (per upstream docs): a same-UID
attacker can forge the pidfile; this is opportunistic defense, not crypto.
