# api.py (CORRETTO)
import requests
import bcrypt
import time
from datetime import datetime, timedelta
import config
from utils import extract_price

# --- QUERY STORICO (FIX: Rimosso inSeason) ---
# tokenPrices non accetta inSeason. Scarichiamo tutto lo storico.
# La media sarà "mista", il che va bene per evidenziare il basso costo delle Classic.
HISTORY_QUERY = """
query GetPriceHistory($slug: String!, $rarity: Rarity!, $since: ISO8601DateTime!) {
  football {
    player(slug: $slug) {
      slug
      tokenPrices(rarity: $rarity, since: $since, first: 1000) {
        nodes {
          amounts { eurCents wei }
          date
        }
      }
    }
  }
}
"""

# --- QUERY PREZZO ATTUALE CLASSIC ---
# Qui MANTENIAMO inSeason: false perché lowestPriceAnyCard LO SUPPORTA.
# Questo è fondamentale per cercare solo le offerte Classic.
PLAYER_FLOOR_QUERY = """
query GetPlayerFloor($slug: String!) {
  football {
    player(slug: $slug) {
      slug
      floorClassic: lowestPriceAnyCard(rarity: limited, inSeason: false) {
        liveSingleSaleOffer {
          receiverSide { amounts { wei eurCents } }
        }
      }
    }
  }
}
"""

# --- QUERY SQUADRE (FIX: activePlayers -> players) ---
# Usiamo 'players' che è la connessione standard per i giocatori di un club.
TEAM_ROSTER_QUERY = """
query GetTeamPlayers($slug: String!) {
  football {
    club(slug: $slug) {
      name
      players(first: 100) { 
        nodes {
          slug
        }
      }
    }
  }
}
"""

# --- AUTH ---
def get_salt(email):
    try:
        url = f"https://api.sorare.com/api/v1/users/{email}"
        resp = requests.get(url, headers={"User-Agent": config.USER_AGENT})
        if resp.status_code == 200: return resp.json().get('salt')
    except: pass
    return None

def hash_password(password, salt):
    return bcrypt.hashpw(password.encode('utf-8'), salt.encode('utf-8')).decode('utf-8')

def login(email, password):
    salt = get_salt(email)
    if not salt: return None
    hashed_pw = hash_password(password, salt)
    url = 'https://api.sorare.com/graphql'
    headers = {'Content-Type': 'application/json', 'User-Agent': config.USER_AGENT}
    payload = {'query': """mutation SignIn($input: signInInput!) { signIn(input: $input) { jwtToken(aud: "%s") { token expiredAt } } }""" % config.AUDIENCE, 'variables': {'input': {'email': email, 'password': hashed_pw}}}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        token_data = resp.json().get('data', {}).get('signIn', {}).get('jwtToken')
        if token_data: return token_data['token']
    except: pass
    return None

# --- FUNZIONI DATI ---

def fetch_sales_history(token, player_slug, rarity):
    url = 'https://api.sorare.com/graphql'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}', 'User-Agent': config.USER_AGENT, 'JWT-AUD': config.AUDIENCE}
    since_date = (datetime.utcnow() - timedelta(days=90)).isoformat()
    
    payload = {
        'query': HISTORY_QUERY,
        'variables': {'slug': player_slug, 'rarity': rarity, 'since': since_date}
    }

    try:
        print(f"   📊 Studio {player_slug}...", end="", flush=True)
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 401: return "EXPIRED"
        
        data = resp.json()
        if 'errors' in data: 
            # Stampa l'errore ma non crashare
            print(f" ⚠️ API Err: {data['errors'][0]['message']}")
            return []

        nodes = data['data']['football']['player']['tokenPrices']['nodes']
        sales = []
        for sale in nodes:
            if sale['amounts'].get('eurCents'):
                sales.append(sale['amounts']['eurCents'] / 100.0)
        
        # print(f" {len(sales)} vendite.") # Decommenta per debug
        return sales
    except Exception as e: 
        print(f" ⚠️ Py Err: {e}")
        return []

def fetch_classic_floor(token, player_slug, rarity):
    url = 'https://api.sorare.com/graphql'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}', 'User-Agent': config.USER_AGENT, 'JWT-AUD': config.AUDIENCE}
    
    payload = {
        'query': PLAYER_FLOOR_QUERY,
        'variables': {'slug': player_slug}
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 401: return "EXPIRED"
        
        data = resp.json()
        p_data = data.get('data', {}).get('football', {}).get('player')
        
        if not p_data: return None

        offer = p_data.get('floorClassic', {}).get('liveSingleSaleOffer')
        return extract_price(offer)

    except:
        return None

def fetch_team_players(token, club_slug):
    """Scarica i giocatori di una squadra (FIXED)"""
    url = 'https://api.sorare.com/graphql'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}', 'User-Agent': config.USER_AGENT, 'JWT-AUD': config.AUDIENCE}
    
    payload = {
        'query': TEAM_ROSTER_QUERY,
        'variables': {'slug': club_slug}
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        
        # DEBUG: Se c'è un errore API, stampiamolo!
        if 'errors' in data:
            print(f"   ⚠️ Errore Club {club_slug}: {data['errors'][0]['message']}")
            return []
            
        try:
            club_data = data['data']['football']['club']
            if not club_data:
                print(f"   ⚠️ Club non trovato: {club_slug}")
                return []
                
            club_name = club_data['name']
            players = club_data['players']['nodes']
            
            slugs = [p['slug'] for p in players]
            print(f"   ⚽ {club_name}: {len(slugs)} giocatori.")
            return slugs
        except KeyError as e:
            print(f"   ⚠️ Errore struttura dati ({club_slug}): {e}")
            return []
            
    except Exception as e:
        print(f"   ⚠️ Errore Connessione Club {club_slug}: {e}")
        return []