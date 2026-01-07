import requests
import api
import config

# Lista delle competizioni Top 5 (Slug standard Sorare)
TOP_5_LEAGUES = [
    "serie-a-it",             # Serie A
    "premier-league-gb-eng",  # Premier League
    "laliga-es",              # La Liga
    "bundesliga-de",          # Bundesliga
    "ligue-1-fr"              # Ligue 1
]

QUERY_CLUBS = """
query GetLeagueClubs($slug: String!) {
  football {
    competition(slug: $slug) {
      name
      clubs {
        nodes {
          name
          slug
        }
      }
    }
  }
}
"""

def discover_all_teams():
    print("🚀 Avvio Discovery Top 5 Leghe...\n")
    
    token = api.login(config.SORARE_EMAIL, config.SORARE_PASSWORD)
    if not token:
        print("❌ Login fallito.")
        return

    url = 'https://api.sorare.com/graphql'
    headers = {
        'Content-Type': 'application/json', 
        'Authorization': f'Bearer {token}', 
        'User-Agent': config.USER_AGENT, 
        'JWT-AUD': config.AUDIENCE
    }

    print("COPIA E INCOLLA TUTTO QUESTO IN CONFIG.PY (dentro TARGET_TEAMS):\n")
    print("TARGET_TEAMS = [")

    for league_slug in TOP_5_LEAGUES:
        payload = {
            'query': QUERY_CLUBS,
            'variables': {'slug': league_slug}
        }

        try:
            resp = requests.post(url, json=payload, headers=headers)
            data = resp.json()
            
            if 'errors' in data:
                # Se fallisce lo slug, proviamo a stampare l'errore ma continuiamo
                # print(f"   ⚠️ Errore {league_slug}: {data['errors'][0]['message']}")
                continue

            comp_data = data.get('data', {}).get('football', {}).get('competition')
            if not comp_data: continue
            
            league_name = comp_data['name']
            clubs = comp_data['clubs']['nodes']
            
            print(f"    # --- {league_name} ---")
            for club in clubs:
                print(f'    "{club["slug"]}",  # {club["name"]}')
            print("")

        except Exception as e:
            print(f"# Errore script su {league_slug}: {e}")

    print("]")
    print("\n✅ Fatto.")

if __name__ == "__main__":
    discover_all_teams()