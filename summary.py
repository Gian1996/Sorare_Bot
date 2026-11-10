"""Riepilogo giornaliero: ogni utente lo riceve all'ora che sceglie con /riepilogo.

Per tutti: affari e avvisi della watchlist delle ultime 24 ore, quanti sono stati comprati
(esiti chiesti a Sorare, come in /bilancio) e il miglior affare del giorno.
Per l'amministratore anche come è andato il bot: tempo reale, offerte viste, riavvii, problemi.
Arriva anche nei giorni senza affari: così fa anche da "il bot è vivo".
"""
import html
import logging
import os
import re
import time
from datetime import datetime

import bilancio as report
import config
from alerts import duration
from sorare_api import SorareError
from storage import JsonStore

log = logging.getLogger(__name__)

DAY = 86400
STATUS_LABEL = {"accepted": "✅ comprata", "cancelled": "❌ ritirata", "ended": "⌛ scaduta",
                "opened": "🟢 ancora in vendita", "unknown": "❔"}

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(config.TIMEZONE)
except Exception:  # niente database dei fusi orari: uso l'ora del telefono
    TZ = None


def local_now():
    return datetime.now(TZ) if TZ else datetime.now()


def parse_time(text):
    """"21:00", "21.30", "9" -> "21:00", "21:30", "09:00"; None se non è un'ora valida."""
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?", text.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _empty():
    return {"since": time.time(), "offers": 0, "relevant": 0, "sales": 0, "rt_up": 0, "rt_total": 0,
            "restarts": 0, "problems": []}


