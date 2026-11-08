"""Salvataggio dello stato su file JSON (scrittura atomica: un crash non corrompe il file)."""
import json
import logging
import os

log = logging.getLogger(__name__)


class JsonStore:
    def __init__(self, path, default, compact=False):
        self.path = path
        self.compact = compact  # file grandi: niente spazi e a capo, più piccolo e veloce da scrivere
        self.data = default
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError) as e:
                log.error("Impossibile leggere %s (%s): riparto da zero", path, e)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            if self.compact:
                json.dump(self.data, f, separators=(",", ":"), ensure_ascii=False)
            else:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
