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
2. **Prezzo di riferimento**: per ogni giocatore e rarità calcola la **mediana** delle vendite Classic degli ultimi 30 giorni, o 90 se le vendite sono poche. Si ricalcola ogni 24 ore. Al primo avvio servono circa 45 minuti per tutte le mediane, ma i giocatori con offerte in arrivo vengono calcolati per primi.
3. **Tempo reale**: resta collegato al websocket di Sorare e riceve ogni nuova offerta di vendita entro circa un secondo. Se la connessione cade si ricollega da solo. In più, ogni 5 minuti fa un controllo di sicurezza (ogni minuto se il tempo reale è scollegato).
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

| Modalità | Come | Limite | Va bene? |
|---|---|---|---|
| **API key** (consigliata) | `SORARE_API_KEY` nel `.env`, da [sorare.com/settings/developer](https://sorare.com/settings/developer) | 200 chiamate/min | Sì |
| Login | `SORARE_EMAIL`, `SORARE_PASSWORD`, `AUDIENCE` | 60 chiamate/min | Sì, ma non con la 2FA attiva |
| Anonimo | niente | 20 chiamate/min | No: Sorare blocca dopo pochi minuti |

Con la API key il login non serve: `USE_PASSWORD_LOGIN=0` nel `.env` lo disattiva.

## Altre impostazioni (`config.py`)

| Impostazione | Default | Cosa fa |
|---|---|---|
| `MIN_SALES` | `5` | Vendite minime per fidarsi della mediana |
| `HISTORY_DAYS` / `HISTORY_FALLBACK_DAYS` | `30` / `90` | Finestra delle vendite |
| `REALTIME` | `True` | Tempo reale via websocket |
| `SAFETY_CHECK_SECONDS` | `300` | Controllo di sicurezza con il tempo reale attivo |

## File

- `main.py`: cuore del bot (rose, mediane, valutazione delle offerte, ciclo principale)
- `realtime.py`: offerte in tempo reale dal websocket di Sorare
- `commands.py`: comandi Telegram
- `settings.py`: impostazioni modificabili da Telegram
- `sorare_api.py`: chiamate a Sorare, con limite di chiamate, retry e re-login
- `pricing.py`: conversione in euro, mediana, regola dell'affare
- `notifier.py`: invio e modifica dei messaggi Telegram
- `storage.py`: salvataggio dello stato in `data/`

## Disclaimer

Software a scopo personale. Il trading su Sorare comporta rischi finanziari, e l'uso dell'API è soggetto ai termini di Sorare.
