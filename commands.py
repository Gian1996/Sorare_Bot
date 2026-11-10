"""Comandi Telegram del bot.

Ogni comando riceve (chat_id, nome utente, argomenti) e restituisce un testo HTML.
- /start è aperto a tutti: chi non è abilitato chiede l'accesso all'amministratore.
- Gli altri comandi funzionano solo per gli utenti approvati, ognuno sulle proprie impostazioni.
- /utenti, /approva, /rifiuta, /rimuovi sono solo per l'amministratore (CHAT_ID nel .env).
"""
import html
import time
import unicodedata

import bilancio as report
import config
from pricing import ago
from sorare_api import SorareError
from users import SEASON_LABEL, SEASONS, VALID_RARITIES, parse_season, stat_key

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


def _seasons_label(seasons):
    return " e ".join(SEASON_LABEL[x] for x in seasons)


def _max_label(s):
    return f"{s.max_price_eur:.2f} €" if s.max_price_eur else "nessun limite"


MAX_WATCHLIST = 50


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
            "/massimo <i>50</i> · prezzo massimo degli affari (0 = nessun limite)\n"
            "/rarita <i>limited rare</i> · rarità da monitorare\n"
            "/stagioni <i>classic inseason</i> · Classic, In Season o entrambe\n"
            "/campionati <i>serie-a-it ...</i> · campionati da monitorare\n"
            "/pausa · /riprendi · sospende le notifiche\n"
            "/bilancio <i>7</i> · quanti avvisi sono stati comprati davvero e quanto in fretta\n\n"
            "<b>Watchlist</b> · avvisi su giocatori precisi, anche se non sono affari\n"
            "/segui <i>lautaro 15 limited</i> · avvisami sotto i 15 € (aggiungi <i>inseason</i> per le In Season)\n"
            "/lista · giocatori seguiti\n"
            "/nonseguire <i>2</i> · smetti di seguire (numero da /lista)\n\n"
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
            f"Offerte viste: {c['offers']} · affari notificati: {c['deals']}"
            f" · avvisi watchlist: {c.get('watch', 0)}\n"
            f"Vendite registrate in tempo reale: {c.get('sales', 0)}\n\n"
            f"<b>Le tue impostazioni</b>\n"
            f"{'⏸ Notifiche in pausa' if s.paused else '▶️ Notifiche attive'}\n"
            f"Campionati: {html.escape(', '.join(s.competitions))}\n"
            f"Rarità: {', '.join(s.rarities)} · {_seasons_label(s.seasons)}\n"
            f"Soglia: -{s.discount_percent:g}% e almeno {s.min_discount_eur:.2f} €\n"
            f"Prezzo massimo: {_max_label(s)}\n"
            f"Watchlist: {len(users.watchlist(chat_id))} giocatori (/lista)"
        )
        if users.is_admin(chat_id):
            text += f"\n\nAccesso Sorare: {bot.client.mode} · utenti abilitati: {len(users.active())} (/utenti)"
            problems = bot.alerts.active()
            text += ("\n\n<b>Problemi in corso</b>\n" + "\n\n".join(problems)) if problems else \
                "\nProblemi in corso: nessuno ✅"
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
            for rarity, season in ((r, x) for r in s.rarities for x in s.seasons):
                key = stat_key(slug, rarity, season)
                entry = bot.stats.data.get(key)
                ref = bot.ref_for(key)
                rarity = f"{rarity} {SEASON_LABEL[season]}"
                if not entry:
                    bot._enqueue(key)
                    lines.append(f"   {rarity}: lo scarico adesso, riprova tra qualche secondo")
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
    async def massimo(chat_id, s, args):
        try:
            value = _number(args[0])
            if value < 0:
                raise ValueError
        except (IndexError, ValueError):
            return (f"Prezzo massimo attuale: {_max_label(s)}.\n"
                    "Per cambiarlo: <code>/massimo 50</code> · per toglierlo: <code>/massimo 0</code>")
        s.set("max_price_eur", value)
        if not value:
            return "✅ Nessun limite di prezzo: ti avviso per tutti gli affari."
        return f"✅ Ti avviso solo per gli affari fino a {value:.2f} €."

    # --- watchlist --------------------------------------------------------------------

    async def _find_player(query_words):
        """(slug, nome) oppure un messaggio di errore se non si trova o è ambiguo."""
        query = _plain(" ".join(query_words))
        players = bot.roster.data["players"]
        as_slug = "-".join(query.split())
        if as_slug in players:
            return (as_slug, players[as_slug]), None
        matches = [slug for slug, name in players.items() if query in _plain(name) or query in slug]
        exact = [slug for slug in matches if _plain(players[slug]) == query]
        if len(exact) == 1 or len(matches) == 1:
            slug = (exact or matches)[0]
            return (slug, players[slug]), None
        if matches:
            shown = "\n".join(f"• {html.escape(players[m])}: <code>{m}</code>" for m in matches[:8])
            more = f"\n…e altri {len(matches) - 8}" if len(matches) > 8 else ""
            return None, f"Più giocatori corrispondono, scrivi lo slug:\n{shown}{more}"
        # Fuori dai campionati monitorati: provo lo slug direttamente su Sorare.
        player = await bot.client.player(as_slug)
        if player:
            return (player["slug"], player["displayName"]), None
        return None, (f"Nessun giocatore trovato per «{html.escape(' '.join(query_words))}».\n"
                      "Fuori dai tuoi campionati serve lo slug, cioè l'ultima parte dell'indirizzo della "
                      "pagina del giocatore su Sorare, es. <code>/segui cristiano-ronaldo-dos-santos-aveiro 30 limited</code>")

    @for_users
    async def segui(chat_id, s, args):
        usage = ("Uso: <code>/segui nome prezzo rarità [inseason]</code>, es. <code>/segui lautaro 15 limited</code>\n"
                 "Ti avviso appena una sua carta va in vendita a quel prezzo o meno. "
                 "Senza <i>inseason</i> seguo le Classic.")
        args = " ".join(args).lower().replace("in season", "inseason").split()
        seasons = [parse_season(a) for a in args if parse_season(a)]
        args = [a for a in args if not parse_season(a)]
        rarities = [a.lower().replace("-", "_") for a in args if a.lower().replace("-", "_") in VALID_RARITIES]
        rest = [a for a in args if a.lower().replace("-", "_") not in VALID_RARITIES]
        if len(set(seasons)) > 1:
            return usage + "\nUna stagione per volta: per seguirle entrambe usa /segui due volte."
        season = seasons[0] if seasons else "classic"
        price = None
        if rest:
            try:
                price = _number(rest[-1])
                rest = rest[:-1]
            except ValueError:
                pass
        if not rest or price is None or price <= 0 or len(rarities) > 1:
            return usage
        if rarities:
            rarity = rarities[0]
        elif len(s.rarities) == 1:
            rarity = s.rarities[0]
        else:
            return usage + f"\nDimmi la rarità: {', '.join(VALID_RARITIES)}."
        if len(users.watchlist(chat_id)) >= MAX_WATCHLIST:
            return f"Hai già {MAX_WATCHLIST} giocatori in watchlist: togline qualcuno con /nonseguire."
        found, error = await _find_player(rest)
        if error:
            return error
        slug, name = found
        updated = users.watch_add(chat_id, slug, name, rarity, season, price)
        bot._enqueue(stat_key(slug, rarity, season))  # storico per mostrare il riferimento negli avvisi
        verb = "aggiornato" if updated else "aggiunto"
        return (f"👀 {html.escape(name)} {verb}: ti avviso per le {rarity} {SEASON_LABEL[season]} "
                f"fino a {price:.2f} €.\n"
                "/lista per vedere tutti i giocatori seguiti.")

    @for_users
    async def lista(chat_id, s, args):
        items = users.watchlist(chat_id)
        if not items:
            return "La watchlist è vuota. Aggiungi un giocatore con <code>/segui lautaro 15 limited</code>"
        lines = ["👀 <b>Watchlist</b>"]
        for i, item in enumerate(items, 1):
            season = item.get("season", "classic")
            ref = bot.ref_for(stat_key(item["slug"], item["rarity"], season))
            median = (f" · riferimento {ref['median']:.2f} €" if ref and ref.get("median") else "")
            lines.append(f"{i}. {html.escape(item['name'])} · {item['rarity']} {SEASON_LABEL[season]} · "
                         f"fino a <b>{item['price']:.2f} €</b>{median}")
        lines.append("\n/nonseguire <i>numero</i> per toglierne uno")
        if s.paused:
            lines.append("⏸ Notifiche in pausa: anche la watchlist è ferma (/riprendi)")
        return "\n".join(lines)

    @for_users
    async def nonseguire(chat_id, s, args):
        items = users.watchlist(chat_id)
        if not args:
            return "Uso: <code>/nonseguire 2</code> (il numero è quello di /lista) o <code>/nonseguire nome</code>"
        index = None
        if len(args) == 1 and args[0].isdigit():
            index = int(args[0]) - 1
            if not 0 <= index < len(items):
                return f"Non c'è il numero {args[0]} nella watchlist: guarda /lista."
        else:
            query = _plain(" ".join(args))
            matches = [i for i, it in enumerate(items) if query in _plain(it["name"]) or query in it["slug"]]
            if len(matches) != 1:
                return ("Nessun giocatore seguito corrisponde." if not matches else
                        "Più giocatori corrispondono: usa il numero di /lista.")
            index = matches[0]
        item = users.watch_remove(chat_id, index)
        return (f"🗑 Non seguo più {html.escape(item['name'])} "
                f"({item['rarity']} {SEASON_LABEL[item.get('season', 'classic')]}).")

    @for_users
    async def bilancio(chat_id, s, args):
        try:
            days = int(args[0]) if args else config.NOTIFIED_MEMORY_DAYS
            if not 1 <= days <= config.NOTIFIED_MEMORY_DAYS:
                raise ValueError
        except ValueError:
            return (f"Uso: <code>/bilancio</code> (ultimi {config.NOTIFIED_MEMORY_DAYS} giorni) "
                    f"o <code>/bilancio 7</code> (da 1 a {config.NOTIFIED_MEMORY_DAYS})")
        since = time.time() - days * 86400
        mine = {oid: e for oid, e in bot.notified.data.items()
                if isinstance(e, dict) and e.get("ts", 0) >= since
                and chat_id in report.recipients(e, users.admin_id)}
        note = ""
        try:
            if await report.refresh_outcomes(bot.client, mine):
                bot.notified.save()
        except SorareError as e:
            note = f"\n\n⚠️ Sorare non risponde ({html.escape(str(e)[:80])}): esiti forse non aggiornati."
        return report.summary(report.rows(mine, chat_id, users.admin_id, since), days) + note

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
    async def stagioni(chat_id, s, args):
        words = " ".join(args).lower().replace("in season", "inseason").split()
        values = [parse_season(w) for w in words]
        if not values or None in values:
            return (f"Stagioni attuali: {_seasons_label(s.seasons)}.\n"
                    "Per cambiarle: <code>/stagioni classic</code>, <code>/stagioni inseason</code> "
                    "o <code>/stagioni classic inseason</code>.\n"
                    "Le due hanno sempre prezzi di riferimento separati.")
        s.set("seasons", [x for x in SEASONS if x in values])
        bot.wake.set()
        extra = ""
        if "in_season" in s.seasons:
            extra = ("\nGli storici In Season si preparano per chi ha una In Season come carta più economica; "
                     "per gli altri li scarico al volo alla prima offerta.")
        return f"✅ Monitoro le carte {_seasons_label(s.seasons)}.{extra}"

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
        return "↩️ Impostazioni riportate a quelle predefinite (la watchlist resta).\n\n" + await stato(chat_id, "", [])

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
                             f"{_seasons_label(s.seasons)} · "
                             f"-{s.discount_percent:g}% e {s.min_discount_eur:.2f} €"
                             + (f" · max {s.max_price_eur:.2f} €" if s.max_price_eur else "")
                             + f" · watchlist {len(users.watchlist(chat_id))}"
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
    n.add_command("massimo", "Prezzo massimo degli affari", massimo)
    n.add_command("segui", "Segui un giocatore sotto un prezzo", segui)
    n.add_command("lista", "Giocatori seguiti", lista)
    n.add_command("nonseguire", "Smetti di seguire un giocatore", nonseguire)
    n.add_command("bilancio", "Quanti avvisi sono stati comprati", bilancio)
    n.add_command("rarita", "Rarità da monitorare", rarita)
    n.add_command("stagioni", "Classic, In Season o entrambe", stagioni)
    n.add_command("campionati", "Campionati da monitorare", campionati)
    n.add_command("pausa", "Sospendi le notifiche", pausa)
    n.add_command("riprendi", "Riattiva le notifiche", riprendi)
    n.add_command("reset", "Torna alle impostazioni predefinite", reset)
    n.add_command("utenti", "Elenco utenti", utenti, menu=False)
    n.add_command("approva", "Approva un utente", approva, menu=False)
    n.add_command("rifiuta", "Rifiuta una richiesta", rimuovi, menu=False)
    n.add_command("rimuovi", "Rimuovi un utente", rimuovi, menu=False)
