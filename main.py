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
from pricing import compute_stats, evaluate_deal, to_eur
from realtime import OfferStream
from settings import Settings
from sorare_api import ComplexityError, SorareClient, SorareError
from storage import JsonStore

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
        self.settings = Settings()
        self.client = SorareClient()
        self.notifier = Notifier(config.TELEGRAM_TOKEN, config.CHAT_ID, config.DRY_RUN)
        path = lambda name: os.path.join(config.DATA_DIR, name)
        # stats: {"<slug>|<rarità>": {"median", "p25", "count", "days", "updated_at"}}
        self.stats = JsonStore(path("stats.json"), {})
        # notified: {"<id offerta>": {"ts", "msg_id", "text", "closed"}}
        self.notified = JsonStore(path("notified.json"), {})
        # roster: {"updated_at", "sources", "players": {slug: nome}}
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
        competitions = self.settings.competitions
        sources = sorted(competitions) + sorted(watchlists.EXTRA_PLAYERS)
        fresh = now_ts() - self.roster.data["updated_at"] < config.ROSTER_REFRESH_HOURS * 3600
        if fresh and self.roster.data["sources"] == sources and self.roster.data["players"]:
            return

        log.info("Scarico le rose delle squadre...")
        players = {}
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
        for slug in watchlists.EXTRA_PLAYERS:
            players.setdefault(slug, self.roster.data["players"].get(slug, slug))

        if not players:
            if self.roster.data["players"]:
                log.error("Nessun giocatore scaricato, tengo la lista precedente")
                return
            raise SorareError("Nessun giocatore da monitorare: controlla i campionati")
        self.roster.data = {"updated_at": now_ts(), "sources": sources, "players": players}
        self.roster.save()
        log.info("Giocatori monitorati: %d", len(players))

    async def refresh_market(self):
        """Quali giocatori hanno carte in vendita: le mediane si calcolano in anticipo solo per loro."""
        key = f"{self.roster.data['updated_at']}|{','.join(sorted(self.settings.rarities))}"
        fresh = now_ts() - self.market.data["updated_at"] < config.MARKET_REFRESH_HOURS * 3600
        if fresh and self.market.data["key"] == key:
            return
        slugs = list(self.roster.data["players"])
        on_sale = {}
        for rarity in self.settings.rarities:
            on_sale[rarity] = await self.client.market_scan(slugs, rarity)
            log.info("%s: %d/%d giocatori con carte in vendita", rarity, len(on_sale[rarity]), len(slugs))
        self.market.data = {"updated_at": now_ts(), "key": key, "on_sale": on_sale}
        self.market.save()

    # --- mediane --------------------------------------------------------------------

    def all_keys(self):
        """Giocatori e rarità con carte in vendita: le loro mediane vengono tenute aggiornate."""
        players = self.roster.data["players"]
        on_sale = self.market.data["on_sale"]
        return [f"{slug}|{r}" for r in self.settings.rarities for slug in on_sale.get(r, {}) if slug in players]

    def ready_count(self):
        return sum(1 for k in self.all_keys() if self.stats.data.get(k, {}).get("count", 0) >= config.MIN_SALES)

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
        now = datetime.now(timezone.utc)
        long_days = max(config.HISTORY_DAYS, config.HISTORY_FALLBACK_DAYS)
        self.inflight.add(key)
        try:
            sales = await self._price_history(slug, rarity, iso_utc(now - timedelta(days=long_days)))
        except SorareError as e:
            log.warning("Storico di %s non disponibile: %s", slug, e)
            self.stats.data[key] = {**self.stats.data.get(key, {}), "updated_at": now_ts()}
            self.pending.pop(key, None)
            return False
        finally:
            self.inflight.discard(key)
        # Le date ISO in UTC si confrontano bene anche come stringhe.
        recent_cutoff = iso_utc(now - timedelta(days=config.HISTORY_DAYS))
        recent = [to_eur(s["amounts"], self.rates) for s in sales if s["date"] >= recent_cutoff]
        stats, days = compute_stats(recent), config.HISTORY_DAYS
        if stats["count"] < config.MIN_SALES and long_days > config.HISTORY_DAYS:
            stats = compute_stats([to_eur(s["amounts"], self.rates) for s in sales])
            days = long_days
        self.stats.data[key] = {**stats, "days": days, "updated_at": now_ts()}
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
                if done % 25 == 0:
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

    async def handle_offer(self, offer, retry=False):
        """Valuta un'offerta (dal tempo reale, dal controllo periodico o dopo il calcolo al volo)."""
        status = offer.get("status")
        if status in CLOSED_LABEL:
            await self._mark_closed(offer["id"], status)
            return
        if status not in (None, "opened"):
            return
        if not retry:
            self.counters["offers"] += 1

        cards = offer["senderSide"]["anyCards"]
        if len(cards) != 1 or offer["receiverSide"]["anyCards"]:
            return  # pacchetti di più carte o scambi carta-contro-carta
        card = cards[0]
        slug, rarity = card["anyPlayer"]["slug"], card["rarityTyped"]
        names = self.roster.data["players"]
        if slug not in names or rarity not in self.settings.rarities or card["inSeasonEligible"]:
            return
        if not retry:
            self.counters["relevant"] += 1

        key = f"{slug}|{rarity}"
        stats = self.stats.data.get(key)
        if stats is None:
            # Nessuna mediana ancora: la calcolo subito e poi rivaluto questa offerta.
            waiting = self.pending.setdefault(key, [])
            if len(waiting) < 5:
                waiting.append(offer)
            self._enqueue(key)
            return
        if "count" not in stats:
            return  # storico non disponibile al momento
        if now_ts() - stats.get("updated_at", 0) > config.STATS_REFRESH_HOURS * 3600:
            self._enqueue(key)  # mediana vecchia: la uso, ma intanto la aggiorno
        if offer["id"] in self.notified.data or not self.rates:
            return
        price = to_eur(offer["receiverSide"]["amounts"], self.rates)
        discount = evaluate_deal(price, stats, self.settings.discount_percent,
                                 self.settings.min_discount_eur, config.MIN_SALES)
        if discount is None:
            return
        name = card["anyPlayer"]["displayName"] or names.get(slug, slug)
        log.info("AFFARE: %s %s a %.2f € (-%.0f%%)", name, rarity, price, discount)
        if self.settings.paused:
            return
        self.counters["deals"] += 1
        text = self.deal_message(name, rarity, price, discount, stats, card)
        message_id = await self.notifier.send(text)
        if message_id is not None:
            self.notified.data[offer["id"]] = {"ts": now_ts(), "msg_id": message_id, "text": text}
            self.notified.save()

    async def _mark_closed(self, offer_id, status):
        """Aggiorna la notifica di un affare quando qualcuno lo compra o viene ritirato."""
        entry = self.notified.data.get(offer_id)
        if not isinstance(entry, dict) or entry.get("closed") or not entry.get("text"):
            return
        entry["closed"] = status
        self.notified.save()
        await self.notifier.edit(entry["msg_id"], f"{entry['text']}\n\n<b>{CLOSED_LABEL[status]}</b>")

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
    def deal_message(name, rarity, price, discount, stats, card):
        season = card["seasonYear"]
        url = f"https://sorare.com/football/cards/{card['slug']}"
        return (
            f"🚨 <b>Affare {RARITY_LABEL.get(rarity, rarity)}</b>\n"
            f"⚽ <b>{html.escape(name)}</b> · {season}-{str(season + 1)[-2:]} #{card['serialNumber']}\n"
            f"💶 Prezzo: <b>{price:.2f} €</b>\n"
            f"📊 Mediana {stats.get('days', config.HISTORY_DAYS)}gg: {stats['median']:.2f} € "
            f"({stats['count']} vendite)\n"
            f"📉 Sconto: <b>-{discount:.0f}%</b> ({stats['median'] - price:.2f} € in meno)\n"
            f'🔗 <a href="{url}">Apri su Sorare</a>'
        )

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
            await self.notifier.send(
                f"🤖 <b>SorareBot avviato</b>\n"
                f"Campionati: {html.escape(', '.join(self.settings.competitions))}\n"
                f"Rarità: {', '.join(self.settings.rarities)}\n"
                f"Giocatori: {len(self.roster.data['players'])} · mediane pronte: "
                f"{self.ready_count()}/{len(self.all_keys())}\n"
                f"Soglia: -{self.settings.discount_percent:g}% e almeno {self.settings.min_discount_eur:.2f} €\n"
                f"Tempo reale: {'sì' if self.stream else 'no'} · /aiuto per i comandi"
                + ("\n⏸ Notifiche in pausa (/riprendi)" if self.settings.paused else "")
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
