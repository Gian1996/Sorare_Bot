"""Client asincrono per l'API GraphQL di Sorare.

Accesso con API key (o anonimo, solo per prove), limite di chiamate,
retry su errori di rete, 429 (Retry-After) e 5xx.
Il bot non usa mai email e password del tuo account.
"""
import asyncio
import logging
import time

import httpx

import config

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.sorare.com/graphql"
MAX_RETRIES = 5

# Chiamate al minuto per modalità (un po' sotto i limiti ufficiali 20/200).
CALLS_PER_MINUTE = {"anonimo": 18, "api key": 180}


class SorareError(Exception):
    pass


class ComplexityError(SorareError):
    """La query supera il limite di complessità: va spezzata in parti più piccole."""


# --- QUERY ----------------------------------------------------------------------

EXCHANGE_RATE_QUERY = """
query ExchangeRate { config { exchangeRate { ethRates { eurCents usdCents } } } }
"""

COMPETITION_TEAMS_QUERY = """
query CompetitionTeams($slug: String!, $after: String) {
  football {
    competition(slug: $slug) {
      name
      teams(first: 50, after: $after) {
        nodes { slug name }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

TEAM_PLAYERS_QUERY = """
query TeamPlayers($slug: String!, $after: String) {
  team(slug: $slug) {
    name
    activePlayers(first: 50, after: $after) {
      nodes { slug displayName }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# Campi di un'offerta, uguali per il controllo periodico e per il tempo reale.
OFFER_FIELDS = """
  id
  status
  createdAt
  senderSide {
    anyCards {
      slug rarityTyped seasonYear serialNumber inSeasonEligible
      anyPlayer { slug displayName }
    }
  }
  receiverSide { amounts { eurCents usdCents wei } anyCards { slug } }
"""

# Tutte le offerte di vendita calcio create o modificate dopo `since`.
# Nota: Player.lowestPriceAnyCard ignora il filtro inSeason, per questo non lo usiamo.
LIVE_OFFERS_QUERY = """
query LiveOffers($since: ISO8601DateTime!, $first: Int!, $after: String) {
  tokens {
    liveSingleSaleOffers(sport: FOOTBALL, updatedAfter: $since, first: $first, after: $after) {
      nodes { %s }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""" % OFFER_FIELDS

# Tempo reale: scatta quando un'offerta viene aperta, accettata, ritirata o scade.
OFFER_SUBSCRIPTION = """
subscription OnOfferUpdated { tokenOfferWasUpdated(sports: [FOOTBALL]) { %s } }
""" % OFFER_FIELDS

# Carta più economica in vendita per giocatore (qualsiasi stagione: il filtro inSeason
# viene ignorato da Sorare). Serve solo a sapere se c'è qualcosa sul mercato.
MARKET_QUERY = """
query Market($slugs: [String!], $rarity: Rarity) {
  players(slugs: $slugs) {
    slug
    ... on Player { lowestPriceAnyCard(rarity: $rarity) { inSeasonEligible } }
  }
}
"""
MARKET_BATCH = 100  # massimo consentito da Sorare per players(slugs:)

PRICE_HISTORY_QUERY = """
query PriceHistory($slug: String!, $rarity: Rarity!, $since: ISO8601DateTime!, $last: Int!, $before: String) {
  anyPlayer(slug: $slug) {
    ... on Player {
      tokenPrices(rarity: $rarity, seasonEligibility: CLASSIC, since: $since, last: $last, before: $before) {
        nodes { date amounts { eurCents usdCents wei } }
        pageInfo { hasPreviousPage startCursor }
      }
    }
  }
}
"""

class SorareClient:
    def __init__(self):
        self.http = httpx.AsyncClient(
            timeout=30,
            headers={"Content-Type": "application/json", "User-Agent": config.USER_AGENT},
        )
        self.mode = "anonimo"
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._min_interval = 60 / CALLS_PER_MINUTE["anonimo"]

    @property
    def authenticated(self):
        return self.mode != "anonimo"

    async def close(self):
        await self.http.aclose()

    # --- accesso -----------------------------------------------------------------

    async def authenticate(self):
        if config.SORARE_API_KEY:
            self.http.headers["APIKEY"] = config.SORARE_API_KEY
            self.mode = "api key"
        self._min_interval = 60 / CALLS_PER_MINUTE[self.mode]
        log.info("Accesso a Sorare: modalità %s (%d chiamate/min)", self.mode, CALLS_PER_MINUTE[self.mode])
        if self.mode == "anonimo":
            log.warning("Senza API key il bot è lento: creane una su https://sorare.com/settings/developer")

    # --- chiamate ----------------------------------------------------------------

    async def _throttle(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = max(now, self._next_slot) + self._min_interval

    async def query(self, query, variables=None):
        for attempt in range(MAX_RETRIES + 1):
            await self._throttle()
            try:
                resp = await self.http.post(
                    GRAPHQL_URL,
                    json={"query": query, "variables": variables or {}},
                )
            except httpx.HTTPError as e:
                delay = min(2 ** attempt * 2, 60)
                log.warning("Errore di rete (%s), riprovo tra %ds", e, delay)
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 429:
                delay = int(resp.headers.get("Retry-After", "30") or 30)
                log.warning("Troppe richieste a Sorare, attendo %ds", delay)
                await asyncio.sleep(delay)
                continue
            if resp.status_code == 401:
                raise SorareError("API key rifiutata da Sorare (401): controlla SORARE_API_KEY nel .env")
            if resp.status_code >= 500:
                delay = min(2 ** attempt * 2, 60)
                log.warning("Sorare ha risposto %d, riprovo tra %ds", resp.status_code, delay)
                await asyncio.sleep(delay)
                continue

            try:
                body = resp.json()
            except ValueError:
                raise SorareError(f"Risposta non valida (HTTP {resp.status_code})")
            if body.get("errors"):
                message = "; ".join(e.get("message", "?") for e in body["errors"])
                if "complexity" in message.lower():
                    raise ComplexityError(message)
                raise SorareError(message)
            if resp.status_code != 200 or "data" not in body:
                raise SorareError(f"HTTP {resp.status_code}")
            return body["data"]
        raise SorareError("Troppi tentativi falliti")

    # --- dati --------------------------------------------------------------------

    async def exchange_rates(self):
        data = await self.query(EXCHANGE_RATE_QUERY)
        rates = data["config"]["exchangeRate"]["ethRates"]
        return {"eth_eur": rates["eurCents"] / 100, "eth_usd": rates["usdCents"] / 100}

    async def competition_teams(self, slug):
        teams, after, name = [], None, slug
        while True:
            data = await self.query(COMPETITION_TEAMS_QUERY, {"slug": slug, "after": after})
            competition = data["football"]["competition"]
            name = competition["name"]
            conn = competition["teams"]
            teams.extend(conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                return name, teams
            after = conn["pageInfo"]["endCursor"]

    async def team_players(self, slug):
        players, after = [], None
        while True:
            data = await self.query(TEAM_PLAYERS_QUERY, {"slug": slug, "after": after})
            conn = data["team"]["activePlayers"]
            players.extend(conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                return players
            after = conn["pageInfo"]["endCursor"]

    async def market_scan(self, slugs, rarity):
        """Per ogni giocatore con carte in vendita: True se la più economica è Classic."""
        on_sale = {}
        for i in range(0, len(slugs), MARKET_BATCH):
            batch = slugs[i:i + MARKET_BATCH]
            try:
                players = (await self.query(MARKET_QUERY, {"slugs": batch, "rarity": rarity}))["players"]
            except SorareError as e:
                # Un giocatore non valido fa fallire il lotto: nel dubbio li considero tutti in vendita.
                log.warning("Controllo mercato fallito per un lotto (%s)", e)
                on_sale.update({slug: False for slug in batch})
                continue
            for player in players:
                card = player.get("lowestPriceAnyCard")
                if card:
                    on_sale[player["slug"]] = not card["inSeasonEligible"]
        return on_sale

    async def live_offers_page(self, since_iso, page_size, after=None):
        """Una pagina di offerte di vendita. Restituisce (offerte, pageInfo)."""
        data = await self.query(LIVE_OFFERS_QUERY, {"since": since_iso, "first": page_size, "after": after})
        conn = data["tokens"]["liveSingleSaleOffers"]
        return conn["nodes"], conn["pageInfo"]

    async def price_history(self, slug, rarity, since_iso, page_size):
        """Vendite Classic più recenti fino a `since_iso`, al massimo HISTORY_MAX_SALES."""
        sales, before = [], None
        for _ in range(config.HISTORY_MAX_PAGES):
            if len(sales) >= config.HISTORY_MAX_SALES:
                break  # per una mediana affidabile bastano le vendite più recenti
            data = await self.query(PRICE_HISTORY_QUERY, {
                "slug": slug, "rarity": rarity, "since": since_iso, "last": page_size, "before": before,
            })
            conn = (data.get("anyPlayer") or {}).get("tokenPrices")
            if not conn:
                break
            sales.extend(conn["nodes"])
            if not conn["pageInfo"]["hasPreviousPage"]:
                break
            before = conn["pageInfo"]["startCursor"]
        return sales
