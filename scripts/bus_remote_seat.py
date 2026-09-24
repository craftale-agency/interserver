#!/usr/bin/env python3
"""bus_remote_seat.py — raw WebSocket seat on a remote inter-session hub.

Third operating mode (after "hub-local plugin" and "bridge forwarder"):
skip the plugin client entirely and speak the wire protocol from
docs/protocol.md straight to the hub. No bin/server.py masquerade, no
pidfile identity trick, no machine-local hub to fight — just a WebSocket
to a hub you can reach over the tailnet.

Modes:
  listen             persistent agent seat (auto-reconnect, name fallback)
  send <to> <text>   one-shot control-role send (listener must be ONLINE)
  list               throwaway probe: list fleet peers

Config (env):
  BUS_HUB    hub endpoint          (default ws://127.0.0.1:9473/)
  BUS_NAME   seat base name        (default "seat"; fallbacks -2, -3, -4)
  BUS_STATE  state file path       (default /tmp/bus_remote_session.json)
  BUS_LOG    inbound log path      (default /tmp/bus_remote.log)

Token: read from ~/.claude/data/inter-session/token (fleet-shared, 0600,
never printed or committed).

Semantics that matter:
  - send has NO ack — silence = delivered; only error frames mean failure.
  - The hub does NOT queue for disconnected seats: keep `listen` running
    (nohup … & disown — supervisor-reaped children die with the supervisor)
    or replies to your sends land nowhere.
  - State (session_id + nonce + won name) is persisted and reused: same
    session_id + matching nonce reconnect-replaces cleanly instead of
    colliding.
  - If a stale hub-side seat squats your name (gotcha: "name_taken with no
    live owner"), the listener walks the fallback ladder and persists the
    name it won. Tell peers which name is live (`list` shows it).

Requires: Python 3.10+ with `websockets` — the plugin venv at
~/.claude/data/inter-session/venv already has it.
"""
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import websockets

HUB = os.environ.get("BUS_HUB", "ws://127.0.0.1:9473/")
BASE_NAME = os.environ.get("BUS_NAME", "seat")
STATE = Path(os.environ.get("BUS_STATE", "/tmp/bus_remote_session.json"))
LOG = Path(os.environ.get("BUS_LOG", "/tmp/bus_remote.log"))
TOKEN_PATH = Path.home() / ".claude/data/inter-session/token"


def token() -> str:
    return TOKEN_PATH.read_text().strip()


def log(line: str) -> None:
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def name_candidates() -> list[str]:
    return [BASE_NAME, f"{BASE_NAME}-2", f"{BASE_NAME}-3", f"{BASE_NAME}-4"]


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    st = {"session_id": str(uuid.uuid4()), "nonce": uuid.uuid4().hex}
    STATE.write_text(json.dumps(st))
    return st


def agent_hello(st: dict, name: str) -> dict:
    return {
        "op": "hello", "token": token(), "name": name, "label": "",
        "cwd": os.getcwd(), "pid": os.getpid(), "role": "agent",
        "session_id": st["session_id"], "nonce": st["nonce"],
    }


async def listen() -> None:
    st = load_state()
    tried: list[str] = []
    names = ([st["name"]] if st.get("name") else []) + \
            [n for n in name_candidates() if n != st.get("name")]
    while True:
        name = next((n for n in names if n not in tried), None)
        if name is None:
            log("all name candidates rejected; restarting cycle in 60s")
            tried = []
            await asyncio.sleep(60)
            continue
        try:
            async with websockets.connect(HUB) as ws:
                await ws.send(json.dumps(agent_hello(st, name)))
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if first.get("op") == "error":
                    log(f"hello error name={name}: {first}")
                    if first.get("code") == "name_taken":
                        tried.append(name)
                        continue
                    if first.get("code") == "unauthorized":
                        log("bad token — not retrying")
                        return
                    await asyncio.sleep(5)
                    continue
                st["name"] = name
                STATE.write_text(json.dumps(st))
                tried = []
                log(f"connected session={st['session_id'][:8]} name={name}")
                async for raw in ws:
                    log(f"in  {raw}")
                log("disconnected")
        except Exception as e:
            log(f"reconnect in 5s: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


async def send(to: str, text: str) -> int:
    st = load_state()
    hello = {
        "op": "hello", "token": token(), "role": "control",
        "for_session": st["session_id"], "nonce": st["nonce"],
    }
    async with websockets.connect(HUB) as ws:
        await ws.send(json.dumps(hello))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        if json.loads(raw).get("op") != "welcome":
            print(f"hello rejected: {raw}", file=sys.stderr)
            return 1
        await ws.send(json.dumps({"op": "send", "to": to, "text": text}))
        # NO ack exists — only error frames (e.g. unknown_peer) mean failure.
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            if json.loads(raw).get("op") == "error":
                print(f"send error: {raw}", file=sys.stderr)
                return 1
        except asyncio.TimeoutError:
            pass
        return 0  # silence = delivered


async def list_peers() -> int:
    st = dict(load_state())
    st["session_id"] = str(uuid.uuid4())  # throwaway — don't clash with our seat
    async with websockets.connect(HUB) as ws:
        await ws.send(json.dumps(agent_hello(st, f"{BASE_NAME}-probe")))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        if json.loads(raw).get("op") != "welcome":
            print(f"hello rejected: {raw}", file=sys.stderr)
            return 1
        await ws.send(json.dumps({"op": "list"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        print(raw)
        return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "listen":
        asyncio.run(listen())
    elif len(sys.argv) == 2 and sys.argv[1] == "list":
        sys.exit(asyncio.run(list_peers()))
    elif len(sys.argv) == 4 and sys.argv[1] == "send":
        sys.exit(asyncio.run(send(sys.argv[2], sys.argv[3])))
    else:
        print(__doc__)
        sys.exit(2)
