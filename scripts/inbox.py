#!/usr/bin/env python3
"""inbox.py — guaranteed-delivery inbox for the inter-session bus.

Turns "message that maybe wakes the agent" into "message that definitely
activates the agent":

  Stop hook          unread messages at end of turn -> block the stop and feed
                     them to the agent as the reason (zero mid-turn queueing)
  SessionStart hook  unread messages accumulated while the session was closed
                     -> injected as additionalContext on open
  --hook check       manual: print unreads (CLI debugging)

State: per-session watermark at
  ~/.claude/data/inter-session/inbox-<session_id>.watermark  (last processed ts)

Unread = messages.log entries newer than the watermark that are
  - direct: to_session_id == this session, or
  - broadcast (kind == "broadcast")
  - not sent by this session's own seat name (parsed from clients/*.session)

Idempotent: the watermark advances when messages are *handed over* (in the
block reason / context), so a replay never re-triggers work.

stdin: Claude Code hook JSON ({"session_id": "...", ...}). Ignored in check
mode (uses the "manual" namespace).
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(os.environ.get("INTER_SESSION_DATA",
                           os.path.expanduser("~/.claude/data/inter-session")))
LOG = DATA / "messages.log"
# The bus server (hub) is the only writer of messages.log. On a spoke the
# local copy is stale (island era) — pull the authoritative tail from the hub.
HUB_SSH = os.environ.get("INTERSERVER_HUB_SSH", "nebula")
HUB_LOG = os.environ.get(
    "INTERSERVER_HUB_LOG",
    "/home/ppezz/.claude/data/inter-session/messages.log")
SSH_LINES = 500
DEFAULT_LOOKBACK_HOURS = 6
MAX_FEED = 20  # cap messages per handover to keep the reason/context bounded


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def lookback_floor(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)) \
        .strftime("%Y-%m-%dT%H:%M:%S")


def session_id_from_stdin() -> str:
    try:
        raw = sys.stdin.read()
        if raw.strip():
            sid = (json.loads(raw) or {}).get("session_id")
            if sid:
                return re.sub(r"[^A-Za-z0-9_-]", "_", str(sid))
    except Exception:
        pass
    return "manual"


def my_seat_name(session_id: str) -> str:
    """Seat name for this session, from clients/*.session (may be empty)."""
    cdir = DATA / "clients"
    if not cdir.is_dir():
        return ""
    for f in cdir.glob("*.session"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if d.get("session_id") == session_id:
            return d.get("name", "")
    return ""


def watermark_path(session_id: str) -> Path:
    return DATA / f"inbox-{session_id}.watermark"


def read_watermark(session_id: str, floor: str) -> str:
    p = watermark_path(session_id)
    try:
        ts = p.read_text().strip()
        if ts:
            return ts
    except OSError:
        pass
    return floor  # first run: only recent history


def advance(session_id: str, ts: str) -> None:
    watermark_path(session_id).write_text(ts)


def parse_lines(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def fetch_entries() -> list:
    """Authoritative source = the hub's messages.log. Fallback: local log
    (correct on the hub machine itself, stale-but-better-than-nothing on a
    spoke when ssh is unreachable)."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes",
             HUB_SSH, "tail -n %d %s" % (SSH_LINES, HUB_LOG)],
            capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            return parse_lines(r.stdout)
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        return parse_lines(LOG.read_text(errors="replace"))
    except OSError:
        return []


def unread(entries: list, session_id: str, seat: str, since: str) -> list:
    hits = []
    for e in entries:
        ts = (e.get("ts") or "")[:19]
        if not ts or ts <= since:
            continue
        frm = e.get("from_name") or ""
        if seat and frm == seat:
            continue
        if e.get("kind") == "broadcast" or e.get("to_session_id") == session_id:
            hits.append(e)
    return hits


def fmt(e: dict) -> str:
    frm = e.get("from_name") or "?"
    kind = "broadcast" if e.get("kind") == "broadcast" else "direct"
    text = (e.get("text") or "").replace("\n", " ")[:400]
    return f"- [{frm} {kind}] {text}"


def main() -> int:
    args = sys.argv[1:]
    mode = "check"
    if "--hook" in args:
        mode = args[args.index("--hook") + 1]
    lookback = DEFAULT_LOOKBACK_HOURS
    if "--lookback" in args:
        try:
            lookback = float(args[args.index("--lookback") + 1])
        except (ValueError, IndexError):
            pass

    session_id = session_id_from_stdin() if mode in ("stop", "start") else "manual"
    floor = lookback_floor(lookback)
    since = read_watermark(session_id, floor)
    seat = my_seat_name(session_id)
    msgs = unread(fetch_entries(), session_id, seat, since)

    if not msgs:
        if mode == "check":  # report even when empty; hooks stay silent
            print(f"unread for {session_id}" +
                  (f" (seat {seat})" if seat else "") + ": 0 (since " + since + ")")
        return 0  # nothing to hand over: hooks exit silently

    newest = max((e.get("ts") or since)[:19] for e in msgs)
    feed = msgs[-MAX_FEED:]
    body = "\n".join(fmt(e) for e in feed)
    n = len(msgs)

    if mode == "check":
        print(f"unread for {session_id}" + (f" (seat {seat})" if seat else "") +
              f": {n}")
        print(body)
        print(f"(watermark not advanced in check mode: {since})")
        return 0

    advance(session_id, newest)  # hand over => mark processed (idempotent replay)

    if mode == "stop":
        print(json.dumps({
            "decision": "block",
            "reason": (f"{n} unread inter-session bus message(s) — process them "
                       f"now (reply per protocol: done:/answer:/status:), then "
                       f"finish your previous task:\n{body}"),
        }))
    elif mode == "start":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    f"{n} unread inter-session bus message(s) arrived while this "
                    f"session was closed:\n{body}"),
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
