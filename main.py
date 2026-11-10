"""SorareBot: notifica su Telegram le carte Classic messe in vendita sotto il prezzo di mercato."""
import asyncio
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import commands
import config
import watchlists
from notifier import Notifier
from pricing import ago, evaluate_deal, reference, to_eur
from realtime import OfferStream
from sorare_api import ComplexityError, SorareClient, SorareError
from storage import JsonStore
from users import Users

log = logging.getLogger("sorarebot")

RATES_REFRESH_SECONDS = 600
RARITY_LABEL = {"limited": "Limited 🟡", "rare": "Rare 🔴", "super_rare": "Super Rare 🔵", "unique": "Unique ⚫"}
CLOSED_LABEL = {"accepted": "✅ Venduta", "cancelled": "❌ Ritirata dal venditore", "ended": "⌛ Scaduta"}


def now_ts():
    return time.time()


def iso_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class SniperBot:
    def __init__(self):
        self.users = Users(config.CHAT_ID or 0)
        self.client = SorareClient()
        self.notifier = Notifier(config.TELEGRAM_TOKEN, config.CHAT_ID, config.DRY_RUN)
        path = lambda name: os.path.join(config.DATA_DIR, name)
        # stats: {"<slug>|<rarità>": {"sales": [[timestamp, prezzo €], ...], "updated_at"}}
        # (al posto di "sales" c'è "error" se lo storico non era disponibile)
        self.stats = JsonStore(path("stats.json"), {}, compact=True)
        # Il vecchio formato salvava solo la mediana, senza le vendite: va ricalcolato.
        self.stats.data = {k: v for k, v in self.stats.data.items() if "sales" in v or "error" in v}
        self.stats_dirty = False
        self.seen_sales = {}
        self._player_competitions = None  # id offerte vendute già registrate (il websocket può ripeterle)
        # notified: {"<id offerta>": {"ts", "msg_id", "text", "closed"}}
        self.notified = JsonStore(path("notified.json"), {})
        # roster: {"updated_at", "sources", "players": {slug: nome}, "competitions": {campionato: [slug]}}
        self.roster = JsonStore(path("roster.json"), {"updated_at": 0, "sources": [], "players": {}})
        # market: {"updated_at", "key", "on_sale": {rarità: {slug: True se la più economica è Classic}}}
        self.market = JsonStore(path("market.json"), {"updated_at": 0, "key": None, "on_sale": {}})
        self.stream = OfferStream(self.on_stream_offer, self.on_stream_connected) if config.REALTIME else None
        self.wake = asyncio.Event()
        self.rates = None
        self.rates_at = 0
        self.last_poll = None
        self.offer_page = 0
        self.history_page = 0
        self.started_at = now_ts()
        self.counters = {"offers": 0, "relevant": 0, "deals": 0}
        # Calcolo al volo: offerte arrivate prima della mediana, in attesa di essere rivalutate.
        self.pending = {}
        self.ondemand = asyncio.Queue()
        self.queued = set()
        self.inflight = set()
        commands.register(self)

    # --- dati di base -------------------------------------------------------------

    async def refresh_rates(self):
        if self.rates and now_ts() - self.rates_at < RATES_REFRESH_SECONDS:
            return
        try:
            self.rates = await self.client.exchange_rates()
            self.rates_at = now_ts()
            log.debug("Cambio ETH: %.2f €", self.rates["eth_eur"])
        except SorareError as e:
            if not self.rates:
                raise
            log.warning("Cambio ETH non aggiornato (%s), uso l'ultimo noto", e)

    async def refresh_roster(self):
        competitions = self.users.all_competitions()
        sources = sorted(competitions) + sorted(watchlists.EXTRA_PLAYERS)
        fresh = now_ts() - self.roster.data["updated_at"] < config.ROSTER_REFRESH_HOURS * 3600
        if (fresh and self.roster.data["sources"] == sources and self.roster.data["players"]
                and "competitions" in self.roster.data):
            return

        log.info("Scarico le rose delle squadre...")
        players, by_competition = {}, {}
        for competition in competitions:
            try:
                name, teams = await self.client.competition_teams(competition)
            except SorareError as e:
                log.error("Campionato %s non trovato (%s)", competition, e)
                continue
            log.info("%s: %d squadre", name, len(teams))
            for team in teams:
                try:
                    team_players = await self.client.team_players(team["slug"])
                except SorareError as e:
                    log.error("Rosa di %s non disponibile (%s)", team["name"], e)
                    continue
                log.debug("  %s: %d giocatori", team["name"], len(team_players))
                players.update({p["slug"]: p["displayName"] for p in team_players})
                by_competition.setdefault(competition, []).extend(p["slug"] for p in team_players)
        for slug in watchlists.EXTRA_PLAYERS:
            players.setdefault(slug, self.roster.data["players"].get(slug, slug))

        if not players:
            if self.roster.data["players"]:
                log.error("Nessun giocatore scaricato, tengo la lista precedente")
                return
            raise SorareError("Nessun giocatore da monitorare: controlla i campionati")
        self.roster.data = {"updated_at": now_ts(), "sources": sources, "players": players,
                            "competitions": by_competition}
        self.roster.save()
        self._player_competitions = None
        log.info("Giocatori monitorati: %d", len(players))

    def player_competitions(self, slug):
        """Campionati di un giocatore ("*" per i giocatori extra di watchlists.py, validi per tutti)."""
        if self._player_competitions is None:
            index = {}
            for competition, slugs in self.roster.data.get("competitions", {}).items():
                for player in slugs:
                    index.setdefault(player, set()).add(competition)
            for player in watchlists.EXTRA_PLAYERS:
                index.setdefault(player, set()).add("*")
            self._player_competitions = index
        return self._player_competitions.get(slug, set())

    async def refresh_market(self):
        """Quali giocatori hanno carte in vendita: le mediane si calcolano in anticipo solo per loro."""
        key = f"{self.roster.data['updated_at']}|{','.join(sorted(self.users.all_rarities()))}"
        fresh = now_ts() - self.market.data["updated_at"] < config.MARKET_REFRESH_HOURS * 3600
        if fresh and self.market.data["key"] == key:
            return
        slugs = list(self.roster.data["players"])
        on_sale = {}
        for rarity in self.users.all_rarities():
            on_sale[rarity] = await self.client.market_scan(slugs, rarity)
            log.info("%s: %d/%d giocatori con carte in vendita", rarity, len(on_sale[rarity]), len(slugs))
        self.market.data = {"updated_at": now_ts(), "key": key, "on_sale": on_sale}
        self.market.save()

    # --- mediane --------------------------------------------------------------------

    def all_keys(self):
        """Giocatori e rarità con carte in vendita: le loro mediane vengono tenute aggiornate."""
        players = self.roster.data["players"]
        on_sale = self.market.data["on_sale"]
        return [f"{slug}|{r}" for r in self.users.all_rarities() for slug in on_sale.get(r, {}) if slug in players]

    def ref_for(self, key):
        """Prezzo di riferimento attuale (mediana pesata e ultime vendite), None se mai calcolato."""
        entry = self.stats.data.get(key)
        if not entry or "sales" not in entry:
            return None
        return reference(entry["sales"], config.HISTORY_DAYS, config.HALF_LIFE_DAYS, config.RECENT_SALES_CHECK)

    def ready_count(self):
        return sum(1 for k in self.all_keys() if (self.ref_for(k) or {}).get("count", 0) >= config.MIN_SALES)

    def stale_keys(self):
        cutoff = now_ts() - config.STATS_REFRESH_HOURS * 3600
        on_sale = self.market.data["on_sale"]

        def order(key):
            slug, rarity = key.split("|")
            updated = self.stats.data.get(key, {}).get("updated_at", 0)
            # prima i mai calcolati, tra questi chi ha una Classic come più economica, poi i più vecchi
            return (updated > 0, not on_sale.get(rarity, {}).get(slug, False), updated)

        stale = [k for k in self.all_keys()
                 if k not in self.inflight and self.stats.data.get(k, {}).get("updated_at", 0) < cutoff]
        return sorted(stale, key=order)

    async def compute_key(self, key):
        """Scarica lo storico di un giocatore e ne calcola la mediana, poi rivaluta le offerte in attesa."""
        slug, rarity = key.split("|")
        since = iso_utc(datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS))
        self.inflight.add(key)
        try:
            history = await self._price_history(slug, rarity, since)
        except SorareError as e:
            log.warning("Storico di %s non disponibile: %s", slug, e)
            old = self.stats.data.get(key, {})
            self.stats.data[key] = {**old, "updated_at": now_ts()} if "sales" in old else \
                {"error": str(e)[:200], "updated_at": now_ts()}
            self.pending.pop(key, None)
            return False
        finally:
            self.inflight.discard(key)
        sales = []
        for sale in history:
            price = to_eur(sale["amounts"], self.rates)
            if price:
                ts = datetime.strptime(sale["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
                sales.append([int(ts), round(price, 2)])
        # Vendite arrivate in tempo reale mentre scaricavo, che lo storico non ha ancora.
        newest = max((ts for ts, _ in sales), default=0)
        sales += [s for s in self.stats.data.get(key, {}).get("sales", []) if s[0] > newest]
        sales.sort(reverse=True)
        self.stats.data[key] = {"sales": sales[:config.HISTORY_MAX_SALES], "updated_at": now_ts()}
        for offer in self.pending.pop(key, []):
            await self.handle_offer(offer, retry=True)
        return True

    async def ondemand_worker(self):
        """Calcola subito la mediana dei giocatori che ricevono offerte ma non ne hanno una."""
        while True:
            key = await self.ondemand.get()
            self.queued.discard(key)
            if key in self.inflight:
                continue  # la sta già calcolando il giro normale, che poi rivaluta le offerte
            try:
                await self.compute_key(key)
                self.stats.save()
            except Exception:
                log.exception("Errore nel calcolo al volo di %s", key)

    def _enqueue(self, key):
        if key not in self.queued and key not in self.inflight:
            self.queued.add(key)
            self.ondemand.put_nowait(key)

    async def refresh_stats(self, deadline):
        """Ricalcola le mediane scadute finché c'è tempo prima del prossimo giro."""
        stale = self.stale_keys()
        if not stale:
            return
        todo = iter(stale)
        done = 0

        async def worker():
            # Più calcoli contemporanei: il limite di chiamate al minuto resta quello del client.
            nonlocal done
            for key in todo:
                if time.monotonic() >= deadline or self.wake.is_set():
                    return
                if key in self.inflight:
                    continue
                await self.compute_key(key)
                done += 1
                if done % 200 == 0:
                    self.stats.save()
                if done % 100 == 0:
                    log.info("Mediane: %d calcolate, ne mancano %d", done, len(stale) - done)

        await asyncio.gather(*(worker() for _ in range(config.STATS_CONCURRENCY)))
        self.stats.save()
        total = len(self.all_keys())
        log.info("Mediane aggiornate: %d in questo giro, pronte %d/%d",
                 done, total - len(self.stale_keys()), total)

    async def _price_history(self, slug, rarity, since):
        if not self.history_page:
            self.history_page = 100 if self.client.authenticated else 25
        while True:
            try:
                return await self.client.price_history(slug, rarity, since, self.history_page)
            except ComplexityError:
                if self.history_page <= 5:
                    raise
                self.history_page = max(5, self.history_page // 2)
                log.info("Query troppo pesante, riduco le pagine di storico a %d", self.history_page)

    # --- offerte ----------------------------------------------------------------------

    @staticmethod
    def _classic_card(offer):
        """La carta dell'offerta se è una singola carta Classic in vendita per soldi."""
        cards = offer["senderSide"]["anyCards"]
        if len(cards) != 1 or offer["receiverSide"]["anyCards"]:
            return None  # pacchetti di più carte o scambi carta-contro-carta
        card = cards[0]
        return None if card["inSeasonEligible"] else card

    def _is_monitored(self, slug, rarity):
        """Giocatore e rarità controllati per gli affari (campionati e rarità degli utenti)."""
        return slug in self.roster.data["players"] and rarity in self.users.all_rarities()

    def _record_sale(self, offer):
        """Una carta è stata venduta: aggiorno subito il prezzo di riferimento di quel giocatore."""
        card = self._classic_card(offer)
        if not card or offer["id"] in self.seen_sales or not self.rates:
            return
        price = to_eur(offer["receiverSide"]["amounts"], self.rates)
        entry = self.stats.data.get(f"{card['anyPlayer']['slug']}|{card['rarityTyped']}")
        if not price or not entry or "sales" not in entry:
            return  # senza storico la vendita arriverà comunque con il prossimo download
        self.seen_sales[offer["id"]] = now_ts()
        if len(self.seen_sales) > 5000:
            self.seen_sales = dict(sorted(self.seen_sales.items(), key=lambda kv: kv[1])[-2500:])
        entry["sales"].insert(0, [int(now_ts()), round(price, 2)])
        del entry["sales"][config.HISTORY_MAX_SALES:]
        self.stats_dirty = True
        self.counters["sales"] = self.counters.get("sales", 0) + 1

    async def handle_offer(self, offer, retry=False):
        """Valuta un'offerta (dal tempo reale, dal controllo periodico o dopo il calcolo al volo)."""
        status = offer.get("status")
        if status == "accepted":
            self._record_sale(offer)
        if status in CLOSED_LABEL:
            await self._mark_closed(offer["id"], status)
            return
        if status not in (None, "opened"):
            return
        if not retry:
            self.counters["offers"] += 1

        card = self._classic_card(offer)
        if not card:
            return
        slug, rarity = card["anyPlayer"]["slug"], card["rarityTyped"]
        key = f"{slug}|{rarity}"
        watchers = self.users.watchers(slug, rarity)
        monitored = self._is_monitored(slug, rarity)
        if not watchers and not monitored:
            return
        if not retry:
            self.counters["relevant"] += 1

        if watchers and self.rates:
            # Watchlist: basta che il prezzo sia sotto il limite scelto, non serve lo storico.
            await self._watch_alerts(offer, card, key, watchers)
        if not monitored:
            return

        entry = self.stats.data.get(key)
        if entry is None:
            # Nessuno storico ancora: lo scarico subito e poi rivaluto questa offerta.
            waiting = self.pending.setdefault(key, [])
            if len(waiting) < 5:
                waiting.append(offer)
            self._enqueue(key)
            return
        if "sales" not in entry:
            return  # storico non disponibile al momento
        if now_ts() - entry.get("updated_at", 0) > config.STATS_REFRESH_HOURS * 3600:
            self._enqueue(key)  # storico vecchio: lo uso, ma intanto lo riscarico
        if not self.rates:
            return
        price = to_eur(offer["receiverSide"]["amounts"], self.rates)
        ref = self.ref_for(key)
        competitions = self.player_competitions(slug)
        sent = self._sent_to(offer["id"])
        text = None
        for chat_id, user in self.users.active():
            # Ognuno con le sue regole: campionati, rarità, soglie, prezzo massimo, pausa.
            if chat_id in sent or user.paused or rarity not in user.rarities:
                continue
            if "*" not in competitions and not competitions & set(user.competitions):
                continue
            if user.max_price_eur and price > user.max_price_eur:
                continue
            discount = evaluate_deal(price, ref, user.discount_percent, user.min_discount_eur,
                                     config.MIN_SALES, config.RECENT_MAX_AGE_DAYS)
            if discount is None:
                continue
            if text is None:
                name = card["anyPlayer"]["displayName"] or self.roster.data["players"].get(slug, slug)
                log.info("AFFARE: %s %s a %.2f € (-%.0f%%)", name, rarity, price, discount)
                text = self.deal_message(name, rarity, price, discount, ref, card)
            if await self._send_alert(offer["id"], chat_id, text, "deal", price, discount):
                self.counters["deals"] += 1

    async def _watch_alerts(self, offer, card, key, watchers):
        price = to_eur(offer["receiverSide"]["amounts"], self.rates)
        if price is None:
            return
        entry = self.stats.data.get(key)
        if not entry or now_ts() - entry.get("updated_at", 0) > config.STATS_REFRESH_HOURS * 3600:
            self._enqueue(key)  # così /lista e i prossimi avvisi mostrano anche il riferimento
        sent = self._sent_to(offer["id"])
        ref = self.ref_for(key)
        for chat_id, user, limit in watchers:
            if chat_id in sent or user.paused or price > limit:
                continue
            name = card["anyPlayer"]["displayName"] or card["anyPlayer"]["slug"]
            log.info("WATCHLIST: %s %s a %.2f € (limite %.2f €) per %s", name, card["rarityTyped"],
                     price, limit, chat_id)
            text = self.watch_message(name, card["rarityTyped"], price, limit, ref, card)
            discount = (ref["median"] - price) / ref["median"] * 100 if ref and ref.get("median") else None
            if await self._send_alert(offer["id"], chat_id, text, "watch", price, discount):
                self.counters["watch"] = self.counters.get("watch", 0) + 1

    def _sent_to(self, offer_id):
        """{chat_id: id messaggio} di chi ha già ricevuto questa offerta."""
        previous = self.notified.data.get(offer_id)
        if isinstance(previous, dict) and "msgs" in previous:
            return previous["msgs"]
        if previous is not None:
            return {self.users.admin_id: None}  # formato vecchio: era già stata notificata a te
        return {}

    async def _send_alert(self, offer_id, chat_id, text, kind, price, discount):
        """Invia una notifica e la ricorda, per aggiornarla quando la carta viene venduta e per /bilancio."""
        message_id = await self.notifier.send(text, chat_id)
        if message_id is None:
            return False
        entry = self.notified.data.get(offer_id)
        if not isinstance(entry, dict) or "msgs" not in entry:
            entry = self.notified.data[offer_id] = {"ts": now_ts(), "text": text, "msgs": {},
                                                    "price": round(price, 2), "discount": discount}
        entry["msgs"][chat_id] = message_id
        if kind != "deal":
            entry.setdefault("kinds", {})[chat_id] = kind
        if text != entry["text"]:
            entry.setdefault("texts", {})[chat_id] = text  # watchlist e affare hanno testi diversi
        self.notified.save()
        return True

    async def _mark_closed(self, offer_id, status):
        """Aggiorna la notifica di un affare quando qualcuno lo compra o viene ritirato."""
        entry = self.notified.data.get(offer_id)
        if not isinstance(entry, dict) or entry.get("closed") or not entry.get("text"):
            return
        entry["closed"] = status
        self.notified.save()
        msgs = entry.get("msgs") or {self.users.admin_id: entry.get("msg_id")}  # vecchio formato: solo admin
        for chat_id, message_id in msgs.items():
            text = entry.get("texts", {}).get(chat_id, entry["text"])
            await self.notifier.edit(chat_id, message_id, f"{text}\n\n<b>{CLOSED_LABEL[status]}</b>")

    async def on_stream_offer(self, offer):
        await self.handle_offer(offer)

    async def on_stream_connected(self):
        pass  # il ciclo di manutenzione si accorge da solo che il tempo reale è tornato

    async def poll_offers(self):
        """Controllo periodico: legge le offerte cambiate dall'ultimo giro."""
        poll_start = datetime.now(timezone.utc)
        if self.last_poll is None:
            since = poll_start - timedelta(minutes=config.OFFERS_BACKFILL_MINUTES)
        else:
            since = self.last_poll - timedelta(seconds=60)  # margine: meglio rivedere che perdere
        offers = await self._live_offers(iso_utc(since))
        self.last_poll = poll_start
        latest = max((o.get("createdAt") or "" for o in offers), default="")
        if latest:
            lag = (poll_start - datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
            if lag > timedelta(minutes=15):
                log.warning("Il flusso periodico di Sorare è in ritardo di %d minuti", lag.total_seconds() // 60)
        before = dict(self.counters)
        for offer in offers:
            await self.handle_offer(offer)
        self._prune_notified()
        log.info("Controllo offerte: %d lette · %d del campionato · %d affari",
                 len(offers), self.counters["relevant"] - before["relevant"],
                 self.counters["deals"] - before["deals"])

    async def _live_offers(self, since_iso):
        if not self.offer_page:
            self.offer_page = 200 if self.client.authenticated else 20
        offers, after, pages = [], None, 0
        while pages < config.OFFERS_MAX_PAGES:
            try:
                nodes, info = await self.client.live_offers_page(since_iso, self.offer_page, after)
            except ComplexityError:
                if self.offer_page <= 5:
                    raise
                self.offer_page = max(5, self.offer_page // 2)
                log.info("Query troppo pesante, riduco le pagine di offerte a %d", self.offer_page)
                continue
            offers.extend(nodes)
            pages += 1
            if not info["hasNextPage"]:
                return offers
            after = info["endCursor"]
        log.warning("Troppe offerte da leggere: mi fermo a %d (alza OFFERS_MAX_PAGES)", len(offers))
        return offers

    def _prune_notified(self):
        cutoff = now_ts() - config.NOTIFIED_MEMORY_DAYS * 86400
        ts = lambda v: v["ts"] if isinstance(v, dict) else v  # vecchio formato: solo timestamp
        self.notified.data = {k: v for k, v in self.notified.data.items() if ts(v) >= cutoff}
        self.notified.save()

    @staticmethod
    def deal_message(name, rarity, price, discount, ref, card):
        season = card["seasonYear"]
        url = f"https://sorare.com/football/cards/{card['slug']}"
        recent = " · ".join(f"{p:.2f} € ({ago(ts)})" for ts, p in ref["recent"])
        return (
            f"🚨 <b>Affare {RARITY_LABEL.get(rarity, rarity)}</b>\n"
            f"⚽ <b>{html.escape(name)}</b> · {season}-{str(season + 1)[-2:]} #{card['serialNumber']}\n"
            f"💶 Prezzo: <b>{price:.2f} €</b>\n"
            f"📊 Riferimento: {ref['median']:.2f} € (mediana pesata, {ref['count']} vendite)\n"
            f"🕒 Ultime vendite: {recent}\n"
            f"📉 Sconto: <b>-{discount:.0f}%</b> ({ref['median'] - price:.2f} € in meno)\n"
            f'🔗 <a href="{url}">Apri su Sorare</a>'
        )

    @staticmethod
    def watch_message(name, rarity, price, limit, ref, card):
        season = card["seasonYear"]
        url = f"https://sorare.com/football/cards/{card['slug']}"
        lines = [
            f"👀 <b>Watchlist · {RARITY_LABEL.get(rarity, rarity)}</b>",
            f"⚽ <b>{html.escape(name)}</b> · {season}-{str(season + 1)[-2:]} #{card['serialNumber']}",
            f"💶 Prezzo: <b>{price:.2f} €</b> (il tuo limite: {limit:.2f} €)",
        ]
        if ref and ref.get("median"):
            diff = (price - ref["median"]) / ref["median"] * 100
            lines.append(f"📊 Riferimento: {ref['median']:.2f} € ({diff:+.0f}%, {ref['count']} vendite)")
        if ref and ref.get("recent"):
            lines.append("🕒 Ultime vendite: " + " · ".join(f"{p:.2f} € ({ago(ts)})" for ts, p in ref["recent"]))
        lines.append(f'🔗 <a href="{url}">Apri su Sorare</a>')
        return "\n".join(lines)

    # --- ciclo principale -------------------------------------------------------

    async def maintenance_loop(self):
        while True:
            realtime_ok = self.stream and self.stream.connected
            interval = config.SAFETY_CHECK_SECONDS if realtime_ok else config.CHECK_INTERVAL_SECONDS
            deadline = time.monotonic() + interval
            self.wake.clear()
            try:
                await self.refresh_rates()
                await self.refresh_roster()
                await self.refresh_market()
                if self.stats_dirty:
                    self.stats.save()  # vendite arrivate in tempo reale
                    self.stats_dirty = False
                if realtime_ok:
                    # Il tempo reale vede tutto: il flusso periodico di Sorare può essere in ritardo
                    # anche di un'ora, quindi lo uso solo come piano B quando il websocket cade.
                    self.last_poll = datetime.now(timezone.utc)
                    self._prune_notified()
                else:
                    await self.poll_offers()
                await self.refresh_stats(deadline)
            except SorareError as e:
                log.error("Giro interrotto: %s", e)
            try:
                await asyncio.wait_for(self.wake.wait(), max(5, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                pass

    async def run(self):
        await self.client.authenticate()
        await self.notifier.start()
        tasks = []
        try:
            await self.refresh_rates()
            await self.refresh_roster()
            await self.refresh_market()
            admin = self.users.settings(self.users.admin_id)
            others = len(self.users.active()) - 1
            await self.notifier.send(
                f"🤖 <b>SorareBot avviato</b>\n"
                f"Campionati: {html.escape(', '.join(admin.competitions))}\n"
                f"Rarità: {', '.join(admin.rarities)}\n"
                f"Giocatori: {len(self.roster.data['players'])} · storici pronti: "
                f"{self.ready_count()}/{len(self.all_keys())}\n"
                f"Soglia: -{admin.discount_percent:g}% e almeno {admin.min_discount_eur:.2f} €\n"
                f"Tempo reale: {'sì' if self.stream else 'no'} · /aiuto per i comandi"
                + (f"\nAltri utenti: {others} (/utenti)" if others else "")
                + ("\n⏸ Notifiche in pausa (/riprendi)" if admin.paused else "")
            )
            tasks.append(asyncio.create_task(self.maintenance_loop()))
            tasks.append(asyncio.create_task(self.ondemand_worker()))
            if self.stream:
                tasks.append(asyncio.create_task(self.stream.run()))
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            self.stats.save()
            await self.notifier.stop()
            await self.client.close()


def main():
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for noisy in ("httpx", "telegram", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(SniperBot().run())
    except KeyboardInterrupt:
        log.info("Bot fermato.")


if __name__ == "__main__":
    main()
