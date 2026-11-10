"""Utenti del bot e impostazioni di ognuno.

L'amministratore è la chat in CHAT_ID (.env). Gli altri utenti scrivono /start, l'amministratore
li approva con /approva, e da lì ognuno ha i suoi campionati, rarità, soglie e pausa.
Ogni utente salva solo le impostazioni che ha cambiato: il resto viene da config.py.
"""
import json
import os
import time

import config
from storage import JsonStore

VALID_RARITIES = ("limited", "rare", "super_rare", "unique")
SEASONS = ("classic", "in_season")
SEASON_LABEL = {"classic": "Classic", "in_season": "In Season"}


# Codici brevi per i pulsanti di Telegram (al massimo 64 caratteri di dati per pulsante).
RARITY_CODE = {"limited": "l", "rare": "r", "super_rare": "s", "unique": "u"}
SEASON_CODE = {"classic": "c", "in_season": "i"}
CODE_RARITY = {v: k for k, v in RARITY_CODE.items()}
CODE_SEASON = {v: k for k, v in SEASON_CODE.items()}


def parse_season(word):
    """"classic", "in_season" oppure None: accetta inseason, in-season, is, classiche..."""
    w = word.lower().replace("-", "").replace("_", "")
    if w in ("classic", "classica", "classiche", "cl"):
        return "classic"
    if w in ("inseason", "is", "stagione"):
        return "in_season"
    return None


def stat_key(slug, rarity, season="classic"):
    """Chiave dello storico. Le Classic restano "slug|rarità" come prima, le In Season hanno "|in_season"."""
    return f"{slug}|{rarity}" if season == "classic" else f"{slug}|{rarity}|{season}"


def parse_key(key):
    """(slug, rarità, stagione) da una chiave di stat_key."""
    parts = key.split("|")
    return parts[0], parts[1], parts[2] if len(parts) > 2 else "classic"
ACTIVE = ("admin", "approved")


def defaults():
    return {
        "competitions": list(config.COMPETITIONS),
        "rarities": list(config.RARITIES),
        "seasons": list(config.SEASONS),
        "discount_percent": float(config.DISCOUNT_PERCENT),
        "min_discount_eur": float(config.MIN_DISCOUNT_EUR),
        "paused": False,
        "max_price_eur": 0.0,  # 0 = nessun limite
        "summary_time": config.DAILY_SUMMARY_TIME,  # riepilogo giornaliero, None = spento
    }


class UserSettings:
    """Impostazioni di un utente: si leggono come attributi (s.rarities), si cambiano con set()."""

    def __init__(self, users, chat_id):
        self._users = users
        self.chat_id = chat_id

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        values = {**defaults(), **self._users.data[self.chat_id]["settings"]}
        if name not in values:
            raise AttributeError(name)
        return values[name]

    def set(self, name, value):
        self._users.data[self.chat_id]["settings"][name] = value
        self._users.save()

    def reset(self):
        self._users.data[self.chat_id]["settings"] = {}
        self._users.save()


