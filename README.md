# SorareBot

Bot Telegram che controlla il mercato di Sorare **in tempo reale** e ti avvisa quando qualcuno mette in vendita una carta **Classic** a un prezzo molto più basso del solito.

## Come avviarlo

Da terminale, nella cartella `script`:

```bash
./avvia.sh prova   # prova: le notifiche vengono stampate a schermo, niente Telegram
./avvia.sh         # normale: notifiche e comandi su Telegram
```

Per fermarlo: `Ctrl+C`. Deve restare acceso per funzionare: se chiudi il terminale, il bot si ferma.

Prima volta su un nuovo computer:

```bash
python3 -m venv sorare                  # dalla cartella del progetto
sorare/bin/pip install -r script/requirements.txt
cp script/.env.example script/.env      # poi compila .env (token Telegram, API key Sorare)
```

## Come funziona

1. **Giocatori**: per ogni campionato scelto (di default Serie A, Premier League, LaLiga, Bundesliga, Ligue 1) scarica squadre e rose. Si aggiornano da sole ogni 24 ore.
2. **Prezzo di riferimento**: per ogni giocatore e rarità calcola la **mediana** delle vendite Classic (le ultime 100 al massimo, negli ultimi 30 giorni, o 90 se sono poche). Le carte In Season non vengono mai mescolate con le Classic. Si ricalcola ogni 24 ore.
   - Le mediane vengono preparate in anticipo solo per i giocatori che hanno carte in vendita (circa l'80%), a partire da quelli la cui carta più economica è Classic. Al primo avvio servono circa 30 minuti.
   - Se arriva un'offerta per un giocatore senza mediana, il bot la calcola al volo in pochi secondi e poi valuta l'offerta: anche durante il primo avvio non si perdono affari.
3. **Tempo reale**: resta collegato al websocket di Sorare e riceve ogni nuova offerta di vendita entro circa un secondo. Se la connessione cade si ricollega da solo e, finché è scollegato, controlla le offerte ogni minuto. Non usa sempre il controllo periodico perché il flusso periodico di Sorare a volte è in ritardo anche di un'ora.
4. **Notifica**: se il prezzo è almeno il 20% sotto la mediana **e** costa almeno 1 € in meno, ti scrive su Telegram con il link alla carta.
5. **Aggiornamento**: se la carta viene comprata o ritirata, il messaggio si aggiorna con "✅ Venduta", "❌ Ritirata" o "⌛ Scaduta".

Prezzi in ETH e USD vengono convertiti in euro con il cambio fornito da Sorare.

## Comandi Telegram

Il bot risponde solo alla chat in `CHAT_ID`.

| Comando | Cosa fa |
|---|---|
| `/stato` | Tempo reale, mediane pronte, offerte viste, impostazioni |
| `/prezzo lautaro` | Mediana di un giocatore |
| `/soglia 25` | Sconto minimo in % |
| `/minimo 2` | Risparmio minimo in € |
| `/rarita limited rare` | Rarità da monitorare (`limited`, `rare`, `super_rare`, `unique`) |
| `/campionati serie-a-it laliga-es` | Campionati da monitorare (slug Sorare) |
| `/pausa` · `/riprendi` | Sospende o riattiva le notifiche |
| `/reset` | Torna alle impostazioni di `config.py` |

Le impostazioni cambiate da Telegram vengono salvate in `data/settings.json` e **vincono su `config.py`**.

Slug dei campionati principali: `serie-a-it`, `premier-league-gb-eng`, `laliga-es`, `bundesliga-de`, `ligue-1-fr`, `primeira-liga-pt`.

## Accesso a Sorare

Il bot usa solo una **API key**, da mettere come `SORARE_API_KEY` nel `.env`. La crei su [sorare.com/settings/developer](https://sorare.com/settings/developer) (200 chiamate/min).
Non usa mai email e password del tuo account, e legge soltanto dati pubblici: non compra né vende nulla.
Senza API key funziona solo per brevi prove: il limite è di 20 chiamate/min e Sorare blocca dopo pochi minuti.

## Altre impostazioni (`config.py`)

| Impostazione | Default | Cosa fa |
|---|---|---|
| `MIN_SALES` | `5` | Vendite minime per fidarsi della mediana |
| `HISTORY_DAYS` / `HISTORY_FALLBACK_DAYS` | `30` / `90` | Finestra delle vendite |
| `REALTIME` | `True` | Tempo reale via websocket |
| `HISTORY_MAX_SALES` | `100` | Mediana sulle ultime N vendite al massimo |
| `STATS_CONCURRENCY` | `3` | Mediane calcolate in parallelo |
| `MARKET_REFRESH_HOURS` | `6` | Ogni quanto ricontrollare chi ha carte in vendita |

## File

- `main.py`: cuore del bot (rose, mediane, valutazione delle offerte, ciclo principale)
- `realtime.py`: offerte in tempo reale dal websocket di Sorare
- `commands.py`: comandi Telegram
- `settings.py`: impostazioni modificabili da Telegram
- `sorare_api.py`: chiamate a Sorare, con limite di chiamate e retry
- `pricing.py`: conversione in euro, mediana, regola dell'affare
- `notifier.py`: invio e modifica dei messaggi Telegram
- `storage.py`: salvataggio dello stato in `data/`

## Disclaimer

Software a scopo personale. Il trading su Sorare comporta rischi finanziari, e l'uso dell'API è soggetto ai termini di Sorare.
