#!/usr/bin/env python3
# Tiny TCP forwarder (stdlib only): forwarder.py BIND_IP BIND_PORT TARGET_HOST TARGET_PORT
# Thread-per-conn with half-close propagation. Survives nothing but a reboot: re-arm manually.
import socket
import sys
import threading

bind_ip, bind_port, tgt_host, tgt_port = (
    sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]),
)


def pump(src, dst):
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
server.listen(64)
print(f"forwarding {bind_ip}:{bind_port} -> {tgt_host}:{tgt_port}", flush=True)
while True:
    conn, _ = server.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