class Users:
    def __init__(self, admin_id):
        self.admin_id = str(admin_id)
        path = os.path.join(config.DATA_DIR, "users.json")
        new = not os.path.exists(path)
        self._store = JsonStore(path, {})
        if self.admin_id not in self.data:
            self.data[self.admin_id] = {"name": "admin", "status": "admin", "settings": {}, "since": time.time()}
            if new:
                self._migrate_old_settings()
            self.save()

    @property
    def data(self):
        return self._store.data

    def save(self):
        self._store.save()

    def _migrate_old_settings(self):
        """Prima c'era un solo utente con le impostazioni in settings.json: diventano dell'amministratore."""
        old = os.path.join(config.DATA_DIR, "settings.json")
        if os.path.exists(old):
            try:
                with open(old, encoding="utf-8") as f:
                    self.data[self.admin_id]["settings"] = json.load(f)
                os.replace(old, old + ".migrato")
            except (OSError, ValueError):
                pass

    # --- chi è chi -------------------------------------------------------------------

    def status(self, chat_id):
        return self.data.get(str(chat_id), {}).get("status")

    def is_active(self, chat_id):
        return self.status(chat_id) in ACTIVE

    def is_admin(self, chat_id):
        return str(chat_id) == self.admin_id

    def settings(self, chat_id):
        return UserSettings(self, str(chat_id))

    def active(self):
        """[(chat_id, impostazioni)] di tutti gli utenti abilitati, amministratore compreso."""
        return [(cid, UserSettings(self, cid)) for cid, u in self.data.items() if u["status"] in ACTIVE]

    def add_pending(self, chat_id, name):
        self.data[str(chat_id)] = {"name": name, "status": "pending", "settings": {}, "since": time.time()}
        self.save()

    def approve(self, chat_id):
        self.data[str(chat_id)]["status"] = "approved"
        self.save()

    def remove(self, chat_id):
        self.data.pop(str(chat_id), None)
        self.save()

    # --- watchlist: giocatori seguiti con un prezzo massimo ------------------------
    # Sta fuori da "settings" così /reset non la cancella.

    def watchlist(self, chat_id):
        """[{"slug", "name", "rarity", "season", "price"}] dell'utente, nell'ordine in cui li ha aggiunti.

        "season" manca nelle voci aggiunte prima delle In Season: vuol dire "classic".
        """
        return self.data[str(chat_id)].setdefault("watchlist", [])

    def watch_add(self, chat_id, slug, name, rarity, season, price):
        """Aggiunge o aggiorna (stesso giocatore, rarità e stagione). Restituisce True se era già seguito."""
        items = self.watchlist(chat_id)
        for item in items:
            if item["slug"] == slug and item["rarity"] == rarity and item.get("season", "classic") == season:
                item.update(name=name, price=price, season=season)
                self.save()
                return True
        items.append({"slug": slug, "name": name, "rarity": rarity, "season": season, "price": price})
        self.save()
        return False

    def watch_remove(self, chat_id, index):
        item = self.watchlist(chat_id).pop(index)
        self.save()
        return item

    def watch_remove_card(self, chat_id, slug, rarity, season):
        """Toglie dalla watchlist un giocatore (rarità e stagione). Restituisce la voce tolta o None."""
        items = self.watchlist(chat_id)
        for i, item in enumerate(items):
            if item["slug"] == slug and item["rarity"] == rarity and item.get("season", "classic") == season:
                return self.watch_remove(chat_id, i)
        return None

    def is_watching(self, chat_id, slug, rarity, season):
        return any(item["slug"] == slug and item["rarity"] == rarity and item.get("season", "classic") == season
                   for item in self.data[str(chat_id)].get("watchlist", []))

    # --- giocatori ignorati: niente affari su di loro (la watchlist vince) --------------

    def ignored(self, chat_id):
        """[{"slug", "name"}] dei giocatori ignorati dall'utente."""
        return self.data[str(chat_id)].setdefault("ignored", [])

    def is_ignored(self, chat_id, slug):
        return any(item["slug"] == slug for item in self.data[str(chat_id)].get("ignored", []))

    def ignore(self, chat_id, slug, name):
        if not self.is_ignored(chat_id, slug):
            self.ignored(chat_id).append({"slug": slug, "name": name})
            self.save()

    def unignore(self, chat_id, slug):
        items = self.ignored(chat_id)
        kept = [item for item in items if item["slug"] != slug]
        if len(kept) != len(items):
            self.data[str(chat_id)]["ignored"] = kept
            self.save()
            return True
        return False

    def watchers(self, slug, rarity, season):
        """[(chat_id, impostazioni, prezzo massimo)] degli utenti abilitati che seguono questa carta."""
        found = []
        for chat_id, s in self.active():
            for item in self.data[chat_id].get("watchlist", []):
                if (item["slug"] == slug and item["rarity"] == rarity
                        and item.get("season", "classic") == season):
                    found.append((chat_id, s, item["price"]))
        return found

    # --- cosa monitorare: l'unione delle scelte di tutti gli utenti -----------------

    def all_competitions(self):
        return list(dict.fromkeys(c for _, s in self.active() for c in s.competitions))

    def all_rarities(self):
        return list(dict.fromkeys(r for _, s in self.active() for r in s.rarities))

    def all_seasons(self):
        return list(dict.fromkeys(x for _, s in self.active() for x in s.seasons))
