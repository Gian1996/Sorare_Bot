"""Avvisi all'amministratore: riavvii, tempo reale scollegato, problemi con Sorare o Telegram.

Ogni problema viene segnalato una volta, ricordato ogni ALERT_REMIND_HOURS se continua,
e quando si risolve arriva un messaggio di conferma con la durata.
Il "segno di vita" in data/heartbeat.json serve a capire, al riavvio, se il bot si era
fermato da solo e da quanto tempo era fermo.
"""
import html
import logging
import os
import time

import config
from storage import JsonStore

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60
EXIT_REASONS = {
    "137": "ucciso dal sistema (codice 137): di solito è Android che chiude Termux in background",
    "143": "terminato dal sistema (codice 143)",
    "1": "errore nel programma (codice 1): guarda logs/bot.log",
}


def duration(seconds):
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return "meno di un minuto"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60:02d} min"
    return f"{minutes // 1440} giorni"


class AdminAlerts:
    def __init__(self, bot):
        self.bot = bot
        self.heartbeat = JsonStore(os.path.join(config.DATA_DIR, "heartbeat.json"), {})
        self.problems = {}  # chiave -> {"since", "sent_at", "text"}
        self.realtime_down_since = None
        self.cycle_failures = 0
        self.last_cycle_error = ""
        self.telegram_conflict_at = None
        self._last_beat = 0

    # --- riavvio ------------------------------------------------------------------

    def startup_note(self):
        """Riga da aggiungere al messaggio di avvio: com'era finita la volta prima."""
        previous = dict(self.heartbeat.data)
        now = time.time()
        self.heartbeat.data = {"started": now, "alive": now, "stopped": None}
        self.heartbeat.save()
        if not previous.get("alive"):
            return ""
        if previous.get("stopped"):
            return f"\n⏹ Era stato fermato {duration(now - previous['stopped'])} fa"
        self.bot.summary.note_restart()
        code = os.getenv("BOT_LAST_EXIT", "").strip()
        if code:
            reason = EXIT_REASONS.get(code, f"uscito con codice {code}: guarda logs/bot.log")
        else:
            reason = "il telefono, Termux o il servizio si erano spenti"
        return (f"\n⚠️ <b>Ripartito dopo un arresto imprevisto</b>: {reason}.\n"
                f"Fermo per {'circa ' if now - previous['alive'] >= 60 else ''}{duration(now - previous['alive'])} "
                f"(ultimo segno di vita {time.strftime('%d/%m %H:%M', time.localtime(previous['alive']))}).")

    def beat(self):
        if time.time() - self._last_beat >= HEARTBEAT_SECONDS:
            self.heartbeat.data["alive"] = self._last_beat = time.time()
            self.heartbeat.save()

    def stopped_cleanly(self):
        self.heartbeat.data["alive"] = self.heartbeat.data["stopped"] = time.time()
        self.heartbeat.save()

    # --- segnali dal resto del bot -----------------------------------------------

    def cycle_ok(self):
        self.cycle_failures = 0

    def cycle_failed(self, error):
        self.cycle_failures += 1
        self.last_cycle_error = str(error)[:200]

    def on_telegram_conflict(self):
        self.telegram_conflict_at = time.time()

    # --- controllo periodico ---------------------------------------------------------

    async def check(self):
        self.beat()
        if not config.ADMIN_ALERTS:
            return
        now = time.time()

        stream = self.bot.stream
        if stream:
            if stream.connected:
                self.realtime_down_since = None
            elif self.realtime_down_since is None:
                self.realtime_down_since = now
        down = self.realtime_down_since and now - self.realtime_down_since >= config.REALTIME_ALERT_MINUTES * 60
        await self._set("realtime", down,
                        lambda: f"🔴 <b>Tempo reale scollegato</b> da {duration(now - self.realtime_down_since)}.\n"
                                "Intanto controllo le offerte ogni minuto, ma il flusso periodico di Sorare "
                                "a volte è in ritardo.",
                        "🟢 Tempo reale di nuovo collegato", since=self.realtime_down_since)

        client = self.bot.client
        window = now - 600
        recent = [kind for ts, kind in client.problems if ts >= window]
        too_many = recent.count("429")
        await self._set("429", too_many >= config.API_ALERT_ERRORS,
                        lambda: f"⚠️ <b>Sorare rallenta il bot</b>: {too_many} risposte «troppe richieste» "
                                "negli ultimi 10 min.\nC'è un'altra copia del bot (o una prova) accesa con la "
                                "stessa API key? Per esempio sul PC.",
                        "✅ Sorare non rallenta più il bot")
        network = len(recent) - too_many
        await self._set("network", network >= config.API_ALERT_ERRORS,
                        lambda: f"⚠️ <b>Problemi a raggiungere Sorare</b>: {network} errori di rete o del server "
                                "negli ultimi 10 min.",
                        "✅ Sorare risponde di nuovo bene")
        await self._set("auth", client.auth_failed,
                        lambda: "⛔ <b>API key rifiutata da Sorare</b>: controlla SORARE_API_KEY nel .env "
                                "(scaduta o cancellata?).",
                        "✅ API key di nuovo accettata")
        await self._set("cycle", self.cycle_failures >= 3,
                        lambda: f"⚠️ <b>Aggiornamenti falliti {self.cycle_failures} volte di fila</b>:\n"
                                f"<code>{html.escape(self.last_cycle_error)}</code>",
                        "✅ Aggiornamenti di nuovo regolari")
        conflict = self.telegram_conflict_at and now - self.telegram_conflict_at < 120
        await self._set("telegram", conflict,
                        lambda: "⚠️ <b>Un'altra copia del bot è accesa con lo stesso token Telegram</b> "
                                "(sul PC?). Le due copie si rubano i comandi e si dividono l'API key: spegnine una.",
                        "✅ Non vedo più altre copie del bot")

    def active(self):
        """Testi dei problemi in corso, per /stato."""
        return [p["text"] for p in self.problems.values()]

    async def _set(self, key, active, text, resolved, since=None):
        now = time.time()
        problem = self.problems.get(key)
        if active:
            if problem is None:
                problem = self.problems[key] = {"since": since or now, "sent_at": None, "text": ""}
            problem["text"] = text()
            remind = config.ALERT_REMIND_HOURS * 3600
            if problem["sent_at"] is None or now - problem["sent_at"] >= remind:
                first = problem["sent_at"] is None
                if await self.bot.notifier.send(problem["text"]) is not None:
                    problem["sent_at"] = now
                    if first:
                        self.bot.summary.note_problem(problem["text"])
                    log.warning("Avviso all'amministratore: %s", key)
        elif problem is not None:
            del self.problems[key]
            if problem["sent_at"] is not None:
                await self.bot.notifier.send(f"{resolved} (problema durato {duration(now - problem['since'])}).")
