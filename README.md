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
2. **Prezzo di riferimento**: per ogni giocatore e rarità salva le ultime vendite Classic (fino a 100, negli ultimi 90 giorni) e ne calcola la **mediana pesata**: il peso di una vendita si dimezza ogni 3 giorni (oggi 1, dopo 3 giorni 0,5, dopo 6 giorni 0,25...). Così un calo recente, per esempio un infortunio, conta subito. Le carte In Season non vengono mai mescolate con le Classic.
   - Le vendite nuove arrivano **in tempo reale**: il riferimento si aggiorna pochi secondi dopo ogni vendita. Lo storico completo si riscarica ogni 24 ore.
   - Gli storici vengono preparati in anticipo solo per i giocatori che hanno carte in vendita (circa l'80%), a partire da quelli la cui carta più economica è Classic. Al primo avvio servono circa 30 minuti.
   - Se arriva un'offerta per un giocatore senza storico, il bot lo scarica al volo in pochi secondi e poi valuta l'offerta: anche durante il primo avvio non si perdono affari.
3. **Tempo reale**: resta collegato al websocket di Sorare e riceve ogni nuova offerta di vendita entro circa un secondo. Se la connessione cade si ricollega da solo e, finché è scollegato, controlla le offerte ogni minuto. Non usa sempre il controllo periodico perché il flusso periodico di Sorare a volte è in ritardo anche di un'ora.
4. **Notifica**: ti scrive su Telegram, con le ultime vendite e il link alla carta, solo se tutte queste condizioni sono vere:
   - il prezzo è almeno il 20% sotto il riferimento e costa almeno 1 € in meno;
   - il prezzo è sotto anche la mediana delle **ultime 3 vendite** (se le ultime vendite sono già tutte basse, il prezzo sta calando e non è un affare);
   - quelle ultime 3 vendite sono degli **ultimi 30 giorni** (se il giocatore vende troppo poco, non si sa quanto vale oggi).
5. **Aggiornamento**: se la carta viene comprata o ritirata, il messaggio si aggiorna con "✅ Venduta", "❌ Ritirata" o "⌛ Scaduta".

Prezzi in ETH e USD vengono convertiti in euro con il cambio fornito da Sorare.

## Comandi Telegram

Il bot risponde solo alla chat in `CHAT_ID`.

| Comando | Cosa fa |
|---|---|
| `/stato` | Tempo reale, mediane pronte, offerte viste, impostazioni |
| `/prezzo lautaro` | Prezzo di riferimento e ultime vendite di un giocatore |
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
| `HALF_LIFE_DAYS` | `3` | Ogni quanti giorni il peso di una vendita si dimezza |
| `RECENT_SALES_CHECK` | `3` | Il prezzo deve essere sotto la mediana delle ultime N vendite |
| `RECENT_MAX_AGE_DAYS` | `30` | ...e quelle N vendite devono essere degli ultimi N giorni |
| `MIN_SALES` | `5` | Vendite minime in 90 giorni per fidarsi del riferimento |
| `HISTORY_DAYS` | `90` | Vendite più vecchie non contano |
| `REALTIME` | `True` | Tempo reale via websocket |
| `HISTORY_MAX_SALES` | `100` | Vendite salvate al massimo per giocatore |
| `STATS_CONCURRENCY` | `5` | Storici scaricati in parallelo |
| `MARKET_REFRESH_HOURS` | `6` | Ogni quanto ricontrollare chi ha carte in vendita |

## File

- `main.py`: cuore del bot (rose, mediane, valutazione delle offerte, ciclo principale)
- `realtime.py`: offerte in tempo reale dal websocket di Sorare
- `commands.py`: comandi Telegram
- `settings.py`: impostazioni modificabili da Telegram
- `sorare_api.py`: chiamate a Sorare, con limite di chiamate e retry
- `pricing.py`: conversione in euro, mediana pesata, regola dell'affare
- `notifier.py`: invio e modifica dei messaggi Telegram
- `storage.py`: salvataggio dello stato in `data/`

## Disclaimer

Software a scopo personale. Il trading su Sorare comporta rischi finanziari, e l'uso dell'API è soggetto ai termini di Sorare.
