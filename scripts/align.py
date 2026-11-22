#!/usr/bin/env python3
"""align.py — allineamento flotta su un obiettivo condiviso (protocollo sync v1).

Uso:
  align.py --objective "portare X in produzione" --to nebula,craftops-1,craftlead-2
  align.py --objective "..." --to a,b --wait-min 15 --lock

NON e' sync.py (che e' il request/reply 1-to-1 sincrono): align.py coordina
N agenti su un obiettivo. Cosa fa:
  1. invia a ogni target un messaggio `sync <sid>:` con l'obiettivo
  2. aspetta da ogni target una `answer` che quoti <sid> con la struttura
     role / plan / deps / state (cosa contribuisce col suo ambiente/ruolo,
     cosa fara', da chi dipende, cosa ha gia' in corso)
  3. stampa la tabella di allineamento; con --lock invia il brief consolidato
     `status: sync locked <sid>` a tutti i target (da quel momento si torna
     al reporting normale status:/done:)

Protocollo (vedi README sezione "Sync protocol"):
  sync <sid>: <obiettivo>   -> richiesta di allineamento (agire SOLO dopo lock)
  answer <sid>: role/plan/deps/state -> ack del singolo agente
  status: sync locked <sid> -> obiettivo assegnato, si esegue
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HUB_SSH = os.environ.get("INTERSERVER_HUB_SSH", "nebula")
HUB_LOG = os.environ.get(
    "INTERSERVER_HUB_LOG",
    "/home/ppezz/.claude/data/inter-session/messages.log")
_send_cands = sorted(Path(Path.home(),
    ".claude/plugins/cache/inter-session/inter-session").glob(
    "*/skills/inter-session/bin/send.py"))
SEND = str(_send_cands[-1]) if _send_cands else "send.py"


def send(to: str, text: str) -> bool:
    r = subprocess.run([sys.executable, SEND, "--to", to, "--text", text],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ! send a {to} fallito: {r.stderr.strip()}", file=sys.stderr)
        return False
    return True


def fetch_entries(lines: int = 300) -> list:
    r = subprocess.run(
        ["ssh", HUB_SSH, f"tail -n {lines} {HUB_LOG}"],
        capture_output=True, text=True)
    out = []
    for line in (r.stdout or "").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--objective", required=True)
    ap.add_argument("--to", required=True, help="seat names, comma separated")
    ap.add_argument("--wait-min", type=int, default=10)
    ap.add_argument("--lock", action="store_true",
                    help="dopo gli ack invia il brief consolidato")
    args = ap.parse_args()

    targets = [t.strip() for t in args.to.split(",") if t.strip()]
    sid = uuid.uuid4().hex[:6]
    print(f"sync {sid}: {args.objective}")
    print(f"targets: {', '.join(targets)} (attesa ack fino a {args.wait_min}m)\n")

    ask = (f"sync {sid} (protocollo sync v1 — allineamento, NON agire ancora): "
           f"{args.objective} — rispondi `answer {sid}:` con role/plan/deps/state: "
           f"role=cosa contribuisci col tuo ambiente/ruolo/conoscenza, "
           f"plan=azioni concrete tue, deps=da chi/dipendenze, "
           f"state=cosa hai gia in corso su questo obiettivo.")
    for t in targets:
        send(t, ask)

    deadline = time.time() + args.wait_min * 60
    acks: dict[str, str] = {}
    while time.time() < deadline and len(acks) < len(targets):
        time.sleep(20)
        for e in fetch_entries():
            frm = e.get("from_name") or ""
            txt = (e.get("text") or "")
            if frm in targets and frm not in acks and sid in txt \
                    and txt.strip().lower().startswith("answer"):
                acks[frm] = txt
                print(f"[ack] {frm}: {txt[:200]}")
        print(f"  ... {len(acks)}/{len(targets)} ack "
              f"({datetime.now(timezone.utc).strftime('%H:%M:%SZ')})")

    print()
    missing = [t for t in targets if t not in acks]
    if missing:
        print(f"SENZA ack: {', '.join(missing)} — sono idle o al confine turno; "
              f"ricalcola o re-invoca sync piu tardi.")
    if acks and args.lock:
        brief = "; ".join(f"{t}: {acks[t][:150]}" for t in acks)
        for t in targets:
            send(t, f"status: sync locked {sid} — obiettivo ASSEGNATO, si esegue "
                    f"secondo il proprio plan; reporting status:/done: come da protocollo. "
                    f"Plani ricevuti — {brief}")
        print(f"sync locked {sid} inviato a {', '.join(targets)}")
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
