import os
from dotenv import load_dotenv

# Carica le variabili dal file .env
load_dotenv() # TELEGRAM & SORARE AUTH 
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# SORARE AUTH
SORARE_EMAIL = os.getenv('SORARE_EMAIL')
SORARE_PASSWORD = os.getenv('SORARE_PASSWORD')
AUDIENCE = os.getenv('AUDIENCE')
USER_AGENT = os.getenv('USER_AGENT')

# IMPOSTAZIONI
INTERVALLO_CONTROLLO = 600  # 10 minuti
MAX_CARDS_SCAN = 5000       # Limite deep scan

# IMPOSTAZIONI SORARE
COMPETITIONS = ["champion-europe"]  # Competizione dove cercare le carte
SNIPER_THRESHOLD_PERCENT = 20              # Percentuale per calcolo soglie di acquisto/vendita
ONLY_CLASSIC = True                 # Considera solo carte Classic (non In Season)

TARGET_TEAMS = [
    "internazionale-milano",            # Inter
    "roma-roma",                        # Roma
    "milan-milano",                     # Milan
    "fulham-london",                    # Fulham
    "freiburg-freiburg-im-breisgau",    # Friburgo (SC Freiburg)
    "atalanta-ciserano"                 # Atalanta
]