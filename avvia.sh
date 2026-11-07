#!/usr/bin/env bash
# Avvia SorareBot. Uso: ./avvia.sh        (normale)
#                       ./avvia.sh prova  (DRY RUN: niente messaggi su Telegram)
cd "$(dirname "$0")"
if [ "$1" = "prova" ]; then
  export DRY_RUN=1
fi
exec ../sorare/bin/python main.py
