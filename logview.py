"""Lettura del log del servizio (logs/bot.log, scritto da servizio.sh) per il comando /log."""
import html
import os
import re

import config

TELEGRAM_LIMIT = 3900  # Telegram accetta 4096 caratteri per messaggio: tengo un margine per l'intestazione
TAIL_BYTES = 300_000   # basta leggere la coda del file (che può arrivare a 5 MB)
PROBLEM = re.compile(r" (WARNING|ERROR|CRITICAL) |Traceback|^===|Error\b|Exception\b")


def tail(n, only_problems=False, path=None):
    """Ultime `n` righe del log (solo avvisi, errori e riavvii se `only_problems`). None se non c'è il log."""
    path = path or config.LOG_FILE
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]  # la prima riga letta è quasi sempre a metà
    if only_problems:
        lines = [line for line in lines if PROBLEM.search(line)]
    return lines[-n:]


def as_message(lines, title):
    """Righe del log in un messaggio HTML che sta nei limiti di Telegram (taglia le più vecchie)."""
    body, used = [], 0
    for line in reversed(lines):
        line = line if len(line) <= 300 else line[:300] + "…"
        cost = len(html.escape(line)) + 1
        if used + cost > TELEGRAM_LIMIT:
            break
        body.append(line)
        used += cost
    body.reverse()
    cut = f" (ultime {len(body)}: le altre non stanno in un messaggio)" if len(body) < len(lines) else ""
    return f"📜 <b>{title}</b>{cut}\n<pre>{html.escape(chr(10).join(body))}</pre>"
