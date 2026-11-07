"""SorareBot: notifica su Telegram le carte Classic messe in vendita sotto il prezzo di mercato."""
import asyncio
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import config
import watchlists
from notifier import Notifier
from pricing import compute_stats, evaluate_deal, to_eur
from sorare_api import ComplexityError, SorareClient, SorareError
from storage import JsonStore

log = logging.getLogger("sorarebot")

RATES_REFRESH_SECONDS = 600
RARITY_LABEL = {"limited": "Limited 🟡", "rare": "Rare 🔴", "super_rare": "Super Rare 🔵", "unique": "Unique ⚫"}


def now_ts():
    return time.time()


class SniperBot:
    def __init__(self):
        self.client = SorareClient()
        self.notifier = Notifier(config.TELEGRAM_TOKEN, config.CHAT_ID, config.DRY_RUN)
        path = lambda name: os.path.join(config.DATA_DIR, name)
        # stats: {"<slug>|<rarità>": {"median", "p25", "count", "updated_at"}}
        self.stats = JsonStore(path("stats.json"), {})
        # notified: {"<id offerta>": timestamp}
        self.notified = JsonStore(path("notified.json"), {})
        # roster: {"updated_at", "sources", "players": {slug: nome}}
        self.roster = JsonStore(path("roster.json"), {"updated_at": 0, "sources": [], "players": {}})
        self.rates = None
        self.rates_at = 0
        self.last_poll = None
        self.offer_page = 0
        self.history_page = 0

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
        sources = sorted(config.COMPETITIONS) + sorted(watchlists.EXTRA_PLAYERS)
        fresh = now_ts() - self.roster.data["updated_at"] < config.ROSTER_REFRESH_HOURS * 3600
        if fresh and self.roster.data["sources"] == sources and self.roster.data["players"]:
            return

        log.info("Scarico le rose delle squadre...")
        players = {}
        for competition in config.COMPETITIONS:
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
                log.info("  %s: %d giocatori", team["name"], len(team_players))
                players.update({p["slug"]: p["displayName"] for p in team_players})
        for slug in watchlists.EXTRA_PLAYERS:
            players.setdefault(slug, self.roster.data["players"].get(slug, slug))

        if not players:
            if self.roster.data["players"]:
                log.error("Nessun giocatore scaricato, tengo la lista precedente")
                return
            raise SorareError("Nessun giocatore da monitorare: controlla COMPETITIONS in config.py")
        self.roster.data = {"updated_at": now_ts(), "sources": sources, "players": players}
        self.roster.save()
        log.info("Giocatori monitorati: %d", len(players))

    # --- mediane --------------------------------------------------------------------

    def _stale_keys(self):
        cutoff = now_ts() - config.STATS_REFRESH_HOURS * 3600
        keys = []
        for slug in self.roster.data["players"]:
            for rarity in config.RARITIES:
                key = f"{slug}|{rarity}"
                updated = self.stats.data.get(key, {}).get("updated_at", 0)
                if updated < cutoff:
                    keys.append((updated, key))
        keys.sort()  # prima quelli mai calcolati, poi i più vecchi
        return [key for _, key in keys]

    async def refresh_stats(self, deadline):
        """Ricalcola le mediane scadute finché c'è tempo prima del prossimo giro."""
        stale = self._stale_keys()
        if not stale:
            return
        now = datetime.now(timezone.utc)
        long_days = max(config.HISTORY_DAYS, config.HISTORY_FALLBACK_DAYS)
        since = (now - timedelta(days=long_days)).isoformat()
        recent_cutoff = (now - timedelta(days=config.HISTORY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        done = 0
        for key in stale:
            if time.monotonic() >= deadline:
                break
            slug, rarity = key.split("|")
            try:
                sales = await self._price_history(slug, rarity, since)
            except SorareError as e:
                log.warning("Storico di %s non disponibile: %s", slug, e)
                self.stats.data[key] = {**self.stats.data.get(key, {}), "updated_at": now_ts()}
                continue
            # Le date ISO in UTC si confrontano bene anche come stringhe.
            recent = [to_eur(s["amounts"], self.rates) for s in sales if s["date"] >= recent_cutoff]
            stats, days = compute_stats(recent), config.HISTORY_DAYS
            if stats["count"] < config.MIN_SALES and long_days > config.HISTORY_DAYS:
                stats = compute_stats([to_eur(s["amounts"], self.rates) for s in sales])
                days = long_days
            self.stats.data[key] = {**stats, "days": days, "updated_at": now_ts()}
            done += 1
            if done % 10 == 0:
                self.stats.save()
        self.stats.save()
        remaining = len(self._stale_keys())
        total = len(self.roster.data["players"]) * len(config.RARITIES)
        log.info("Mediane aggiornate: %d in questo giro, pronte %d/%d", done, total - remaining, total)

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

    # --- controllo mercato ---------------------------------------------------------

    async def check_market(self):
        """Legge le offerte nuove dall'ultimo giro e notifica quelle sotto la mediana."""
        poll_start = datetime.now(timezone.utc)
        if self.last_poll is None:
            since = poll_start - timedelta(minutes=config.OFFERS_BACKFILL_MINUTES)
        else:
            since = self.last_poll - timedelta(seconds=60)  # margine: meglio rivedere che perdere
        offers = await self._live_offers(since.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.last_poll = poll_start

        names = self.roster.data["players"]
        relevant = deals = 0
        for offer in offers:
            cards = offer["senderSide"]["anyCards"]
            if len(cards) != 1 or offer["receiverSide"]["anyCards"]:
                continue  # pacchetti di più carte o scambi carta-contro-carta
            card = cards[0]
            slug, rarity = card["anyPlayer"]["slug"], card["rarityTyped"]
            if slug not in names or rarity not in config.RARITIES or card["inSeasonEligible"]:
                continue
            relevant += 1
            stats = self.stats.data.get(f"{slug}|{rarity}")
            if not stats or offer["id"] in self.notified.data:
                continue
            price = to_eur(offer["receiverSide"]["amounts"], self.rates)
            discount = evaluate_deal(price, stats, config.DISCOUNT_PERCENT,
                                     config.MIN_DISCOUNT_EUR, config.MIN_SALES)
            if discount is None:
                continue
            deals += 1
            name = card["anyPlayer"]["displayName"] or names.get(slug, slug)
            log.info("AFFARE: %s %s a %.2f € (-%.0f%%)", name, rarity, price, discount)
            if await self.notifier.send(self._deal_message(name, rarity, price, discount, stats, card)):
                self.notified.data[offer["id"]] = now_ts()
        self._prune_notified()
        log.info("Offerte lette: %d · del campionato: %d · affari: %d", len(offers), relevant, deals)

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
        self.notified.data = {k: v for k, v in self.notified.data.items() if v >= cutoff}
        self.notified.save()

    @staticmethod
    def _deal_message(name, rarity, price, discount, stats, card):
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

    async def run(self):
        await self.client.authenticate()
        await self.notifier.start()
        try:
            await self.refresh_rates()
            await self.refresh_roster()
            ready = sum(1 for k in self._all_keys() if self.stats.data.get(k, {}).get("count", 0) >= config.MIN_SALES)
            await self.notifier.send(
                f"🤖 <b>SorareBot avviato</b>\n"
                f"Campionati: {html.escape(', '.join(config.COMPETITIONS))}\n"
                f"Giocatori: {len(self.roster.data['players'])} · mediane pronte: {ready}\n"
                f"Soglia: -{config.DISCOUNT_PERCENT}% e almeno {config.MIN_DISCOUNT_EUR:.2f} €"
            )
            while True:
                deadline = time.monotonic() + config.CHECK_INTERVAL_SECONDS
                try:
                    await self.refresh_rates()
                    await self.refresh_roster()
                    await self.check_market()
                    await self.refresh_stats(deadline)
                except SorareError as e:
                    log.error("Giro interrotto: %s", e)
                await asyncio.sleep(max(10, deadline - time.monotonic()))
        finally:
            self.stats.save()
            await self.notifier.stop()
            await self.client.close()

    def _all_keys(self):
        return [f"{s}|{r}" for s in self.roster.data["players"] for r in config.RARITIES]


def main():
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(SniperBot().run())
    except KeyboardInterrupt:
        log.info("Bot fermato.")


if __name__ == "__main__":
    main()
