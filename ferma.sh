#!/usr/bin/env bash
# Ferma SorareBot avviato con servizio.sh (il ciclo di riavvio e il bot).
cd "$(dirname "$0")"
if [ ! -f logs/servizio.pid ]; then
  echo "Il servizio non risulta acceso."
  exit 0
fi
PID=$(cat logs/servizio.pid)
FIGLI=$(pgrep -P "$PID")          # il bot (o lo sleep tra un riavvio e l'altro)
kill "$PID" 2>/dev/null           # prima il ciclo, così non lo riavvia
[ -n "$FIGLI" ] && kill $FIGLI 2>/dev/null
rm -f logs/servizio.pid
command -v termux-wake-unlock >/dev/null && termux-wake-unlock
echo "Bot fermato."
