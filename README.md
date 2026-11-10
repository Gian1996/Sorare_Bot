# SorareBot

Bot Telegram che controlla il mercato di Sorare **in tempo reale** e ti avvisa quando qualcuno mette in vendita una carta a un prezzo molto più basso del solito. Di default solo **Classic**; con `/stagioni` anche **In Season**.

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
2. **Prezzo di riferimento**: per ogni giocatore, rarità e stagione (Classic o In Season) salva le ultime vendite (fino a 100, negli ultimi 90 giorni) e ne calcola la **mediana pesata**: il peso di una vendita si dimezza ogni 3 giorni (oggi 1, dopo 3 giorni 0,5, dopo 6 giorni 0,25...). Così un calo recente, per esempio un infortunio, conta subito. Classic e In Season hanno **sempre riferimenti separati**: non vengono mai mescolate.
   - Contano **solo le vendite a prezzo fisso tra manager** (carta messa sul mercato e comprata). Non contano le aste, le vendite dirette di Sorare (che per le In Season sono tante e a prezzi diversi) e le offerte d'acquisto accettate, che di solito sono al ribasso. Anche tra le offerte in arrivo il bot guarda solo le carte messe sul mercato dai manager, non le offerte private tra due manager.
   - Le vendite nuove arrivano **in tempo reale**: il riferimento si aggiorna pochi secondi dopo ogni vendita. Lo storico completo si riscarica ogni 24 ore.
   - Gli storici vengono preparati in anticipo solo per i giocatori che hanno carte in vendita (circa l'80%), a partire da quelli la cui carta più economica è Classic. Al primo avvio servono circa 30 minuti.
   - In Season: Sorare dice solo qual è la carta più economica in vendita, quindi lo storico In Season si prepara in anticipo solo per chi ha una In Season come carta più economica. Per gli altri si scarica al volo alla prima offerta (qualche secondo).
   - Se arriva un'offerta per un giocatore senza storico, il bot lo scarica al volo in pochi secondi e poi valuta l'offerta: anche durante il primo avvio non si perdono affari.
3. **Tempo reale**: resta collegato al websocket di Sorare e riceve ogni nuova offerta di vendita entro circa un secondo. Se la connessione cade si ricollega da solo e, finché è scollegato, controlla le offerte ogni minuto. Non usa sempre il controllo periodico perché il flusso periodico di Sorare a volte è in ritardo anche di un'ora.
4. **Notifica**: ti scrive su Telegram, con le ultime vendite e il link alla carta, solo se tutte queste condizioni sono vere:
   - il prezzo è almeno il 20% sotto il riferimento e costa almeno 1 € in meno;
   - il prezzo è sotto anche la mediana delle **ultime 3 vendite** (se le ultime vendite sono già tutte basse, il prezzo sta calando e non è un affare);
   - quelle ultime 3 vendite sono degli **ultimi 30 giorni** (se il giocatore vende troppo poco, non si sa quanto vale oggi).
5. **Pulsanti** sotto ogni notifica: «🔗 Apri su Sorare», «📈 Prezzi» (riferimento e ultime 8 vendite di quel giocatore) e «🔇 Ignora giocatore» (niente più affari su di lui; il pulsante diventa «🔔 Non ignorare più» per annullare). Sotto gli avvisi della watchlist, al posto di Ignora c'è «🗑 Non seguire più».
6. **Aggiornamento**: se la carta viene comprata o ritirata, il messaggio si aggiorna con "✅ Venduta", "❌ Ritirata" o "⌛ Scaduta".

Prezzi in ETH e USD vengono convertiti in euro con il cambio fornito da Sorare.

## Comandi Telegram

| Comando | Cosa fa |
|---|---|
| `/stato` | Tempo reale, storici pronti, offerte viste, le tue impostazioni |
| `/prezzo lautaro` | Prezzo di riferimento e ultime vendite di un giocatore |
| `/soglia 25` | Sconto minimo in % |
| `/minimo 2` | Risparmio minimo in € |
| `/massimo 50` | Prezzo massimo degli affari (`/massimo 0` = nessun limite) |
| `/rarita limited rare` | Rarità da monitorare (`limited`, `rare`, `super_rare`, `unique`) |
| `/stagioni classic inseason` | Classic, In Season o entrambe (di default solo Classic) |
| `/campionati serie-a-it laliga-es` | Campionati da monitorare (slug Sorare) |
| `/pausa` · `/riprendi` | Sospende o riattiva le notifiche |
| `/riepilogo 21:00` | Ora del riepilogo giornaliero (`off` per spegnerlo, `ora` per vederlo subito) |
| `/ignorati` | Giocatori ignorati (dal pulsante 🔇 o con `/ignora nome`); `/nonignorare 2` per riattivarne uno. La watchlist vince: chi segui ti arriva comunque |
| `/bilancio 7` | Quanti avvisi sono stati comprati davvero e quanto in fretta |
| `/reset` | Torna alle impostazioni di `config.py` (la watchlist resta) |
| `/segui lautaro 15 limited` | Watchlist: avvisami se una sua carta Classic va in vendita a 15 € o meno (`inseason` in fondo per le In Season) |
| `/lista` | Giocatori seguiti, con il loro prezzo di riferimento |
| `/nonseguire 2` | Smetti di seguire un giocatore (numero da `/lista`, o il nome) |

### Riepilogo giornaliero

Ogni giorno alle 21:00 (ora italiana) il bot ti manda il riepilogo delle ultime 24 ore: quanti affari e avvisi della watchlist hai ricevuto, quanti sono stati comprati (esiti chiesti a Sorare) e il miglior affare del giorno con com'è finito. L'amministratore vede anche come è andato il bot: quanto è rimasto collegato il tempo reale, offerte viste, vendite registrate, riavvii imprevisti e problemi.

Arriva anche nei giorni senza affari, quindi fa anche da conferma che il bot è vivo. `/riepilogo 8:30` cambia l'ora, `/riepilogo off` lo spegne, `/riepilogo ora` lo mostra subito. Ognuno ha il suo; l'ora predefinita è `DAILY_SUMMARY_TIME` in `config.py`.

### Bilancio

`/bilancio` (o `/bilancio 7` per gli ultimi 7 giorni) dice com'è andata con gli avvisi che hai ricevuto negli ultimi 30 giorni:

- quante carte segnalate sono state **comprate**, ritirate, scadute o sono ancora in vendita;
- quanto in fretta sono state comprate dopo l'avviso (entro 1 min, 10 min, 1 ora...);
- quanto **ritardo** hanno gli avvisi rispetto alla messa in vendita (col tempo reale sono pochi secondi);
- la percentuale di comprate per fascia di sconto (sotto il 30%, 30-50%, oltre il 50%) e per la watchlist.

L'esito di ogni offerta viene chiesto a Sorare, quindi è giusto anche per le offerte chiuse mentre il bot era spento. Serve a regolare `/soglia`: se le carte segnalate vengono comprate in pochi minuti erano affari veri; se restano in vendita per ore o vengono ritirate, la soglia è troppo bassa.

### Watchlist

Con `/segui` il bot ti avvisa per un giocatore preciso appena una sua carta va in vendita sotto il prezzo che scegli, **anche se non è un affare** rispetto alla mediana: per esempio se ti serve per la squadra e sai già quanto vuoi spendere.

- Di default segue le Classic; per le In Season aggiungi `inseason`: `/segui haaland 40 limited inseason`. Per seguirle entrambe usa `/segui` due volte, con due prezzi diversi.
- La rarità si può omettere se ne monitori una sola (es. solo `limited`).
- Se il nome è ambiguo (`/segui lautaro`), il bot ti mostra gli slug tra cui scegliere.
- Puoi seguire anche giocatori **fuori dai tuoi campionati**: in quel caso scrivi lo slug, cioè l'ultima parte dell'indirizzo della pagina del giocatore su Sorare (es. `/segui cristiano-ronaldo-dos-santos-aveiro 30 limited`).
- Se la carta è anche un affare, ricevi un solo messaggio, quello della watchlist, con il riferimento e lo sconto.
- La watchlist è di ognuno, non viene toccata da `/reset` e si ferma con `/pausa`. Massimo 50 giocatori.

Slug dei campionati principali: `serie-a-it`, `premier-league-gb-eng`, `laliga-es`, `bundesliga-de`, `ligue-1-fr`, `primeira-liga-pt`.

## Più utenti

L'**amministratore** è la chat in `CHAT_ID` nel `.env`. Per far provare il bot a un amico:

1. L'amico cerca il bot su Telegram e gli scrive `/start`.
2. All'amministratore arriva la richiesta con nome e id: risponde `/approva <id>` oppure `/rifiuta <id>`.
3. Da lì l'amico usa gli stessi comandi, ma sulle **sue** impostazioni: campionati, rarità, soglie e pausa sono di ognuno.

Il bot monitora l'unione dei campionati e delle rarità scelte da tutti e manda a ciascuno solo gli affari che rispettano le sue regole. Chi non è approvato non riceve niente e non può usare i comandi.

Comandi solo per l'amministratore: `/utenti` (elenco con le impostazioni di ognuno), `/approva <id>`, `/rifiuta <id>`, `/rimuovi <id>`, `/log`.

`/log` mostra le ultime 20 righe di `logs/bot.log` (`/log 50` per vederne di più, fino a 100), `/log errori` solo avvisi, errori e riavvii, `/log file` manda tutto il file. Il log c'è solo se il bot è avviato con `servizio.sh`.

Utenti e impostazioni sono salvati in `data/users.json`. Le impostazioni cambiate da Telegram vincono su `config.py`.

## Avvisi all'amministratore

Il bot scrive all'amministratore (`CHAT_ID`) quando qualcosa non va, così sul telefono te ne accorgi senza aprire Termux:

| Avviso | Quando |
|---|---|
| ⚠️ Ripartito dopo un arresto imprevisto | Al riavvio, se la volta prima non era stato fermato con `ferma.sh`. Dice da quanto era fermo e perché: «codice 137» vuol dire che l'ha chiuso Android; nessun codice vuol dire che si erano spenti il telefono o Termux. |
| 🔴 Tempo reale scollegato | Il websocket resta scollegato più di 10 minuti (`REALTIME_ALERT_MINUTES`). |
| ⚠️ Sorare rallenta il bot | Più di 10 risposte «troppe richieste» (429) in 10 minuti: di solito c'è un'altra copia del bot accesa con la stessa API key. |
| ⚠️ Problemi a raggiungere Sorare | Più di 10 errori di rete o del server in 10 minuti. |
| ⛔ API key rifiutata | Sorare risponde 401: la key è scaduta o cancellata. |
| ⚠️ Aggiornamenti falliti | Il giro di aggiornamento fallisce 3 volte di fila. |
| ⚠️ Un'altra copia del bot | Un altro processo usa lo stesso token Telegram (per esempio il bot rimasto acceso sul PC). |

Ogni problema arriva una volta, viene ricordato ogni 6 ore se continua (`ALERT_REMIND_HOURS`) e quando si risolve arriva «✅ ... (problema durato X)». In `/stato` l'amministratore vede i problemi in corso. Quando lo fermi con `ferma.sh` o `Ctrl+C` il bot salva tutto e scrive «⏹ SorareBot fermato». Si spengono tutti con `ADMIN_ALERTS = False` in `config.py`.

Se Android chiude Termux del tutto, nessuno può avvisarti finché non riparte: in quel caso lo scopri dal messaggio «Ripartito dopo un arresto imprevisto».

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
- `users.py`: utenti, approvazioni e impostazioni di ognuno
- `sorare_api.py`: chiamate a Sorare, con limite di chiamate e retry
- `pricing.py`: conversione in euro, mediana pesata, regola dell'affare
- `notifier.py`: invio e modifica dei messaggi Telegram
- `storage.py`: salvataggio dello stato in `data/`

## Disclaimer

Software a scopo personale. Il trading su Sorare comporta rischi finanziari, e l'uso dell'API è soggetto ai termini di Sorare.

## Sul telefono (Termux), sempre acceso

```bash
./servizio.sh &          # avvia il bot e lo riavvia da solo se si ferma (log in logs/bot.log)
tail -f logs/bot.log     # guarda cosa sta facendo (Ctrl+C per uscire, il bot resta acceso)
./ferma.sh               # spegne il bot
```

Per farlo partire da solo quando il telefono si accende, installa l'app **Termux:Boot**
(da F-Droid, aprila una volta) e crea `~/.termux/boot/sorare.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sleep 30   # aspetta che arrivi il Wi-Fi
~/Sorare_bot/script/servizio.sh
```
