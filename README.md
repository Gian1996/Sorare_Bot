# SorareBot

Bot Telegram che controlla il mercato di Sorare e ti avvisa quando qualcuno mette in vendita una carta **Classic** di un giocatore del campionato che ti interessa a un prezzo molto più basso del solito.

## Come funziona

1. **Giocatori**: per ogni campionato in `config.COMPETITIONS` (di default i 5 campionati "Champion": Serie A, Premier League, LaLiga, Bundesliga, Ligue 1) scarica tutte le squadre e le loro rose. Si aggiornano da sole ogni 24 ore. Altri giocatori si possono aggiungere in `watchlists.py`.
2. **Prezzo di riferimento**: per ogni giocatore calcola la **mediana** delle vendite Classic degli ultimi 30 giorni. Se le vendite sono meno di 5, usa gli ultimi 90 giorni. La mediana non si fa ingannare dalle poche vendite fuori mercato (numeri di maglia, #1...). Si ricalcola ogni 24 ore.
3. **Mercato**: ogni 60 secondi legge tutte le nuove offerte di vendita su Sorare. Tiene solo quelle Classic, della rarità scelta, di giocatori monitorati.
4. **Notifica**: se il prezzo è almeno il 20% sotto la mediana **e** costa almeno 1 € in meno, manda un messaggio Telegram con il link alla carta. Ogni offerta viene notificata una volta sola, anche dopo un riavvio.

Prezzi in ETH e USD vengono convertiti in euro con il cambio fornito da Sorare.

## Installazione

```bash
cd script
python -m venv ../sorare          # solo la prima volta
source ../sorare/bin/activate
pip install -r requirements.txt
cp .env.example .env              # poi compila .env
python main.py
```

## Accesso a Sorare

| Modalità | Come | Limite | Va bene? |
|---|---|---|---|
| **API key** (consigliata) | `SORARE_API_KEY` nel `.env`, da [sorare.com/settings/developer](https://sorare.com/settings/developer) | 200 chiamate/min | Sì |
| Login | `SORARE_EMAIL`, `SORARE_PASSWORD`, `AUDIENCE` | 60 chiamate/min | Sì, ma non con la 2FA attiva |
| Anonimo | niente | 20 chiamate/min | Solo per prove: Sorare blocca le chiamate dopo pochi minuti |

## Impostazioni (`config.py`)

| Impostazione | Default | Cosa fa |
|---|---|---|
| `COMPETITIONS` | i 5 top campionati | Campionati da monitorare |
| `RARITIES` | `["limited"]` | Rarità: `limited`, `rare`, `super_rare`, `unique` |
| `DISCOUNT_PERCENT` | `20` | Sconto minimo rispetto alla mediana |
| `MIN_DISCOUNT_EUR` | `1.0` | Risparmio minimo in euro |
| `MIN_SALES` | `5` | Vendite minime per fidarsi della mediana |
| `HISTORY_DAYS` / `HISTORY_FALLBACK_DAYS` | `30` / `90` | Finestra delle vendite |
| `CHECK_INTERVAL_SECONDS` | `60` | Ogni quanto leggere le nuove offerte |

Con `DRY_RUN=1` nel `.env` le notifiche vengono solo stampate a schermo.

## File

- `main.py`: ciclo principale (rose, mediane, controllo offerte, notifiche)
- `sorare_api.py`: chiamate a Sorare, con limite di chiamate, retry e re-login
- `pricing.py`: conversione in euro, mediana, regola dell'affare
- `notifier.py`: invio su Telegram
- `storage.py`: salvataggio dello stato in `data/`
- `schema.graphql`: schema dell'API Sorare (aggiornalo con `curl -o schema.graphql https://api.sorare.com/graphql/schema`)

## Disclaimer

Software a scopo personale. Il trading su Sorare comporta rischi finanziari, e l'uso dell'API è soggetto ai termini di Sorare.
