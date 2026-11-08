"""Comandi Telegram del bot.

Ogni comando riceve (chat_id, nome utente, argomenti) e restituisce un testo HTML.
- /start è aperto a tutti: chi non è abilitato chiede l'accesso all'amministratore.
- Gli altri comandi funzionano solo per gli utenti approvati, ognuno sulle proprie impostazioni.
- /utenti, /approva, /rifiuta, /rimuovi sono solo per l'amministratore (CHAT_ID nel .env).
"""
import html
import time
import unicodedata

import config
from pricing import ago
from sorare_api import SorareError
from users import VALID_RARITIES

NOT_ALLOWED = "🔒 Non sei abilitato. Scrivi /start per chiedere l'accesso."


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
    users = bot.users

    # --- accesso -------------------------------------------------------------------

    def for_users(fn):
        """Il comando funziona solo per gli utenti abilitati e riceve le loro impostazioni."""
        async def wrapped(chat_id, name, args):
            if not users.is_active(chat_id):
                return NOT_ALLOWED
            if name and name != "?" and users.data[chat_id].get("name") != name:
                users.data[chat_id]["name"] = name  # nome aggiornato dal profilo Telegram
                users.save()
            return await fn(chat_id, users.settings(chat_id), args)
        return wrapped

    def for_admin(fn):
        async def wrapped(chat_id, name, args):
            if not users.is_admin(chat_id):
                return NOT_ALLOWED if not users.is_active(chat_id) else "🔒 Comando riservato all'amministratore."
            return await fn(args)
        return wrapped

    async def start(chat_id, name, args):
        if users.is_active(chat_id):
            return await aiuto(chat_id, name, args)
        if users.status(chat_id) == "pending":
            return "⏳ Richiesta già inviata, aspetta che l'amministratore ti approvi."
        users.add_pending(chat_id, name)
        await bot.notifier.send(
            f"👤 <b>Nuova richiesta di accesso</b>\n{html.escape(name)} (id <code>{chat_id}</code>)\n"
            f"/approva {chat_id} · /rifiuta {chat_id}")
        return "👋 Ciao! Ho inviato la tua richiesta all'amministratore: ti scrivo appena ti approva."

    # --- comandi per tutti gli utenti abilitati ---------------------------------------

    @for_users
    async def aiuto(chat_id, s, args):
        text = (
            "<b>Comandi</b>\n"
            "/stato · come sta andando il bot e le tue impostazioni\n"
            "/prezzo <i>nome</i> · prezzo di riferimento e ultime vendite\n"
            "/soglia <i>25</i> · sconto minimo in %\n"
            "/minimo <i>2</i> · risparmio minimo in €\n"
            "/rarita <i>limited rare</i> · rarità da monitorare\n"
            "/campionati <i>serie-a-it ...</i> · campionati da monitorare\n"
            "/pausa · /riprendi · sospende le notifiche\n"
            "/reset · torna alle impostazioni predefinite"
        )
        if users.is_admin(chat_id):
            text += ("\n\n<b>Amministratore</b>\n"
                     "/utenti · elenco utenti\n"
                     "/approva <i>id</i> · /rifiuta <i>id</i> · /rimuovi <i>id</i>")
        return text

    @for_users
    async def stato(chat_id, s, args):
        stream = bot.stream
        if not stream:
            realtime = "spento (controllo ogni minuto)"
        elif stream.connected:
            last = f", ultima offerta {_ago(stream.last_event)}" if stream.last_event else ""
            realtime = f"🟢 collegato ({stream.events} eventi{last})"
        else:
            realtime = "🔴 scollegato, riprovo (intanto controllo ogni minuto)"
        c = bot.counters
        text = (
            f"<b>Stato</b> · attivo da {_ago(bot.started_at).replace(' fa', '')}\n"
            f"Tempo reale: {realtime}\n"
            f"Giocatori monitorati: {len(bot.roster.data['players'])}\n"
            f"Storici pronti: {bot.ready_count()}/{len(bot.all_keys())}"
            f" ({len(bot.stale_keys())} da aggiornare, {bot.ondemand.qsize()} in calcolo al volo)\n"
            f"Offerte viste: {c['offers']} · affari notificati: {c['deals']}\n"
            f"Vendite registrate in tempo reale: {c.get('sales', 0)}\n\n"
            f"<b>Le tue impostazioni</b>\n"
            f"{'⏸ Notifiche in pausa' if s.paused else '▶️ Notifiche attive'}\n"
            f"Campionati: {html.escape(', '.join(s.competitions))}\n"
            f"Rarità: {', '.join(s.rarities)}\n"
            f"Soglia: -{s.discount_percent:g}% e almeno {s.min_discount_eur:.2f} €"
        )
        if users.is_admin(chat_id):
            text += f"\n\nAccesso Sorare: {bot.client.mode} · utenti abilitati: {len(users.active())} (/utenti)"
        return text

    @for_users
    async def prezzo(chat_id, s, args):
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
                key = f"{slug}|{rarity}"
                entry = bot.stats.data.get(key)
                ref = bot.ref_for(key)
                on_sale = slug in bot.market.data["on_sale"].get(rarity, {})
                if not entry and not on_sale:
                    lines.append(f"   {rarity}: nessuna carta in vendita (lo calcolo al primo annuncio)")
                elif not entry:
                    lines.append(f"   {rarity}: in calcolo")
                elif ref is None:
                    lines.append(f"   {rarity}: storico non disponibile al momento")
                elif ref["count"] < config.MIN_SALES:
                    lines.append(f"   {rarity}: troppo poche vendite ({ref['count']} in {config.HISTORY_DAYS}gg)")
                else:
                    recent = " · ".join(f"{p:.2f} € ({ago(ts)})" for ts, p in ref["recent"])
                    lines.append(f"   {rarity}: riferimento <b>{ref['median']:.2f} €</b> "
                                 f"(mediana pesata, {ref['count']} vendite)\n"
                                 f"      ultime: {recent}")
        return "\n".join(lines)

    @for_users
    async def soglia(chat_id, s, args):
        try:
            value = _number(args[0])
            if not 1 <= value <= 95:
                raise ValueError
        except (IndexError, ValueError):
            return f"Soglia attuale: -{s.discount_percent:g}%. Per cambiarla: <code>/soglia 25</code> (da 1 a 95)"
        s.set("discount_percent", value)
        return f"✅ Ti avviso dagli sconti del {value:g}% in su."

    @for_users
    async def minimo(chat_id, s, args):
        try:
            value = _number(args[0])
            if value < 0:
                raise ValueError
        except (IndexError, ValueError):
            return f"Risparmio minimo attuale: {s.min_discount_eur:.2f} €. Per cambiarlo: <code>/minimo 2</code>"
        s.set("min_discount_eur", value)
        return f"✅ Ti avviso solo se risparmi almeno {value:.2f} €."

    @for_users
    async def rarita(chat_id, s, args):
        values = [a.lower().replace("-", "_") for a in args]
        wrong = [v for v in values if v not in VALID_RARITIES]
        if not values or wrong:
            return (f"Rarità attuali: {', '.join(s.rarities)}.\n"
                    f"Per cambiarle: <code>/rarita limited rare</code> (valori: {', '.join(VALID_RARITIES)})")
        s.set("rarities", list(dict.fromkeys(values)))
        bot.wake.set()
        return (f"✅ Monitoro: {', '.join(s.rarities)}.\n"
                f"Gli storici nuovi si calcolano in background (/stato per l'avanzamento).")

    @for_users
    async def campionati(chat_id, s, args):
        if not args:
            return (f"Campionati attuali:\n<code>{html.escape(' '.join(s.competitions))}</code>\n"
                    "Per cambiarli scrivi gli slug, es. <code>/campionati serie-a-it laliga-es</code>\n"
                    "Principali: serie-a-it, premier-league-gb-eng, laliga-es, bundesliga-de, "
                    "ligue-1-fr, primeira-liga-pt")
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
        return f"✅ Campionati aggiornati:\n{lines}\nScarico le rose e gli storici in background."

    @for_users
    async def pausa(chat_id, s, args):
        s.set("paused", True)
        return "⏸ Notifiche in pausa. /riprendi per riattivarle."

    @for_users
    async def riprendi(chat_id, s, args):
        s.set("paused", False)
        return "▶️ Notifiche riattivate."

    @for_users
    async def reset(chat_id, s, args):
        s.reset()
        bot.wake.set()
        return "↩️ Impostazioni riportate a quelle predefinite.\n\n" + await stato(chat_id, "", [])

    # --- amministratore -------------------------------------------------------------

    def _target(args):
        if not args or args[0] not in users.data:
            return None
        return args[0]

    @for_admin
    async def utenti(args):
        labels = {"admin": "👑 amministratore", "approved": "✅ abilitato", "pending": "⏳ in attesa"}
        lines = ["<b>Utenti</b>"]
        for chat_id, u in users.data.items():
            s = users.settings(chat_id)
            lines.append(f"{labels.get(u['status'], u['status'])} · {html.escape(u['name'])} "
                         f"(<code>{chat_id}</code>)")
            if u["status"] != "pending":
                lines.append(f"   {', '.join(s.competitions)} · {', '.join(s.rarities)} · "
                             f"-{s.discount_percent:g}% e {s.min_discount_eur:.2f} €"
                             + (" · ⏸" if s.paused else ""))
        return "\n".join(lines)

    @for_admin
    async def approva(args):
        target = _target(args)
        if not target or users.status(target) != "pending":
            return "Uso: <code>/approva id</code> (l'id è nella richiesta o in /utenti)"
        users.approve(target)
        bot.wake.set()
        await bot.notifier.send("✅ <b>Sei abilitato!</b> Da ora ricevi gli affari di Sorare.\n"
                                "Scrivi /aiuto per vedere come personalizzarli.", target)
        return f"✅ {html.escape(users.data[target]['name'])} abilitato."

    @for_admin
    async def rimuovi(args):
        target = _target(args)
        if not target or users.is_admin(target):
            return "Uso: <code>/rimuovi id</code> (l'id è in /utenti; l'amministratore non si può rimuovere)"
        name = users.data[target]["name"]
        was_active = users.is_active(target)
        users.remove(target)
        bot.wake.set()
        if was_active:
            await bot.notifier.send("🔒 Il tuo accesso al bot è stato rimosso.", target)
        return f"🗑 {html.escape(name)} rimosso."

    n = bot.notifier
    n.add_command("start", "Inizia / chiedi l'accesso", start)
    n.add_command("aiuto", "Elenco dei comandi", aiuto)
    n.add_command("stato", "Come sta andando il bot", stato)
    n.add_command("prezzo", "Prezzo di riferimento di un giocatore", prezzo)
    n.add_command("soglia", "Sconto minimo in %", soglia)
    n.add_command("minimo", "Risparmio minimo in euro", minimo)
    n.add_command("rarita", "Rarità da monitorare", rarita)
    n.add_command("campionati", "Campionati da monitorare", campionati)
    n.add_command("pausa", "Sospendi le notifiche", pausa)
    n.add_command("riprendi", "Riattiva le notifiche", riprendi)
    n.add_command("reset", "Torna alle impostazioni predefinite", reset)
    n.add_command("utenti", "Elenco utenti", utenti, menu=False)
    n.add_command("approva", "Approva un utente", approva, menu=False)
    n.add_command("rifiuta", "Rifiuta una richiesta", rimuovi, menu=False)
    n.add_command("rimuovi", "Rimuovi un utente", rimuovi, menu=False)
