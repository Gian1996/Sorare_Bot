#!/usr/bin/env bash
# Tiene acceso SorareBot: se si ferma (crash, rete persa...) lo riavvia dopo 30 secondi.
# Pensato per il telefono con Termux. Il log finisce in logs/bot.log.
#   Avvio:   ./servizio.sh &      (oppure lo avvia Termux:Boot all'accensione)
#   Stop:    ./ferma.sh
cd "$(dirname "$0")"
mkdir -p logs
LOG=logs/bot.log

# Impedisce ad Android di addormentare la CPU (se siamo su Termux)
command -v termux-wake-lock >/dev/null && termux-wake-lock

echo $$ > logs/servizio.pid
while true; do
  # Se il log supera 5 MB lo archivia (tiene solo l'ultimo archivio)
  if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ]; then
    mv "$LOG" "$LOG.1"
  fi
  echo "=== $(date '+%F %T') avvio del bot ===" >> "$LOG"
  ./avvia.sh >> "$LOG" 2>&1
  echo "=== $(date '+%F %T') bot fermo (codice $?), riavvio tra 30 secondi ===" >> "$LOG"
  sleep 30
done
