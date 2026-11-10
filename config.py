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
# "classic" (stagioni passate) e/o "in_season" (stagione in corso): hanno sempre riferimenti separati.
SEASONS = ["classic"]

# Nota: campionati, rarità e soglie si possono cambiare anche da Telegram (/aiuto).
# I valori cambiati da Telegram vincono su questi; /reset torna a quelli di config.py.

# --- QUANDO NOTIFICARE --------------------------------------------------------
# Notifica se il prezzo è almeno questa percentuale sotto il prezzo di riferimento
# (mediana pesata delle vendite, vedi PREZZO DI RIFERIMENTO più sotto)...
DISCOUNT_PERCENT = 20
# ...e lo sconto vale almeno questi euro (evita notifiche su carte da 0,40 €).
MIN_DISCOUNT_EUR = 1.0
# Numero minimo di vendite in HISTORY_DAYS per fidarsi del prezzo di riferimento.
MIN_SALES = 5

# --- PREZZO DI RIFERIMENTO ----------------------------------------------------
# Mediana pesata: il peso di una vendita si dimezza ogni HALF_LIFE_DAYS giorni
# (oggi 1, dopo 3 giorni 0,5, dopo 6 giorni 0,25...). Più basso = segue più in fretta i cali.
HALF_LIFE_DAYS = 3
# Il prezzo deve essere anche sotto la mediana delle ultime N vendite:
# se le ultime vendite sono già tutte basse (infortunio, panchina...) non è un affare.
RECENT_SALES_CHECK = 3
# ...e quelle ultime N vendite devono essere tutte degli ultimi N giorni, altrimenti
# il giocatore vende troppo poco per sapere quanto vale oggi.
RECENT_MAX_AGE_DAYS = 30
HISTORY_DAYS = 90           # vendite più vecchie di così non contano
STATS_REFRESH_HOURS = 24    # ogni quanto riscaricare lo storico (le vendite nuove arrivano in tempo reale)
ROSTER_REFRESH_HOURS = 24   # ogni quanto riscaricare le rose delle squadre
MARKET_REFRESH_HOURS = 6    # ogni quanto ricontrollare quali giocatori hanno carte in vendita
HISTORY_MAX_SALES = 100     # mediana sulle ultime N vendite al massimo (di solito 1 sola chiamata)
HISTORY_MAX_PAGES = 10      # limite di sicurezza alle pagine di storico per giocatore
STATS_CONCURRENCY = 5       # storici scaricati in parallelo (il limite di chiamate resta 180/min)

# --- RITMO --------------------------------------------------------------------
REALTIME = True               # offerte in tempo reale via websocket (consigliato)
CHECK_INTERVAL_SECONDS = 60   # controllo periodico delle offerte se il tempo reale è spento o caduto
SAFETY_CHECK_SECONDS = 300    # con il tempo reale attivo: ogni quanto fare manutenzione (cambio, rose)
OFFERS_BACKFILL_MINUTES = 5   # all'avvio, guarda anche le offerte degli ultimi N minuti
OFFERS_MAX_PAGES = 100        # limite di sicurezza alle pagine di offerte per giro
# Riepilogo giornaliero: ora predefinita (ognuno la cambia con /riepilogo, None = spento).
DAILY_SUMMARY_TIME = "21:00"
TIMEZONE = "Europe/Rome"

# Avvisi all'amministratore (CHAT_ID): riavvii, tempo reale scollegato, problemi con Sorare o Telegram.
ADMIN_ALERTS = True
REALTIME_ALERT_MINUTES = 10   # avvisa se il tempo reale resta scollegato più di così
API_ALERT_ERRORS = 10         # avvisa se ci sono più di tanti 429 (o errori di rete) in 10 minuti
ALERT_REMIND_HOURS = 6        # se un problema continua, ricordalo ogni N ore
NOTIFIED_MEMORY_DAYS = 30     # per quanto ricordare le offerte notificate (e quindi il /bilancio)

# Cartella dove il bot salva il suo stato (mediane, notifiche inviate, rose).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
