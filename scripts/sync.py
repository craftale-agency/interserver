#!/usr/bin/env python3
"""sync.py — synchronous in-turn exchange with one agent on the bus.

  sync.py <seat> [opener text] [--timeout <seconds>]

Sends a `request:sync` (with optional opener), then polls the hub's
messages.log until <seat> replies (answer:/status:/done:/question:), and
prints the reply to stdout — so the calling session processes it in the
same turn. This formalizes the manual send→wait→grep pattern used during
the 2026-10 migration epic.

Exit codes: 0 reply printed · 2 seat not found · 3 timeout · 4 send failed.
"""
import os
import re
import subprocess
import sys
import time

DATA = os.path.expanduser("~/.claude/data/inter-session")
HUB_SSH = os.environ.get("INTERSERVER_HUB_SSH", "nebula")
HUB_LOG = os.environ.get(
    "INTERSERVER_HUB_LOG",
    "/home/ppezz/.claude/data/inter-session/messages.log")
POLL_SECONDS = 5
DEFAULT_TIMEOUT = 120


def plugin_bin() -> str:
    import glob
    cands = sorted(glob.glob(os.path.expanduser(
        "~/.claude/plugins/cache/inter-session/inter-session/*/skills/inter-session/bin")))
    if not cands:
        sys.exit("plugin bin not found")
    return cands[-1]


def run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def list_seats():
    r = run(["python3", os.path.join(plugin_bin(), "list.py")])
    seats = []
    for line in r.stdout.splitlines():
        m = re.match(r"([a-z0-9][a-z0-9-]*)\s", line)
        if m:
            seats.append(m.group(1))
    return seats


def send(target, text):
    """Send via a live seat's credentials (gateway pattern, like notify.sh)."""
    import json
    key = None
    cdir = os.path.join(DATA, "clients")
    for f in sorted(os.listdir(cdir) if os.path.isdir(cdir) else [], reverse=True):
        if not f.endswith(".session"):
            continue
        try:
            d = json.loads(open(os.path.join(cdir, f)).read())
        except Exception:
            continue
        lp = d.get("listener_pid", 0)
        try:
            os.kill(int(lp), 0)
        except (OSError, ValueError):
            continue
        if d.get("name") != target:  # never gateway via the target itself
            key = f.split(".")[0]
            gw_name = d.get("name") or key
            break
    if key is None:
        return False
    env = dict(os.environ, INTER_SESSION_PPID_OVERRIDE=key,
               INTERSERVER_ME=gw_name)
    r = subprocess.run(
        ["python3", os.path.join(plugin_bin(), "send.py"),
         "--to", target, "--text", text.replace("(from caller)",
                                                 "(from %s)" % gw_name)],
        capture_output=True, text=True, timeout=15, env=env)
    return r.returncode == 0


def hub_entries():
    r = run(["ssh", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes",
             HUB_SSH, "tail -n 200 %s" % HUB_LOG], timeout=10)
    out = []
    import json
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def main():
    args = sys.argv[1:]
    timeout = DEFAULT_TIMEOUT
    if "--timeout" in args:
        i = args.index("--timeout")
        try:
            timeout = int(args[i + 1])
        except (ValueError, IndexError):
            pass
        args = [a for j, a in enumerate(args) if j not in (i, i + 1)]
    if not args:
        sys.exit("usage: sync.py <seat> [opener] [--timeout <s>]")
    target, opener = args[0], " ".join(args[1:])

    seats = list_seats()
    if target not in seats:
        print("seat '%s' not on the bus. Connected: %s" %
              (target, ", ".join(seats) or "(none)"), file=sys.stderr)
        sys.exit(2)

    msg = ("request:sync (from %s) — synchronous channel open: reply now with "
           "answer:/status: in this turn"
           % os.environ.get("INTERSERVER_ME", "maintenance"))
    if opener:
        msg += " — " + opener
    if not send(target, msg):
        print("send failed (no live gateway seat)", file=sys.stderr)
        sys.exit(4)

    t0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        for e in hub_entries():
            ts = (e.get("ts") or "")[:19]
            if ts <= t0 or e.get("from_name") != target:
                continue
            text = (e.get("text") or "").strip()
            if text.startswith(("answer:", "status:", "done:", "question:")):
                print("[%s] %s" % (target, text))
                return 0
    print("timeout (%ds): %s didn't reply — seat alive but busy, or monitor "
          "down (try /interserver doctor on its machine)" % (timeout, target),
          file=sys.stderr)
    sys.exit(3)


if __name__ == "__main__":
    main()