class DailySummary:
    def __init__(self, bot):
        self.bot = bot
        # Andamento del bot dall'ultimo riepilogo dell'amministratore (sopravvive ai riavvii).
        self.store = JsonStore(os.path.join(config.DATA_DIR, "daily.json"), _empty())
        self._counters = {}
        self._last_tick = None

    # --- raccolta dati (chiamata dal controllo ogni 30 secondi) ----------------------

    def tick(self):
        data = self.store.data
        for name in ("offers", "relevant", "sales"):
            now_value = self.bot.counters.get(name, 0)
            data[name] += max(0, now_value - self._counters.get(name, 0))
            self._counters[name] = now_value
        now = time.monotonic()
        if self.bot.stream and self._last_tick is not None:
            elapsed = min(now - self._last_tick, 120)
            data["rt_total"] += elapsed
            if self.bot.stream.connected:
                data["rt_up"] += elapsed
        self._last_tick = now
        self.store.save()

    def note_restart(self):
        self.store.data["restarts"] += 1
        self.store.save()

    def note_problem(self, text):
        first_line = re.sub(r"<[^>]+>", "", text).split("\n")[0][:120]
        self.store.data["problems"] = (self.store.data["problems"] + [first_line])[-10:]
        self.store.save()

    # --- invio all'ora scelta --------------------------------------------------------------

    async def check(self):
        now = local_now()
        today, clock = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        for chat_id, settings in self.bot.users.active():
            when = settings.summary_time
            user = self.bot.users.data[chat_id]
            if not when or clock < when or user.get("summary_last") == today:
                continue
            text = await self.build(chat_id)
            if await self.bot.notifier.send(text, chat_id) is not None:
                user["summary_last"] = today
                self.bot.users.save()
                if self.bot.users.is_admin(chat_id):
                    self.store.data = _empty()  # l'andamento del bot riparte dal riepilogo
                    self.store.save()

    def mark_sent_today_if_past(self, chat_id, when):
        """Se l'ora appena scelta è già passata, il primo riepilogo arriva domani (non subito)."""
        now = local_now()
        if when and now.strftime("%H:%M") >= when:
            self.bot.users.data[chat_id]["summary_last"] = now.strftime("%Y-%m-%d")
            self.bot.users.save()

    # --- testo ---------------------------------------------------------------------------

    async def build(self, chat_id):
        bot, users = self.bot, self.bot.users
        since = time.time() - DAY
        mine = {oid: e for oid, e in bot.notified.data.items()
                if isinstance(e, dict) and e.get("ts", 0) >= since
                and chat_id in report.recipients(e, users.admin_id)}
        note = ""
        try:
            if await report.refresh_outcomes(bot.client, mine):
                bot.notified.save()
        except SorareError:
            note = "\n<i>(Sorare non risponde: esiti forse non aggiornati)</i>"
        items = report.rows(mine, chat_id, users.admin_id, since)
        deals = [r for r in items if r["kind"] == "deal"]
        watch = [r for r in items if r["kind"] == "watch"]

        lines = [f"📰 <b>Riepilogo del {local_now().strftime('%d/%m')}</b> · ultime 24 ore", ""]
        if not items:
            lines.append("Nessun affare oggi: niente sotto le tue soglie.")
        else:
            lines.append(f"🚨 Affari: <b>{len(deals)}</b>" + (f" · {self._outcomes(deals)}" if deals else ""))
            if watch:
                lines.append(f"👀 Watchlist: <b>{len(watch)}</b> · {self._outcomes(watch)}")
            best = self._best(mine, chat_id)
            if best:
                lines.append(f"🏆 Miglior affare: {best}")

        if users.is_admin(chat_id):
            lines += ["", self._bot_status()]
        settings = users.settings(chat_id)
        lines += ["", f"<i>Prossimo riepilogo domani alle {settings.summary_time} · /riepilogo per cambiare · "
                      "/bilancio per gli ultimi 30 giorni</i>"]
        return "\n".join(lines) + note

    @staticmethod
    def _outcomes(items):
        sold = [r for r in items if r["status"] == "accepted"]
        parts = [f"comprate {len(sold)}"]
        waits = [r["end"] - r["sent"] for r in sold if r["end"]]
        if waits:
            waits.sort()
            middle = waits[len(waits) // 2]
            parts[0] += f" (in mediana dopo {report.duration(middle)})" if middle > 0 else " (prima dell'avviso)"
        still = sum(1 for r in items if r["status"] == "opened")
        if still:
            parts.append(f"ancora in vendita {still}")
        gone = sum(1 for r in items if r["status"] in ("cancelled", "ended"))
        if gone:
            parts.append(f"ritirate o scadute {gone}")
        return " · ".join(parts)

    @staticmethod
    def _best(entries, chat_id):
        """L'affare con lo sconto più alto ricevuto da questo utente, con il suo esito."""
        best, best_discount = None, -1
        for entry in entries.values():
            if entry.get("kinds", {}).get(chat_id, "deal") != "deal":
                continue
            discount = report.discount_of(entry)
            if discount is not None and discount > best_discount:
                best, best_discount = entry, discount
        if best is None:
            return None
        text = best.get("texts", {}).get(chat_id, best.get("text", ""))
        name = re.search(r"⚽ <b>(.*?)</b>", text)
        kind = re.search(r"Affare (.*?)</b>", text)
        price = best.get("price")
        if price is None:
            match = re.search(r"Prezzo: <b>([\d.]+) €", text)
            price = float(match.group(1)) if match else None
        status = STATUS_LABEL.get((best.get("outcome") or {}).get("status", "unknown"), "")
        label = html.unescape(name.group(1)) if name else "?"
        return (f"<b>{html.escape(label)}</b>" + (f" {kind.group(1)}" if kind else "")
                + (f" a {price:.2f} €" if price is not None else "") + f" (-{best_discount:.0f}%) · {status}")

    def _bot_status(self):
        data = self.store.data
        lines = [f"🤖 <b>Il bot</b> · ultime {duration(time.time() - data['since'])}"]
        if data["rt_total"]:
            share = data["rt_up"] / data["rt_total"] * 100
            lines.append(f"Tempo reale collegato: {share:.0f}% del tempo")
        lines.append(f"Offerte viste: {data['offers']} ({data['relevant']} dei campionati seguiti) · "
                     f"vendite registrate: {data['sales']}")
        lines.append(f"Riavvii imprevisti: {data['restarts']}" if data["restarts"] else "Riavvii imprevisti: nessuno")
        if data["problems"]:
            lines.append("Problemi: " + "; ".join(html.escape(p) for p in data["problems"]))
        else:
            lines.append("Problemi: nessuno ✅")
        return "\n".join(lines)
