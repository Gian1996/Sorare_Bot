SorareBot

SorareBot è un software automatizzato in Python per il monitoraggio del mercato di Sorare. Il bot individua opportunità di acquisto (sniping) confrontando i prezzi attuali delle carte "Classic" con la loro media storica e notifica l'utente su Telegram quando viene rilevato uno sconto significativo.
Funzionalità principali
1. Classic Manager Sniper

Il bot analizza il mercato secondario delle carte delle stagioni precedenti:

Analisi Storica: Scarica i dati di vendita degli ultimi 90 giorni per ogni giocatore.

Valutazione Prezzo: Calcola un prezzo medio basato sulle vendite effettive, escludendo le aste delle carte nuove per mantenere i dati accurati.

Notifiche: Invia un messaggio Telegram quando il prezzo più basso (Floor Price) scende sotto una soglia di sconto personalizzabile (es. 20%).

2. Monitoraggio Automatico Squadre

Non è necessario inserire i singoli nomi dei giocatori:

È sufficiente inserire gli "slug" delle squadre nel file di configurazione (es. ac-milan, roma-roma).

Il bot identifica automaticamente tutti i giocatori attivi della rosa e inizia a monitorarli.

3. Memoria Locale (Caching)

Per garantire velocità e rispettare i limiti delle API di Sorare:

Le medie e le statistiche vengono salvate in sorare_memory.json.

Al riavvio, il bot carica i dati locali evitando di scaricare nuovamente migliaia di punti dati, riducendo i tempi di attesa.

4. Conversione ETH/EUR

Il bot interroga l'API di Coinbase per ottenere il tasso di cambio Ethereum -> Euro in tempo reale.

Tutti i calcoli e le notifiche vengono mostrati in Euro.

Architettura del Progetto

main.py: Punto di ingresso del programma. Gestisce il loop principale di scansione.

api.py: Gestisce le chiamate GraphQL verso Sorare (autenticazione, query mercato e rose).

utils.py: Contiene la logica per il calcolo delle medie, la gestione dei messaggi Telegram e dei tassi di cambio.

config.py: Gestisce il caricamento delle credenziali e dei parametri di trading.

watchlists.py: Permette l'aggiunta manuale di giocatori specifici.

Requisiti

Python 3.8 o superiore

Token del Bot Telegram e Chat ID

Account Sorare (Email/Password o Token API)

Installazione e Avvio

Clona il repository
    Bash

    git clone https://github.com/tuo-username/Sorare_Bot.git
    cd Sorare_Bot/script

Installa le dipendenze
    Bash

    pip install -r requirements.txt

Configura l'ambiente Crea un file .env nella cartella principale e inserisci i tuoi dati:
    Plaintext

    TELEGRAM_TOKEN=il_tuo_token
    CHAT_ID=il_tuo_chat_id

Avvia il bot
    Bash

    python main.py

Disclaimer

Questo software è a scopo didattico e di supporto personale. Il trading su Sorare comporta rischi finanziari. L'autore non è responsabile per eventuali perdite economiche derivanti dall'uso di questo bot o da ban dell'account dovuti a un uso improprio delle API.