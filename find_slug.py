import requests

# Inserisci qui parte del nome della squadra che cerchi
SEARCH_TERM = "Milan" 

query = """
query SearchTeam($term: String!) {
  football {
    clubs(search: $term) {
      nodes {
        name
        slug
      }
    }
  }
}
"""

url = 'https://api.sorare.com/graphql'
response = requests.post(url, json={'query': query, 'variables': {'term': SEARCH_TERM}})
data = response.json()

print(f"--- Risultati per '{SEARCH_TERM}' ---")
for club in data['data']['football']['clubs']['nodes']:
    print(f"Nome: {club['name']} | Slug API: {club['slug']}")