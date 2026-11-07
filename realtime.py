"""Offerte in tempo reale dal websocket di Sorare (protocollo ActionCable).

Si riconnette da solo se la connessione cade o se Sorare smette di mandare dati.
"""
import asyncio
import json
import logging
import time

import websockets

import config
from sorare_api import OFFER_SUBSCRIPTION

log = logging.getLogger(__name__)

URL = "wss://ws.sorare.com/cable"
IDENTIFIER = json.dumps({"channel": "GraphqlChannel"})
RECV_TIMEOUT = 30        # Sorare manda un ping ogni ~3s: se tace per 30s la connessione è morta
SILENCE_RECONNECT = 180  # nessuna offerta per 3 minuti = Sorare ha smesso di mandarle


class OfferStream:
    def __init__(self, on_offer, on_connected):
        self.on_offer = on_offer
        self.on_connected = on_connected
        self.connected = False
        self.events = 0
        self.last_event = None
        self.reconnects = 0

    async def run(self):
        delay = 5
        while True:
            started = time.monotonic()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # qualsiasi errore: riconnessione
                log.warning("Tempo reale disconnesso (%s), riprovo tra %ds", e or type(e).__name__, delay)
            if self.connected:
                self.reconnects += 1
            self.connected = False
            if time.monotonic() - started > 300:
                delay = 5  # la connessione era durata: non è un problema persistente
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

    async def _session(self):
        headers = {"User-Agent": config.USER_AGENT}
        if config.SORARE_API_KEY:
            headers["APIKEY"] = config.SORARE_API_KEY
        async with websockets.connect(URL, subprotocols=["actioncable-v1-json"],
                                      additional_headers=headers, open_timeout=20) as ws:
            await ws.send(json.dumps({"command": "subscribe", "identifier": IDENTIFIER}))
            last_offer = time.monotonic()
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), RECV_TIMEOUT))
                kind = message.get("type")
                if time.monotonic() - last_offer > SILENCE_RECONNECT:
                    raise ConnectionError("nessuna offerta da 3 minuti")
                if kind in ("ping", "welcome"):
                    continue
                if kind == "confirm_subscription":
                    await ws.send(json.dumps({
                        "command": "message", "identifier": IDENTIFIER,
                        "data": json.dumps({"query": OFFER_SUBSCRIPTION, "variables": {},
                                            "operationName": "OnOfferUpdated", "action": "execute"}),
                    }))
                    continue
                if kind in ("reject_subscription", "disconnect"):
                    raise ConnectionError(f"{kind} {message.get('reason', '')}".strip())

                result = (message.get("message") or {}).get("result") or {}
                if result.get("errors"):
                    raise ConnectionError("; ".join(e.get("message", "?") for e in result["errors"]))
                if not self.connected:
                    self.connected = True
                    log.info("Tempo reale collegato")
                    await self.on_connected()
                offer = (result.get("data") or {}).get("tokenOfferWasUpdated")
                if not offer:
                    continue
                last_offer = time.monotonic()
                self.events += 1
                self.last_event = time.time()
                try:
                    await self.on_offer(offer)
                except Exception:
                    log.exception("Errore nel gestire un'offerta in tempo reale")
