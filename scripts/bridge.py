#!/usr/bin/env python3
# Bridge variant of inter-session's bin/server.py.
#
# Instead of hosting the bus, this "server" is a plain TCP forwarder:
# local BIND_IP:BIND_PORT -> the real bus at TARGET_HOST:TARGET_PORT.
#
# THE TRICK: it deliberately lives at a path ending in `bin/server.py` and
# writes the standard pidfile/meta JSON, so the client's
# `verify_server_identity` (shared.py — requires the listener's cmdline to
# contain "bin/server.py") accepts the intentional bridge. This makes
# cross-server operation work WITHOUT forking the plugin.
#
# Install it as, e.g.:
#   mkdir -p ~/.claude/data/inter-session/bin
#   cp bridge.py ~/.claude/data/inter-session/bin/server.py
#
# Run (re-arm after reboot):
#   nohup python3 ~/.claude/data/inter-session/bin/server.py \
#     127.0.0.1 9473 100.a.b.c 9474 >> /tmp/bus-bridge.log 2>&1 & disown
#
# Args: BIND_IP BIND_PORT TARGET_HOST TARGET_PORT
# Optional env: INTER_SESSION_DATA_DIR (defaults to
# ~/.claude/data/inter-session — must match the client's data dir so the
# pidfile/meta land where verify_server_identity looks for them).
import json
import os
import socket
import sys
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    "INTER_SESSION_DATA_DIR",
    str(Path.home() / ".claude" / "data" / "inter-session"),
))

if len(sys.argv) != 5:
    sys.exit(f"usage: {sys.argv[0]} BIND_IP BIND_PORT TARGET_HOST TARGET_PORT")
bind_ip, bind_port, tgt_host, tgt_port = (
    sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]),
)


def write_pidfile():
    # Identity files the client checks BEFORE it will send its token.
    # Mirrors shared.write_server_identity: server.<port>.pid + .pid.meta
    # (0600-ish hygiene; the client only needs them to exist and match).
    # Note: non-default bind hosts get a host-scoped stem in the real
    # server; for the usual 127.0.0.1 bridge the plain stem is correct.
    pid = os.getpid()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"server.{bind_port}.pid").write_text(str(pid))
    (DATA_DIR / f"server.{bind_port}.pid.meta").write_text(json.dumps({
        "pid": pid, "host": bind_ip, "port": bind_port,
    }))


def pump(src, dst):
    # Raw byte pump; WebSocket handshakes/frames pass through untouched.
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock, how in ((src, socket.SHUT_RD), (dst, socket.SHUT_WR)):
            try:
                sock.shutdown(how)
            except OSError:
                pass


def handle(client):
    try:
        upstream = socket.create_connection((tgt_host, tgt_port), timeout=10)
    except OSError:
        client.close()
        return
    threads = [
        threading.Thread(target=pump, args=(a, b), daemon=True)
        for a, b in ((client, upstream), (upstream, client))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    client.close()
    upstream.close()


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((bind_ip, bind_port))
write_pidfile()  # BEFORE listen(): the client's TCP probe must never
                 # succeed before the pidfile exists (race noted in the
                 # plugin's server.py).
server.listen(64)
print(f"forwarding {bind_ip}:{bind_port} -> {tgt_host}:{tgt_port}", flush=True)
while True:
    conn, _ = server.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
