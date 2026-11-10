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
ACTIVE = ("admin", "approved")


def defaults():
    return {
        "competitions": list(config.COMPETITIONS),
        "rarities": list(config.RARITIES),
        "discount_percent": float(config.DISCOUNT_PERCENT),
        "min_discount_eur": float(config.MIN_DISCOUNT_EUR),
        "paused": False,
        "max_price_eur": 0.0,  # 0 = nessun limite
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
        """[{"slug", "name", "rarity", "price"}] dell'utente, nell'ordine in cui li ha aggiunti."""
        return self.data[str(chat_id)].setdefault("watchlist", [])

    def watch_add(self, chat_id, slug, name, rarity, price):
        """Aggiunge o aggiorna (stesso giocatore e rarità). Restituisce True se era già seguito."""
        items = self.watchlist(chat_id)
        for item in items:
            if item["slug"] == slug and item["rarity"] == rarity:
                item.update(name=name, price=price)
                self.save()
                return True
        items.append({"slug": slug, "name": name, "rarity": rarity, "price": price})
        self.save()
        return False

    def watch_remove(self, chat_id, index):
        item = self.watchlist(chat_id).pop(index)
        self.save()
        return item

    def watchers(self, slug, rarity):
        """[(chat_id, impostazioni, prezzo massimo)] degli utenti abilitati che seguono questa carta."""
        found = []
        for chat_id, s in self.active():
            for item in self.data[chat_id].get("watchlist", []):
                if item["slug"] == slug and item["rarity"] == rarity:
                    found.append((chat_id, s, item["price"]))
        return found

    # --- cosa monitorare: l'unione delle scelte di tutti gli utenti -----------------

    def all_competitions(self):
        return list(dict.fromkeys(c for _, s in self.active() for c in s.competitions))

    def all_rarities(self):
        return list(dict.fromkeys(r for _, s in self.active() for r in s.rarities))
