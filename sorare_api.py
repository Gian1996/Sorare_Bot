"""Client asincrono per l'API GraphQL di Sorare.

Gestisce autenticazione (API key / login / anonimo), limite di chiamate,
retry su errori di rete, 429 (Retry-After), 5xx e token scaduti.
"""
import asyncio
import logging
import time

import bcrypt
import httpx

import config

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.sorare.com/graphql"
SALT_URL = "https://api.sorare.com/api/v1/users/{email}"
MAX_RETRIES = 5

# Chiamate al minuto per modalità (un po' sotto i limiti ufficiali 20/60/200).
CALLS_PER_MINUTE = {"anonimo": 18, "login": 55, "api key": 180}


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

SIGN_IN_MUTATION = """
mutation SignIn($input: signInInput!) {
  signIn(input: $input) {
    currentUser { slug }
    jwtToken(aud: "%s") { token expiredAt }
    otpSessionChallenge
    errors { message }
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
        self.jwt = None
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._min_interval = 60 / CALLS_PER_MINUTE["anonimo"]

    @property
    def authenticated(self):
        return self.mode != "anonimo"

    async def close(self):
        await self.http.aclose()

    # --- autenticazione ---------------------------------------------------------

    async def authenticate(self):
        if config.SORARE_API_KEY:
            self.http.headers["APIKEY"] = config.SORARE_API_KEY
            self.mode = "api key"
        if config.USE_PASSWORD_LOGIN and config.SORARE_EMAIL and config.SORARE_PASSWORD and config.AUDIENCE:
            if await self._login() and self.mode == "anonimo":
                self.mode = "login"
        self._min_interval = 60 / CALLS_PER_MINUTE[self.mode]
        log.info("Accesso a Sorare: modalità %s (%d chiamate/min)", self.mode, CALLS_PER_MINUTE[self.mode])
        if self.mode == "anonimo":
            log.warning("Senza API key il bot è lento: creane una su https://sorare.com/settings/developer")

    async def _login(self):
        try:
            resp = await self.http.get(SALT_URL.format(email=config.SORARE_EMAIL))
            resp.raise_for_status()
            salt = resp.json()["salt"]
            hashed = bcrypt.hashpw(config.SORARE_PASSWORD.encode(), salt.encode()).decode()
            resp = await self.http.post(GRAPHQL_URL, json={
                "query": SIGN_IN_MUTATION % config.AUDIENCE,
                "variables": {"input": {"email": config.SORARE_EMAIL, "password": hashed}},
            })
            result = (resp.json().get("data") or {}).get("signIn") or {}
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.error("Login Sorare fallito: %s", e)
            return False

        if result.get("otpSessionChallenge"):
            log.error("Login Sorare: l'account ha la 2FA attiva, il login automatico non è possibile. "
                      "Usa una SORARE_API_KEY.")
            return False
        token = (result.get("jwtToken") or {}).get("token")
        if not token:
            errors = "; ".join(e.get("message", "?") for e in result.get("errors") or []) or "risposta vuota"
            log.error("Login Sorare fallito: %s", errors)
            return False
        self.jwt = token
        log.info("Login Sorare riuscito (%s)", (result.get("currentUser") or {}).get("slug"))
        return True

    def _auth_headers(self):
        if not self.jwt:
            return {}
        return {"Authorization": f"Bearer {self.jwt}", "JWT-AUD": config.AUDIENCE}

    # --- chiamate ----------------------------------------------------------------

    async def _throttle(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = max(now, self._next_slot) + self._min_interval

    async def query(self, query, variables=None):
        relogged = False
        for attempt in range(MAX_RETRIES + 1):
            await self._throttle()
            try:
                resp = await self.http.post(
                    GRAPHQL_URL,
                    json={"query": query, "variables": variables or {}},
                    headers=self._auth_headers(),
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
            if resp.status_code == 401 and self.jwt and not relogged:
                log.info("Token scaduto, rifaccio il login")
                relogged = True
                if not await self._login():
                    self.jwt = None
                continue
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

    async def live_offers_page(self, since_iso, page_size, after=None):
        """Una pagina di offerte di vendita. Restituisce (offerte, pageInfo)."""
        data = await self.query(LIVE_OFFERS_QUERY, {"since": since_iso, "first": page_size, "after": after})
        conn = data["tokens"]["liveSingleSaleOffers"]
        return conn["nodes"], conn["pageInfo"]

    async def price_history(self, slug, rarity, since_iso, page_size):
        """Vendite Classic dal più recente al più vecchio, fino a `since_iso`."""
        sales, before = [], None
        for _ in range(config.HISTORY_MAX_PAGES):
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
