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
import os
import runpy
import sys

FWD = os.path.expanduser("~/.claude/data/inter-session/forwarder.py")
HUB = os.environ.get("INTERSERVER_HUB", "100.70.94.8")
PORT = os.environ.get("INTERSERVER_PORT", "9473")

# Positional args are kept in sys.argv[0..2] (the OS cmdline must show
# "bin/server.py 127.0.0.1 9473" for the identity check's legacy path).
sys.argv = [FWD, "127.0.0.1", PORT, HUB, PORT]
runpy.run_path(FWD, run_name="__main__")
