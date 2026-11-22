#!/usr/bin/env bash
# wake-guard.sh — exit BEFORE invoking the LLM when there is nothing to do.
#
# Regola ppezz (2026-11-22): i wake autonomi (digest/morning/agent-activation)
# gravano sui token API se girano a vuoto. Ogni wake script deve iniziare con:
#
#   bash <plugin-root>/scripts/wake-guard.sh || exit 0
#
# ...e prosegue SOLO se la guard passa. La guard passa quando:
#   1. esiste almeno un flag ~/.inter-session/sync-active-*  (obiettivo locked
#      in corso su questa macchina), oppure
#   2. l'inbox del bus ha messaggi non letti per un seat di questa macchina.
# In ogni altro caso il wake e' un no-op a costo zero (nessuna chiamata LLM).
#
# Ciclo di vita del flag: l'esecutore di un sync locked tocca
#   ~/.inter-session/sync-active-<sid>
# all'avvio del proprio plan e lo rimuove come ULTIMO step (il done).
# L'orchestratore verifica la rimozione nel brief.
set -u
FLAGS_DIR="$HOME/.inter-session"

# 1) obiettivi locked attivi su questa macchina
if ls "$FLAGS_DIR"/sync-active-* >/dev/null 2>&1; then
  exit 1   # c'e' lavoro: il chiamante prosegue
fi

# 2) messaggi bus non letti (watermark piu' vecchio del journal lato hub):
#    riusiamo inbox.py in check mode per il seat di questa sessione; se non
#    riesce a risolvere l'identita', fallisce-aperto (il messaggio si perde
#    solo se anche il successivo giro lo salta — meglio un falso positivo
#    che un lavoro mancato).
INBOX="$HOME/.claude/data/inter-session/inbox.py"
if [ -f "$INBOX" ]; then
  out=$(python3 "$INBOX" --hook check 2>/dev/null)
  if echo "$out" | grep -qE 'unread .*: [1-9]'; then
    exit 1   # ci sono non letti: il chiamante prosegue
  fi
fi

exit 0   # nulla da fare: no-op, zero token
