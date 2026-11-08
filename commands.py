"""Comandi Telegram del bot. Ogni comando riceve gli argomenti e restituisce un testo HTML."""
import html
import time
import unicodedata

import config
from settings import VALID_RARITIES
from sorare_api import SorareError


def _plain(text):
    """Minuscolo e senza accenti, per cercare "joao" e trovare "João"."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _number(text):
    return float(text.replace(",", ".").replace("%", "").replace("€", ""))


def _ago(ts):
    minutes = int((time.time() - ts) / 60)
    if minutes < 60:
        return f"{minutes} min fa"
    if minutes < 48 * 60:
        return f"{minutes // 60} ore fa"
    return f"{minutes // 1440} giorni fa"


def register(bot):
    s = bot.settings

    async def aiuto(args):
        return (
            "<b>Comandi</b>\n"
            "/stato · come sta andando il bot\n"
            "/prezzo <i>nome</i> · mediana di un giocatore\n"
            "/soglia <i>25</i> · sconto minimo in %\n"
            "/minimo <i>2</i> · risparmio minimo in €\n"
            "/rarita <i>limited rare</i> · rarità da monitorare\n"
            "/campionati <i>serie-a-it ...</i> · campionati da monitorare\n"
            "/pausa · /riprendi · sospende le notifiche\n"
            "/reset · torna alle impostazioni di config.py"
        )

    async def stato(args):
        stream = bot.stream
        if not stream:
            realtime = "spento (controllo ogni minuto)"
        elif stream.connected:
            last = f", ultima offerta {_ago(stream.last_event)}" if stream.last_event else ""
            realtime = f"🟢 collegato ({stream.events} eventi{last})"
        else:
            realtime = "🔴 scollegato, riprovo (intanto controllo ogni minuto)"
        total = len(bot.all_keys())
        c = bot.counters
        return (
            f"<b>Stato</b> · attivo da {_ago(bot.started_at).replace(' fa', '')}\n"
            f"{'⏸ Notifiche in pausa' if s.paused else '▶️ Notifiche attive'}\n"
            f"Tempo reale: {realtime}\n"
            f"Accesso Sorare: {bot.client.mode}\n"
            f"Giocatori: {len(bot.roster.data['players'])}\n"
            f"Mediane pronte: {bot.ready_count()}/{total} giocatori×rarità con carte in vendita"
            f" ({len(bot.stale_keys())} da aggiornare, {bot.ondemand.qsize()} in calcolo al volo)\n"
            f"Offerte viste: {c['offers']} · del campionato: {c['relevant']} · affari: {c['deals']}\n\n"
            f"Campionati: {html.escape(', '.join(s.competitions))}\n"
            f"Rarità: {', '.join(s.rarities)}\n"
            f"Soglia: -{s.discount_percent:g}% e almeno {s.min_discount_eur:.2f} €"
        )

    async def prezzo(args):
        if not args:
            return "Scrivi il nome, es. <code>/prezzo lautaro</code>"
        query = _plain(" ".join(args))
        players = bot.roster.data["players"]
        matches = [slug for slug, name in players.items()
                   if query in _plain(name) or query in slug][:5]
        if not matches:
            return f"Nessun giocatore monitorato corrisponde a «{html.escape(' '.join(args))}»."
        lines = []
        for slug in matches:
            lines.append(f"⚽ <b>{html.escape(players[slug])}</b>")
            for rarity in s.rarities:
                st = bot.stats.data.get(f"{slug}|{rarity}")
                on_sale = slug in bot.market.data["on_sale"].get(rarity, {})
                if not st and not on_sale:
                    lines.append(f"   {rarity}: nessuna carta in vendita (la calcolo al primo annuncio)")
                elif not st or "count" not in st:
                    lines.append(f"   {rarity}: in calcolo")
                elif st["count"] < config.MIN_SALES:
                    lines.append(f"   {rarity}: troppo poche vendite ({st['count']})")
                else:
                    lines.append(f"   {rarity}: mediana {st['median']:.2f} € su {st['count']} vendite"
                                 f" in {st.get('days', config.HISTORY_DAYS)}gg ({_ago(st['updated_at'])})")
        return "\n".join(lines)

    async def soglia(args):
        try:
            value = _number(args[0])
            if not 1 <= value <= 95:
                raise ValueError
        except (IndexError, ValueError):
            return f"Soglia attuale: -{s.discount_percent:g}%. Per cambiarla: <code>/soglia 25</code> (da 1 a 95)"
        s.set("discount_percent", value)
        return f"✅ Ti avviso dagli sconti del {value:g}% in su."

    async def minimo(args):
        try:
            value = _number(args[0])
            if value < 0:
                raise ValueError
        except (IndexError, ValueError):
            return f"Risparmio minimo attuale: {s.min_discount_eur:.2f} €. Per cambiarlo: <code>/minimo 2</code>"
        s.set("min_discount_eur", value)
        return f"✅ Ti avviso solo se risparmi almeno {value:.2f} €."

    async def rarita(args):
        values = [a.lower().replace("-", "_") for a in args]
        wrong = [v for v in values if v not in VALID_RARITIES]
        if not values or wrong:
            return (f"Rarità attuali: {', '.join(s.rarities)}.\n"
                    f"Per cambiarle: <code>/rarita limited rare</code> (valori: {', '.join(VALID_RARITIES)})")
        s.set("rarities", list(dict.fromkeys(values)))
        bot.wake.set()
        return (f"✅ Monitoro: {', '.join(s.rarities)}.\n"
                f"Le mediane nuove si calcolano in background (/stato per l'avanzamento).")

    async def campionati(args):
        if not args:
            return (f"Campionati attuali:\n<code>{html.escape(' '.join(s.competitions))}</code>\n"
                    "Per cambiarli scrivi gli slug, es. <code>/campionati serie-a-it laliga-es</code>")
        found, wrong = [], []
        for slug in dict.fromkeys(a.lower() for a in args):
            try:
                name, teams = await bot.client.competition_teams(slug)
            except SorareError:
                wrong.append(slug)
                continue
            if teams:
                found.append((slug, name, len(teams)))
            else:
                wrong.append(slug)
        if wrong:
            return f"❌ Non trovati (o senza squadre): {html.escape(', '.join(wrong))}. Nessuna modifica."
        s.set("competitions", [slug for slug, _, _ in found])
        bot.wake.set()
        lines = "\n".join(f"• {html.escape(name)} ({n} squadre)" for _, name, n in found)
        return f"✅ Campionati aggiornati:\n{lines}\nScarico le rose e calcolo le mediane in background."

    async def pausa(args):
        s.set("paused", True)
        return "⏸ Notifiche in pausa. /riprendi per riattivarle."

    async def riprendi(args):
        s.set("paused", False)
        return "▶️ Notifiche riattivate."

    async def reset(args):
        s.reset()
        bot.wake.set()
        return "↩️ Impostazioni riportate a quelle di config.py.\n\n" + await stato([])

    n = bot.notifier
    n.add_command("aiuto", "Elenco dei comandi", aiuto)
    n.add_command("start", "Elenco dei comandi", aiuto)
    n.add_command("stato", "Come sta andando il bot", stato)
    n.add_command("prezzo", "Mediana di un giocatore", prezzo)
    n.add_command("soglia", "Sconto minimo in %", soglia)
    n.add_command("minimo", "Risparmio minimo in euro", minimo)
    n.add_command("rarita", "Rarità da monitorare", rarita)
    n.add_command("campionati", "Campionati da monitorare", campionati)
    n.add_command("pausa", "Sospendi le notifiche", pausa)
    n.add_command("riprendi", "Riattiva le notifiche", riprendi)
    n.add_command("reset", "Torna alle impostazioni di config.py", reset)
