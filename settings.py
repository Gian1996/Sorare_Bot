"""Impostazioni modificabili da Telegram.

I valori iniziali vengono da config.py. Quello che cambi con i comandi Telegram
viene salvato in data/settings.json e vince su config.py; /reset torna a config.py.
"""
import os

import config
from storage import JsonStore

VALID_RARITIES = ("limited", "rare", "super_rare", "unique")


def _defaults():
    return {
        "competitions": list(config.COMPETITIONS),
        "rarities": list(config.RARITIES),
        "discount_percent": float(config.DISCOUNT_PERCENT),
        "min_discount_eur": float(config.MIN_DISCOUNT_EUR),
        "paused": False,
    }


class Settings:
    def __init__(self):
        self._store = JsonStore(os.path.join(config.DATA_DIR, "settings.json"), {})
        self._values = {**_defaults(), **self._store.data}

    def __getattr__(self, name):
        try:
            return self.__dict__["_values"][name]
        except KeyError:
            raise AttributeError(name) from None

    def set(self, name, value):
        self._values[name] = value
        self._store.data[name] = value
        self._store.save()

    def reset(self):
        self._values = _defaults()
        self._store.data = {}
        self._store.save()
