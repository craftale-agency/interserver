#!/usr/bin/env python3
"""wrapper-server.py — co-opt the inter-session plugin's self-healing.

Installed OVER the plugin's bin/server.py (backup the original first). Whatever
launches "the local server" (manual start, monitor respawn, self-healing) then
produces a forwarder lane to the hub instead of an island hub.

Env:
  INTERSERVER_HUB   hub tailnet IP (default 100.70.94.8)
  INTERSERVER_PORT  hub port       (default 9473)

The forwarder manages its own pidfile/meta. After a plugin update, re-apply
this wrapper (doctor checks for it).
"""
import json
import os
import runpy
import socket
import sys

FWD = os.path.expanduser("~/.claude/data/inter-session/forwarder.py")
HUB = os.environ.get("INTERSERVER_HUB", "100.70.94.8")
PORT = os.environ.get("INTERSERVER_PORT", "9473")

# Positional args are kept in sys.argv[0..2] (the OS cmdline must show
# "bin/server.py 127.0.0.1 9473" for the identity check's legacy path).
sys.argv = [FWD, "127.0.0.1", PORT, HUB, PORT]

# The bare TCP forwarder never writes a pidfile, and any island-hub episode
# overwrites it in a format without host/port — then every client's
# anti-squatting check (shared.verify_server_identity) fails closed forever
# (field-tested 2026-10-27: seats dead for 3 days, HUD tag gone). Claim the
# pidfile here, in the exact format the check requires.
probe = socket.socket()
try:
    probe.bind(("127.0.0.1", int(PORT)))
    probe.close()
except OSError:
    # Lane already up elsewhere — do NOT touch the pidfile (it belongs to
    # the live instance) and exit; duplicate spawns stay no-ops.
    sys.exit(0)
DATA = os.path.expanduser("~/.claude/data/inter-session")
with open(os.path.join(DATA, f"server.{PORT}.pid"), "w") as f:
    f.write(str(os.getpid()))
with open(os.path.join(DATA, f"server.{PORT}.pid.meta"), "w") as f:
    f.write(json.dumps({"pid": os.getpid(), "host": "127.0.0.1", "port": int(PORT)}) + "\n")

runpy.run_path(FWD, run_name="__main__")
