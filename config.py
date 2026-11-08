"""Configurazione del bot.

I segreti (token Telegram, API key Sorare) stanno nel file .env.
Le impostazioni di trading stanno qui sotto e si possono modificare liberamente.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sì", "on")


# --- SEGRETI (.env) ---------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Consigliata: API key gratuita da https://sorare.com/settings/developer
# (alza il limite da 20 a 200 chiamate al minuto).
SORARE_API_KEY = os.getenv("SORARE_API_KEY")

USER_AGENT = os.getenv("USER_AGENT") or "sorare-sniper-bot/2.0"

# DRY_RUN=1 nel .env: le notifiche vengono solo stampate, non inviate.
DRY_RUN = _env_bool("DRY_RUN", default=False)

# --- COSA MONITORARE ----------------------------------------------------------
# Slug Sorare dei campionati: il bot prende da solo tutte le squadre e le rose.
# "Champion" di Sorare = i 5 top campionati europei. Altri esempi: "primeira-liga-pt" (Liga Portugal).
COMPETITIONS = [
    "serie-a-it",             # Serie A
    "premier-league-gb-eng",  # Premier League
    "laliga-es",              # LaLiga
    "bundesliga-de",          # Bundesliga
    "ligue-1-fr",             # Ligue 1
]

# Rarità da monitorare: "limited", "rare", "super_rare", "unique".
RARITIES = ["limited", "rare"]

# Nota: campionati, rarità e soglie si possono cambiare anche da Telegram (/aiuto).
# I valori cambiati da Telegram vincono su questi; /reset torna a quelli di config.py.

# --- QUANDO NOTIFICARE --------------------------------------------------------
# Notifica se il prezzo è almeno questa percentuale sotto la mediana delle vendite.
DISCOUNT_PERCENT = 20
# ...e lo sconto vale almeno questi euro (evita notifiche su carte da 0,40 €).
MIN_DISCOUNT_EUR = 1.0
# Numero minimo di vendite nel periodo per fidarsi della mediana.
MIN_SALES = 5

# --- STORICO PREZZI -----------------------------------------------------------
HISTORY_DAYS = 30           # finestra delle vendite Classic su cui calcolare la mediana
HISTORY_FALLBACK_DAYS = 90  # se in HISTORY_DAYS ci sono meno di MIN_SALES vendite, usa questa
STATS_REFRESH_HOURS = 24    # ogni quanto ricalcolare la mediana di un giocatore
ROSTER_REFRESH_HOURS = 24   # ogni quanto riscaricare le rose delle squadre
HISTORY_MAX_PAGES = 10      # limite di sicurezza alle pagine di storico per giocatore

# --- RITMO --------------------------------------------------------------------
REALTIME = True               # offerte in tempo reale via websocket (consigliato)
CHECK_INTERVAL_SECONDS = 60   # controllo periodico delle offerte se il tempo reale è spento o caduto
SAFETY_CHECK_SECONDS = 300    # con il tempo reale attivo, controllo di sicurezza ogni 5 minuti
OFFERS_BACKFILL_MINUTES = 5   # all'avvio, guarda anche le offerte degli ultimi N minuti
OFFERS_MAX_PAGES = 100        # limite di sicurezza alle pagine di offerte per giro
NOTIFIED_MEMORY_DAYS = 14     # per quanto ricordare le offerte già notificate

# Cartella dove il bot salva il suo stato (mediane, notifiche inviate, rose).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
